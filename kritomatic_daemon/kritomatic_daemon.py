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

        if not self._auto_pull_started:
            self._auto_pull_started = True
            threading.Thread(
                target=self._auto_pull_on_startup,
                name="kritomatic-autopull",
                daemon=True,
            ).start()

    def _auto_pull_on_startup(self):
        """Pull the currently-published browser workflow, once, at startup.

        See the module's inline comment about the retry window: 30
        attempts at 2s intervals (plus the 1s startup delay) covers
        "user starts ComfyUI within ~60 seconds of Krita". After a
        successful subscribe the method waits up to 5s for the replay
        to actually reach `_active_workflow_name`, because the replay
        is asynchronous and can be slow under load.
        """
        time.sleep(1.0)

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
