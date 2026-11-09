"""Stable helpers that read a workflow's native-v2 slots.

Three extractors walk a prompt-format graph for the node classes the
v2 pipeline cares about:

  * `LoadImage` / `LoadImageMask` whose `image` input is `@`-prefixed
    become **resource slots** — the daemon reads the document, uploads
    the bytes as an asset, and substitutes a `core/ASSET` reference
    into the input at generate time.

  * `PrimitiveFloat` / `PrimitiveInt` / `PrimitiveString` /
    `PrimitiveBoolean` with a `_meta.title` become **param slots** —
    the daemon substitutes `[params][title]` into the node's `value`
    input at generate time. The extractor also records the node's
    current `value` as `default`, so the panel can pre-populate its
    widgets with the workflow's baked-in value rather than a generic
    kind-based default.

  * `SaveImage` nodes become **output nodes** — the daemon pastes the
    committed bytes of every `SaveImage` entry that carries
    `type: "output"` in the job's response. `PreviewImage`
    (`type: "temp"`) is auto-skipped; recording it here would be
    pointless since the filter is on `type`, not on node id.

Pure functions; no state.
"""

from __future__ import annotations


# Native `LoadImage` / `LoadImageMask` are the only node classes whose
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


def _extract_resource_slots(graph: dict) -> dict:
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


def _extract_param_slots(graph: dict) -> dict:
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

    The `default` field is the node's current `value` input at extraction
    time. It is what the workflow was authored to use — the daemon's
    panel pre-populates its param widgets from it, so a user who does
    not touch a param field still sees the workflow's own value, not a
    generic kind default. `None` when the node has no `value` input
    (malformed workflow; the panel falls back to a kind default).

    Nodes without `_meta.title` are ignored. Two nodes sharing a title is
    an authoring error; raises ValueError naming both.
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


def _extract_output_nodes(graph: dict) -> dict:
    """Walk the graph for terminal output nodes (`SaveImage`). Returns::

        {
          "9": {"class_type": "SaveImage",
                "filename_prefix": "kritomatic"},
        }

    Empty when the workflow has no recognized output node — ComfyUI will
    reject the submission with "Prompt has no outputs", which the proxy
    surfaces as ``422 invalid_workflow``.
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
