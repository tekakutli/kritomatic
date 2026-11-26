"""Central registry of playground ports.

Single source of truth for every playground port in this repo.  Each
playground's `_server.py` defines `PLAYGROUND_PORT = port_for("<id>")`;
its `_playground.py` does `PORT = <server>.PLAYGROUND_PORT`.  Cross-
playground URLs (controlnets → inpaint, multimask → inpaint, layout →
magi) use `base_url_for()` / `url_for()` instead of hardcoded
literals.

`kritomatic_nodes` (symlinked into ComfyUI's `custom_nodes/`) imports
from here too — see `kritomatic_nodes/__init__.py`'s sys.path
bootstrap.  Ports are validated unique at import time so a future
re-collision fails loudly instead of silently.
"""

from __future__ import annotations

PLAYGROUND_PORTS: dict[str, int] = {
    # Original 10 in-scope playgrounds.
    "board":        8770,
    "controlnets":  8779,
    "inpaint":      8774,
    "interceptor":  8775,
    "layout":       8777,
    "magi":         8778,
    "multimask":    8776,
    "outpaint":     8768,
    "segment":      8773,
    "text_boxes":   8771,
    # Additional playgrounds added in the follow-up pass.
    "cable":        8765,
    "cone":         8766,
    "remote_panel": 8772,
    "room":         8769,   # was 8766, collided with cone
    "text_wall":    8767,
    # Non-playground service.  The pgkit hub serves every playground's
    # `/remote` WebSocket under a single port; registering it here keeps
    # the fixed-port surface single-sourced even though it isn't a
    # playground in the same sense.
    "hub":          8780,
    "workbench":    8781,
}


def _validate_unique() -> None:
    seen: dict[int, str] = {}
    for pid, port in PLAYGROUND_PORTS.items():
        if port in seen:
            raise RuntimeError(
                f"pgruntime.ports: port {port} assigned to both "
                f"{seen[port]!r} and {pid!r}"
            )
        seen[port] = pid


_validate_unique()


def port_for(playground_id: str) -> int:
    try:
        return PLAYGROUND_PORTS[playground_id]
    except KeyError:
        raise KeyError(
            f"No port registered for playground {playground_id!r}. "
            f"Known: {sorted(PLAYGROUND_PORTS)}"
        ) from None


def base_url_for(playground_id: str) -> str:
    return f"http://127.0.0.1:{port_for(playground_id)}"


def url_for(playground_id: str, path: str = "") -> str:
    base = base_url_for(playground_id)
    if not path:
        return base
    return base + (path if path.startswith("/") else "/" + path)


def ws_url_for(target: str, path: str = "") -> str:
    """WebSocket variant of `url_for` — same port lookup, `ws://` scheme."""
    base = f"ws://127.0.0.1:{port_for(target)}"
    if not path:
        return base
    return base + (path if path.startswith("/") else "/" + path)
