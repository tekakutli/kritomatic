from krita import *
from .server.socket_server import WebSocketServer
from .server.event_server import EventServer
from .handlers.base import CommandHandler
from .decorators import command
from .registry import get_command_registry
import threading
import time
import traceback


class _HandlerBox:
    """
    Indirection between the server's command_received signal and the
    current command handler. The signal is connected to box.dispatch
    once, at startup. Reloading swaps box.handler in place; the
    connection never needs to change.

    dispatch() runs the handler on a freshly-spawned Python thread
    rather than on whichever thread Qt invoked it from.

    Deliberately NOT a QObject. If it were, Qt's thread-affinity rules
    would apply to the connection and the receiver would be tied to a
    specific thread; keeping it a plain Python object means the
    connection's thread choice cannot leak into the daemon's runtime
    behavior, because dispatch overrides it either way.
    """
    def __init__(self, handler):
        self.handler = handler

    def dispatch(self, command, client_socket):
        h = self.handler
        if h is None:
            return
        threading.Thread(
            target=h.handle_command,
            args=(command, client_socket),
            name="kritomatic-handler",
            daemon=True,
        ).start()


class KritomaticDaemon(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server = None
        self.event_server = None
        self.handler_box = _HandlerBox(CommandHandler())
        self._auto_pull_started = False

    def setup(self):
        pass

    def createActions(self, window):
        self.start_servers()

    def start_servers(self):
        if not self.server:
            self.server = WebSocketServer()
            self.server.command_received.connect(self.handler_box.dispatch)
            self.server.start()
            print("✓ Kritomatic Daemon loaded")

        if not self.event_server:
            self.event_server = EventServer(
                port=12347,
                state_provider=self._current_status,
                panel_provider=self._current_panel,
            )
            self.event_server.start()

        # Auto-pull the currently-published browser workflow exactly
        # once, after the servers are up. See _auto_pull_on_startup for
        # why this works and when it does not.
        if not self._auto_pull_started:
            self._auto_pull_started = True
            threading.Thread(
                target=self._auto_pull_on_startup,
                name="kritomatic-autopull",
                daemon=True,
            ).start()

    def _auto_pull_on_startup(self):
        """Pull the currently-published browser workflow, once, at startup.

        The daemon's workflow state is in-memory; a Krita restart
        wipes it. The user's last-used workflow is typically the graph
        currently published by a ComfyUI browser tab, and the
        tooling-nodes package's WorkflowExchange will replay that graph
        to any client that subscribes.

        So: at startup, call comfy_connect with watch=true. The
        subscribe handshake triggers the replay; the daemon receives
        `etn_workflow_published`, promotes the graph to active
        workflow, and the panel description re-publishes with the
        workflow's parameters. By the time the user looks at the
        docker, the panel has the workflow's sections.

        This works only when ComfyUI is running and has kept its
        in-memory publisher record since the last browser publish. If
        ComfyUI was restarted since then, its `_publishers` dict is
        empty, nothing is replayed, and the panel stays empty until
        the browser tab publishes again (which happens on any graph
        edit). That limitation is in ComfyUI's tooling-nodes package,
        not here; the daemon cannot conjure a workflow it never saw.

        Retry window
        ------------
        ComfyUI may not be up yet when Krita starts: the user may have
        launched Krita first, or ComfyUI may take longer to initialise.
        The auto-pull therefore retries for up to 60 seconds (30
        attempts, 2s apart, plus the 1s initial delay). That covers
        "user starts ComfyUI within a minute of Krita". Beyond that,
        the panel logs `gave up` and stays on the "no workflow"
        message; a manual `kritomatic comfyui panel run` or a browser
        publish will recover it.

        Once the subscribe succeeds, the replay arrives asynchronously
        on the daemon's own watcher thread. The wait loop below gives
        the replay up to 5 seconds to reach `_active_workflow_name`; if
        it does, this logs the restored workflow. If it doesn't, the
        subscribe still succeeded, but ComfyUI had nothing cached to
        replay.
        """
        time.sleep(1.0)  # let the event server bind, and let the
                         # docker's initial connect complete first so
                         # the panel starts empty and then updates

        handler = self.handler_box.handler
        if handler is None:
            return
        comfy = None
        try:
            comfy = handler.handlers.get('comfyui')
        except Exception:
            pass
        if comfy is None:
            return

        print("[kritomatic] auto-pull: connecting to ComfyUI")

        for attempt in range(30):
            try:
                result = comfy.comfy_connect({
                    "watch": True,
                    "check": True,
                    "subscribe_timeout": 5.0,
                })
            except Exception as e:
                print(f"[kritomatic] auto-pull attempt "
                      f"{attempt + 1} raised: {e}")
                time.sleep(2.0)
                continue

            if result.get("success") and (result.get("data") or {}).get("subscribed", False):
                # Subscribed. Now wait up to 5s for the replay to land
                # on the daemon's state. The replay is asynchronous and
                # arrives on the watcher thread; without this wait the
                # status check below would almost always see an empty
                # workflow even when a replay is coming.
                for _ in range(5):
                    time.sleep(1.0)
                    status = comfy.comfy_status({})
                    status_data = (status.get("data") or {}) \
                        if status.get("success") else {}
                    workflow = status_data.get("workflow")
                    if workflow:
                        print(f"[kritomatic] auto-pull: restored "
                              f"{workflow}")
                        return
                print("[kritomatic] auto-pull: subscribed, "
                      "no workflow replayed "
                      "(no browser publish cached in ComfyUI)")
                return

            msg = result.get("message", "unknown")
            print(f"[kritomatic] auto-pull attempt "
                  f"{attempt + 1}: {msg}")
            time.sleep(2.0)

        print("[kritomatic] auto-pull: gave up after 30 attempts")

    def _current_status(self):
        """Return the current comfy_status payload, or None.

        Used by the event server to give each new subscriber an initial
        snapshot. Reads through whatever handler is currently installed
        in the box, so a reload_daemon that swaps the handler tree is
        transparently picked up.
        """
        handler = self.handler_box.handler
        if handler is None:
            return None
        try:
            result = handler.execute('comfy_status', {})
            if isinstance(result, dict) and result.get('success'):
                return result.get('data', {})
        except Exception:
            pass
        return None

    def _current_panel(self):
        """Return the current panel description, or None."""
        handler = self.handler_box.handler
        if handler is None:
            return None
        try:
            result = handler.execute('panel_description', {})
            if isinstance(result, dict) and result.get('success'):
                return result.get('data', {})
        except Exception:
            pass
        return None
