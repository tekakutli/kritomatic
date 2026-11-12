"""Panel-config consumption for the native v2 pipeline.

Three commands:

  * `comfy_apply_config` — takes the parsed `[resources]` / `[params]` /
    `[crop]` / `[paste]` sections from a panel config, validates them
    against the loaded workflow's slot tables, and stores the normalized
    result on the handler for `comfy_generate` to consume.

  * `comfy_prepare_only` — reads resources per the applied config,
    uploads them to the comfy-api-proxy, substitutes `core/ASSET` refs
    and param values into the graph, and returns the prepared graph
    WITHOUT submitting. Diagnostic.

  * `comfy_panel_run` — convenience wrapper: connect (auto-starts the
    proxy), apply the supplied config, then generate.

  * `comfy_save_config` — write the current panel config to
    `data/comfy_panel/panel_configs/<workflow>.json`. Creates the file
    if it doesn't exist; preserves `host`/`port`/`exclusive`/`watch`/
    `timeout`/`count`/`seed` from the existing file if present; always
    sets `workflow` to `_active_workflow_name`. Values for
    `resources`/`params`/`crop`/`paste` come from the caller if
    provided (docker path), or from `_active_config` if not (CLI path).

Panel config files are JSON. The TOML format is gone.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..decorators import command, AutoDispatchHandler


_RESOURCE_SOURCES = frozenset({"active", "global", "layer"})
_CROP_CONTEXTS = frozenset({"entire_image", "automatic", "mask_bounds"})
_PASTE_BATCH_MODES = frozenset({"default", "layers", "animation"})

_KIND_ACCEPTORS = {
    "float":   lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "int":     lambda v: isinstance(v, int) and not isinstance(v, bool),
    "string":  lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
}


class ConfigCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Apply a panel config (resources, params, crop, paste) "
                  "to the active workflow. Validates every "
                  "'@'-prefixed resource, every param title, and every "
                  "enum value against the loaded graph's slot tables.",
        args={
            "--resources": {
                "type": "str", "default": "{}",
                "help": "JSON object mapping @-names to resource specs.",
            },
            "--params": {
                "type": "str", "default": "{}",
                "help": "JSON object mapping Primitive _meta.title to "
                        "the value to substitute.",
            },
            "--crop": {
                "type": "str", "default": "{}",
                "help": "JSON object: {'context': ..., 'padding': ...}.",
            },
            "--paste": {
                "type": "str", "default": "{}",
                "help": "JSON object: {'x': ..., 'y': ..., 'name': ..., "
                        "'batch_mode': ..., 'resize_canvas': ...}.",
            },
        },
    )
    def comfy_apply_config(self, params):
        parent = self.parent
        if parent._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first"}

        try:
            resources = json.loads(params.get("resources") or "{}")
            params_obj = json.loads(params.get("params") or "{}")
            crop = json.loads(params.get("crop") or "{}")
            paste = json.loads(params.get("paste") or "{}")
        except json.JSONDecodeError as e:
            return {"success": False,
                    "message": f"config parse error: {e}"}

        for name, obj in (("resources", resources), ("params", params_obj),
                          ("crop", crop), ("paste", paste)):
            if not isinstance(obj, dict):
                return {"success": False,
                        "message": f"'{name}' must be a JSON object"}

        resource_slots = parent._active_workflow_resource_slots
        param_slots = parent._active_workflow_param_slots

        unknown_resources = set(resources) - set(resource_slots)
        if unknown_resources:
            return {
                "success": False,
                "message": (
                    f"[resources] references unknown @-name(s): "
                    f"{sorted(unknown_resources)}. "
                    f"Available in the loaded graph: "
                    f"{sorted(resource_slots) or '(none)'}."
                ),
            }

        unknown_params = set(params_obj) - set(param_slots)
        if unknown_params:
            return {
                "success": False,
                "message": (
                    f"[params] references unknown _meta.title(s): "
                    f"{sorted(unknown_params)}. "
                    f"Available in the loaded graph: "
                    f"{sorted(param_slots) or '(none)'}. "
                    f"Set the title on the Primitive node in ComfyUI's "
                    f"browser."
                ),
            }

        normalized_resources: dict[str, dict] = {}
        for res_name, res_spec in resources.items():
            if not isinstance(res_spec, dict):
                return {"success": False,
                        "message": (
                            f"resource {res_name!r}: must be a JSON "
                            f"object with a 'source' field"
                        )}
            source = res_spec.get("source")
            if source not in _RESOURCE_SOURCES:
                return {"success": False,
                        "message": (
                            f"resource {res_name!r}: source must be "
                            f"one of {sorted(_RESOURCE_SOURCES)}, got "
                            f"{source!r}"
                        )}
            layer_name = res_spec.get("layer_name")
            if source == "layer":
                if not isinstance(layer_name, str) or not layer_name:
                    return {"success": False,
                            "message": (
                                f"resource {res_name!r}: source='layer' "
                                f"requires a non-empty layer_name"
                            )}
            else:
                layer_name = None

            normalized_resources[res_name] = {
                "source": source,
                "layer_name": layer_name,
            }

        for param_name, param_value in params_obj.items():
            kind = param_slots[param_name]["kind"]
            if not _KIND_ACCEPTORS[kind](param_value):
                return {"success": False,
                        "message": (
                            f"param {param_name!r}: expected {kind}, "
                            f"got {type(param_value).__name__} "
                            f"({param_value!r})"
                        )}

        crop_context = crop.get("context", "entire_image")
        if crop_context not in _CROP_CONTEXTS:
            return {"success": False,
                    "message": (
                        f"crop.context must be one of "
                        f"{sorted(_CROP_CONTEXTS)}, got "
                        f"{crop_context!r}"
                    )}
        crop_padding = crop.get("padding", 0)
        if (not isinstance(crop_padding, int)
                or isinstance(crop_padding, bool)
                or crop_padding < 0):
            return {"success": False,
                    "message": (
                        f"crop.padding must be a non-negative integer, "
                        f"got {crop_padding!r}"
                    )}

        paste_batch_mode = paste.get("batch_mode", "default")
        if paste_batch_mode not in _PASTE_BATCH_MODES:
            return {"success": False,
                    "message": (
                        f"paste.batch_mode must be one of "
                        f"{sorted(_PASTE_BATCH_MODES)}, got "
                        f"{paste_batch_mode!r}"
                    )}

        def _coerce_int(obj, key, default=0):
            v = obj.get(key, default)
            try:
                return int(v)
            except (TypeError, ValueError):
                return default

        normalized_paste = {
            "x": _coerce_int(paste, "x"),
            "y": _coerce_int(paste, "y"),
            "name": str(paste.get("name") or ""),
            "batch_mode": paste_batch_mode,
            "resize_canvas": bool(paste.get("resize_canvas", False)),
        }

        parent._active_config = {
            "resources": normalized_resources,
            "params": dict(params_obj),
            "crop": {
                "context": crop_context,
                "padding": crop_padding,
            },
            "paste": normalized_paste,
        }

        return {
            "success": True,
            "message": (
                f"applied: {len(normalized_resources)} resource(s), "
                f"{len(params_obj)} param(s), "
                f"crop={crop_context} (padding={crop_padding}), "
                f"paste={paste_batch_mode}"
            ),
            "data": {
                "resources": sorted(normalized_resources.keys()),
                "params": sorted(params_obj.keys()),
                "output_nodes": sorted(
                    parent._active_workflow_output_nodes.keys()
                ),
                "crop": parent._active_config["crop"],
                "paste": parent._active_config["paste"],
            },
        }

    @command(
        category="comfyui",
        help_text="Read resources per the applied config, upload them to "
                  "the proxy, substitute them into the graph, and return "
                  "the prepared graph WITHOUT submitting.",
        args={
            "--seed": {"type": "int", "required": False,
                       "help": "Override the 'seed' param."},
        },
    )
    def comfy_prepare_only(self, params):
        from krita import Krita

        from ..utils.comfy_apply import prepare_workflow
        from ..utils.comfyui_proxy_client import ProxyV2, ProxyUnreachable

        parent = self.parent
        if parent._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first"}
        if parent._active_config is None:
            return {"success": False,
                    "message": "No panel config applied; run "
                               "comfy_apply_config first"}

        doc = Krita.instance().activeDocument()
        if doc is None:
            return {"success": False,
                    "message": "No active document open in Krita"}

        try:
            client = ProxyV2.from_pid_file()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False,
                    "message": f"proxy unavailable: {e}"}

        seed_raw = params.get("seed")
        seed = None
        if seed_raw is not None:
            try:
                seed = int(seed_raw)
            except (TypeError, ValueError):
                return {"success": False,
                        "message": f"--seed must be an integer, got "
                                   f"{seed_raw!r}"}

        try:
            result = prepare_workflow(
                doc,
                parent._active_workflow_graph,
                parent._active_workflow_resource_slots,
                parent._active_workflow_param_slots,
                parent._active_config,
                client,
                seed=seed,
            )
        except ValueError as e:
            return {"success": False, "message": str(e)}

        params_applied = set(parent._active_config["params"].keys())
        if result.seed_applied is not None:
            params_applied.add("seed")

        return {
            "success": True,
            "message": (
                f"prepared: {len(result.assets)} asset(s) uploaded, "
                f"{len(result.graph)} node(s) in graph, "
                f"crop={list(result.crop_bounds)}"
            ),
            "data": {
                "crop_bounds": list(result.crop_bounds),
                "assets": {
                    name: {"id": a.get("id"), "hash": a.get("hash")}
                    for name, a in result.assets.items()
                },
                "params_applied": sorted(params_applied),
                "seed_applied": result.seed_applied,
                "warnings": result.warnings,
                "graph": result.graph,
            },
        }

    @command(
        category="comfyui",
        help_text="Connect (auto-starting the proxy), apply the supplied "
                  "panel config, then generate.  Sections omitted from "
                  "the call fall back to the daemon's current "
                  "_active_config, and the response notes which sections "
                  "used the fallback.",
        args={
            "--resources": {"type": "str", "required": False,
                            "help": "JSON object. Omit to use "
                                    "_active_config's current value."},
            "--params": {"type": "str", "required": False,
                         "help": "JSON object. Omit to use current."},
            "--crop": {"type": "str", "required": False,
                       "help": "JSON object. Omit to use current."},
            "--paste": {"type": "str", "required": False,
                        "help": "JSON object. Omit to use current."},
            "--timeout": {"type": "float", "default": 55.0},
            "--count": {"type": "int", "default": 1},
            "--seed": {"type": "int", "required": False},
            "--no_paste": {"type": "bool", "default": False},
            "--output_rotation": {"type": "float", "default": 0.0},
        },
    )
    def comfy_panel_run(self, params):
        parent = self.parent

        conn = parent._connection_cmds.comfy_connect({
            "host": parent._host,
            "port": parent._port,
            "check": False,
        })
        if not isinstance(conn, dict):
            return {
                "success": False,
                "message": f"comfy_connect returned non-dict: {conn!r}",
            }
        if not conn.get("success"):
            return {
                "success": False,
                "message": (
                    f"connect failed before apply/generate: "
                    f"{conn.get('message', 'unknown error')}"
                ),
            }

        # Presence check ("is this key in params?") rather than
        # value check ("is it empty?"), because a caller that
        # explicitly sends `"resources": "{}"` means "apply empty
        # resources" — it cleared them on purpose.  A caller that
        # omits the key entirely means "I don't care, use what
        # _active_config already has".  The web panel's Run button
        # and the Docker's Run button both always supply all four
        # sections (assembled from current widget state); only the
        # schema-based CLI path `kritomatic comfyui comfy_panel_run`
        # can omit them.  That CLI path is what the args schema
        # above enables: with no `default: "{}"`, an unsupplied
        # section is truly absent from `params`, so the fallback
        # fires.
        active = parent._active_config or {}
        apply_params = {}
        fallback_keys: list[str] = []
        for key in ("resources", "params", "crop", "paste"):
            if key in params:
                apply_params[key] = params[key]
            else:
                value = active.get(key) or {}
                if isinstance(value, str):
                    apply_params[key] = value
                else:
                    apply_params[key] = json.dumps(value)
                fallback_keys.append(key)

        apply_result = self.comfy_apply_config(apply_params)
        if not isinstance(apply_result, dict):
            return {
                "success": False,
                "message": f"comfy_apply_config returned non-dict: "
                           f"{apply_result!r}",
            }
        if not apply_result.get("success"):
            return apply_result

        gen_params: dict = {
            "timeout": params.get("timeout", 55.0),
            "count": params.get("count", 1),
            "no_paste": params.get("no_paste", False),
            "output_rotation": params.get("output_rotation", 0.0),
        }
        if params.get("seed") is not None:
            gen_params["seed"] = params.get("seed")
        if params.get("_batch_id"):
            gen_params["_batch_id"] = params.get("_batch_id")

        gen_result = parent._generation_cmds.comfy_generate(gen_params)

        if fallback_keys and isinstance(gen_result, dict):
            note = (f" (used _active_config for "
                    f"{', '.join(fallback_keys)})")
            gen_result["message"] = (
                (gen_result.get("message") or "") + note
            )
            data = gen_result.get("data")
            if isinstance(data, dict):
                data["fallback_sections"] = list(fallback_keys)

        return gen_result

    # ------------------------------------------------------------------
    #  comfy_save_config
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Write the current panel config to "
                  "data/comfy_panel/panel_configs/<workflow>.json. "
                  "Creates the file if missing; preserves "
                  "host/port/exclusive/watch/timeout/count/seed from the "
                  "existing file if present; always sets workflow to the "
                  "active workflow name. Resources/params/crop/paste come "
                  "from the provided args if any are supplied (docker "
                  "path), else from the current _active_config (CLI path).",
        args={
            "--resources": {"type": "str", "required": False,
                            "help": "JSON resources. Omit to use "
                                    "_active_config's current value."},
            "--params": {"type": "str", "required": False,
                         "help": "JSON params. Omit to use current."},
            "--crop": {"type": "str", "required": False,
                       "help": "JSON crop. Omit to use current."},
            "--paste": {"type": "str", "required": False,
                        "help": "JSON paste. Omit to use current."},
        },
    )
    def comfy_save_config(self, params):
        parent = self.parent
        if parent._active_workflow_name is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first"}

        # If the caller supplied any config section, apply it first so
        # validation happens and _active_config reflects the values
        # that will be written. If nothing was supplied, use the
        # current _active_config as-is.
        supplied_any = any(
            params.get(k) is not None
            for k in ("resources", "params", "crop", "paste")
        )
        if supplied_any:
            apply_args = {
                "resources": params.get("resources") or "{}",
                "params": params.get("params") or "{}",
                "crop": params.get("crop") or "{}",
                "paste": params.get("paste") or "{}",
            }
            apply_result = self.comfy_apply_config(apply_args)
            if not apply_result.get("success"):
                return apply_result

        if parent._active_config is None:
            return {"success": False,
                    "message": "No panel config to save; run "
                               "comfy_apply_config first"}

        # Lazy import — sys.path bootstrap only runs when this command
        # is actually used, not at handler construction.
        from ..utils.panel_config import (
            CONFIG_DIR, read_config_file, write_config_file,
        )

        workflow_name = parent._active_workflow_name
        path = CONFIG_DIR / f"{workflow_name}.json"

        existing: dict = {}
        if path.is_file():
            try:
                existing = read_config_file(path)
            except ValueError:
                existing = {}

        file_data: dict = {
            "host": existing.get("host", parent._host),
            "port": existing.get("port", parent._port),
            "workflow": workflow_name,
            "exclusive": existing.get("exclusive", True),
            "watch": existing.get("watch", False),
            "timeout": existing.get("timeout", 55.0),
            "count": existing.get("count", 1),
            "seed": existing.get("seed"),
            "resources": parent._active_config["resources"],
            "params": parent._active_config["params"],
            "crop": parent._active_config["crop"],
            "paste": parent._active_config["paste"],
        }
        # Preserve any _notes the user wrote by hand.
        if "_notes" in existing:
            file_data["_notes"] = existing["_notes"]

        try:
            write_config_file(path, file_data)
        except OSError as e:
            return {"success": False,
                    "message": f"could not write {path}: {e}"}

        return {
            "success": True,
            "message": f"Saved config to {path}",
            "data": {
                "path": str(path),
                "workflow": workflow_name,
                "resource_count": len(file_data["resources"]),
                "param_count": len(file_data["params"]),
            },
        }
