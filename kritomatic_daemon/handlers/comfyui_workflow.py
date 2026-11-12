"""Workflow loading.

`comfy_load` is the entry point: it reads a workflow JSON from disk,
parses it, and sets it active. `_set_active_workflow` (in the container)
then walks the graph for native-v2 slot tables.

UI-format sources are auto-converted to API format before the graph
is loaded. The conversion algorithm lives in
`kritomatic_shared.comfy_convert`; this module reaches it through
`kritomatic_daemon/utils/comfy_convert.py`, whose `sys.path` bootstrap
makes the shared package importable from Krita's Python. The
converted file is written to `data/convert/<source>.json` and
substituted for the source for the rest of this command's execution.

If a saved panel config exists at
`data/comfy_panel/panel_configs/<slug>.json` (JSON, since the TOML
migration), it is applied via `comfy_apply_config` automatically
after the workflow loads. Structural problems are logged as warnings
in the response; they do not fail the load.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..decorators import command, AutoDispatchHandler
from .comfyui_types import _WORKFLOW_DIR


class WorkflowCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="List workflow files available in the panel's "
                  "workflows directory.",
        args={},
    )
    def comfy_list_workflows(self, params):
        if not _WORKFLOW_DIR.is_dir():
            return {"success": True,
                    "message": "No workflows directory",
                    "data": {"workflows": []}}
        names = sorted(p.stem for p in _WORKFLOW_DIR.glob("*.json"))
        return {
            "success": True,
            "message": f"{len(names)} workflow(s)",
            "data": {"workflows": names},
        }

    @command(
        category="comfyui",
        help_text="Load a workflow by slug from the panel's workflows "
                  "directory, or by absolute filesystem path. UI-format "
                  "sources are auto-converted to API format before "
                  "loading. If a saved panel config exists for the "
                  "slug, it is applied to _active_config automatically.",
        args={
            "--name": {"type": "str", "required": True,
                       "help": "Workflow slug, or an absolute path to "
                               "a .json file."},
            "--exclusive": {"type": "bool", "default": False,
                            "help": "Accepted for backward compatibility; "
                                    "no-op after Stage 6 (the browser "
                                    "watcher is gone)."},
        },
    )
    def comfy_load(self, params):
        parent = self.parent
        name = params.get("name", "")
        if not name:
            return {"success": False, "message": "No workflow name given"}

        is_path = name.startswith("/") or name.startswith("~")

        if is_path:
            path = Path(name).expanduser()
            if not path.is_absolute():
                return {"success": False,
                        "message": f"Path must be absolute: {name!r}"}
            if not path.is_file():
                return {"success": False,
                        "message": f"Workflow file not found: {path}"}
            display_name = path.stem
        else:
            path = _WORKFLOW_DIR / f"{name}.json"
            if not path.is_file():
                available = sorted(
                    p.stem for p in _WORKFLOW_DIR.glob("*.json")
                )
                return {
                    "success": False,
                    "message": f"Workflow {name!r} not found in "
                               f"{_WORKFLOW_DIR}",
                    "data": {"available": available},
                }
            display_name = name

        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return {"success": False,
                    "message": f"Workflow {display_name!r} is not valid "
                               f"JSON: {e}"}

        if not isinstance(workflow, dict) or not workflow:
            return {"success": False,
                    "message": f"Workflow {display_name!r} is empty or "
                               f"not an object"}

        converted = False
        converted_path: Path | None = None
        if "nodes" in workflow or "links" in workflow:
            from ..utils.comfy_convert import (
                convert_if_ui, ConversionError,
            )
            try:
                converted_path, _records, was = convert_if_ui(
                    path, annotate=False,
                )
            except ConversionError as e:
                return {
                    "success": False,
                    "message": (
                        f"Workflow {display_name!r} is in ComfyUI's UI "
                        f"format and auto-conversion failed: {e}\n"
                        f"Convert manually with "
                        f"`kritomatic comfyui convert {path}`."
                    ),
                }
            if was:
                workflow = json.loads(
                    converted_path.read_text(encoding="utf-8")
                )
                converted = True

        parent._set_active_workflow(display_name, workflow, "file")

        result_data: dict = {
            "name": display_name,
            "node_count": len(workflow),
            "source": "file",
        }
        if is_path:
            result_data["path"] = str(path)
        if converted and converted_path is not None:
            result_data["converted"] = True
            result_data["converted_from"] = str(path)
            result_data["converted_path"] = str(converted_path)

        # ---- Autoload saved panel config ----
        autoload_warnings: list[str] = []
        autoloaded = False
        try:
            from ..utils.panel_config import CONFIG_DIR, read_config_file
            cfg_path = CONFIG_DIR / f"{display_name}.json"
            if cfg_path.is_file():
                cfg_data = read_config_file(cfg_path)
                apply_args = {
                    "resources": json.dumps(
                        cfg_data.get("resources", {}) or {}
                    ),
                    "params": json.dumps(
                        cfg_data.get("params", {}) or {}
                    ),
                    "crop": json.dumps(cfg_data.get("crop", {}) or {}),
                    "paste": json.dumps(cfg_data.get("paste", {}) or {}),
                }
                apply_result = parent._config_cmds.comfy_apply_config(
                    apply_args
                )
                if apply_result.get("success"):
                    autoloaded = True
                else:
                    autoload_warnings.append(
                        f"config autoload rejected: "
                        f"{apply_result.get('message', '')}"
                    )
        except ValueError as e:
            autoload_warnings.append(f"config autoload failed: {e}")
        except Exception as e:  # noqa: BLE001 — never fail the load
            autoload_warnings.append(f"config autoload failed: {e}")

        if autoloaded:
            result_data["autoloaded_config"] = True
        if autoload_warnings:
            result_data["autoload_warnings"] = autoload_warnings

        msg = (f"Loaded workflow {display_name!r} "
               f"({len(workflow)} nodes)")
        if converted:
            msg += " \u2014 auto-converted from UI format"
        if autoloaded:
            msg += " \u2014 config autoloaded"
        if autoload_warnings:
            msg += f" ({len(autoload_warnings)} autoload warning(s))"

        return {
            "success": True,
            "message": msg,
            "data": result_data,
        }

    @command(
        category="comfyui",
        help_text="Return the most recently used layer alias targets, "
                  "most recent first. Kept for the docker's alias-"
                  "dropdown refresh even after Stage 6 removed the "
                  "binding commands; returns an empty list unless the "
                  "aliases file has legacy entries.",
        args={},
    )
    def comfy_list_recent_aliases(self, params):
        parent = self.parent
        with parent._recent_aliases_lock:
            snapshot = list(parent._recent_aliases)
        return {
            "success": True,
            "message": f"{len(snapshot)} recent alias(es)",
            "data": {"recent_aliases": snapshot},
        }