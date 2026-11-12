"""comfyui_inspect.py — extract native-v2 slot tables from a prompt graph.

Three extractors walk a ComfyUI prompt-format graph (a dict of node
id -> {class_type, inputs}) and return the tables the native-v2
pipeline operates on:

  * `extract_resource_slots` finds `LoadImage` / `LoadImageMask`
    nodes whose `image` input is `@`-prefixed, and records the
    `@-name` -> {node_id, input_field, kind} mapping.

  * `extract_param_slots` finds `Primitive*` nodes carrying a
    `_meta.title`, and records the `title` ->
    {node_id, kind, default} mapping.

  * `extract_output_nodes` finds `SaveImage` nodes and records them
    as `node_id` -> {class_type, filename_prefix}.

All three are pure functions of the graph; nothing here touches
Krita, ComfyUI, the filesystem, or any state.

Duplicate detection
===================
A graph with two `@`-names on different nodes, or two `Primitive*`
nodes sharing a `_meta.title`, is ambiguous: the config or panel
cannot tell which node a value was meant for.  Both cases raise
`ValueError` naming both node ids.  The daemon's `comfy_load` catches
the error and clears its slot tables (a malformed graph cannot be
driven by the native pipeline); the playground's `handle_inpaint`
lets the error surface directly.  Either way, the caller learns about
the problem instead of silently losing one of the two slots.

Shared between
==============
`kritomatic_daemon` (which uses all three), the inpaint playground's
server (which uses the resource and param extractors), and the CLI's
panel template generator (which derives a human-editable template
from all three).  All three import from `kritomatic_shared` — the
daemon via the `sys.path` bootstrap in
`kritomatic_daemon/handlers/comfyui_inspect.py`, the CLI via pip
install, the playground via `pg_comfy`'s own bootstrap.

Previously the logic lived in three places with subtly different
behaviour: this daemon-side module had duplicate-checking and
recorded param defaults; `inpaint_server.py` (the playground) had
neither; `comfyui_panel_config.extract_native_workflow` (the CLI)
produced a flat name list with no node ids.  Those three copies are
gone.  One module, one behaviour.
"""

from __future__ import annotations


# `LoadImage` / `LoadImageMask` are the only native node classes whose
# `image` input is scanned for `@`-prefix resource references. A value
# that does not start with `@` is a real ComfyUI input filename and is
# left alone.
_RESOURCE_NODE_FIELDS: dict[str, tuple[str, str]] = {
    "LoadImage": ("image", "image"),
    "LoadImageMask": ("image", "mask"),
}

# Native `Primitive*` classes with their coerced value kind.
_PARAM_NODE_KINDS: dict[str, str] = {
    "PrimitiveFloat": "float",
    "PrimitiveInt": "int",
    "PrimitiveString": "string",
    "PrimitiveBoolean": "boolean",
}

# The only native output node the paste filter cares about.
_OUTPUT_NODE_CLASSES: frozenset[str] = frozenset({"SaveImage"})


def extract_resource_slots(graph: dict) -> dict:
    """Walk the graph for native `LoadImage`/`LoadImageMask` nodes whose
    image input is `@`-prefixed. Returns::

        {
          "@thecanvas":    {"node_id": "1", "input_field": "image",
                            "kind": "image"},
          "@theselection": {"node_id": "2", "input_field": "image",
                            "kind": "mask"},
          ...
        }

    Empty when the workflow has no `@`-prefixed inputs. A single
    `@`-name appearing on two nodes is an authoring error — the daemon
    cannot know which node the config meant. Raises ValueError naming
    both node ids.
    """
    out: dict[str, dict] = {}
    for node_id, node in graph.items():
        cls = node.get("class_type", "")
        spec = _RESOURCE_NODE_FIELDS.get(cls)
        if spec is None:
            continue
        field, kind = spec
        inputs = node.get("inputs", {}) or {}
        value = inputs.get(field)
        if not isinstance(value, str) or not value.startswith("@"):
            continue
        if value in out:
            raise ValueError(
                f"duplicate @-prefixed resource {value!r}: appears on "
                f"node {out[value]['node_id']!r} and node {node_id!r}. "
                f"Each @-name must be used by exactly one node."
            )
        out[value] = {
            "node_id": node_id,
            "input_field": field,
            "kind": kind,
        }
    return out


def extract_param_slots(graph: dict) -> dict:
    """Walk the graph for native `Primitive*` nodes with `_meta.title`.
    Returns::

        {
          "denoise":    {"node_id": "10", "kind": "float",
                         "default": 0.7},
          "prompt":     {"node_id": "11", "kind": "string",
                         "default": "a cat"},
          "Illustrous?":{"node_id": "12", "kind": "boolean",
                         "default": True},
          ...
        }

    The `default` field is the node's current `value` input at
    extraction time.  It is what the workflow was authored to use.
    `None` when the node has no `value` input (malformed workflow; the
    panel falls back to a kind default).

    Nodes without `_meta.title` are ignored.  Two nodes sharing a
    title is an authoring error; raises ValueError naming both.
    """
    out: dict[str, dict] = {}
    for node_id, node in graph.items():
        cls = node.get("class_type", "")
        kind = _PARAM_NODE_KINDS.get(cls)
        if kind is None:
            continue
        meta = node.get("_meta") or {}
        title = meta.get("title", "")
        if not isinstance(title, str) or not title.strip():
            continue
        if title in out:
            raise ValueError(
                f"duplicate Primitive title {title!r}: appears on "
                f"node {out[title]['node_id']!r} and node {node_id!r}. "
                f"Each _meta.title must be unique."
            )
        inputs = node.get("inputs", {}) or {}
        out[title] = {
            "node_id": node_id,
            "kind": kind,
            "default": inputs.get("value"),
        }
    return out


def extract_output_nodes(graph: dict) -> dict:
    """Walk the graph for terminal output nodes (`SaveImage`). Returns::

        {
          "9": {"class_type": "SaveImage",
                "filename_prefix": "kritomatic"},
        }

    Empty when the workflow has no recognized output node — ComfyUI
    will reject the submission with "Prompt has no outputs", which the
    proxy surfaces as ``422 invalid_workflow``.
    """
    out: dict[str, dict] = {}
    for node_id, node in graph.items():
        cls = node.get("class_type", "")
        if cls not in _OUTPUT_NODE_CLASSES:
            continue
        inputs = node.get("inputs", {}) or {}
        out[node_id] = {
            "class_type": cls,
            "filename_prefix": inputs.get("filename_prefix", ""),
        }
    return out


def extract_all_slots(graph: dict) -> tuple[dict, dict, dict]:
    """Run all three extractors once and return
    ``(resource_slots, param_slots, output_nodes)``.

    Convenience for callers that need the whole picture; it makes no
    difference which order they call the three functions in, but
    having one entry point lets the caller validate up front
    (extract_param_slots' duplicate check runs before extract_output_nodes
    touches the graph, same as the three-call form).
    """
    return (
        extract_resource_slots(graph),
        extract_param_slots(graph),
        extract_output_nodes(graph),
    )
