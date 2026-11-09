"""Workflow loading.

`comfy_load` is the entry point: it reads a workflow JSON from disk,
parses it, and sets it active. `_set_active_workflow` (in the container)
then walks the graph for native-v2 slot tables.

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
                  "directory, or by absolute filesystem path.",
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

        if "nodes" in workflow or "links" in workflow:
            return {
                "success": False,
                "message": (
                    f"Workflow {display_name!r} appears to be in "
                    f"ComfyUI's UI format. Export it via "
                    f"'Save (API Format)' in the ComfyUI menu, or run "
                    f"`kritomatic comfyui convert` on the file, which "
                    f"produces the prompt format this handler requires."
                ),
            }

        parent._set_active_workflow(display_name, workflow, "file")

        result_data: dict = {
            "name": display_name,
            "node_count": len(workflow),
            "source": "file",
        }
        if is_path:
            result_data["path"] = str(path)

        return {
            "success": True,
            "message": f"Loaded workflow {display_name!r} "
                       f"({len(workflow)} nodes)",
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