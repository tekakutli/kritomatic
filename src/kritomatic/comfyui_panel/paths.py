"""
paths.py — directory constants for the panel.

The panel's data lives under <repo>/data/comfy_panel/:

    workflows/        JSON workflow definitions; only present if a
                      workflow has been injected from disk
    panel_configs/    the .toml files the panel edits
    presets/          preset .toml files
    history/          generation records (written by the daemon)

Constants are computed from the location of this package's source file,
so they resolve the same regardless of the working directory the CLI is
invoked from.
"""

from pathlib import Path

# <repo>/src/kritomatic/comfyui_panel/paths.py
#   .parent            -> <repo>/src/kritomatic/comfyui_panel
#   .parent.parent     -> <repo>/src/kritomatic
#   .parent.parent.parent        -> <repo>/src
#   .parent.parent.parent.parent -> <repo>
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

PANEL_DIR = _REPO_ROOT / "data" / "comfy_panel"
WORKFLOW_DIR = PANEL_DIR / "workflows"
CONFIG_DIR = PANEL_DIR / "panel_configs"
PRESET_DIR = PANEL_DIR / "presets"
