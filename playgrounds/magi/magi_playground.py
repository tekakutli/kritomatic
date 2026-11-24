"""Magi playground — entry point.

    python magi_playground.py

Writes magi.html, State is
served at /state for any consumer (currently the DrawatoonSetIP
node, which reads it via the layout playground once wiring lands).

Registers with the kritomatic daemon on startup.  The docker renders
a "Playground Input" section with one row per (playground, layer
slot) — for this playground, one row for `@thecharacter` with a
`[source▼][layer]` picker.  The playground polls that selector and
re-fetches the displayed layer whenever it changes.  When the daemon
dies, the playground exits (background watcher, ~10s of consecutive
failures); the user restarts both manually.

State shape (POST /state):

    {
      "op": "set",
      "state": {
        "character_bboxes":     [[x1,y1,x2,y2], ...],
        "reference_embeddings": [null | [768 floats], ...],
        "image_dims":           [W, H]
      }
    }

All bboxes are normalized [0,1] of image, in (x1,y1,x2,y2) order.
`reference_embeddings` length always matches `character_bboxes`.

Fixed port, no silent fallback
==============================
Later, the layout playground (or a bridging node) will consume this
playground's output at `pgruntime.ports.url_for("magi", "/state")`.
If this playground silently bound to a different port, the consumer
would see a stale / empty state and silently produce wrong output.

So: bind first, verify the port matches `PORT`, and refuse to start
if it doesn't.  The failure mode becomes a loud startup error
instead of a silent misbehaviour downstream.

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

from magi import magi_html, magi_server
from pgruntime import daemon_registry as playground_registry

from pgkit.server import serve_nonblocking


HTML_FILE    = "magi.html"
PORT         = magi_server.PLAYGROUND_PORT
OPEN_BROWSER = True

PLAYGROUND_ID   = magi_server.PLAYGROUND_ID
PLAYGROUND_NAME = magi_server.PLAYGROUND_NAME
LAYER_SLOTS     = magi_server.LAYER_SLOTS

_DAEMON_WATCH_INTERVAL_S = 2.0
_DAEMON_WATCH_MAX_FAILS  = 5


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    magi_html.render,
        "routes":         magi_server.ROUTES,
        "raw_routes":     magi_server.RAW_ROUTES,
        "module_sources": magi_html.module_sources(),
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
                f"[magi] daemon unreachable for "
                f"{consecutive} consecutive checks; exiting",
                file=sys.stderr, flush=True,
            )
            os._exit(0)


def _unregister():
    playground_registry.unregister(PLAYGROUND_ID)


def _sigterm(signum, frame):
    # Raising SystemExit from a signal handler propagates out of
    # serve_forever() (which doesn't catch it), running the
    # surrounding try/finally and the atexit-registered _cleanup.
    # Calling httpd.shutdown() here would deadlock: shutdown() blocks
    # until serve_forever() returns, but serve_forever() is paused
    # inside this handler.
    sys.exit(0)


def main():
    html = magi_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}", flush=True)

    httpd, hub_conn, hub_url = serve_nonblocking(
        port           = PORT,
        html_file      = HTML_FILE,
        html_render    = magi_html.render,
        routes         = magi_server.ROUTES,
        raw_routes     = magi_server.RAW_ROUTES,
        module_sources = magi_html.module_sources(),
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
            f"       Downstream consumers hardcode "
            f"http://127.0.0.1:{PORT}/state.",
            file=sys.stderr, flush=True,
        )
        print(
            f"       If this playground runs on {actual_port} instead, "
            f"they cannot reach it.",
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

    state_url = f"http://127.0.0.1:{actual_port}"
    print(f"  routes   : "
          f"{', '.join(sorted(list(magi_server.ROUTES) + list(magi_server.RAW_ROUTES)))}",
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

    threading.Thread(
        target=_watch_daemon_and_exit,
        name="magi-daemon-watch",
        daemon=True,
    ).start()

    try:
        httpd.serve_forever()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
