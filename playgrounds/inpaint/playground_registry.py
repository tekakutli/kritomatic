"""Playground registry client — announce presence to the kritomatic daemon.

Called once at startup (retry loop, ~30s budget), once at shutdown
(best-effort), and (via `/playground_reregister`) from the browser's
autosync when it notices the daemon has removed our registry entry.
Same wire protocol as controlnets' copy; the daemon does not
distinguish callers.  See `controlnets/playground_registry.py` for the
full contract — this module exists so inpaint can register its layer
slots without importing from a sibling playground's package.

RE-REGISTRATION
===============
`reregister_once` is the single-shot variant used by the inpaint
server's `/playground_reregister` route.  The browser's autosync
poller detects "our entry is missing from `comfy_status`'s
`playgrounds` list" on its next tick and POSTs to that route, which
calls this function synchronously.  Recovering in ≤500 ms is what
keeps the docker's Playgrounds section from flickering when the
daemon's sweep thread misjudges the process's liveness — see
`kritomatic_daemon/handlers/playgrounds.py`'s module docstring,
SWEEP: WHAT "DEAD" MEANS.

The initial `register` call at process startup (from
`inpaint_playground.py`) uses `register`, not this function — it has
the retry loop and the "warn on failure, keep serving" semantics
that boot needs.  `reregister_once` is the fast-fail variant, since
autosync will try again on its own schedule if this call fails.
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


def reregister_once(playground_id, name, url, port,
                    layer_slots=None) -> bool:
    """Single-shot registration attempt.  Used by the browser's
    autosync poller (via the inpaint server's
    `/playground_reregister` route) when it detects that the daemon
    has removed our registry entry.

    Fails fast (2 s socket timeout, no retry loop) so the browser's
    `/playground_reregister` POST returns quickly and autosync can
    reschedule on its normal cadence.  Contrast with `register`,
    which retries for ~30 s because it is called once at startup and
    a silent miss there means the user has to restart the playground
    manually.  See the module docstring, RE-REGISTRATION.
    """
    cmd = _build_register_cmd(playground_id, name, url, port, layer_slots)
    resp = _send(cmd, timeout=2.0)
    return _response_is_success(resp)


def unregister(playground_id: str) -> None:
    try:
        _send(
            {"type": "playground_unregister", "id": playground_id},
            timeout=2.0,
        )
    except Exception as e:
        print(f"[inpaint] unregister on exit failed: {e}", flush=True)