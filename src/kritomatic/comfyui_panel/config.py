"""
config.py — panel config manipulation (CLI-side wrapper).

The logic is in `kritomatic_shared.comfyui_panel_config`. This module
re-exports the pieces the CLI uses and provides the file-level
`apply_preset` that reads/writes the two JSON files.
"""

from __future__ import annotations

import sys
from pathlib import Path

from kritomatic_shared.comfyui_panel_config import (
    PanelConfig,
    PasteSpec,
    ResourceSpec,
    apply_preset_dict,
    build_batch,
    build_file_template,
    extract_native_workflow,
    list_presets,
    parse_config_dict,
    parse_config_file,
    read_config_file,
    render_template_string,
    resolve_preset_path,
    serialize_config,
    write_config_file,
)
from .paths import CONFIG_DIR, PRESET_DIR, WORKFLOW_DIR


__all__ = [
    "PanelConfig",
    "PasteSpec",
    "ResourceSpec",
    "apply_preset",
    "build_batch",
    "build_file_template",
    "existing_configs",
    "extract_native_workflow",
    "list_presets",
    "load_config",
    "parse_config_dict",
    "parse_config_file",
    "parse_panel_config",
    "read_config_file",
    "resolve_preset_path",
    "serialize_config",
    "write_config_file",
]


# Backwards-compatible names
load_config = read_config_file
parse_panel_config = parse_config_file


def existing_configs() -> list[Path]:
    if not CONFIG_DIR.is_dir():
        return []
    return sorted(CONFIG_DIR.glob("*.json"))


def apply_preset(preset_path: Path, config_path: Path,
                 dry_run: bool = False) -> int:
    """Splice a preset's values into a config file.

    JSON-preserving by construction (no round-trip loss). `_notes`
    from the config are preserved because `apply_preset_dict` only
    touches the sections it knows about, and `_notes` is not one.
    """
    try:
        preset = read_config_file(preset_path)
    except ValueError as e:
        print(f"Preset error: {e}", file=sys.stderr)
        return 1

    try:
        config = read_config_file(config_path)
    except ValueError as e:
        print(f"Config error: {e}", file=sys.stderr)
        return 1

    applied, skipped, deleted = apply_preset_dict(preset, config)

    print(f"Preset:   {preset_path}")
    print(f"Target:   {config_path}")
    print()

    if applied:
        print(f"Applied ({len(applied)}):")
        for k in applied:
            print(f"  {k}")
    if deleted:
        print(f"Deleted ({len(deleted)}):")
        for k in deleted:
            print(f"  {k}")
    if skipped:
        print(f"Skipped ({len(skipped)}) — not present in target:")
        for k in skipped:
            print(f"  {k}")

    if not (applied or deleted):
        print()
        print("This preset matches nothing in the target config.")
        print("Check that it is meant for this workflow.")

    if dry_run:
        print()
        print("(dry run; file not modified)")
        return 0

    write_config_file(config_path, config)
    print()
    print(f"Wrote {config_path}")
    return 0