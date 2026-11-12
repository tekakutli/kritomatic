"""Copy of inpaint/krita_client.py — minimal TCP client to the
kritomatic daemon.  Unchanged."""

import json
import socket
from typing import Callable, Optional


DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 12346


def is_reachable(timeout: float = 1.5) -> bool:
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
            try: sock.close()
            except OSError: pass


def send(command: dict, timeout: float = 60.0) -> dict:
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((DAEMON_HOST, DAEMON_PORT))
        sock.sendall((json.dumps(command) + "\n").encode("utf-8"))

        buf = b""
        while b"\n" not in buf:
            chunk = sock.recv(65536)
            if not chunk: break
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
            try: sock.close()
            except OSError: pass