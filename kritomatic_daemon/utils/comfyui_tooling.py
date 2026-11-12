"""Daemon-side bridge to the shared ComfyUI tooling (ETN_* rewrite).

The logic lives in `kritomatic_shared.comfyui_tooling`, under
`<repo>/src/`. This module re-exports the pre-migration public
surface so every daemon import site keeps working unchanged.

See `kritomatic_daemon/utils/comfy_convert.py` for the sys.path
bootstrap pattern this file follows.

Note on the removed fallbacks: the pre-migration daemon copy of this
module had `layer_images["thecanvas"]` / `layer_images["theselection"]`
fallbacks inside `_expand_canvas` / `_expand_selection`, added when the
inpaint playground was pushing named layers via `comfy_set_layer_pixels`
while the workflow was still using `ETN_Krita*` nodes. That path is
gone — inpaint moved to native v2 in Stage 8, and `segment` sets
`resources.canvas_image` directly. The fallbacks are unreachable under
any current call site, so the shared copy omits them and raises the
clear `ExpansionError` when a resource is missing instead of silently
falling through to a named layer. If a future caller needs the
fallback behaviour, add it in `kritomatic_shared/comfyui_tooling.py`.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/utils/comfyui_tooling.py
#   .parent                → .../utils
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_tooling import (  # noqa: E402,F401
    EncodedImage,
    KritaResources,
    WorkflowRequirements,
    ExpansionResult,
    ExpansionError,
    expand_custom,
    scan_requirements,
)

__all__ = [
    "EncodedImage",
    "KritaResources",
    "WorkflowRequirements",
    "ExpansionResult",
    "ExpansionError",
    "expand_custom",
    "scan_requirements",
]
