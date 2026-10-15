from krita import *
from .server.socket_server import WebSocketServer
from .server.event_server import EventServer
from .handlers.base import CommandHandler
from .decorators import command
from .registry import get_command_registry
import threading


class _HandlerBox:
    """
    Indirection between the server's command_received signal and the
    current command handler. The signal is connected to box.dispatch
    once, at startup. Reloading swaps box.handler in place; the
    connection never needs to change.

    dispatch() runs the handler on a freshly-spawned Python thread
    rather than on whichever thread Qt invoked it from.

    Why a thread here, and not the invoking thread:

      Qt dispatches a signal-slot connection either directly (the slot
      runs synchronously on the emitting thread) or via the queue (the
      slot runs later, on the receiver's thread). Which one Qt picks
      depends on the connection type, and PyQt6's default for a slot
      that is a plain Python method has changed across versions. Under
      Krita 6 with PyQt6, the signal from the server's worker thread
      is being queued to the GUI thread, so the handler ran on the
      main thread — and a `comfy_generate` blocking on its generation
      event then blocked the whole Krita window.

      Rather than depend on a Qt detail we cannot reliably observe,
      dispatch unconditionally hands the command to a new thread. The
      GUI thread is never the one that runs a handler. The client
      worker thread, when the connection happens to be direct, is not
      blocked either — it returns to its socket read immediately.

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
            )
            self.event_server.start()

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
