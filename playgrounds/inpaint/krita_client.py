"""Minimal TCP client to the kritomatic daemon.

The playground talks to ComfyUI over HTTP and to Krita via the
kritomatic daemon's TCP socket. Two transports, one pattern: the
playground server is the only HTTP endpoint the browser talks to; it
fans out to whichever backend the operation needs.

The daemon protocol is newline-delimited JSON. A single command gets
one response line; a bundled batch (`{"id": ..., "commands": [...]}`)
gets a stream of `progress` lines followed by one `batch_complete`.
"""

import json
import socket
from typing import Callable, Optional


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


def send_batch(payload: dict,
               on_progress: Optional[Callable[[int, dict], None]] = None,
               timeout: float = 120.0) -> Optional[dict]:
    """Send one bundled `{"id": ..., "commands": [...]}` payload and
    stream per-command results.

    Calls `on_progress(index, result_dict)` for each `progress` line the
    daemon emits. Returns the final `batch_complete` summary dict, or
    None on transport failure.
    """
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((DAEMON_HOST, DAEMON_PORT))
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        sock.settimeout(None)

        buf = b""
        summary = None
        while summary is None:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    continue
                mtype = msg.get("type")
                if mtype == "progress":
                    if on_progress is not None:
                        try:
                            on_progress(msg.get("index", 0),
                                        msg.get("result", {}))
                        except Exception:
                            pass
                elif (mtype == "batch_complete"
                      or msg.get("status") == "batch_complete"):
                    summary = msg
                    break
                else:
                    summary = msg
                    break

        return summary
    except socket.timeout:
        return {"status": "error", "message": "batch timeout"}
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
