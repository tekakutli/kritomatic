"""Panel-config consumption for the native v2 pipeline.

Two commands:

  * `comfy_apply_config` — takes the parsed `[resources]` / `[params]` /
    `[crop]` / `[paste]` sections from a panel config, validates them
    against the loaded workflow's slot tables, and stores the normalized
    result on the handler for `comfy_generate` to consume.

  * `comfy_prepare_only` — reads resources per the applied config,
    uploads them to the comfy-api-proxy, substitutes `core/ASSET` refs
    and param values into the graph, and returns the prepared graph
    WITHOUT submitting. Diagnostic; Stage 5's `comfy_generate` consumes
    the same `PreparedWorkflow` via `utils.comfy_apply.prepare_workflow`.

  * `comfy_panel_run` — convenience wrapper: connect (auto-starts the
    proxy), apply the supplied config, then generate. One command for
    the docker's Run button, which collects the config from its widgets
    at click time and cannot feasibly send three separate commands
    without exposing the intermediate states to a race.

Where the slots come from
=========================
`comfy_load` walks the loaded graph and populates:

    _active_workflow_resource_slots  —  {"@thecanvas": {node_id, ...}, ...}
    _active_workflow_param_slots     —  {"denoise": {node_id, kind}, ...}
    _active_workflow_output_nodes    —  {"9": {class_type, filename_prefix}, ...}

See `comfyui_inspect._extract_resource_slots` / `_extract_param_slots` /
`_extract_output_nodes`.

Validation rules
================
`comfy_apply_config` is rejected if any of the following hold, each
with a message naming the offending key so the user can fix the TOML:

  * A `[resources]` key does not match a slot's `@`-name.
  * A `[resources]` entry has `source` outside `{active, global, layer}`.
  * A `[resources]` entry has `source = "layer"` without `layer_name`.
  * A `[params]` key does not match a slot's `_meta.title`.
  * A `[params]` value's Python type does not match the slot's kind
    (`float` accepts int/float, rejects bool; `int` rejects bool;
    `string` rejects non-str; `boolean` rejects non-bool).
  * `crop.context` outside `{entire_image, automatic, mask_bounds}`.
  * `crop.padding` is negative or non-int.
  * `paste.batch_mode` outside `{default, layers, animation}`.

Unknown top-level keys in the config are ignored (forward-compat).

Storage
=======
Normalized config is stored on `ComfyUIHandler._active_config`. Cleared
by `comfy_load` and by `_set_active_workflow` so a stale config from a
previous workflow can never accidentally apply.

Apply is idempotent: applying a new config replaces the previous one.
Applying an empty config (`{}` for all four sections) is valid — it
clears the config and leaves the graph as-is, useful to reset after a
failed validation.

`comfy_panel_run` and state publishing
======================================
`comfy_panel_run` calls `comfy_apply_config` and `comfy_generate` as
methods on the sub-handler instances, not through the container's
`execute`. That means the intermediate config-applied state does not
publish on its own. It is still the `comfy_panel_run` command's own
`execute` (in the container) that publishes the final state once
`comfy_panel_run` returns — so the docker still sees the correct final
state (`_active_config` set, batch records present or absent depending
on timing).

The generate call's internal `parent._publish_state_change()` calls
(batch start, batch end) are unaffected — they happen on the daemon's
container, not on this sub-handler, and are called explicitly by
`comfy_generate`'s implementation.
"""

from __future__ import annotations

import json

from ..decorators import command, AutoDispatchHandler


_RESOURCE_SOURCES = frozenset({"active", "global", "layer"})
_CROP_CONTEXTS = frozenset({"entire_image", "automatic", "mask_bounds"})
_PASTE_BATCH_MODES = frozenset({"default", "layers", "animation"})

