"""Playground registry client — announce presence to the kritomatic daemon.

Called once at startup (retry loop, ~30s budget), once at shutdown
(best-effort), and once per browser-side self-heal trigger.

Wire commands:

    playground_register    { id, name, url, port, layer_slots: [...] }
    playground_unregister  { id }

Wire format: the daemon's socket server returns
`{"status": "success"|"error", "message": str, "data": {...}}`.
Checks key on `status`, not `success`.

Each registered playground declares one or more **layer slots** on
register.  A slot is `{"id": "@name", "label": str|None,
"auto_select_layer": str|None}`.  The daemon stores per-slot
selectors; the docker renders one row per (playground, slot); the
playground reads its own slot's selector from `comfy_status` and
re-fetches the displayed layer whenever it changes.
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
            try:
                sock.close()
            except OSError:
                pass


def _response_is_success(resp: dict) -> bool:
    if not isinstance(resp, dict):
        return False
    if resp.get("status") == "success":
        return True
    if resp.get("success") is True:
        return True
    return False


def _build_register_cmd(
    playground_id: str, name: str, url: str, port: int,
    layer_slots,
) -> dict:
    return {
        "type":        "playground_register",
        "id":          playground_id,
        "name":        name,
        "url":         url,
        "port":        int(port),
        "layer_slots": list(layer_slots or []),
    }


def register(
    playground_id: str,
    name: str,
    url: str,
    port: int,
    layer_slots=None,
    retry_s: float = 30.0,
) -> bool:
    """Register with the daemon, retrying up to `retry_s`."""
    cmd = _build_register_cmd(
        playground_id, name, url, port, layer_slots,
    )
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
                f"[playground] register failed after {retry_s:.0f}s: "
                f"{last_msg}",
                flush=True,
            )
            return False
        time.sleep(1.0)


def reregister_once(
    playground_id: str, name: str, url: str, port: int,
    layer_slots=None,
) -> bool:
    """Single-attempt registration.  Used by the browser self-heal path."""
    cmd = _build_register_cmd(
        playground_id, name, url, port, layer_slots,
    )
    resp = _send(cmd, timeout=2.0)
    return _response_is_success(resp)


def unregister(playground_id: str) -> None:
    """Best-effort unregister.  Idempotent.  Never raises."""
    try:
        _send(
            {"type": "playground_unregister", "id": playground_id},
            timeout=2.0,
        )
    except Exception as e:
        print(f"[playground] unregister on exit failed: {e}", flush=True)