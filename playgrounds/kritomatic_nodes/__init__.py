# kritomatic_nodes/__init__.py
"""
Kritomatic Nodes — all nodes registered through the V3 ComfyExtension API.

There is no NODE_CLASS_MAPPINGS in this module. The loader sees
comfy_entrypoint, calls it, and gets every node from the returned
extension's get_node_list(). That is the only registration route used.

Nodes exposed:
  * DrawatoonCheckpointLoader   (MODEL)
  * DrawatoonSetIP              (CONDITIONING)
  * PlaygroundResolutionSelect  (INT, INT)
  * MultiAreaConditioning       (CONDITIONING)
  * MagiLoader                  (MAGI_CROP_EMBEDDER)

HTTP routes registered on import:
  * POST /magi/crop_embed       (magi playground → ViT-MAE → 768-d)

Layout:
  layout_client.py          shared /state transport (URLs, fetch, probe)
  drawatoon_nodes.py        DrawatoonCheckpointLoader + DrawatoonSetIP
  playground_resolution.py  PlaygroundResolutionSelect
  multimask/                MultiAreaConditioning
  PixArt/                   drawatoon transformer port (unchanged)
  Magi/                     ViT-MAE crop embedder (feed for DrawatoonSetIP)
"""

from __future__ import annotations

from comfy_api.latest import ComfyExtension

from .drawatoon_nodes import DrawatoonCheckpointLoader, DrawatoonSetIP
from .playground_resolution import PlaygroundResolutionSelect
from .multimask import MultiAreaConditioning
from .Magi.nodes import MagiLoader

# Importing `Magi.nodes` registers `POST /magi/crop_embed` on the
# ComfyUI PromptServer as a side effect.  No further action needed.


class KritomaticExtension(ComfyExtension):
    async def get_node_list(self):
        return [
            DrawatoonCheckpointLoader,
            DrawatoonSetIP,
            PlaygroundResolutionSelect,
            MultiAreaConditioning,
            MagiLoader,
        ]


async def comfy_entrypoint():
    return KritomaticExtension()