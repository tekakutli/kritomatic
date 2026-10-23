"""Minimal TCP client to the kritomatic daemon.

The playground talks to ComfyUI over HTTP and to Krita via the
kritomatic daemon's TCP socket. Two transports, one pattern: the
playground server is the only HTTP endpoint the browser talks to; it
fans out to whichever backend the operation needs.

The daemon protocol is newline-delimited JSON, one command in, one
response out, then the socket closes (for non-batch commands).
"""

import json
import socket


DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 12346


def is_reachable(timeout: float = 1.5) -> bool:
    """TCP probe: True iff something is listening."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((DAEMON_HOST, DAEMON_PORT))
        return True
    except OSError:
        return False
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def send(command: dict, timeout: float = 60.0) -> dict:
    """Open a fresh socket, send one JSON line, read one JSON line.

    Returns a dict with 'status' key ('success'|'error'). Never raises;
    connection failures come back as {'status': 'error', 'message': ...}.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((DAEMON_HOST, DAEMON_PORT))
        sock.sendall((json.dumps(command) + "\n").encode("utf-8"))

        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk

        if not buf:
            return {"status": "error", "message": "empty response from daemon"}

        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))
    except socket.timeout:
        return {"status": "error", "message": "daemon timeout"}
    except ConnectionRefusedError:
        return {"status": "error", "message": "kritomatic daemon unreachable"}
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"bad daemon response: {e}"}
    except OSError as e:
        return {"status": "error", "message": f"daemon error: {e}"}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass
