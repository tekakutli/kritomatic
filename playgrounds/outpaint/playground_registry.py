"""Playground registry client — announce presence to the kritomatic daemon.

Called once at startup (retry loop, ~30s budget), once at shutdown
(best-effort), and once per browser-side self-heal trigger.

Wire commands:

    playground_register    { id, name, url, port,
                             layer_slots: [...], param_slots: [...],
                             control_params: [...] }
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

Each registered playground may also declare one or more **param
slots** — a flat list of workflow-parameter names it owns.  Those
parameters disappear from the docker's Parameters section, and at
generation time the daemon queries the playground's `/params` route
for current values.  See `kritomatic_daemon/handlers/playgrounds.py`'s
PARAM SLOTS.

Additionally, a playground may declare one or more **control params**
— daemon-owned routing hints (e.g. `DESTINATION`) that live in the
registry and are edited from the docker.  Control params are
singletons: at most one playground may claim each name.  See
`kritomatic_daemon/handlers/playgrounds.py`'s CONTROL PARAMS.
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
    param_slots,
    control_params,
) -> dict:
    cmd = {
        "type":           "playground_register",
        "id":             playground_id,
        "name":           name,
        "url":            url,
        "port":           int(port),
        "layer_slots":    list(layer_slots or []),
        "param_slots":    list(param_slots or []),
        "control_params": list(control_params or []),
    }
    return cmd


def register(
    playground_id: str,
    name: str,
    url: str,
    port: int,
    layer_slots=None,
    param_slots=None,
    control_params=None,
    retry_s: float = 30.0,
) -> bool:
    """Register with the daemon, retrying up to `retry_s`.

    Returns True on success.  A playground that fails to register
    still works — it just doesn't appear in the docker.

    `control_params` (if provided) must be a list of dicts with
    `{name, type, options?, default}`.  See
    `kritomatic_daemon/handlers/playgrounds.py`'s CONTROL PARAMS.
    """
    cmd = _build_register_cmd(
        playground_id, name, url, port,
        layer_slots, param_slots, control_params,
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
    param_slots=None,
    control_params=None,
) -> bool:
    """Single-attempt registration.  Used by the browser self-heal path.

    Never retries, never sleeps.
    """
    cmd = _build_register_cmd(
        playground_id, name, url, port,
        layer_slots, param_slots, control_params,
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