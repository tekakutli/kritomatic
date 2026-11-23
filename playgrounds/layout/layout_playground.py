"""Layout playground — entry point.

    python layout_playground.py

Writes layout.html, serves it at http://127.0.0.1:8777/.  State is
served at /state for the `DrawatoonSetIP` ComfyUI node.

Registers with the kritomatic daemon on startup.  The docker renders
a "Playground Input" section with one row per (playground, layer
slot) — for this playground, one row for `@thelayout` with a
`[source▼][layer]` picker.  The playground polls that selector and
re-fetches the displayed layer whenever it changes.  When the daemon
dies, the playground exits (background watcher, ~10s of consecutive
failures); the user restarts both manually.

State shape (POST /state):

    {
      "op": "set",
      "state": {
        "text_bboxes":          [[x1,y1,x2,y2], ...],
        "character_bboxes":     [[x1,y1,x2,y2], ...],
        "reference_embeddings": [null | [768 floats], ...],
        "image_dims":           [W, H],
        "viewer":               {"x","y","w","h","ratio"}
      }
    }

All bboxes are normalized [0,1] in (x1,y1,x2,y2) order.

drawatoon is a **512-px model**.  The workflow uses a
`PixArtResolutionSelect`-shaped node at ~512 area; this playground's
`/generate` overrides `EmptyLatentImage.width/.height` at submit time
based on the current viewer ratio, so the working size is
`_LATENT_PRESET_512[ratio]`.

Fixed port, no silent fallback
==============================
`DrawatoonSetIP` in the ComfyUI workflow hardcodes
`http://127.0.0.1:8777/state`.  If this playground silently bound to a
different port (e.g., because 8777 was occupied by a stale process),
the node would fail to fetch bboxes and fall back to `void_ip_embed`
— generation would succeed but **layout boxes would have no effect**,
with no error anywhere.

So: bind first, verify the port matches `PORT`, and refuse to start
if it doesn't.  The failure mode becomes a loud startup error
instead of a silent misbehaviour downstream.

The previous flow (register with the daemon at `PORT`, then
`kit_serve(port=PORT)` which silently falls back) was exactly this
trap.  It worked only when nothing else happened to be on 8777.

Signal handling
===============
SIGINT and SIGTERM raise SystemExit, which propagates out of
`serve_forever()` and runs the surrounding `try/finally` (plus the
atexit-registered `_cleanup`).  Calling `httpd.shutdown()` directly
from a signal handler would deadlock: `shutdown()` blocks until
`serve_forever()` returns, but `serve_forever()` is paused inside
the handler that's blocking it.
"""

import atexit
import os
import signal
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLAYGROUNDS = os.path.dirname(_HERE)

try:
    sys.path.remove(_HERE)
except ValueError:
    pass
if _PLAYGROUNDS not in sys.path:
    sys.path.insert(0, _PLAYGROUNDS)

from layout import layout_html, layout_server
from pgruntime import daemon_registry as playground_registry

from pgkit.server import serve_nonblocking


HTML_FILE    = "layout.html"
PORT         = 8777
OPEN_BROWSER = True

PLAYGROUND_ID   = layout_server.PLAYGROUND_ID
PLAYGROUND_NAME = layout_server.PLAYGROUND_NAME
LAYER_SLOTS     = layout_server.LAYER_SLOTS

_DAEMON_WATCH_INTERVAL_S = 2.0
_DAEMON_WATCH_MAX_FAILS  = 5


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    layout_html.render,
        "routes":         layout_server.ROUTES,
        "raw_routes":     layout_server.RAW_ROUTES,
        "module_sources": layout_html.module_sources(),
    }


def _watch_daemon_and_exit():
    """Exit the playground when the daemon dies."""
    from pgruntime import daemon_client as krita_client

    consecutive = 0
    while True:
        time.sleep(_DAEMON_WATCH_INTERVAL_S)
        try:
            resp = krita_client.send(
                {"type": "comfy_status"}, timeout=3.0,
            )
            ok = (isinstance(resp, dict)
                  and resp.get("status") == "success")
        except Exception:
            ok = False

        if ok:
            consecutive = 0
            continue

        consecutive += 1
        if consecutive >= _DAEMON_WATCH_MAX_FAILS:
            print(
                f"[layout] daemon unreachable for "
                f"{consecutive} consecutive checks; exiting",
                file=sys.stderr, flush=True,
            )
            os._exit(0)


