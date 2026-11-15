# kritomatic_nodes/__init__.py
"""
Kritomatic Nodes — pack entrypoint.

Layout:

    kritomatic_nodes/
    ├── PixArt/         drawatoon port (ExtraModels-derived)
    │   └── nodes.py    DrawatoonCheckpointLoader
    ├── multimask/      MultiAreaConditioning (V3 ComfyNode)
    │   └── __init__.py
    ├── __init__.py     this file
    └── install.py

Two registries are exported from here:

  * V1 — `NODE_CLASS_MAPPINGS` exposes `DrawatoonCheckpointLoader` only,
    deliberately avoiding name collisions with `ComfyUI_ExtraModels`
    (which registers the stock `PixArtCheckpointLoader`,
    `PixArtResolutionSelect`, `PixArtLoraLoader`, ...).

  * V3 — `comfy_entrypoint` returns the `KritomaticExtension` defined in
    `multimask/`, which registers `MultiAreaConditioning`.

Each submodule is imported inside its own try/except so a failure in one
(for example a missing `diffusers` import, or a broken ComfyUI V3 API
surface) does not prevent the other from registering.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# V3 extension — MultiAreaConditioning
# ---------------------------------------------------------------------------
try:
    from .multimask import KritomaticExtension

    async def comfy_entrypoint():
        return KritomaticExtension()

except Exception as _e:
    print(f"[kritomatic] failed to import multimask extension: {_e!r}")
    KritomaticExtension = None
    # `comfy_entrypoint` is intentionally left undefined so ComfyUI's V3
    # loader falls through to the V1 registry below without erroring.


# ---------------------------------------------------------------------------
# V1 registrations — DrawatoonCheckpointLoader only
# ---------------------------------------------------------------------------
try:
    from .PixArt.nodes import NODE_CLASS_MAPPINGS as _drawatoon_mappings
    NODE_CLASS_MAPPINGS = dict(_drawatoon_mappings)
    NODE_DISPLAY_NAME_MAPPINGS = {
        k: getattr(v, "TITLE", k) for k, v in NODE_CLASS_MAPPINGS.items()
    }
except Exception as _e:
    print(f"[kritomatic] failed to register drawatoon nodes: {_e!r}")
    NODE_CLASS_MAPPINGS = {}
    NODE_DISPLAY_NAME_MAPPINGS = {}
