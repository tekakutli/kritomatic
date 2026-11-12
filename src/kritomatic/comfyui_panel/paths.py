"""
paths.py — directory constants for the panel.

Thin re-export of `kritomatic_shared.comfyui_panel_config`, which
owns the actual path definitions. Kept so existing imports like
`from .paths import CONFIG_DIR` continue to work.
"""

from kritomatic_shared.comfyui_panel_config import (
    CONFIG_DIR,
    PANEL_DIR,
    PRESET_DIR,
    WORKFLOW_DIR,
)

__all__ = ["PANEL_DIR", "WORKFLOW_DIR", "CONFIG_DIR", "PRESET_DIR"]