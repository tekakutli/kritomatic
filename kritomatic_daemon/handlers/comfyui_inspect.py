"""Daemon-side bridge to the shared ComfyUI slot extractors.

The logic lives in `kritomatic_shared.comfyui_inspect`, under
`<repo>/src/`. This module re-exports the public names so the daemon's
own imports (`handlers/comfyui.py`) keep working, and so any external
code that imported the previous private aliases
(`_extract_resource_slots` etc.) continues to function.

See `kritomatic_daemon/utils/comfy_convert.py` for the sys.path
bootstrap pattern this file follows.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/handlers/comfyui_inspect.py
#   .parent                → .../handlers
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_inspect import (  # noqa: E402,F401
    extract_all_slots,
    extract_output_nodes,
    extract_param_slots,
    extract_resource_slots,
)

# Backward-compat aliases for any caller that imported the old private
# names. `handlers/comfyui.py` has been updated to the public names;
# these aliases exist for anything else.
_extract_resource_slots = extract_resource_slots
_extract_param_slots = extract_param_slots
_extract_output_nodes = extract_output_nodes

__all__ = [
    "extract_all_slots",
    "extract_output_nodes",
    "extract_param_slots",
    "extract_resource_slots",
    "_extract_output_nodes",
    "_extract_param_slots",
    "_extract_resource_slots",
]
