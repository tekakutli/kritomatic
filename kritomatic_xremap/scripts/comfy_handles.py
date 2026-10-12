#!/usr/bin/env python3
"""
comfy_handles.py

Shared helpers for reading and filling {{handle_id}} tokens in
API-format ComfyUI workflows.

The token grammar and the discovery/fill semantics live here so that
every consumer script — bg_remove, bg_remove_prompted, comfy_img2img,
and any future sibling — reads and writes handles identically. A change
to the grammar or to the fill rules happens in exactly one place.

The workflow contract this module implements is established by
comfy_workflow_to_api_converter.py:

    - Every user-supplied input on a node in HANDLE_FIELDS is replaced
      by a "{{handle_id}}" string token.
    - A sibling entry in that node's _meta.handles[field] records the
      handle's id, semantic type, required flag, and a description.

Consumers of this module treat that contract as data: they discover
handles by walking _meta.handles (with a regex fallback for any
undeclared token still present in inputs), then substitute values by
handle id.

This module has no third-party dependencies and no side effects on
import.
"""

import re


# Matches {{handle_id}} tokens anywhere in a string value. The id
# grammar is deliberately narrow — [A-Za-z0-9_]+ — so that a token can
# always be typed inside a shell argument without quoting tricks.
TOKEN_RE = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def discover_handles(workflow):
    """
    Return {handle_id: spec} for every handle in the workflow.

    Primary source is each node's _meta.handles block, which is what
    the converter writes. As a safety net, any raw {{token}} found in
    inputs that is not described there is surfaced with a generic spec,
    so a workflow that was generated inconsistently — or hand-edited —
    still fails loudly rather than silently leaving a token unfilled.

    Each spec is a dict with keys:
        node_id   the node the handle lives on
        field     the input field on that node
        type      semantic type ("image_filename", "string", ...)
        required  whether the handle must be filled before submission
    """
    found = {}

    # Declared handles: _meta.handles is authoritative.
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        for field, h in node.get("_meta", {}).get("handles", {}).items():
            found[h["id"]] = {
                "node_id": node_id,
                "field": field,
                "type": h.get("type", "string"),
                "required": h.get("required", True),
            }

    # Fallback: catch tokens that were not declared in _meta.
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        for field, value in node.get("inputs", {}).items():
            if not isinstance(value, str):
                continue
            for m in TOKEN_RE.finditer(value):
                hid = m.group(1)
                found.setdefault(hid, {
                    "node_id": node_id,
                    "field": field,
                    "type": "string",
                    "required": True,
                })

    return found


def fill_handles(workflow, values):
    """
    Substitute values into handle slots, keyed by handle id.

    Raises RuntimeError if a required handle has no value, or if any
    {{token}} survives the substitution. Both checks matter: the first
    catches "the caller forgot", the second catches "the substitution
    wrote something that still looks like a token", which would
    otherwise be posted to ComfyUI and fail with an unhelpful error.
    """
    handles = discover_handles(workflow)

    missing = [hid for hid, spec in handles.items()
               if spec["required"] and hid not in values]
    if missing:
        raise RuntimeError("Missing required handles: " + ", ".join(missing))

    for hid, spec in handles.items():
        if hid in values:
            workflow[spec["node_id"]]["inputs"][spec["field"]] = values[hid]

    leftover = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        for field, value in node.get("inputs", {}).items():
            if isinstance(value, str):
                m = TOKEN_RE.search(value)
                if m:
                    leftover.append(f"{node_id}.{field} -> {m.group(0)}")
    if leftover:
        raise RuntimeError("Unresolved tokens after fill:\n  " +
                           "\n  ".join(leftover))

    return workflow
