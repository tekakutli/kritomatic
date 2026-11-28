"""
pgruntime.bootstrap — the launcher pattern every playground's
`<name>_playground.py` was implementing locally.

Three shapes appear across the tree:

    full     serve_nonblocking + port-verify + register + daemon-watch
             (magi, layout, controlnets, outpaint)
    medium   serve + register, no port-verify, no watcher
             (segment, inpaint)
    light    serve only (multimask, board, text_boxes, cone,
             text_wall, interceptor)

`serve_playground` covers all three via keyword flags.  Callers
that need only the light shape get the simplest signature; the
full shape exercises every option.

The function blocks on `serve_forever()`; it never returns except
via `SystemExit`.
"""

import atexit
import os
import signal
import sys
import threading
import time

from . import krita_api
from .daemon_registry import register, unregister
from .daemon_client import send as daemon_send



_DAEMON_WATCH_INTERVAL_S = 2.0
_DAEMON_WATCH_MAX_FAILS  = 5


def _sigterm(signum, frame):
    # See layout_playground.py's docstring for why this raises
    # SystemExit instead of calling httpd.shutdown(): shutdown()
    # blocks until serve_forever() returns, but serve_forever() is
    # paused inside the handler that's blocking it.
    sys.exit(0)


def serve_playground(
    *,
    playground_id: str,
    playground_name: str,
    port: int,
    html_render,
    module_sources: dict,
    routes: dict,
    raw_routes: dict = None,
    layer_slots=None,
    param_slots=None,
    control_params=None,
    port_verify: bool = False,
    watch_daemon: bool = False,
    open_browser: bool = True,
    html_file: str = None,
    log_prefix: str = None,
    port_error_extra: str = "",
    on_ready=None,
    on_cleanup=None,
) -> None:
    """Bind, optionally verify the port, register with the daemon,
    optionally start a death watcher, then block on serve_forever.

    Returns only via `sys.exit`.  All cleanup is registered with
    `atexit` and also runs in the `finally` around `serve_forever`.
    """
    log_prefix = log_prefix or playground_id
    html_file  = html_file or f"{playground_id}_playground.html"
    raw_routes = raw_routes or {}

    try:
        from pgkit.server import serve, serve_nonblocking
        from pgkit.remote_hub import Hub  # noqa: F401 - imported for consumers

        html = html_render()
        with open(html_file, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"Wrote {html_file}", flush=True)

        if port_verify or watch_daemon or layer_slots or param_slots or control_params:
            httpd, hub_conn, hub_url = serve_nonblocking(
                port           = port,
                html_file      = html_file,
                html_render    = html_render,
                routes         = routes,
                raw_routes     = raw_routes,
                module_sources = module_sources,
                hub_name       = playground_id,
                open_browser   = open_browser,
            )
        else:
            serve(
                port           = port,
                open_browser   = open_browser,
                html_file      = html_file,
                routes         = routes,
                raw_routes     = raw_routes,
                module_sources = module_sources,
                hub_name       = playground_id,
            )
            return

    except ImportError as e:
        print(f"[{log_prefix}] pgkit import failed: {e}", file=sys.stderr)
        raise

    actual_port = httpd.server_address[1]

    if port_verify and actual_port != port:
        _port_error_and_exit(
            log_prefix, port, actual_port, port_error_extra,
            httpd, hub_conn,
        )

    state_url = f"http://127.0.0.1:{actual_port}"
    print(
        f"  routes   : {', '.join(sorted(list(routes) + list(raw_routes)))}",
        flush=True,
    )
    print(f"  state    : {state_url}/state", flush=True)

    if on_ready:
        try:
            on_ready(actual_port)
        except Exception as e:
            print(
                f"[{log_prefix}] on_ready raised: {e}",
                file=sys.stderr, flush=True,
            )

    registered = False
    if layer_slots or param_slots or control_params:
        registered = register(
            playground_id, playground_name,
            state_url, actual_port,
            layer_slots=layer_slots,
            param_slots=param_slots,
            control_params=control_params,
            retry_s=30.0,
            log_prefix=log_prefix,
        )
        if registered:
            slots = [s["id"] for s in (layer_slots or []) if s.get("id")]
            print(
                f"  registered with daemon as {playground_id!r} "
                f"(slot(s): {slots})",
                flush=True,
            )
        else:
            print(
                f"  WARNING: could not register with daemon; the "
                f"playground works independently but the docker will "
                f"not show it.  Restart after the daemon is up to "
                f"register.",
                file=sys.stderr, flush=True,
            )

    def _cleanup():
        if registered:
            try:
                unregister(playground_id, log_prefix=log_prefix)
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
        if on_cleanup:
            try:
                on_cleanup()
            except Exception:
                pass

    atexit.register(_cleanup)

    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT,  _sigterm)

    if watch_daemon:
        threading.Thread(
            target=_watch_daemon_and_exit,
            args=(log_prefix,),
            name=f"{playground_id}-daemon-watch",
            daemon=True,
        ).start()

    try:
        httpd.serve_forever()
    finally:
        _cleanup()


def _port_error_and_exit(log_prefix, expected, actual, extra,
                          httpd, hub_conn):
    print("", file=sys.stderr, flush=True)
    print("=" * 64, file=sys.stderr, flush=True)
    print(
        f"ERROR: could not bind to port {expected}; the kernel "
        f"assigned {actual} instead.",
        file=sys.stderr, flush=True,
    )
    print(
        f"       Something is already listening on port {expected}.",
        file=sys.stderr, flush=True,
    )
    if extra:
        print(extra, file=sys.stderr, flush=True)
    print("", file=sys.stderr, flush=True)
    print(
        f"       To find and free port {expected}:",
        file=sys.stderr, flush=True,
    )
    print(f"         lsof -i :{expected}", file=sys.stderr, flush=True)
    print(f"         ss -tlnp | grep :{expected}", file=sys.stderr, flush=True)
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


def _watch_daemon_and_exit(log_prefix: str) -> None:
    consecutive = 0
    while True:
        time.sleep(_DAEMON_WATCH_INTERVAL_S)
        try:
            resp = daemon_send({"type": "comfy_status"}, timeout=3.0)
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
                f"[{log_prefix}] daemon unreachable for "
                f"{consecutive} consecutive checks; exiting",
                file=sys.stderr, flush=True,
            )
            os._exit(0)
