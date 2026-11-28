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
re-collision fails loudly instead of silently, and against
`RESERVED_PORTS` so a playground can never take over a port that
belongs to another tool on this machine.
"""

from __future__ import annotations

RESERVED_PORTS: dict[int, str] = {
    8765: "DeepSeek CLI pipe/type/enter",
    8766: "DeepSeek daemon OpenAI endpoint",
    8767: "DeepSeek daemon extension SSE/relay",
}

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
    "rmbg":         8785,
    # Additional playgrounds added in the follow-up pass.
    "cable":        8782,
    "cone":         8783,
    "remote_panel": 8772,
    "room":         8769,
    "text_wall":    8784,
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


def _validate_not_reserved() -> None:
    for pid, port in PLAYGROUND_PORTS.items():
        if port in RESERVED_PORTS:
            raise RuntimeError(
                f"pgruntime.ports: {pid!r} claims port {port}, which "
                f"is reserved ({RESERVED_PORTS[port]}).  Pick a "
                f"different port."
            )


_validate_unique()
_validate_not_reserved()


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
