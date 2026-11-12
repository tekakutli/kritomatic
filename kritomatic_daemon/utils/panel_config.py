"""Daemon-side bridge to the shared panel config module.

The logic lives in `kritomatic_shared.comfyui_panel_config`, under
`<repo>/src/`. The daemon reaches that path by prepending
`<repo>/src/` to `sys.path`. Same pattern as
`kritomatic_daemon/utils/comfy_convert.py`.

Callers use:

    from ..utils.panel_config import (
        CONFIG_DIR, read_config_file, write_config_file, parse_config_file,
    )

Note on hot-reload: `kritomatic_shared.*` is not in `reload_daemon`'s
drop list, so editing `comfyui_panel_config.py` requires a full Krita
restart. The sys.path bootstrap here re-runs on this module's fresh
import if you do add it to the drop list later.
"""

import sys
from pathlib import Path

# <repo>/kritomatic_daemon/utils/panel_config.py
#   .parent                → .../utils
#   .parent.parent         → .../kritomatic_daemon
#   .parent.parent.parent  → <repo>
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_panel_config import (  # noqa: E402
    CONFIG_DIR,
    PANEL_DIR,
    PRESET_DIR,
    WORKFLOW_DIR,
    PanelConfig,
    PasteSpec,
    ResourceSpec,
    build_batch,
    extract_native_workflow,
    parse_config_dict,
    parse_config_file,
    read_config_file,
    serialize_config,
    write_config_file,
)

__all__ = [
    "CONFIG_DIR",
    "PANEL_DIR",
    "PRESET_DIR",
    "WORKFLOW_DIR",
    "PanelConfig",
    "PasteSpec",
    "ResourceSpec",
    "build_batch",
    "extract_native_workflow",
    "parse_config_dict",
    "parse_config_file",
    "read_config_file",
    "serialize_config",
    "write_config_file",
]