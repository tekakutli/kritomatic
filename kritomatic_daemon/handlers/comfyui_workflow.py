"""Workflow loading.

`comfy_load` is the entry point: it reads a workflow JSON from disk,
parses it, and sets it active. `_set_active_workflow` (in the container)
then walks the graph for native-v2 slot tables.

UI-format sources are auto-converted to API format before the graph
is loaded. The conversion algorithm lives in
`kritomatic_shared.comfy_convert`; this module reaches it through
`kritomatic_daemon/utils/comfy_convert.py`, whose `sys.path` bootstrap
makes the shared package importable from Krita's Python. Result: a
source at `data/comfy_panel/workflows/foo.json` in UI format loads
exactly the same way as `data/convert/foo.json` in API format, with
the converted file landing at `data/convert/foo.json` in both cases.

`comfy_set_param` and `comfy_bind_layer_alias` were removed in Stage 6
along with the ETN_Parameter / layer-alias machinery they populated.
The native pipeline takes values through `comfy_apply_config` instead.
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
                  "loading.",
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

        # ---- Auto-convert UI format to API format ----
        #
        # The conversion algorithm lives in `kritomatic_shared.comfy_
        # convert`. The daemon reaches it through the bootstrap in
        # `kritomatic_daemon/utils/comfy_convert.py`, which prepends
        # `<repo>/src/` to sys.path. Import is lazy — inside this
        # branch — so a broken `kritomatic_shared` install cannot break
        # API-format loads. The cost of the import (already in
        # sys.modules after the first UI-format load) is effectively
        # zero.
        converted = False
        converted_path: Path | None = None
        if "nodes" in workflow or "links" in workflow:
            from ..utils.comfy_convert import (
                convert_if_ui, ConversionError,
            )
            try:
                converted_path, _records, was = convert_if_ui(path, annotate=False)
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
                # Re-read from the converted file. The output is API
                # format, so the second json.loads sees a well-formed
                # graph. If somehow `was` is False (shouldn't happen
                # in this branch — we already detected UI format), we
                # keep the original `workflow` in memory.
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

        msg = (f"Loaded workflow {display_name!r} "
               f"({len(workflow)} nodes)")
        if converted:
            msg += " \u2014 auto-converted from UI format"

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