# Type acceptors per slot kind. Deliberately explicit so `bool` vs `int`
# is unambiguous: `isinstance(True, int)` is True in Python, so we
# reject bool where a number is expected.
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
                  "enum value against the loaded graph's slot tables. "
                  "comfy_generate consumes the result: resources become "
                  "uploaded assets, params are substituted into "
                  "Primitive nodes, crop controls selection cropping, "
                  "paste controls output placement.",
        args={
            "--resources": {
                "type": "str", "default": "{}",
                "help": "JSON object mapping @-names to resource specs: "
                        "{'@thecanvas': {'source': 'active'}, "
                        "'@theselection': {'source': 'global'}, "
                        "'@thecontext': {'source': 'layer', "
                        "'layer_name': 'context'}}. Empty = no "
                        "resource substitutions.",
            },
            "--params": {
                "type": "str", "default": "{}",
                "help": "JSON object mapping Primitive _meta.title to "
                        "the value to substitute: {'denoise': 0.7, "
                        "'prompt': 'a cat', 'Illustrous?': true}. "
                        "Empty = all Primitive nodes keep their "
                        "defaults.",
            },
            "--crop": {
                "type": "str", "default": "{}",
                "help": "JSON object: {'context': "
                        "'entire_image'|'automatic'|'mask_bounds', "
                        "'padding': 0}. Applied when a resource has "
                        "source='global'.",
            },
            "--paste": {
                "type": "str", "default": "{}",
                "help": "JSON object: {'x': 0, 'y': 0, 'name': '', "
                        "'batch_mode': 'default'|'layers'|'animation', "
                        "'resize_canvas': false}. Applied when pasting "
                        "SaveImage outputs.",
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

        # ---- slot membership ----
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

        # ---- per-resource validation ----
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
                # Not meaningful for other sources; normalize away.
                layer_name = None

            normalized_resources[res_name] = {
                "source": source,
                "layer_name": layer_name,
            }

        # ---- per-param validation ----
        for param_name, param_value in params_obj.items():
            kind = param_slots[param_name]["kind"]
            if not _KIND_ACCEPTORS[kind](param_value):
                return {"success": False,
                        "message": (
                            f"param {param_name!r}: expected {kind}, "
                            f"got {type(param_value).__name__} "
                            f"({param_value!r})"
                        )}

        # ---- crop validation ----
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

        # ---- paste validation ----
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

        # ---- store ----
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
                  "the proxy, substitute them into the graph (along "
                  "with params and seed), and return the prepared graph "
                  "WITHOUT submitting. The prepared graph is what "
                  "comfy_generate submits (Stage 5); use this to inspect "
                  "substitutions without running a job.",
        args={
            "--seed": {"type": "int", "required": False,
                       "help": "Override the 'seed' param (if the "
                               "workflow has a Primitive titled 'seed')."},
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
                  "panel config, then generate. Convenience wrapper "
                  "around comfy_connect + comfy_apply_config + "
                  "comfy_generate. If connect or apply fails, generate "
                  "is not attempted; the failure is returned in the "
                  "same response shape as the underlying command.",
        args={
            "--resources": {
                "type": "str", "default": "{}",
                "help": "JSON object (same as comfy_apply_config).",
            },
            "--params": {
                "type": "str", "default": "{}",
                "help": "JSON object (same as comfy_apply_config).",
            },
            "--crop": {
                "type": "str", "default": "{}",
                "help": "JSON object (same as comfy_apply_config).",
            },
            "--paste": {
                "type": "str", "default": "{}",
                "help": "JSON object (same as comfy_apply_config).",
            },
            "--timeout": {
                "type": "float", "default": 55.0,
                "help": "Seconds per run (same as comfy_generate).",
            },
            "--count": {
                "type": "int", "default": 1,
                "help": "Number of runs (same as comfy_generate).",
            },
            "--seed": {
                "type": "int", "required": False,
                "help": "Base seed (same as comfy_generate).",
            },
            "--no_paste": {
                "type": "bool", "default": False,
                "help": "Skip pasting (same as comfy_generate).",
            },
            "--output_rotation": {
                "type": "float", "default": 0.0,
                "help": "Rotation degrees (same as comfy_generate).",
            },
        },
    )
    def comfy_panel_run(self, params):
        parent = self.parent

        # ---- connect (auto-starts proxy if needed) ----
        # check=False so we do not spend a round trip on /object_info.
        # comfy_generate itself does not need object_info; it uses the
        # proxy directly. The _client is only used as a "connected?"
        # flag by comfy_generate's precondition check.
        conn = parent._connection_cmds.comfy_connect({
            "host": parent._host,
            "port": parent._port,
            "check": False,
        })
        if not conn.get("success"):
            return {
                "success": False,
                "message": (
                    f"connect failed before apply/generate: "
                    f"{conn.get('message', 'unknown error')}"
                ),
            }

        # ---- apply config ----
        apply_params = {
            "resources": params.get("resources", "{}"),
            "params": params.get("params", "{}"),
            "crop": params.get("crop", "{}"),
            "paste": params.get("paste", "{}"),
        }
        apply_result = self.comfy_apply_config(apply_params)
        if not apply_result.get("success"):
            return apply_result

        # ---- generate ----
        gen_params: dict = {
            "timeout": params.get("timeout", 55.0),
            "count": params.get("count", 1),
            "no_paste": params.get("no_paste", False),
            "output_rotation": params.get("output_rotation", 0.0),
        }
        if params.get("seed") is not None:
            gen_params["seed"] = params.get("seed")
        # Forward the outer batch id so the daemon's _BatchRecord is
        # keyed by the docker's run-<timestamp>, not a fresh uuid. The
        # docker's _running state and the daemon's cancel target both
        # reference the same id.
        if params.get("_batch_id"):
            gen_params["_batch_id"] = params.get("_batch_id")

        return parent._generation_cmds.comfy_generate(gen_params)