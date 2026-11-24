"""Controlnets playground — entry point.

    python controlnets_playground.py

Writes controlnets.html

The playground is a state host: it registers with the daemon with an
`@thecontrolnet` layer slot, pulls the current image from that slot
via the daemon's `/krita/resource_image` route (through autosync),
and exposes `{image_hash, image_dims, controlnets}` at `/state` for a
future `MultiControlNetConditioning`-style node to consume at ComfyUI
execution time.

The `controlnets` list is currently empty — this is a starter
skeleton.  A future feature will populate it with entries of the
shape `{id, name, strength, image: {hash, width, height}}` and the
corresponding `/controlnet` route will serve each item's image by
hash.  The node author is expected to mirror `MultiAreaConditioning`'s
shape: `/state` pull + `/version` IS_CHANGED + per-item hash fetch.

Fixed port, no silent fallback
==============================
Downstream consumers derive the URL from
`pgruntime.ports.url_for("controlnets", "/state")`.  If this
playground silently bound to a different port, they'd see a stale /
empty state.  Bind first, verify, refuse to start otherwise.

Signal handling
===============
SIGINT / SIGTERM raise SystemExit which propagates out of
`serve_forever()`.  Calling `httpd.shutdown()` from a signal handler
would deadlock — `shutdown()` blocks until `serve_forever()` returns,
but `serve_forever()` is paused inside the handler.  So we exit and
let the `try/finally` + `atexit` run cleanup.
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

from controlnets import controlnets_html, controlnets_server
from pgruntime import daemon_registry as playground_registry

from pgkit.server import serve_nonblocking


HTML_FILE    = "controlnets.html"
PORT         = controlnets_server.PLAYGROUND_PORT
OPEN_BROWSER = True

PLAYGROUND_ID   = controlnets_server.PLAYGROUND_ID
PLAYGROUND_NAME = controlnets_server.PLAYGROUND_NAME
LAYER_SLOTS     = controlnets_server.LAYER_SLOTS

_DAEMON_WATCH_INTERVAL_S = 2.0
_DAEMON_WATCH_MAX_FAILS  = 5


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    controlnets_html.render,
        "routes":         controlnets_server.ROUTES,
        "raw_routes":     controlnets_server.RAW_ROUTES,
        "module_sources": controlnets_html.module_sources(),
    }


def _watch_daemon_and_exit():
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
                f"[controlnets] daemon unreachable for "
                f"{consecutive} consecutive checks; exiting",
                file=sys.stderr, flush=True,
            )
            os._exit(0)


def _sigterm(signum, frame):
    sys.exit(0)


def main():
    html = controlnets_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}", flush=True)

    httpd, hub_conn, hub_url = serve_nonblocking(
        port           = PORT,
        html_file      = HTML_FILE,
        html_render    = controlnets_html.render,
        routes         = controlnets_server.ROUTES,
        raw_routes     = controlnets_server.RAW_ROUTES,
        module_sources = controlnets_html.module_sources(),
        hub_name       = PLAYGROUND_ID,
        open_browser   = OPEN_BROWSER,
    )

    actual_port = httpd.server_address[1]
    if actual_port != PORT:
        print("", file=sys.stderr, flush=True)
        print("=" * 64, file=sys.stderr, flush=True)
        print(
            f"ERROR: could not bind to port {PORT}; kernel assigned "
            f"{actual_port} instead.  Downstream consumers will "
            f"hardcode http://127.0.0.1:{PORT}/state.",
            file=sys.stderr, flush=True,
        )
        print("=" * 64, file=sys.stderr, flush=True)
        try: httpd.server_close()
        except Exception: pass
        if hub_conn is not None:
            try: hub_conn.stop_now()
            except Exception: pass
        sys.exit(1)

    state_url = f"http://127.0.0.1:{actual_port}"
    print(f"  routes   : "
          f"{', '.join(sorted(list(controlnets_server.ROUTES) + list(controlnets_server.RAW_ROUTES)))}",
          flush=True)
    print(f"  state    : {state_url}/state", flush=True)

    registered = playground_registry.register(
        PLAYGROUND_ID, PLAYGROUND_NAME, state_url, actual_port,
        layer_slots=LAYER_SLOTS,
        retry_s=30.0,
        log_prefix="controlnets",
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
            f"playground works independently but the docker will not "
            f"show it.  Restart after daemon is up to register.",
            file=sys.stderr, flush=True,
        )

    def _cleanup():
        try:
            playground_registry.unregister(PLAYGROUND_ID, log_prefix="controlnets")
        except Exception:
            pass
        try:
            httpd.server_close()
        except Exception:
            pass
        if hub_conn is not None:
            try: hub_conn.stop_now()
            except Exception: pass

    atexit.register(_cleanup)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    threading.Thread(
        target=_watch_daemon_and_exit,
        name="controlnets-daemon-watch",
        daemon=True,
    ).start()

    try:
        httpd.serve_forever()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()
