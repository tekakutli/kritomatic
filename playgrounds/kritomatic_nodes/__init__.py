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
  * ControlNetsConditioning     (CONDITIONING) — playground-driven
                                replacement for the "ControlNets"
                                subgraph
  * MagiLoader                  (MAGI_CROP_EMBEDDER)

HTTP routes registered on import:
  * POST /magi/crop_embed           (magi playground → ViT-MAE → 768-d)
  * POST /kritomatic/save_workflow  (frontend "Save To Kritomatic"
                                     → <repo>/data/comfy_panel/workflows)

Frontend extension (served from ./web):
  * save_to_kritomatic.js       (adds "Save To Kritomatic" to File menu)

Layout:
  layout_client.py          shared /state transport (URLs, fetch, probe)
  drawatoon_nodes.py        DrawatoonCheckpointLoader + DrawatoonSetIP
  playground_resolution.py  PlaygroundResolutionSelect
  multimask/                MultiAreaConditioning
  controlnets/              ControlNetsConditioning
  PixArt/                   drawatoon transformer port (unchanged)
  Magi/                     ViT-MAE crop embedder (feed for DrawatoonSetIP)
  kritomatic_workflows.py   HTTP route for saving UI workflows into repo
  web/                      frontend extensions (JavaScript only)
"""

from __future__ import annotations

import sys
from pathlib import Path

# `kritomatic_nodes` is symlinked into ComfyUI's custom_nodes/
# (see install.py).  `resolve()` follows the symlink back to
# <repo>/playgrounds/kritomatic_nodes/, so `.parent.parent` is
# <repo>/playgrounds — the home of `pgruntime`.
_PLAYGROUNDS = Path(__file__).resolve().parent.parent
if str(_PLAYGROUNDS) not in sys.path:
    sys.path.insert(0, str(_PLAYGROUNDS))

try:
    import pgruntime  # noqa: F401
except ImportError as e:
    raise ImportError(
        f"kritomatic_nodes requires the 'pgruntime' package, expected "
        f"at {_PLAYGROUNDS}/pgruntime.  If this pack was installed as "
        f"a copy (not a symlink), re-run install.py so the symlink "
        f"points back to the repo."
    ) from e

from comfy_api.latest import ComfyExtension

from .drawatoon_nodes import DrawatoonCheckpointLoader, DrawatoonSetIP
from .playground_resolution import PlaygroundResolutionSelect
from .multimask import MultiAreaConditioning
from .controlnets import ControlNetsConditioning
from .Magi.nodes import MagiLoader

# Importing `kritomatic_workflows` registers
# `POST /kritomatic/save_workflow` on the ComfyUI PromptServer as a
# side effect (same pattern as `Magi.nodes`). No further action needed.
from . import kritomatic_workflows  # noqa: F401

# Tell ComfyUI to serve everything under ./web as client-side JS.
WEB_DIRECTORY = "./web"

__all__ = ["WEB_DIRECTORY", "comfy_entrypoint"]


class KritomaticExtension(ComfyExtension):
    async def get_node_list(self):
        return [
            DrawatoonCheckpointLoader,
            DrawatoonSetIP,
            PlaygroundResolutionSelect,
            MultiAreaConditioning,
            ControlNetsConditioning,
            MagiLoader,
        ]


async def comfy_entrypoint():
    return KritomaticExtension()
