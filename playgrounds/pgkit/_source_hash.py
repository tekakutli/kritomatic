"""pgkit._source_hash — content hash of the hub↔client contract.

Both `pgkit/hub.py` (the server) and `pgkit/hub_client.py` (the
client) compute this hash on startup. If they disagree, the running
hub was loaded from a different version of the source than the
caller is running now — Python does not reload already-imported
modules when their `.py` file changes on disk, so a hub that started
before an edit will keep serving the old code until it is killed.

`hub_client.ensure_running` uses the mismatch to restart the running
hub, so a developer who edits `hub.py` sees their changes on the next
playground start without remembering to `pgkit.hub --shutdown`
manually.

WHICH FILES ARE COVERED
=======================
`_CONTRACT_MODULES` names the files whose content, if changed, forces
a restart. It must include every file that defines a wire-format
decision or a cross-process invariant:

    __init__.py      PROTOCOL_VERSION, __version__
    hub.py           the server itself
    hub_client.py    the client's expectations of the server
    ws.py            the framing (both sides)

Files that are imported by one side only and do not affect the wire
format — `panel_core.py`, `shell.py`, `styles.py`, `js_check.py`,
`lint.py`, `html_shell.py`, `modules.py`, `server.py`,
`remote_client.py`, `remote_cli_client.py`, `remote_hub.py`,
`launcher.py` — are deliberately excluded. Editing one of those
should not force every running playground to restart.

Adding a file to this tuple is a compatibility commitment: any
running hub started before the addition will be considered stale on
the next playground start, shut down, and relaunched. That is the
correct semantics — a new contract file means the old hub does not
have it — but it should be done deliberately, not by accident.

WHY NOT A MANUAL VERSION CONSTANT
=================================
`PROTOCOL_VERSION` is already bumped by hand for wire-compatible
changes. But forgetting to bump it is exactly the failure mode this
module exists to prevent. mtime-based detection is also unreliable:
`touch` without content changes, `git checkout` of an unchanged
branch, a save with no edits — all update mtime without changing the
code. Hashing the bytes sidesteps both: the hash changes iff the
bytes change.

CACHING
=======
The hash is computed once per process and cached. This is
deliberate. The caller's question is "does the running hub have the
same code I loaded?" — not "does the code on disk right now match
what I loaded?" Those become different questions the moment someone
edits a file mid-session. The first question is the one that
determines whether the wire protocol will interoperate, and the
first question is what a cache preserves.

READING FROM DISK, NOT FROM `sys.modules`
=========================================
`source_hash` reads the `.py` files' bytes directly rather than
calling `inspect.getsource` on the imported modules. That means it
works even for files the caller does not import (`hub.py` on the
client side, `hub_client.py` on the server side) — both sides are
contract-committed to include both, and reading from disk is how
either side can honour that commitment without dragging the other
into its import graph.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


_CONTRACT_MODULES = (
    "__init__.py",
    "hub.py",
    "hub_client.py",
    "ws.py",
)

_CACHE: "str | None" = None


def source_hash() -> str:
    """Hex sha256 of the contract modules' current on-disk content.

    Cached after the first call; the result is stable for the process
    lifetime. A missing file contributes ``missing:<name>`` to the
    hash. That cannot happen in a working checkout, but it keeps the
    hash deterministic if it does — the alternative, raising, would
    make the whole playground unusable for an unrelated packaging
    mistake.
    """
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    h = hashlib.sha256()
    here = Path(__file__).resolve().parent
    for name in _CONTRACT_MODULES:
        try:
            data = (here / name).read_bytes()
        except OSError:
            data = b"missing:" + name.encode("utf-8")
        h.update(data)
        h.update(b"\0")
    _CACHE = h.hexdigest()
    return _CACHE


def short_hash() -> str:
    """8-character prefix of `source_hash`, for log messages."""
    return source_hash()[:8]
