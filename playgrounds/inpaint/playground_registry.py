"""Playground registry client — announce presence to the kritomatic daemon.

Called once at startup (retry loop, ~30s budget), once at shutdown
(best-effort).  Same wire protocol as controlnets' copy; the daemon
does not distinguish callers.  See `controlnets/playground_registry.py`
for the full contract — this module exists so inpaint can register its
layer slots without importing from a sibling playground's package.
"""

from __future__ import annotations

import json
import socket
import time


DAEMON_HOST = "127.0.0.1"
DAEMON_PORT = 12346


def _send(command: dict, timeout: float = 5.0) -> dict:
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
            return {"status": "error", "message": "empty response"}
        line = buf.split(b"\n", 1)[0]
        return json.loads(line.decode("utf-8"))
    except (socket.timeout, ConnectionRefusedError, OSError) as e:
        return {"status": "error", "message": str(e)}
    except json.JSONDecodeError as e:
        return {"status": "error", "message": f"bad response: {e}"}
    finally:
        if sock is not None:
            try: sock.close()
            except OSError: pass


def _response_is_success(resp: dict) -> bool:
    if not isinstance(resp, dict):
        return False
    if resp.get("status") == "success":
        return True
    if resp.get("success") is True:
        return True
    return False


def _build_register_cmd(playground_id, name, url, port, layer_slots):
    return {
        "type":        "playground_register",
        "id":          playground_id,
        "name":        name,
        "url":         url,
        "port":        int(port),
        "layer_slots": list(layer_slots or []),
    }


def register(playground_id, name, url, port, layer_slots=None,
             retry_s: float = 30.0) -> bool:
    cmd = _build_register_cmd(playground_id, name, url, port, layer_slots)
    deadline = time.monotonic() + retry_s
    last_msg = "unknown error"
    while True:
        resp = _send(cmd, timeout=2.0)
        if _response_is_success(resp):
            return True
        last_msg = (
            (resp.get("message") if isinstance(resp, dict) else None)
            or last_msg
        )
        if time.monotonic() >= deadline:
            print(
                f"[inpaint] register failed after {retry_s:.0f}s: "
                f"{last_msg}", flush=True,
            )
            return False
        time.sleep(1.0)


def unregister(playground_id: str) -> None:
    try:
        _send(
            {"type": "playground_unregister", "id": playground_id},
            timeout=2.0,
        )
    except Exception as e:
        print(f"[inpaint] unregister on exit failed: {e}", flush=True)