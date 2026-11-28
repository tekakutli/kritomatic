"""
config.py — panel config manipulation (CLI-side wrapper).

The logic is in `kritomatic_shared.comfyui_panel_config`. This module
re-exports the pieces the CLI uses and provides the file-level
`apply_preset` that reads/writes the two JSON files.

It also provides `load_config_for_batch`, the CLI-specific resolver
that maps the `panel run [<wf>] [--config <cfg>]` argument pair into
the `PanelConfig` that `build_batch` consumes. This resolver lives
here, not in the shared module, because it is CLI-shaped: it decides
whether to accept a missing config file (workflow-only invocation,
where the daemon's autoload is the fallback) or to hard-error
(explicit `--config`, where the user asked for a specific file that
is not there).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

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
    "load_config_for_batch",
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


# ============================================================================
#  Batch-time (workflow, --config) resolution
# ============================================================================

def load_config_for_batch(
    workflow: Optional[str] = None,
    config_name: Optional[str] = None,
) -> PanelConfig:
    """Resolve the CLI's (positional workflow, --config) pair into a
    PanelConfig, ready to hand to `build_batch`.

    Combinations:

      · workflow=None, config_name=None
          → PanelConfig defaults. `build_batch` sends neither
            `comfy_load` nor `comfy_apply_config`, so the daemon
            runs against whatever workflow and config it currently
            has active.

      · workflow="X", config_name=None
          → load X on the daemon. `panel_configs/X.json` is read
            here only for the CLI-side runtime fields (timeout,
            count, seed) — the daemon's autoload is what actually
            pushes the sections to `_active_config`. A missing
            `panel_configs/X.json` is tolerated; the daemon will
            simply report "No panel config applied" from
            `comfy_generate`.

      · workflow=None, config_name="Y"
          → use the daemon's active workflow. `panel_configs/Y.json`
            is applied explicitly via `comfy_apply_config`. Missing
            `panel_configs/Y.json` is a hard error — the user
            named it on the command line.

      · workflow="X", config_name="Y"
          → load X, then apply `panel_configs/Y.json` explicitly.
            Same missing-file rule for Y.

    Raises ValueError when `--config` names a file that does not
    exist, or when any file that *does* exist fails to parse.
    """
    source_name = config_name or workflow
    data: dict = {}
    if source_name:
        path = CONFIG_DIR / f"{source_name}.json"
        if path.is_file():
            data = read_config_file(path)  # raises ValueError on bad JSON
        elif config_name:
            # Explicit --config with a missing file: hard error. A
            # silent fall-through to empty defaults would leave the
            # daemon in a state the user did not ask for.
            raise ValueError(f"config file not found: {path}")
        # else: workflow-only invocation. Missing file is tolerated —
        # comfy_load's daemon-side autoload will simply not fire, and
        # comfy_generate will report "No panel config applied" with
        # the daemon's own error message.

    # Positional workflow wins over the config file's own `workflow`
    # field, because that's what the user typed on the command line.
    # When both are absent, `parse_config_dict` defaults to "".
    data["workflow"] = workflow or ""

    cfg = parse_config_dict(data)
    cfg.config_name = config_name if config_name else None
    return cfg


# ============================================================================
#  Preset application
# ============================================================================

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