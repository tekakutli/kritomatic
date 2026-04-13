#!/usr/bin/env python3
import socket
import json
import time

class KritaClient:
    def __init__(self, host='localhost', port=12346):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            return True
        except Exception as e:
            print(f"❌ Failed to connect: {e}")
            return False

    def send_command(self, command):
        if not self.connected and not self.connect():
            return None
        try:
            self.socket.send(json.dumps(command).encode('utf-8'))
            self.socket.settimeout(2.0)
            response_data = self.socket.recv(65536).decode('utf-8')
            if response_data:
                return json.loads(response_data)
            return None
        except socket.timeout:
            print("Response timeout - server may be busy")
            return None
        except Exception as e:
            print(f"Error: {e}")
            self.connected = False
            return None

    def execute(self, cmd_type, **kwargs):
        command = {'type': cmd_type, **kwargs}
        return self.send_command(command)

    def close(self):
        if self.socket:
            try:
                self.socket.close()
            except:
                pass
        self.connected = False
