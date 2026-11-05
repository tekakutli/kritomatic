"""
pgkit.launcher — multi-playground launcher.

Run several playgrounds in one Python process.  Each keeps its own
HTTP+WS server, its own `/modules/` manifest, its own per-playground
`pgkit.remote_hub.Hub` for `/remote`, and its own hub registration.

THE SHARED-PROCESS MODEL
========================
Six playgrounds, one interpreter, one Ctrl+C:

    python playgrounds/run_all.py

Every playground was refactored into a package (`board`, `cone`,
..., `text_wall`) and every internal import became relative, so two
`pg_base` files coexist as two distinct modules.  Failure coupling
is the accepted trade-off: a crash in one playground takes down the
whole group; cooperative kill of one member kills the whole group.
That is the intended behavior and is surfaced by the hub and by the
interactive panel.

NON-BLOCKING SERVE
==================
`Launcher.start` uses `pgkit.server.serve_nonblocking` per playground,
which returns an `httpd` and a `hub_conn` WITHOUT starting
`serve_forever`.  The launcher threads each `serve_forever` and owns
the shutdown path.  `Launcher.stop` reverses it: `httpd.shutdown()`,
`httpd.server_close()`, `hub_conn.stop_now()`, join the thread,
forget the entry.  Start/stop can be called repeatedly at runtime.

LAUNCHER HUB CHANNEL
====================
On init, the launcher opens a *second* WS to the hub, tagged
`role="launcher"`, `name=<group>`, `group=<group>`.  This is the
control channel for the panel.  It stays up for the launcher's
lifetime, even when zero playgrounds are running.

The launcher pushes `launcher_state` messages carrying `available`
(the full descriptor list) on every start/stop and after initial
registration.  The hub broadcasts that to panels.

The launcher also receives `launcher_start` / `launcher_stop` /
`launcher_restart` messages on this channel, forwarded from panels
by the hub.  It handles them in-process and replies with
`launcher_result`, which the hub relays back to the sending panel.

INTERACTIVE MODE
================
`run_all.py -i` drives a `Launcher` from a REPL: `start <name>` /
`stop <name>` / `restart <name>`.  Same Launcher, same process, same
group.  Fixed-set mode (`serve_group`) is the non-interactive
counterpart: it starts the initial set, blocks, then shuts down.
"""

import signal
import sys
import threading

from .server import serve_nonblocking


# ============================================================================
#  Launcher
# ============================================================================

