"""Workflow loading, parameter values, and layer aliases.

This is where the user pushes new workflows into the daemon, tweaks
their parameters, and maps declared layer names to actual document
names. Actively worked on — new commands land here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..decorators import command, AutoDispatchHandler
from .comfyui_types import (
    _RECENT_ALIASES_CAP,
    _WORKFLOW_DIR,
    _save_recent_aliases,
)


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
                            "help": "If true, treat this file as the "
                                    "exclusive source until the lock "
                                    "is cleared."},
        },
    )
    def comfy_load(self, params):
        parent = self.parent
        name = params.get("name", "")
        if not name:
            return {"success": False, "message": "No workflow name given"}

        exclusive = bool(params.get("exclusive", False))
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

        parent._file_exclusive = exclusive
        parent._set_active_workflow(display_name, workflow, "file")
        parent._params = {}
        parent._layer_aliases = {}

        result_data: dict = {
            "name": display_name,
            "node_count": len(workflow),
            "source": "file",
            "signature": parent._active_workflow_signature,
            "exclusive": exclusive,
        }
        if is_path:
            result_data["path"] = str(path)

        return {
            "success": True,
            "message": f"Loaded workflow {display_name!r} "
                       f"({len(workflow)} nodes)"
                       + (", exclusive" if exclusive else ""),
            "data": result_data,
        }

    @command(
        category="comfyui",
        help_text="Set a value for an ETN_Parameter in the active "
                  "workflow.",
        args={
            "--name": {"type": "str", "required": True,
                       "help": "Parameter name (the node's `name` widget)"},
            "--value": {"type": "str", "required": True,
                        "help": "Value, as a string. Coerced later."},
        },
    )
    def comfy_set_param(self, params):
        parent = self.parent
        if parent._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first, "
                               "or publish a graph from ComfyUI's browser"}

        name = params.get("name", "")
        raw = params.get("value", "")
        if not name:
            return {"success": False, "message": "No parameter name given"}

        if isinstance(raw, str):
            low = raw.lower()
            if low == "true":
                value: Any = True
            elif low == "false":
                value = False
            else:
                value = raw
        else:
            value = raw

        parent._params[name] = value
        return {
            "success": True,
            "message": f"{name} = {value!r}",
            "data": {"name": name, "value": value,
                     "params": dict(parent._params)},
        }

    @command(
        category="comfyui",
        help_text="Map a layer name the workflow declares to a different "
                  "layer in the document.",
        args={
            "--logical_name": {"type": "str", "required": True,
                               "help": "The name the workflow declares"},
            "--actual_name": {"type": "str", "required": True,
                              "help": "The name to look up in the "
                                      "document instead"},
        },
    )
    def comfy_bind_layer_alias(self, params):
        parent = self.parent
        if parent._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first, "
                               "or publish a graph from ComfyUI's browser"}

        logical = params.get("logical_name", "")
        actual = params.get("actual_name", "")
        if not logical or not actual:
            return {"success": False,
                    "message": "Both --logical_name and --actual_name are "
                               "required"}

        parent._layer_aliases[logical] = actual
        self._record_recent_alias(actual)
        return {
            "success": True,
            "message": f"{logical} -> {actual}",
            "data": {"logical_name": logical, "actual_name": actual,
                     "layer_aliases": dict(parent._layer_aliases)},
        }

    @command(
        category="comfyui",
        help_text="Return the most recently used layer alias targets, "
                  "most recent first.",
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

    def _record_recent_alias(self, actual: str) -> None:
        parent = self.parent
        if not actual:
            return
        with parent._recent_aliases_lock:
            updated = [actual] + [
                a for a in parent._recent_aliases if a != actual
            ]
            del updated[_RECENT_ALIASES_CAP:]
            parent._recent_aliases = updated
            _save_recent_aliases(updated)