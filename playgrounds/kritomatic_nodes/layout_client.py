"""Transport to the layout playground's `/state` and `/version`.

Shared by every node in this pack that reads the layout playground —
`DrawatoonSetIP` (bboxes) and `PlaygroundResolutionSelect` (viewer
ratio/model).  Both nodes fetch the same JSON; keeping the transport
in one module avoids duplicated URL constants and duplicated
exception handling.

Nothing here is node-specific.  `fetch_state_dict()` returns `{}` on
any failure (connection refused, timeout, non-JSON body) so callers
can uniformly decide how to fall back — `DrawatoonSetIP` falls back
to `void_ip_embed`, `PlaygroundResolutionSelect` falls back to
512×512 with a log warning.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


from pgruntime.ports import url_for

LAYOUT_STATE_URL   = url_for("layout", "/state")
LAYOUT_VERSION_URL = url_for("layout", "/version")


STATE_FETCH_TIMEOUT_S   = 2.0
VERSION_FETCH_TIMEOUT_S = 1.0


def post_json(url: str, body: dict, timeout: float) -> dict:
    """One-shot POST → parsed JSON response.  Raises on transport failure."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def probe_version() -> str:
    """Best-effort `/version` probe.  Returns "" when unreachable.

    Used by `fingerprint_inputs` so the node re-executes only when the
    playground's state hash actually changes.
    """
    try:
        r = post_json(LAYOUT_VERSION_URL, {}, timeout=VERSION_FETCH_TIMEOUT_S)
    except Exception:
        return ""
    if not isinstance(r, dict):
        return ""
    v = r.get("version")
    return str(v) if v is not None else ""


def fetch_state_dict() -> dict:
    """Fetch the full `/state`.  Returns `{}` on any failure.

    Callers decide the fallback.  The playground returns a dict with
    at minimum `text_bboxes`, `character_bboxes`,
    `reference_embeddings`, `image_dims`, and `viewer` when reachable.
    """
    try:
        r = post_json(LAYOUT_STATE_URL, {}, timeout=STATE_FETCH_TIMEOUT_S)
    except Exception:
        return {}
    return r if isinstance(r, dict) else {}
