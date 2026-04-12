from PyQt5.QtCore import QThread, pyqtSignal
import socket
import threading
import json

class WebSocketServer(QThread):
    command_received = pyqtSignal(dict, object)

    def __init__(self, port=12346):
        super().__init__()
        self.port = port
        self.running = True
        self.active_connections = []
        self.connections_lock = threading.Lock()

    def run(self):
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(('localhost', self.port))
        server.listen(5)
        server.settimeout(1.0)
        print(f"✓ Krita WebSocket Server listening on port {self.port}")

        while self.running:
            try:
                client, addr = server.accept()
                client_thread = threading.Thread(target=self.handle_client, args=(client, addr))
                client_thread.daemon = True
                client_thread.start()
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"Server error: {e}")

        server.close()

    def handle_client(self, client_socket, addr):
        buffer = ""
        with self.connections_lock:
            self.active_connections.append(client_socket)

        try:
            while self.running:
                try:
                    client_socket.settimeout(0.5)
                    data = client_socket.recv(65536).decode('utf-8')
                    if not data:
                        break
                    buffer += data
                    while buffer:
                        try:
                            command = json.loads(buffer)
                            buffer = ""
                            self.command_received.emit(command, client_socket)
                        except json.JSONDecodeError:
                            break
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"Error: {e}")
                    break
        finally:
            with self.connections_lock:
                if client_socket in self.active_connections:
                    self.active_connections.remove(client_socket)
            client_socket.close()

    def stop(self):
        self.running = False
        with self.connections_lock:
            for sock in self.active_connections:
                try:
                    sock.close()
                except:
                    pass
            self.active_connections.clear()
