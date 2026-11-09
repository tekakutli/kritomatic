"""Stable helpers that read a workflow's interface.

Extract the parameter list, layer requirements, canvas info, seed
wiring, and — for the native v2 pipeline — the resource slots, param
slots, and output nodes from a prompt-format workflow graph. Called
from the container when a workflow is loaded or published, and from the
config sub-handler when applying a panel config.

Pure functions; no state.

Slot extraction vs. old ETN inspection
======================================
The old `_extract_parameters` / `_extract_layer_requirements` /
`_extract_canvas_info` walk for `ETN_*` node classes. Those functions
remain for the transition (Stage 6 deletes them along with the rest of
the tooling-nodes path).

The new `_extract_resource_slots` / `_extract_param_slots` /
`_extract_output_nodes` walk for *native* node classes:

  * `LoadImage` / `LoadImageMask` whose `image` input is `@`-prefixed
    become **resource slots** — the daemon reads the document, uploads
    the bytes as an asset, and substitutes a `core/ASSET` reference
    into the input at generate time.

  * `PrimitiveFloat` / `PrimitiveInt` / `PrimitiveString` /
    `PrimitiveBoolean` with a `_meta.title` become **param slots** —
    the daemon substitutes `[params][title]` into the node's `value`
    input at generate time.

  * `SaveImage` nodes become **output nodes** — the daemon pastes the
    committed bytes of every `SaveImage` entry that carries
    `type: "output"` in the job's response. `PreviewImage`
    (`type: "temp"`) is auto-skipped; recording it here would be
    pointless since the filter is on `type`, not on node id.

Both families coexist while `ETN_*` workflows still load. `expand_custom`
consumes the old extracts; `comfy_apply_config` consumes the new ones.
Stage 6 removes the old.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .comfyui_types import _SEED_PARAMETER_NAME, _CANVAS_SEED_SLOT


# ============================================================================
#  Legacy ETN_* extracts (Stage 6 deletes these)
# ============================================================================

def _extract_parameters(graph: dict) -> list:
    result: list = []
    for node in graph.values():
        if node.get("class_type") != "ETN_Parameter":
            continue
        ins = node.get("inputs", {}) or {}
        name = ins.get("name", "")
        if not name:
            continue
        result.append({
            "name": str(name),
            "type": str(ins.get("type", "auto")),
            "default": ins.get("default", ""),
        })
    result.sort(key=lambda p: p["name"])
    return result


def _extract_layer_requirements(graph: dict) -> dict:
    image_layers: set = set()
    mask_layers: set = set()
    for node in graph.values():
        cls = node.get("class_type", "")
        if cls not in ("ETN_KritaImageLayer", "ETN_KritaMaskLayer"):
            continue
        ins = node.get("inputs", {}) or {}
        name = ins.get("name", "")
        if not name:
            continue
        if cls == "ETN_KritaImageLayer":
            image_layers.add(str(name))
        else:
            mask_layers.add(str(name))
    return {
        "image_layers": sorted(image_layers),
        "mask_layers": sorted(mask_layers),
    }


def _extract_canvas_info(graph: dict) -> dict:
    has_canvas = False
    selection_context: Optional[str] = None
    selection_padding: Optional[int] = None
    for node in graph.values():
        cls = node.get("class_type", "")
        if cls == "ETN_KritaCanvas":
            has_canvas = True
        elif cls == "ETN_KritaSelection" and selection_context is None:
            ins = node.get("inputs", {}) or {}
            selection_context = str(ins.get("context", "entire_image"))
            selection_padding = int(ins.get("padding", 0) or 0)
    return {
        "has_canvas": has_canvas,
        "has_selection": selection_context is not None,
        "selection_context": selection_context or "entire_image",
        "selection_padding": selection_padding or 0,
    }


def _canvas_seed_is_wired(graph: dict) -> bool:
    canvas_ids = {
        nid for nid, n in graph.items()
        if n.get("class_type") == "ETN_KritaCanvas"
    }
    if not canvas_ids:
        return False

    def scan(value: Any) -> bool:
        if (isinstance(value, list) and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
                and not isinstance(value[1], bool)
                and value[0] in canvas_ids
                and value[1] == _CANVAS_SEED_SLOT):
            return True
        if isinstance(value, list):
            return any(scan(x) for x in value)
        return False

    for node in graph.values():
        for v in (node.get("inputs") or {}).values():
            if scan(v):
                return True
    return False


def _compute_signature(graph: dict) -> str:
    params = sorted(
        str((node.get("inputs") or {}).get("name", ""))
        for node in graph.values()
        if node.get("class_type") == "ETN_Parameter"
    )
    outputs = sorted(
        str((node.get("inputs") or {}).get("name", ""))
        for node in graph.values()
        if node.get("class_type") == "ETN_KritaOutput"
    )

    if not params and not outputs:
        identity = "classes:" + "|".join(
            sorted(str(n.get("class_type", "")) for n in graph.values())
        )
    else:
        identity = (
            "params:" + "|".join(params)
            + ";outputs:" + "|".join(outputs)
        )

    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()
    return digest[:12]


# ============================================================================
#  Native-v2 slot extracts (Stage 3)
# ============================================================================

# Native `LoadImage` / `LoadImageMask` are the only node classes whose
# `image` input is scanned for `@`-prefix resource references. A value
# that does not start with `@` is a real ComfyUI input filename and is
# left alone.
_RESOURCE_NODE_FIELDS: dict[str, tuple[str, str]] = {
    # class_type -> (input field name, kind)
    "LoadImage": ("image", "image"),
    "LoadImageMask": ("image", "mask"),
}

# Native `Primitive*` classes with their coerced value kind. The kind is
# checked against the incoming `[params]` value at apply time so a
# float-typed title receiving `true` is rejected before submission
# instead of surfacing as a ComfyUI schema error mid-run.
_PARAM_NODE_KINDS: dict[str, str] = {
    "PrimitiveFloat": "float",
    "PrimitiveInt": "int",
    "PrimitiveString": "string",
    "PrimitiveBoolean": "boolean",
}

# The only native output node the paste filter cares about. `PreviewImage`
# entries are `type: "temp"` in the job's response and are filtered on
# that field, not on class type — so they are not recorded here. If a
# future workflow uses another terminal class (`SaveAnimatedWEBP`,
# `SaveAudio`, ...), extend this set.
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

    Empty when the workflow has no `@`-prefixed inputs (e.g. an old
    tooling-nodes workflow). A single `@`-name appearing on two nodes is
    an authoring error — the daemon cannot know which node the config
    meant. Raises ValueError naming both node ids.
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
          "denoise":    {"node_id": "10", "kind": "float"},
          "prompt":     {"node_id": "11", "kind": "string"},
          "Illustrous?":{"node_id": "12", "kind": "boolean"},
          ...
        }

    Nodes without `_meta.title` are ignored — they are not user-facing
    parameters, so the config cannot address them. Two nodes sharing a
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
        out[title] = {"node_id": node_id, "kind": kind}
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