def _sigterm(signum, frame):
    # Raising SystemExit from a signal handler propagates out of
    # serve_forever() (which doesn't catch it), running the
    # surrounding try/finally and the atexit-registered _cleanup.
    # Calling httpd.shutdown() here would deadlock: shutdown() blocks
    # until serve_forever() returns, but serve_forever() is paused
    # inside this handler.
    sys.exit(0)


def main():
    html = layout_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}", flush=True)

    # ---- Bind FIRST so we know the actual port before advertising it ----
    # serve_nonblocking binds the socket but does NOT start the accept
    # loop.  The pgkit hub registration happens inside it with the
    # actual bound port, which is correct.  We then verify the port
    # matches what the ComfyUI node expects, or fail hard.
    httpd, hub_conn, hub_url = serve_nonblocking(
        port           = PORT,
        html_file      = HTML_FILE,
        html_render    = layout_html.render,
        routes         = layout_server.ROUTES,
        raw_routes     = layout_server.RAW_ROUTES,
        module_sources = layout_html.module_sources(),
        hub_name       = PLAYGROUND_ID,
        open_browser   = OPEN_BROWSER,
    )

    actual_port = httpd.server_address[1]
    if actual_port != PORT:
        print("", file=sys.stderr, flush=True)
        print("=" * 64, file=sys.stderr, flush=True)
        print(
            f"ERROR: could not bind to port {PORT}; the kernel "
            f"assigned {actual_port} instead.",
            file=sys.stderr, flush=True,
        )
        print(
            f"       Something is already listening on port {PORT}.",
            file=sys.stderr, flush=True,
        )
        print(
            f"       The DrawatoonSetIP node in the ComfyUI workflow "
            f"hardcodes http://127.0.0.1:{PORT}/state.",
            file=sys.stderr, flush=True,
        )
        print(
            f"       If this playground runs on {actual_port} instead, "
            f"the node cannot reach it and layout boxes will have "
            f"NO EFFECT on the generated image (the node silently "
            f"falls back to void_ip_embed).",
            file=sys.stderr, flush=True,
        )
        print("", file=sys.stderr, flush=True)
        print(
            f"       To find and free port {PORT}:",
            file=sys.stderr, flush=True,
        )
        print(
            f"         lsof -i :{PORT}",
            file=sys.stderr, flush=True,
        )
        print(
            f"         ss -tlnp | grep :{PORT}",
            file=sys.stderr, flush=True,
        )
        print(
            f"       Then stop whatever is on it and restart this "
            f"playground.",
            file=sys.stderr, flush=True,
        )
        print(
            f"       Alternatively, if you intend to run on "
            f"{actual_port}, update _LAYOUT_STATE_URL in "
            f"kritomatic_nodes/PixArt/nodes.py to match.",
            file=sys.stderr, flush=True,
        )
        print("=" * 64, file=sys.stderr, flush=True)

        try:
            httpd.server_close()
        except Exception:
            pass
        if hub_conn is not None:
            try:
                hub_conn.stop_now()
            except Exception:
                pass
        sys.exit(1)

    # ---- On the expected port: register with the daemon at the real URL ----
    state_url = f"http://127.0.0.1:{actual_port}"
    print(f"  routes   : "
          f"{', '.join(sorted(list(layout_server.ROUTES) + list(layout_server.RAW_ROUTES)))}",
          flush=True)
    print(f"  state    : {state_url}/state", flush=True)

    registered = playground_registry.register(
        PLAYGROUND_ID, PLAYGROUND_NAME, state_url, actual_port,
        layer_slots=LAYER_SLOTS,
        retry_s=30.0,
    )
    if registered:
        print(
            f"  registered with daemon as {PLAYGROUND_ID!r} "
            f"(slot(s): {[s['id'] for s in LAYER_SLOTS]})",
            flush=True,
        )
    else:
        print(
            f"  WARNING: could not register with daemon; "
            f"docker will not show this playground.  Playground "
            f"works independently; restart after daemon is up "
            f"to register.",
            file=sys.stderr, flush=True,
        )

    # ---- Clean-exit plumbing ----
    def _cleanup():
        try:
            playground_registry.unregister(PLAYGROUND_ID)
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        if hub_conn is not None:
            try:
                hub_conn.stop_now()
            except Exception:
                pass

    atexit.register(_cleanup)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    # ---- Daemon watcher ----
    threading.Thread(
        target=_watch_daemon_and_exit,
        name="layout-daemon-watch",
        daemon=True,
    ).start()

    # ---- Block on the accept loop ----
    try:
        httpd.serve_forever()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
