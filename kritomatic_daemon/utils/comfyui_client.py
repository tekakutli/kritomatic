"""Daemon-side bridge to the shared ComfyUI client.

The logic lives in `kritomatic_shared.comfyui_client`, under
`<repo>/src/`. This module re-exports the pre-migration public
surface so every daemon import site keeps working unchanged.

See `kritomatic_daemon/utils/comfy_convert.py` for the sys.path
bootstrap pattern this file follows.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/utils/comfyui_client.py
#   .parent                → .../utils
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_client import (  # noqa: E402,F401
    CANCELLED,
    ComfyCancelled,
    ComfyClient,
    ComfyError,
    ComfyTimeout,
    GenerationResult,
    JobInfo,
    TextOutput,
    decode_preview_frame,
    get_client,
    reset_client,
)

__all__ = [
    "CANCELLED",
    "ComfyCancelled",
    "ComfyClient",
    "ComfyError",
    "ComfyTimeout",
    "GenerationResult",
    "JobInfo",
    "TextOutput",
    "decode_preview_frame",
    "get_client",
    "reset_client",
]
