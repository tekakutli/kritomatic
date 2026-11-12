"""Daemon-side bridge to the shared graph-substitution function.

The logic lives in `kritomatic_shared.comfyui_substitute`, under
`<repo>/src/`. This module re-exports `substitute_into_graph` so
`comfy_apply.py` (and any future daemon-side caller) can import it
under a stable path without repeating the sys.path bootstrap.

See `kritomatic_daemon/utils/comfy_convert.py` for the bootstrap
pattern this file follows.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/utils/comfyui_substitute.py
#   .parent                → .../utils
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_substitute import (  # noqa: E402,F401
    substitute_into_graph,
)

__all__ = ["substitute_into_graph"]
