"""
pgkit.launcher — multi-playground launcher.

Run several playgrounds in one Python process.  Each keeps its own
HTTP+WS server, its own `/modules/` manifest, its own per-playground
`pgkit.remote_hub.Hub` for `/remote`, and its own hub registration.

The shared-process model
========================
Six playgrounds, one interpreter, one Ctrl+C.  The alternative —
one subprocess per playground — would mean six terminals, six
shells to stop, and six startup banners to scroll past.  The
launcher trades that for a single-command start:

    python playgrounds/run_all.py

Two costs, both addressed in the plan:

  - Module-name collisions.  All six were flat-module Python
    projects (every one had a top-level `pg_base`, `pg_state`,
    etc.).  They cannot share an interpreter as flat scripts.
    Each was refactored into a package (`board`, `cone`, ...,
    `text_wall`), and every internal import became relative
    (`from .pg_base import ...`).  Two `pg_base` files, two module
    objects, no collision.

  - Failure coupling.  A bug in one playground can now bring down
    the whole group.  The mitigation is the same as for a monolith
    server: keep cross-module state clean, do not add a shared
    singleton, be conservative when touching sys.modules.
    Cooperative kill of one group member shuts down the whole
    group; that is the intended behavior and is communicated by
    the interactive panel and by the hub's kill_result message.

Non-blocking serve
==================
`serve_group` uses `pgkit.server.serve_nonblocking` per playground,
which returns an `httpd` and a `hub_conn` WITHOUT starting
`serve_forever`.  The launcher threads each `serve_forever` and owns
the shutdown path.

Group concept
=============
Every registration carries an optional `group` string.  The hub
treats each registration independently for routing and target
selection; the group is metadata, used only by `_kill_playground`
(to report the whole group shutting down) and by the interactive
panel (to warn before a kill).  Routing, target dropdown, and the
CLI are unaware of the group.
"""

import signal
import sys
import threading

from .server import serve_nonblocking


def serve_group(group, playgrounds, *, open_browser=False, only=None):
    """Start every playground in `playgrounds` in one process.

    `group`       arbitrary string (metadata; see module docstring).
    `playgrounds` list of descriptor dicts:
                    { name, module, port, html_file, html_render,
                      routes, raw_routes, module_sources }
    `only`        optional iterable of names to include; otherwise all.
    """
    selected = [p for p in playgrounds
                if only is None or p["name"] in only]
    if not selected:
        print("No playgrounds selected.", file=sys.stderr)
        return

    servers = []
    for desc in selected:
        name = desc["name"]
        try:
            httpd, hub_conn, url = serve_nonblocking(
                port            = desc["port"],
                html_file       = desc["html_file"],
                html_render     = desc["html_render"],
                routes          = desc.get("routes") or {},
                raw_routes      = desc.get("raw_routes") or {},
                module_sources  = desc.get("module_sources") or {},
                hub_name        = name,
                hub_role        = "playground",
                group           = group,
                open_browser    = open_browser,
            )
        except Exception as e:
            print(f"  FAILED to start {name}: {e}", file=sys.stderr)
            continue
        servers.append({
            "name":     name,
            "httpd":    httpd,
            "hub_conn": hub_conn,
            "url":      url,
            "thread":   None,
        })
        print(f"  [{name}] {url}")

    if not servers:
        print("No playgrounds started.", file=sys.stderr)
        return

    print(f"All {len(servers)} playgrounds in group '{group}' are running.")
    print("  Press Ctrl+C to stop.")

    shutdown_event = threading.Event()

    def _on_signal(signum, frame):
        print(f"\n  Signal {signum} received, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    for srv in servers:
        t = threading.Thread(
            target=srv["httpd"].serve_forever,
            name=f"serve-{srv['name']}",
            daemon=True,
        )
        t.start()
        srv["thread"] = t

    shutdown_event.wait()

    for srv in servers:
        try:
            srv["httpd"].shutdown()
        except Exception:
            pass
    for srv in servers:
        try:
            srv["httpd"].server_close()
        except Exception:
            pass
        if srv["hub_conn"] is not None:
            try:
                srv["hub_conn"].stop_now()
            except Exception:
                pass
    for srv in servers:
        t = srv["thread"]
        if t is not None and t.is_alive():
            t.join(timeout=2.0)

    print("Stopped.")
