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
            self.socket.sendall((json.dumps(command) + '\n').encode('utf-8'))
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

    @staticmethod
    def _synthesize_summary(total, collected):
        """Build a batch_complete-shaped summary from the progress
        lines the client has already received.

        Used when the daemon's own summary does not arrive.  A batch
        that streamed every progress line is complete; the summary
        aggregates the same data and is a convenience, not a
        requirement.  Synthesizing it here lets the client return
        promptly instead of waiting out its full timeout for a
        message that may not come.
        """
        successful = sum(1 for r in collected if r.get('status') == 'success')
        failed = sum(1 for r in collected if r.get('status') == 'error')
        return {
            'type': 'batch_complete',
            'status': 'batch_complete',
            'total': total,
            'successful': successful,
            'failed': failed,
            'results': collected,
            'synthesized': True,
        }

    def send_batch(self, payload, on_progress=None, timeout=60.0,
                   summary_grace=2.0):
        """
        Send a bundled `{"id": ..., "commands": [...]}` payload once
        and stream back per-command results.

        Calls `on_progress(index, result_dict)` for each `progress`
        line the daemon emits.  Returns the final `batch_complete`
        summary.

        If the daemon's summary does not arrive within `summary_grace`
        seconds after the last progress line, a summary is synthesized
        from the progress lines already received.  This decouples the
        client's success from the summary's delivery: once `total`
        progress lines have arrived, the batch is done from the
        client's perspective.

        Uses sendall for the outbound payload: socket.send() may send
        fewer bytes than requested and return the count.
        """
        if not self.connected and not self.connect():
            return None
        try:
            self.socket.sendall((json.dumps(payload) + '\n').encode('utf-8'))

            total = None
            received = 0
            collected = []
            summary = None

            while True:
                # Once every progress line has arrived, shorten the
                # read timeout to the summary grace period.  If the
                # summary is coming, it is coming now; if it is not,
                # we should not wait the full batch timeout for it.
                if total is not None and received >= total:
                    read_timeout = summary_grace
                else:
                    read_timeout = timeout

                try:
                    line = self._read_line(timeout=read_timeout)
                except socket.timeout:
                    if total is not None and received >= total:
                        summary = self._synthesize_summary(total, collected)
                        break
                    raise

                if not line:
                    continue

                try:
                    msg = json.loads(line.decode('utf-8'))
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get('type')
                if msg_type == 'progress':
                    if total is None:
                        total = msg.get('total', None)
                    received += 1
                    result = msg.get('result', {})
                    collected.append(result)
                    if on_progress:
                        try:
                            on_progress(msg.get('index', 0), result)
                        except Exception:
                            pass
                elif msg_type == 'batch_complete' or msg.get('status') == 'batch_complete':
                    summary = msg
                    break
                else:
                    summary = msg
                    break

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
