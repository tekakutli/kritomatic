# kritomatic_daemon/handlers/comfyui_workflow.py
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

`comfy_reload_if_active` is the other entry point that mutates
workflow state. It exists to be called *by the ComfyUI side* after a
workflow file was just written by `POST /kritomatic/save_workflow`
(see `playgrounds/kritomatic_nodes/kritomatic_workflows.py`).  It
re-reads the file only when its slug matches `_active_workflow_name`,
and — unlike `comfy_load` — it *preserves* `_active_config` when the
slot interface (resource `@`-names, param titles, kinds) is unchanged,
so a "Save to Kritomatic" button click does not throw away the
docker's in-memory widget edits.  If the interface changed, the config
is cleared, which is the only safe behaviour — the docker rebuilds
its panel from the new slot tables and the user's next Run starts from
workflow defaults for anything the new interface renames.  See the
docstring on `comfy_reload_if_active` for the full contract.

Slot extraction failures
========================
If the graph is loaded successfully but slot extraction fails
(duplicate @-name or _meta.title), `comfy_load` surfaces the
extractor's message in the response and skips the config autoload —
the saved config would fail validation against empty slot tables,
producing a redundant "config autoload rejected" warning on top of
the real error.

ComfyUI unreachable during auto-conversion
==========================================
comfy-cli (which does the UI→API conversion) needs ComfyUI running
to fetch `/object_info` for widget-schema mapping.  When it cannot
reach ComfyUI, it exits non-zero with a structured error envelope
that, if passed through verbatim, produces a very noisy
"auto-conversion failed: comfy-cli exited with code 1.
Command: ... Output: {envelope JSON} Convert manually with ..."
message.

`comfy_convert.convert_if_ui` detects that specific case and raises
a `ConversionError` tagged `comfyui_unreachable=True` with a
short message ("ComfyUI is not running at <host:port>").  This
handler checks the flag and returns that short message directly,
skipping the workflow-name wrapper and the "Convert manually" hint —
neither is useful when the fix is simply "start ComfyUI and retry".
All other ConversionErrors keep the pre-existing verbose wrapping,
because the subprocess output is genuinely diagnostic there.

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


# ---------------------------------------------------------------------------
#  Shared file reader (used by comfy_load and comfy_reload_if_active)
# ---------------------------------------------------------------------------

