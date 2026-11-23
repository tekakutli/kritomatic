"""TCP client to the kritomatic daemon.

Two transports, one pattern: the playground server is the only HTTP
endpoint the browser talks to; it fans out to whichever backend the
operation needs (ComfyUI over HTTP, Krita via this socket).

The daemon protocol is newline-delimited JSON.  A single command gets
one response line; a bundled batch (`{"id": ..., "commands": [...]}`)
gets a stream of `progress` lines followed by one `batch_complete`.

Union of the six per-playground copies this replaces:

  · controlnets/krita_client.py   (had `send_batch`)
  · inpaint/krita_client.py       (had `send_batch`)
  · layout/krita_client.py
  · magi/krita_client.py
  · multimask/krita_client.py     (identical to inpaint's, by explicit
                                   docstring admission)
  · segment/krita_client.py

All six had identical `is_reachable` and `send`.  Two of six had
`send_batch`.  The union ships here; playgrounds that never batch pay
nothing for the extra function (it isn't called).

DAEMON_HOST and DAEMON_PORT are re-exported so daemon_registry.py can
share them without redefining the constants.
"""

import json
import socket
from typing import Callable, Optional


DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 12346


def is_reachable(timeout: float = 1.5) -> bool:
    """TCP probe: True iff something is listening.

    A pure port-open check, not a command round-trip.  Cheaper than a
    `send({"type": "ping"})` and correct for its use case: every
    playground's "Check Krita" button only needs to know whether
    *anything* is listening on the daemon port.  A half-broken daemon
    that accepts the connection but hangs on commands will still
    report reachable — the real command path lives in `send`, which
    has its own timeout handling.
    """
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

    Returns a dict with 'status' key ('success'|'error').  Never
    raises; connection failures come back as
    `{'status': 'error', 'message': ...}`, matching the daemon's own
    error response shape so callers can treat both uniformly.
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
            return {
                "status": "error",
                "message": "empty response from daemon",
            }

        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))
    except socket.timeout:
        return {"status": "error", "message": "daemon timeout"}
    except ConnectionRefusedError:
        return {
            "status": "error",
            "message": "kritomatic daemon unreachable",
        }
    except json.JSONDecodeError as e:
        return {
            "status": "error",
            "message": f"bad daemon response: {e}",
        }
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

    Calls `on_progress(index, result_dict)` for each `progress` line
    the daemon emits.  Returns the final `batch_complete` summary
    dict, or None on transport failure.

    The batch protocol differs from `send` in two ways that matter
    for the caller:

      · one socket carries N commands and N+1 responses (progress
        lines plus a terminal summary), so the timeout is a total
        budget, not a per-command one;

      · the socket has no per-read timeout after the initial connect
        — a slow ComfyUI preprocessing node would otherwise trip the
        per-read deadline partway through the batch and leave the
        rest of the commands unread.

    Callers that only ever send one command should use `send`, which
    has simpler failure semantics.
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
        return {
            "status": "error",
            "message": "kritomatic daemon unreachable",
        }
    except json.JSONDecodeError as e:
        return {
            "status": "error",
            "message": f"bad daemon response: {e}",
        }
    except OSError as e:
        return {"status": "error", "message": f"daemon error: {e}"}
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass