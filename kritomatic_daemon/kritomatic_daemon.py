from krita import *
from .server.socket_server import WebSocketServer
from .handlers.base import CommandHandler

class KritomaticDaemon(Extension):
    def __init__(self, parent):
        super().__init__(parent)
        self.server = None
        self.handler = CommandHandler()

    def setup(self):
        pass

    def createActions(self, window):
        self.start_server()

    def start_server(self):
        if not self.server:
            self.server = WebSocketServer()
            self.server.command_received.connect(self.handler.handle_command)
            self.server.start()
            print("✓ Kritomatic Daemon loaded")