class Launcher:
    """Own the lifecycle of a set of playgrounds in one process.

    `Launcher(group, descriptors)` builds the playground map and
    opens the launcher control channel.  `start(name)` and `stop(name)`
    add/remove one playground; both are safe at any time.
    `install_signal_handlers()` wires SIGINT/SIGTERM to `trigger_shutdown`.
    `wait()` blocks until shutdown is signalled.
    """

    def __init__(self, group, playgrounds, *, open_browser=False,
                 enable_hub_control=True):
        self.group = group
        self._order = [p["name"] for p in playgrounds]
        self._playgrounds = {p["name"]: p for p in playgrounds}
        self._running = {}
        self._lock = threading.Lock()
        self._open_browser = open_browser
        self._shutdown_event = threading.Event()
        self._signals_installed = False

        self._launcher_ws = None
        self._launcher_ws_ready = threading.Event()
        if enable_hub_control:
            try:
                self._open_launcher_ws()
            except Exception as e:
                print(f"  launcher control channel unavailable: {e}",
                      file=sys.stderr)

    # ---- launcher hub channel ----------------------------------------

    def _open_launcher_ws(self):
        from .hub_client import ensure_running
        self._launcher_ws = ensure_running(
            self.group,               # name = group name
            0,                        # port not meaningful for launcher
            role="launcher",
            group=self.group,
            message_handler=self._on_launcher_message,
            connect_timeout=5.0,
        )
        self._launcher_ws_ready.set()
        self._push_launcher_state()

    def _on_launcher_message(self, msg):
        t = msg.get("type")
        target = msg.get("name") or ""
        request_id = msg.get("request_id") or ""
        result = {
            "type":       "launcher_result",
            "request_id": request_id,
            "name":       target,
            "success":    False,
            "message":    "",
        }

        if t == "launcher_start":
            url = self.start(target)
            result["success"] = url is not None
            result["message"] = (
                f"started {target}: {url}" if url
                else f"could not start {target}"
            )
        elif t == "launcher_stop":
            ok = self.stop(target)
            result["success"] = ok
            result["message"] = (
                f"stopped {target}" if ok
                else f"{target} not running"
            )
        elif t == "launcher_restart":
            self.stop(target)
            url = self.start(target)
            result["success"] = url is not None
            result["message"] = (
                f"restarted {target}: {url}" if url
                else f"could not restart {target}"
            )
        else:
            return

        if self._launcher_ws is not None:
            self._launcher_ws.send(result)
        self._push_launcher_state()

    def _push_launcher_state(self):
        if self._launcher_ws is None:
            return
        self._launcher_ws.send({
            "type":      "launcher_state",
            "available": list(self._order),
        })

    # ---- inspection --------------------------------------------------

    def names(self):
        return list(self._order)

    def running(self):
        with self._lock:
            return {n: self._running[n]["url"] for n in self._running}

    def is_running(self, name):
        with self._lock:
            return name in self._running

    # ---- lifecycle ---------------------------------------------------

    def start(self, name):
        desc = self._playgrounds.get(name)
        if desc is None:
            return None
        with self._lock:
            if name in self._running:
                return self._running[name]["url"]

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
                group           = self.group,
                open_browser    = self._open_browser,
            )
        except Exception as e:
            print(f"  [{name}] FAILED to start: {e}", file=sys.stderr)
            return None

        t = threading.Thread(
            target=httpd.serve_forever,
            name=f"serve-{name}",
            daemon=True,
        )
        t.start()
        with self._lock:
            self._running[name] = {
                "httpd":    httpd,
                "hub_conn": hub_conn,
                "thread":   t,
                "url":      url,
            }
        print(f"  [{name}] {url}")
        self._push_launcher_state()
        return url

    def stop(self, name):
        with self._lock:
            entry = self._running.pop(name, None)
        if entry is None:
            return False

        try:
            entry["httpd"].shutdown()
        except Exception:
            pass
        try:
            entry["httpd"].server_close()
        except Exception:
            pass
        if entry["hub_conn"] is not None:
            try:
                entry["hub_conn"].stop_now()
            except Exception:
                pass
        t = entry["thread"]
        if t is not None and t.is_alive():
            t.join(timeout=2.0)
        print(f"  [{name}] stopped")
        self._push_launcher_state()
        return True

    def start_all(self):
        for name in self._order:
            if not self.is_running(name):
                self.start(name)

    def stop_all(self):
        with self._lock:
            names = list(self._running.keys())
        for name in names:
            self.stop(name)

    # ---- shutdown coordination ---------------------------------------

    def install_signal_handlers(self):
        if self._signals_installed:
            return
        self._signals_installed = True

        def _on_signal(signum, frame):
            print(f"\n  Signal {signum} received, shutting down...")
            self.trigger_shutdown()

        signal.signal(signal.SIGINT, _on_signal)
        signal.signal(signal.SIGTERM, _on_signal)

    def trigger_shutdown(self):
        self._shutdown_event.set()

    def wait(self):
        self._shutdown_event.wait()


# ============================================================================
#  serve_group — fixed-set entry point
# ============================================================================

def serve_group(group, playgrounds, *, open_browser=False, only=None):
    """Start every playground in `playgrounds` (or `only`) in one
    process, block until Ctrl+C, then shut down.

    Panel-side "Stop" and "Start" buttons still work in this mode,
    because the launcher control channel is always open.  Only the
    *starting set* is fixed; runtime start/stop of individual
    members is available via the panel either way.
    """
    launcher = Launcher(group, playgrounds, open_browser=open_browser)
    initial = (list(only) if only is not None
               else [p["name"] for p in playgrounds])
    for name in initial:
        launcher.start(name)

    if not launcher.running():
        print("No playgrounds started.", file=sys.stderr)
        return

    print(f"All {len(launcher.running())} playgrounds in group "
          f"'{group}' are running.")
    print("  Press Ctrl+C to stop.")
    launcher.install_signal_handlers()
    try:
        launcher.wait()
    finally:
        launcher.stop_all()
        print("Stopped.")
