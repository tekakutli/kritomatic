#!/usr/bin/env python3
import socket
import json


class KritaClient:
    def __init__(self, host='localhost', port=12346):
        self.host = host
        self.port = port
        self.socket = None
        self.connected = False
        self._recv_buffer = b''

    def connect(self):
        try:
            self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.socket.connect((self.host, self.port))
            self.connected = True
            self._recv_buffer = b''
            return True
        except Exception as e:
            print(f"❌ Failed to connect: {e}")
            return False

    def _read_line(self, timeout=2.0):
        """
        Read one newline-terminated line from the socket.

        Returns the line as bytes WITHOUT the trailing newline.
        Raises socket.timeout if the timeout elapses before a full line
        arrives, and ConnectionError if the peer closes mid-line.
        """
        self.socket.settimeout(timeout)
        while b'\n' not in self._recv_buffer:
            try:
                chunk = self.socket.recv(65536)
            except socket.timeout:
                raise
            if not chunk:
                # Peer closed. If anything is buffered, treat it as a line.
                if self._recv_buffer:
                    line, self._recv_buffer = self._recv_buffer, b''
                    return line
                raise ConnectionError("Socket closed by peer")
            self._recv_buffer += chunk

        line, self._recv_buffer = self._recv_buffer.split(b'\n', 1)
        return line

    def send_command(self, command):
        """Send one command and read one response (single round trip)."""
        if not self.connected and not self.connect():
            return None
        try:
            self.socket.send((json.dumps(command) + '\n').encode('utf-8'))
            line = self._read_line(timeout=2.0)
            if line:
                return json.loads(line.decode('utf-8'))
            return None
        except socket.timeout:
            print("Response timeout - server may be busy")
            return None
        except Exception as e:
            print(f"Error: {e}")
            self.connected = False
            return None

    def send_batch(self, payload, on_progress=None, timeout=60.0):
        """
        Send a bundled `{"id": ..., "commands": [...]}` payload once and
        stream back per-command results.

        Calls `on_progress(index, result_dict)` for each `progress` line
        the daemon emits. Returns the final `batch_complete` summary, or
        None on error.
        """
        if not self.connected and not self.connect():
            return None
        try:
            self.socket.send((json.dumps(payload) + '\n').encode('utf-8'))

            summary = None
            while summary is None:
                line = self._read_line(timeout=timeout)
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get('type')
                if msg_type == 'progress':
                    if on_progress:
                        try:
                            on_progress(msg.get('index', 0), msg.get('result', {}))
                        except Exception:
                            pass
                elif msg_type == 'batch_complete' or msg.get('status') == 'batch_complete':
                    summary = msg
                else:
                    # Unexpected message. Treat as terminal so we don't hang.
                    summary = msg

            return summary
        except socket.timeout:
            print("Batch response timeout - server may be busy")
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
        self._recv_buffer = b''
