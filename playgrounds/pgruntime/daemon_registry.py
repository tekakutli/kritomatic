"""Playground registry client — announce presence to the daemon.

Wire commands:

    playground_register    { id, name, url, port, layer_slots: [...] }
    playground_unregister  { id }

The daemon's socket server returns
`{"status": "success"|"error", "message": str, "data": {...}}`.
Checks key on `status`, not `success` (the daemon's wire format has
both fields; `status` is the authoritative one and `success` is a
compatibility echo some older handlers emit).

Each registered playground declares one or more **layer slots** on
register.  A slot is `{"id": "@name", "label": str|None,
"auto_select_layer": str|None}`.  The daemon stores per-slot
selectors; the docker renders one row per (playground, slot); the
playground reads its own slot's selector from `comfy_status` and
re-fetches the displayed layer whenever it changes.

CALLED BY
=========
    register(...)         once at startup, in a retry loop
    reregister_once(...)  once per browser-side self-heal trigger,
                          when a poll notices the entry went missing
    unregister(...)       once at shutdown, best-effort

Union of four per-playground copies this replaces:

  · controlnets/playground_registry.py
  · layout/playground_registry.py
  · magi/playground_registry.py
  · inpaint/playground_registry.py     (identical to controlnets's, by
                                        explicit docstring admission)

The only functional difference between them was the log prefix on the
register failure line — controlnets and inpaint printed `[controlnets]`
/ `[inpaint]`; layout and magi printed `[playground]`.  The shared
implementation takes the prefix as a `log_prefix` kwarg so each
caller keeps its own naming.
"""

from __future__ import annotations

import sys
import time

from .daemon_client import send as _send


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
    log_prefix: str = "playground",
) -> bool:
    """Register with the daemon, retrying up to `retry_s`.

    Returns True on success.  A playground that fails to register
    still works — it just doesn't appear in the docker, and its
    `@*` layer slots don't get a Playgrounds row.
    """
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
                f"[{log_prefix}] register failed after {retry_s:.0f}s: "
                f"{last_msg}",
                flush=True,
            )
            return False
        time.sleep(1.0)


def reregister_once(
    playground_id: str, name: str, url: str, port: int,
    layer_slots=None,
) -> bool:
    """Single-attempt registration.  Used by the browser self-heal path.

    Never retries, never sleeps.  A failed reregister is fine — the
    poller that noticed the missing entry will try again on its next
    tick, throttled by the browser-side `_reregisterIfMissing` gate.
    """
    cmd = _build_register_cmd(
        playground_id, name, url, port, layer_slots,
    )
    resp = _send(cmd, timeout=2.0)
    return _response_is_success(resp)


def unregister(playground_id: str,
               log_prefix: str = "playground") -> None:
    """Best-effort unregister.  Idempotent.  Never raises."""
    try:
        _send(
            {"type": "playground_unregister", "id": playground_id},
            timeout=2.0,
        )
    except Exception as e:
        print(
            f"[{log_prefix}] unregister on exit failed: {e}",
            file=sys.stderr,
            flush=True,
        )