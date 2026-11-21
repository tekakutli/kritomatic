"""Outpaint playground — entry point.

    python outpaint_playground.py

Writes outpaint.html, serves it at http://127.0.0.1:8779/.

Registers with the kritomatic daemon on startup under id `outpaint`,
with one layer slot `@theoutpainted`.  When the daemon dies, the
playground exits (background watcher, ~10s of consecutive failures);
the user restarts both manually.

Fixed port, no silent fallback — same rationale as layout: the daemon
docker and the browser both hardcode 8779, so binding elsewhere would
silently misbehave.  See layout_playground.py for the full rationale.

Signal handling — SIGINT and SIGTERM raise SystemExit, which
propagates out of serve_forever() and runs the surrounding
try/finally plus the atexit-registered `_cleanup`.
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

from outpaint import outpaint_html, outpaint_server, playground_registry

from pgkit.server import serve_nonblocking


HTML_FILE    = "outpaint.html"
PORT         = 8779
OPEN_BROWSER = True

PLAYGROUND_ID   = outpaint_server.PLAYGROUND_ID
PLAYGROUND_NAME = outpaint_server.PLAYGROUND_NAME
LAYER_SLOTS     = outpaint_server.LAYER_SLOTS

_DAEMON_WATCH_INTERVAL_S = 2.0
_DAEMON_WATCH_MAX_FAILS  = 5


def descriptor():
    return {
        "name":           PLAYGROUND_ID,
        "port":           PORT,
        "html_file":      HTML_FILE,
        "html_render":    outpaint_html.render,
        "routes":         outpaint_server.ROUTES,
        "raw_routes":     outpaint_server.RAW_ROUTES,
        "module_sources": outpaint_html.module_sources(),
    }


def _watch_daemon_and_exit():
    from outpaint import krita_client

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
                f"[outpaint] daemon unreachable for "
                f"{consecutive} consecutive checks; exiting",
                file=sys.stderr, flush=True,
            )
            os._exit(0)


def _sigterm(signum, frame):
    sys.exit(0)


def main():
    html = outpaint_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}", flush=True)

    # ---- Bind FIRST so we know the actual port before advertising it ----
    httpd, hub_conn, hub_url = serve_nonblocking(
        port           = PORT,
        html_file      = HTML_FILE,
        html_render    = outpaint_html.render,
        routes         = outpaint_server.ROUTES,
        raw_routes     = outpaint_server.RAW_ROUTES,
        module_sources = outpaint_html.module_sources(),
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

    # ---- On the expected port: register with the daemon at the real URL ----
    state_url = f"http://127.0.0.1:{actual_port}"
    print(f"  routes   : "
          f"{', '.join(sorted(list(outpaint_server.ROUTES) + list(outpaint_server.RAW_ROUTES)))}",
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
        name="outpaint-daemon-watch",
        daemon=True,
    ).start()

    # ---- Block on the accept loop ----
    try:
        httpd.serve_forever()
    finally:
        _cleanup()


if __name__ == "__main__":
    main()