def _read_workflow_file(path: Path, display_name: str) -> tuple:
    """Read a workflow JSON file, auto-converting UI→API if needed.

    Returns `(workflow, err, converted, converted_path)`:

      * `workflow` — the parsed graph dict, or `None` on failure.
      * `err` — `None` on success, or a fully-formed
        `{"success": False, "message": ...}` response the caller
        should return verbatim.
      * `converted` — True iff the source was UI-format and
        `convert_if_ui` produced a converted file.
      * `converted_path` — the Path of the converted file, or None.

    Errors are the same shape and wording as `comfy_load` has always
    returned; pulling them into one helper keeps `comfy_load` and
    `comfy_reload_if_active` in lockstep on the conversion path.
    """
    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, {
            "success": False,
            "message": f"Workflow {display_name!r} could not be read: {e}",
        }, False, None

    if not isinstance(workflow, dict) or not workflow:
        return None, {
            "success": False,
            "message": f"Workflow {display_name!r} is empty or not an "
                       f"object",
        }, False, None

    if "nodes" not in workflow and "links" not in workflow:
        return workflow, None, False, None

    # UI format — convert.
    from ..utils.comfy_convert import (
        convert_if_ui, ConversionError,
    )
    try:
        converted_path, _records, was = convert_if_ui(path, annotate=False)
    except ConversionError as e:
        # The "ComfyUI is not running" case: `convert.py` has already
        # reduced this to a short, actionable one-liner and tagged the
        # exception so we can return that message directly.  The
        # workflow-name wrapper and the "convert manually" hint that
        # the general branch adds below are both noise here — the fix
        # is "start ComfyUI and retry", and the "convert manually"
        # hint would just fail again.  See the module docstring,
        # "ComfyUI unreachable during auto-conversion".
        if getattr(e, "comfyui_unreachable", False):
            return None, {"success": False, "message": str(e)}, False, None
        # Every other conversion failure keeps the verbose wrapper:
        # the subprocess output that `convert.py` attached is what a
        # maintainer needs to diagnose a bad workflow, a comfy-cli
        # regression, or a corrupted install, and the "convert
        # manually" hint is a real workaround for those cases.
        return None, {
            "success": False,
            "message": (
                f"Workflow {display_name!r} is in ComfyUI's UI "
                f"format and auto-conversion failed: {e}\n"
                f"Convert manually with "
                f"`kritomatic comfyui convert {path}`."
            ),
        }, False, None

    if not was:
        return workflow, None, False, None

    try:
        converted_workflow = json.loads(
            converted_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as e:
        return None, {
            "success": False,
            "message": (
                f"Workflow {display_name!r} was converted to "
                f"{converted_path} but could not be re-read: {e}"
            ),
        }, False, None

    return converted_workflow, None, True, converted_path


# ---------------------------------------------------------------------------
#  Slot-interface comparison (used by comfy_reload_if_active)
# ---------------------------------------------------------------------------

def _slot_interface_equal(old: dict, new: dict) -> bool:
    """Two slot tables have equal interfaces iff they have the same key
    set and, for each key, the same (kind, input_field).

    `node_id` is ignored: the panel config references `@`-names and
    param titles, not node ids.  A user renumbering nodes in ComfyUI
    does not invalidate the docker's config.

    `default` is ignored: the config's value always overrides the
    default, so a change in the workflow's default does not
    invalidate a config that explicitly sets the value.

    `kind` is *not* ignored.  `comfy_apply_config` rejects a param
    whose type does not match the slot's declared kind; a `kind`
    change therefore must be treated as an interface change, and the
    config must be re-validated (which the reload does by clearing
    it, letting the next edit auto-apply a fresh one).

    `input_field` is not ignored for the same reason on the resource
    side: a future node class could conceivably use a different
    input field name, and the substitution code assumes whichever
    `input_field` was recorded at extraction time.
    """
    if set(old) != set(new):
        return False
    for k in old:
        if old[k].get("kind") != new[k].get("kind"):
            return False
        if old[k].get("input_field") != new[k].get("input_field"):
            return False
    return True


# ---------------------------------------------------------------------------
#  Handler
# ---------------------------------------------------------------------------

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

        workflow, err, converted, converted_path = _read_workflow_file(
            path, display_name,
        )
        if err is not None:
            return err

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
        help_text="Reload a workflow from disk iff its slug matches "
                  "the currently active workflow, preserving the "
                  "current panel config when the workflow's slot "
                  "interface (resource @-names + param titles + kinds) "
                  "is unchanged.  Called by the ComfyUI-side "
                  "'Save To Kritomatic' button so a save updates the "
                  "daemon's view of the workflow without requiring a "
                  "manual re-Load.  Skips silently when the file is "
                  "not the active workflow, and when a batch is "
                  "running (never yanks state out from under a live "
                  "run).",
        args={
            "--filename": {"type": "str", "required": True,
                           "help": "Filename that was just saved. "
                                   "May include '.json'; the extension "
                                   "is stripped for comparison with "
                                   "_active_workflow_name."},
            "--preserve_config": {"type": "bool", "default": True,
                                  "help": "When True (default), keep "
                                          "_active_config across the "
                                          "reload if the slot "
                                          "interface is unchanged. "
                                          "When False, always clear "
                                          "it (same as comfy_load)."},
        },
    )
    def comfy_reload_if_active(self, params):
        parent = self.parent

        filename = (params.get("filename") or "").strip()
        if not filename:
            return {"success": False, "message": "filename is required"}
        if filename.endswith(".json"):
            filename = filename[:-5]
        if not filename:
            return {"success": False,
                    "message": "filename is empty after stripping '.json'"}

        # Only reload when this *is* the active workflow.  A save to a
        # different slug is a deliberate "this is a new file" action;
        # it must not disturb whatever the user currently has loaded.
        if parent._active_workflow_name != filename:
            return {
                "success": True,
                "message": (
                    f"Workflow {filename!r} is not the active workflow "
                    f"({parent._active_workflow_name!r}); no reload "
                    f"needed"
                ),
                "data": {
                    "reloaded": False,
                    "reason": "not_active",
                    "active": parent._active_workflow_name,
                    "filename": filename,
                },
            }

        path = _WORKFLOW_DIR / f"{filename}.json"
        if not path.is_file():
            return {
                "success": False,
                "message": f"Workflow file {path} does not exist",
            }

        workflow, err, converted, converted_path = _read_workflow_file(
            path, filename,
        )
        if err is not None:
            return err

        preserve = bool(params.get("preserve_config", True))

        # Snapshot old state *before* the swap.  _set_active_workflow
        # clears _active_config unconditionally; the point of this
        # command is to restore it when the interface is unchanged.
        old_config = parent._active_config
        old_resource_slots = dict(parent._active_workflow_resource_slots)
        old_param_slots = dict(parent._active_workflow_param_slots)

        # Hold _batches_lock around the swap and the emptiness check,
        # so a concurrently-starting comfy_generate cannot register
        # itself against the pre-reload workflow and then read the
        # post-reload one.  The lock is the same one _comfy_generate_impl
        # uses to register; whoever holds it first wins, and the other
        # will see consistent state.
        with parent._batches_lock:
            running = list(parent._batches.keys())
            if running:
                return {
                    "success": True,
                    "message": (
                        f"Workflow {filename!r} is active but "
                        f"{len(running)} batch(es) running; skipping "
                        f"reload"
                    ),
                    "data": {
                        "reloaded": False,
                        "reason": "batch_running",
                        "batches": running,
                        "filename": filename,
                    },
                }
            try:
                parent._set_active_workflow(filename, workflow, "file")
            except Exception as e:  # noqa: BLE001 — return a clear error
                import traceback
                traceback.print_exc()
                return {
                    "success": False,
                    "message": f"reload failed during slot extraction: "
                               f"{e}",
                }

        slot_error = parent._active_workflow_slot_error
        if slot_error:
            # The graph loaded but is not driveable; _set_active_workflow
            # has already cleared _active_config and the slot tables.
            # No "preserve" attempt makes sense here.
            return {
                "success": True,
                "message": (
                    f"Reloaded workflow {filename!r} "
                    f"({len(workflow)} nodes) \u2014 "
                    f"slot extraction failed: {slot_error}"
                ),
                "data": {
                    "reloaded": True,
                    "converted": converted,
                    "converted_path": (
                        str(converted_path) if converted_path else None
                    ),
                    "slot_error": slot_error,
                    "config_preserved": False,
                },
            }

        # Compare the *interface* (keys, kinds, input fields) — not
        # node ids, not defaults.  See _slot_interface_equal's
        # docstring.
        resource_interface_equal = _slot_interface_equal(
            old_resource_slots, parent._active_workflow_resource_slots,
        )
        param_interface_equal = _slot_interface_equal(
            old_param_slots, parent._active_workflow_param_slots,
        )
        interface_unchanged = (
            resource_interface_equal and param_interface_equal
        )

        config_preserved = False
        if preserve and old_config is not None and interface_unchanged:
            # A shallow copy keeps the outer dict fresh while sharing
            # the inner resource/param/crop/paste dicts — safe, because
            # nothing downstream mutates them in place.
            parent._active_config = dict(old_config)
            config_preserved = True

        msg = (f"Reloaded workflow {filename!r} "
               f"({len(workflow)} nodes)")
        if converted:
            msg += " \u2014 auto-converted from UI format"
        if old_config is None:
            pass  # nothing was there to preserve or clear
        elif config_preserved:
            msg += " \u2014 config preserved"
        else:
            msg += " \u2014 config cleared (interface changed)"

        return {
            "success": True,
            "message": msg,
            "data": {
                "reloaded": True,
                "converted": converted,
                "converted_path": (
                    str(converted_path) if converted_path else None
                ),
                "slot_interface_changed": not interface_unchanged,
                "config_preserved": config_preserved,
                "had_prior_config": old_config is not None,
            },
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
