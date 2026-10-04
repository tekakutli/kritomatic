from krita import *
from .server.socket_server import WebSocketServer
from .handlers.base import CommandHandler
from .decorators import command
from .registry import get_command_registry


class _HandlerBox:
    """
    Mutable indirection between the server's command_received signal
    and the current command handler. The signal is connected to
    box.dispatch once, at startup. Reloading swaps box.handler in
    place; the connection never needs to change.

    Deliberately NOT a QObject. If it were, Qt's thread-affinity rules
    would apply to the connection and the handler would be queued to
    the main thread, blocking the UI on every command. As a plain
    Python object, the connection is direct: the handler runs in the
    same thread the server emits from, which is the same behavior the
    daemon had before this indirection existed.
    """
    def __init__(self, handler):
        self.handler = handler

    def dispatch(self, command, client_socket):
        h = self.handler
        if h is None:
            return
        h.handle_command(command, client_socket)


class KritomaticDaemon(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server = None
        self.handler_box = _HandlerBox(CommandHandler())

    def setup(self):
        pass

    def createActions(self, window):
        self.start_server()

    def start_server(self):
        if not self.server:
            self.server = WebSocketServer()
            self.server.command_received.connect(self.handler_box.dispatch)
            self.server.start()
            print("✓ Kritomatic Daemon loaded")
