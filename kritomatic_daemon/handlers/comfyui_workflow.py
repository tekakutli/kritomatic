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

Slot extraction failures
========================
If the graph is loaded successfully but slot extraction fails
(duplicate @-name or _meta.title), `comfy_load` surfaces the
extractor's message in the response and skips the config autoload —
the saved config would fail validation against empty slot tables,
producing a redundant "config autoload rejected" warning on top of
the real error.

Recent layer aliases
====================
`comfy_list_recent_aliases` reads the MRU list the daemon keeps in
`_RECENT_ALIASES_PATH`; the docker's `_LayerComboBox` fields put
those names at the front of their dropdowns, per kind bucket. The
list is populated by `_record_recent_alias`, which is invoked:

  * internally, from `comfyui_generation._comfy_generate_impl` after
    a batch in which at least one `prepare_workflow` succeeded —
    every `source == "layer"` resource in `_active_config` is
    recorded (see Option B in the design notes);
  * externally, through `comfy_record_recent_alias`, for CLI use and
    manual testing.

Both paths land in `WorkflowCommands._record_recent_alias`, which
dedupes (a re-recorded name moves to the front), caps at
`_RECENT_ALIASES_CAP`, and best-effort persists to disk. The
in-memory list on `ComfyUIHandler` (`_recent_aliases`, guarded by
`_recent_aliases_lock`) is authoritative for the session; the disk
file is loaded once at handler construction and rewritten by
`_record_recent_alias`. The daemon does not watch the file for
external edits.
"""

from __future__ import annotations

import json
from pathlib import Path

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

        # ---- Slot extraction failure ----
        #
        # The graph loaded (ComfyUI will accept it), but the native-v2
        # slot tables are empty because two nodes share an @-name or
        # a _meta.title.  Surface that here and skip the autoload
        # below — comfy_apply_config would reject every saved entry
        # against empty slot tables, producing a redundant error on
        # top of the real one.
        if parent._active_workflow_slot_error:
            result_data["slot_error"] = parent._active_workflow_slot_error
            msg = (
                f"Loaded workflow {display_name!r} "
                f"({len(workflow)} nodes) \u2014 "
                f"slot extraction failed: "
                f"{parent._active_workflow_slot_error}"
            )
            return {
                "success": True,
                "message": msg,
                "data": result_data,
            }

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
                  "most recent first. Used by the docker's alias "
                  "dropdowns to order their items.",
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

    @command(
        category="comfyui",
        help_text="Record a layer name as recently used. Moves it to "
                  "the front of the MRU list (deduping any prior entry) "
                  "and persists. The docker's layer-alias dropdowns "
                  "read this list to order their items. "
                  "comfy_generate invokes the same code path after a "
                  "successful batch; this command exists for CLI use "
                  "and manual testing.",
        args={
            "--actual_name": {"type": "str", "required": True,
                              "help": "The layer name to record"},
        },
    )
    def comfy_record_recent_alias(self, params):
        actual = params.get("actual_name", "")
        if not actual:
            return {"success": False,
                    "message": "--actual_name is required"}
        self._record_recent_alias(actual)
        with self.parent._recent_aliases_lock:
            snapshot = list(self.parent._recent_aliases)
        return {
            "success": True,
            "message": f"recorded {actual!r}",
            "data": {"recorded": actual,
                     "recent_aliases": snapshot},
        }

    def _record_recent_alias(self, actual: str) -> None:
        """Prepend `actual` to the recent-alias list, dedupe, cap, save.

        Under `_recent_aliases_lock` so a concurrent
        `comfy_list_recent_aliases` read sees a consistent snapshot.
        The disk write is best-effort and swallowed inside
        `_save_recent_aliases`; the in-memory list remains
        authoritative for this session even if the write fails.

        Called from `comfy_record_recent_alias` (external CLI path) and
        from `comfyui_generation._comfy_generate_impl` (internal,
        post-prepare path). Both routes land here.
        """
        if not actual:
            return
        parent = self.parent
        with parent._recent_aliases_lock:
            updated = [actual] + [
                a for a in parent._recent_aliases if a != actual
            ]
            del updated[_RECENT_ALIASES_CAP:]
            parent._recent_aliases = updated
            _save_recent_aliases(updated)