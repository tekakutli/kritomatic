"""
layer_resolve.py — scope-aware node lookup.

Every node in a Krita document carries a stable QUuid (`Node.uniqueId()`).
Names are not unique: two layers can both be called "Background" and
`Document.nodeByName` returns whichever it finds first. The bundle
format gives us a natural place to enforce scope — the bundle id, and
the include path within it — so this module keeps a per-scope map of
name → uuid for the duration of one batch, and resolves a name against
that map before falling back to the document-wide name lookup.

Scope ids are hierarchical strings. A top-level command's scope is the
bundle id, e.g. `"poster_7a3f"`. A command inside the first include is
`"poster_7a3f_inc0_1"`. A command inside an include nested one level
deeper is `"poster_7a3f_inc0_1_inc0_2"`. The executor builds these; the
daemon consumes them.

Resolution order for `resolve(doc, key)`:

  1. Current scope's created-nodes map, keyed by the bare name.
  2. If `key` is a UUID string, walk the tree for that UUID.
  3. `doc.nodeByName(key)` — document-wide, may be ambiguous.

Registration happens when a creating command finishes: the handler calls
`register(name, node)` with the bare name it used, and the resolver
records the node's uuid under that name in the current scope. A second
creation with the same bare name in the same scope overwrites the entry;
that matches the "the last one wins" behavior Krita itself exhibits for
duplicate names, but scoped.

Thread-local. The daemon processes one batch per client thread at a
time; two concurrent batches on different sockets get independent maps.
"""

from __future__ import annotations

import re
import threading
from typing import Optional

from krita import Krita


# QUuid string format is 8-4-4-4-12 hex with hyphens.
_UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


_state = threading.local()


def _local():
    if not hasattr(_state, "scopes"):
        _state.scopes = {}
        _state.current_scope = None
    return _state


# ---------------------------------------------------------------------------
#  Batch lifecycle
# ---------------------------------------------------------------------------

def begin_batch() -> None:
    """Reset scope state. Called by the daemon once per batch, before
    the first command."""
    s = _local()
    s.scopes = {}
    s.current_scope = None


def end_batch() -> None:
    """Drop scope state. Called once per batch, after the last command."""
    s = _local()
    s.scopes = {}
    s.current_scope = None


def set_scope(scope_id: Optional[str]) -> None:
    """Set the current scope. Called before each command dispatch.

    A command that carries no `_scope` (a single-command invocation, or
    a command that came from a caller that did not tag it) sets the
    scope to None, and resolution falls back to UUID/nodeByName. That is
    the correct behavior for anything outside a bundle.
    """
    s = _local()
    s.current_scope = scope_id
    if scope_id is not None and scope_id not in s.scopes:
        s.scopes[scope_id] = {}


# ---------------------------------------------------------------------------
#  Registration and resolution
# ---------------------------------------------------------------------------

def register(name: Optional[str], node) -> None:
    """Record `node` under `name` in the current scope.

    Handlers call this immediately after creating a node, passing the
    bare name the bundle used. Overwrites any previous entry with the
    same name in the same scope. A no-op when there is no current
    scope or when either argument is missing.
    """
    if not name or node is None:
        return
    s = _local()
    if s.current_scope is None:
        return
    try:
        uuid = node.uniqueId().toString()
    except Exception:
        return
    s.scopes.setdefault(s.current_scope, {})[name] = uuid


def resolve(doc, key: Optional[str]):
    """Return the node that `key` refers to, or None.

    See the module docstring for the resolution order. Returns None when
    the key is empty, when no node matches, and when `doc` is None or
    has no root.
    """
    if not key or doc is None:
        return None

    s = _local()

    # 1. Current scope's map.
    if s.current_scope is not None:
        uuid = s.scopes.get(s.current_scope, {}).get(key)
        if uuid:
            node = _find_by_uuid(doc, uuid)
            if node is not None:
                return node
        # A key that was registered but no longer resolves means the
        # node was deleted between commands. Fall through; the caller
        # will likely fail on the subsequent operation, with a clearer
        # error than "not found in scope".

    # 2. UUID string.
    if _UUID_RE.match(key):
        node = _find_by_uuid(doc, key)
        if node is not None:
            return node

    # 3. Document-wide name lookup.
    try:
        return doc.nodeByName(key)
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  UUID lookup
# ---------------------------------------------------------------------------
#
# Krita's Python API is inconsistent about exposing a direct
# uuid->node lookup across versions. Walking the tree is O(n) over the
# layer count, which for a document is small; correctness over
# cleverness.

def _find_by_uuid(doc, uuid_str: str):
    try:
        target = doc.rootNode().uniqueId().__class__(uuid_str)
    except Exception:
        return None

    def walk(node):
        try:
            if node.uniqueId() == target:
                return node
        except Exception:
            return None
        for child in node.childNodes():
            found = walk(child)
            if found is not None:
                return found
        return None

    try:
        return walk(doc.rootNode())
    except Exception:
        return None
