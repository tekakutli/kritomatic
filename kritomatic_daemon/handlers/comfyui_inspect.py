"""Stable helpers that read a workflow's interface.

Extract the parameter list, layer requirements, canvas info, and seed
wiring from a prompt-format workflow graph. Called from the container
when a workflow is loaded or published, and from the generation
sub-handler when it needs to know whether the workflow can be batched.

Pure functions; no state.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

from .comfyui_types import _SEED_PARAMETER_NAME, _CANVAS_SEED_SLOT


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