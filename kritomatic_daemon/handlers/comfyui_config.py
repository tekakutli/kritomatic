"""Panel-config consumption, plus the `comfy_set_layer_pixels`
scratch-layer writer.

Config commands:

  * `comfy_apply_config` — takes the parsed `[resources]` / `[params]` /
    `[crop]` / `[paste]` sections from a panel config, validates them
    against the loaded workflow's slot tables, and stores the normalized
    result on the handler for `comfy_generate` to consume.
  * `comfy_prepare_only` — diagnostic.
  * `comfy_panel_run` — connect, apply, generate.
  * `comfy_save_config` — write the current panel config.

Scratch-layer command:

  * `comfy_set_layer_pixels` — write content into a named layer for the
    playground's sync protocol.  Two behaviors depending on what the
    existing layer is:

      · paint layer   — `setPixelData` in place with the RGBA blob
                        (or the mask-as-alpha conversion when
                        `is_mask=True`).
      · mask layer    — `setPixelData` in place with grayscale bytes
                        (only when `is_mask=True`).

    A missing layer is created as a new paint layer at the document
    root, so first-time use works without manual layer setup.  Any
    other existing layer type (group / file / vector / clone / fill /
    filter) is refused — those don't accept `setPixelData` as a
    scratch target, and silently clobbering them would be worse than
    an error message.

    This preserves the user's mask layers: if they had a
    `theselection` selection mask before, it stays a selection mask;
    only its pixel content is updated to reflect the playground's
    current mask.  Same for `thecontext`."""

from __future__ import annotations

import base64
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


def _find_node_by_name(doc, name):
    def walk(node):
        try:
            if node.name() == name:
                return node
        except Exception:
            pass
        try:
            for child in node.childNodes():
                found = walk(child)
                if found is not None:
                    return found
        except Exception:
            pass
        return None
    try:
        return walk(doc.rootNode())
    except Exception:
        return None


class ConfigCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    # ------------------------------------------------------------------
    #  comfy_set_layer_pixels
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Write content into a named layer for the playground's "
                  "sync protocol. Existing paint layers are updated in "
                  "place with RGBA (or mask-as-alpha when is_mask=True). "
                  "Existing mask layers (transparencymask / selectionmask) "
                  "are updated in place with grayscale when is_mask=True; "
                  "other layer types are refused. A missing layer is "
                  "created as a new paint layer at the document root.",
        args={
            "--name":      {"type": "str",  "required": True,
                            "help": "Layer name to create or update."},
            "--image_b64": {"type": "str",  "required": True,
                            "help": "Base64-encoded PNG."},
            "--is_mask":   {"type": "bool", "default": False,
                            "help": "Treat input's R channel as the mask "
                                    "(paint layer: store as alpha with "
                                    "RGB=255; mask layer: store as "
                                    "grayscale bytes)."},
        },
    )
    def comfy_set_layer_pixels(self, params):
        try:
            from krita import Krita
            from PyQt6.QtGui import QImage
            from PyQt6.QtCore import Qt
            import numpy as np
        except ImportError as e:
            return {"success": False, "message": f"import failed: {e}"}

        name = (params.get("name") or "").strip()
        image_b64 = params.get("image_b64") or ""
        is_mask = bool(params.get("is_mask", False))

        if not name:
            return {"success": False, "message": "name is required"}
        if not image_b64:
            return {"success": False, "message": "image_b64 is required"}

        doc = Krita.instance().activeDocument()
        if doc is None:
            return {"success": False, "message": "No active document"}

        try:
            raw = base64.b64decode(image_b64)
        except Exception as e:
            return {"success": False, "message": f"bad base64: {e}"}

        img = QImage()
        if not img.loadFromData(raw, "PNG"):
            return {"success": False, "message": "not a valid PNG"}
        img = img.convertToFormat(QImage.Format.Format_ARGB32)

        W, H = int(doc.width()), int(doc.height())
        if img.width() != W or img.height() != H:
            img = img.scaled(
                W, H,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            img = img.convertToFormat(QImage.Format.Format_ARGB32)

        existing = _find_node_by_name(doc, name)

        mask_layer_types = {"transparencymask", "selectionmask"}
        image_layer_types = {
            "paintlayer", "grouplayer", "filelayer", "vectorlayer",
            "clonelayer", "filllayer", "filterlayer",
        }

        if existing is not None:
            existing_type = existing.type()

            if existing_type in mask_layer_types:
                if not is_mask:
                    return {
                        "success": False,
                        "message": (
                            f"layer {name!r} exists as {existing_type}; "
                            f"only is_mask=True content can be written "
                            f"into it. Rename the layer or delete it."
                        ),
                    }
                # Extract R channel (which is the grayscale mask value
                # after QImage's PNG decode) into a grayscale buffer.
                ptr = img.constBits()
                ptr.setsize(img.sizeInBytes())
                arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(
                    (H, W, 4),
                )
                # Qt ARGB32 memory layout little-endian: BGRA per pixel.
                gray = arr[:, :, 2].copy().tobytes()
                try:
                    existing.setPixelData(gray, 0, 0, W, H)
                except Exception as e:
                    return {"success": False,
                            "message": f"setPixelData failed on mask "
                                       f"layer: {e}"}
                try:
                    doc.refreshProjection()
                except Exception:
                    pass
                return {
                    "success": True,
                    "message": f"updated mask layer {name!r} ({W}x{H})",
                    "data": {"name": name, "width": W, "height": H,
                             "kind": existing_type,
                             "updated": True},
                }

            if existing_type == "paintlayer":
                if is_mask:
                    ptr = img.constBits()
                    ptr.setsize(img.sizeInBytes())
                    arr = np.frombuffer(
                        bytes(ptr), dtype=np.uint8,
                    ).reshape((H, W, 4)).copy()
                    r_channel = arr[:, :, 2].copy()
                    arr[:, :, 0] = 255  # B
                    arr[:, :, 1] = 255  # G
                    arr[:, :, 2] = 255  # R
                    arr[:, :, 3] = r_channel
                    argb_bytes = arr.tobytes()
                else:
                    ptr = img.constBits()
                    ptr.setsize(img.sizeInBytes())
                    argb_bytes = bytes(ptr)
                try:
                    existing.setPixelData(argb_bytes, 0, 0, W, H)
                except Exception as e:
                    return {"success": False,
                            "message": f"setPixelData failed on paint "
                                       f"layer: {e}"}
                try:
                    doc.refreshProjection()
                except Exception:
                    pass
                return {
                    "success": True,
                    "message": f"updated paint layer {name!r} ({W}x{H})",
                    "data": {"name": name, "width": W, "height": H,
                             "kind": "paintlayer",
                             "updated": True},
                }

            # Any other existing type — refuse rather than clobber.
            return {
                "success": False,
                "message": (
                    f"layer {name!r} exists as {existing_type!r}, which "
                    f"cannot be used as a scratch target. Rename or "
                    f"delete it before syncing."
                ),
            }

        # ---- No existing layer: create a paint layer at root ----
        node = doc.createNode(name, "paintlayer")
        if node is None:
            return {"success": False,
                    "message": f"createNode returned None for {name!r}"}
        doc.rootNode().addChildNode(node, None)

        if is_mask:
            ptr = img.constBits()
            ptr.setsize(img.sizeInBytes())
            arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(
                (H, W, 4),
            ).copy()
            r_channel = arr[:, :, 2].copy()
            arr[:, :, 0] = 255
            arr[:, :, 1] = 255
            arr[:, :, 2] = 255
            arr[:, :, 3] = r_channel
            argb_bytes = arr.tobytes()
        else:
            ptr = img.constBits()
            ptr.setsize(img.sizeInBytes())
            argb_bytes = bytes(ptr)

        try:
            node.setPixelData(argb_bytes, 0, 0, W, H)
        except Exception as e:
            return {"success": False,
                    "message": f"setPixelData failed on new layer: {e}"}

        try:
            doc.refreshProjection()
        except Exception:
            pass

        return {
            "success": True,
            "message": f"created {name!r} ({W}x{H} is_mask={is_mask})",
            "data": {"name": name, "width": W, "height": H,
                     "is_mask": is_mask, "created": True},
        }

    # ------------------------------------------------------------------
    #  comfy_apply_config (unchanged)
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Apply a panel config (resources, params, crop, paste) "
                  "to the active workflow.",
        args={
            "--resources": {"type": "str", "default": "{}"},
            "--params":    {"type": "str", "default": "{}"},
            "--crop":      {"type": "str", "default": "{}"},
            "--paste":     {"type": "str", "default": "{}"},
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
                    f"{sorted(param_slots) or '(none)'}."
                ),
            }

        normalized_resources: dict[str, dict] = {}
        for res_name, res_spec in resources.items():
            if not isinstance(res_spec, dict):
                return {"success": False,
                        "message": f"resource {res_name!r}: must be a JSON "
                                   f"object with a 'source' field"}
            source = res_spec.get("source")
            if source not in _RESOURCE_SOURCES:
                return {"success": False,
                        "message": f"resource {res_name!r}: source must be "
                                   f"one of {sorted(_RESOURCE_SOURCES)}, "
                                   f"got {source!r}"}
            layer_name = res_spec.get("layer_name")
            if source == "layer":
                if not isinstance(layer_name, str) or not layer_name:
                    return {"success": False,
                            "message": f"resource {res_name!r}: "
                                       f"source='layer' requires a "
                                       f"non-empty layer_name"}
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
                        "message": f"param {param_name!r}: expected {kind}, "
                                   f"got {type(param_value).__name__} "
                                   f"({param_value!r})"}

        crop_context = crop.get("context", "entire_image")
        if crop_context not in _CROP_CONTEXTS:
            return {"success": False,
                    "message": f"crop.context must be one of "
                               f"{sorted(_CROP_CONTEXTS)}, got "
                               f"{crop_context!r}"}
        crop_padding = crop.get("padding", 0)
        if (not isinstance(crop_padding, int)
                or isinstance(crop_padding, bool)
                or crop_padding < 0):
            return {"success": False,
                    "message": f"crop.padding must be a non-negative "
                               f"integer, got {crop_padding!r}"}

        paste_batch_mode = paste.get("batch_mode", "default")
        if paste_batch_mode not in _PASTE_BATCH_MODES:
            return {"success": False,
                    "message": f"paste.batch_mode must be one of "
                               f"{sorted(_PASTE_BATCH_MODES)}, got "
                               f"{paste_batch_mode!r}"}

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

    # ------------------------------------------------------------------
    #  comfy_prepare_only (unchanged)
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Read resources per the applied config, upload them, "
                  "substitute into the graph, return the prepared graph "
                  "WITHOUT submitting.",
        args={
            "--seed": {"type": "int", "required": False},
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
                f"crop={list(result.crop_bounds)}, "
                f"rotation={result.rotation}"
            ),
            "data": {
                "crop_bounds": list(result.crop_bounds),
                "rotation": result.rotation,
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

    # ------------------------------------------------------------------
    #  comfy_panel_run (unchanged)
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Connect (auto-starting the proxy), apply the supplied "
                  "panel config, then generate.",
        args={
            "--resources": {"type": "str", "required": False},
            "--params":    {"type": "str", "required": False},
            "--crop":      {"type": "str", "required": False},
            "--paste":     {"type": "str", "required": False},
            "--timeout":   {"type": "float", "default": 55.0},
            "--count":     {"type": "int",   "default": 1},
            "--seed":      {"type": "int",   "required": False},
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
            return {"success": False,
                    "message": f"comfy_connect returned non-dict: {conn!r}"}
        if not conn.get("success"):
            return {"success": False,
                    "message": f"connect failed: "
                               f"{conn.get('message', 'unknown error')}"}

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
            return {"success": False,
                    "message": f"comfy_apply_config returned non-dict: "
                               f"{apply_result!r}"}
        if not apply_result.get("success"):
            return apply_result

        gen_params: dict = {
            "timeout": params.get("timeout", 55.0),
            "count": params.get("count", 1),
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
    #  comfy_save_config (unchanged)
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Write the current panel config to "
                  "data/comfy_panel/panel_configs/<workflow>.json.",
        args={
            "--resources": {"type": "str", "required": False},
            "--params":    {"type": "str", "required": False},
            "--crop":      {"type": "str", "required": False},
            "--paste":     {"type": "str", "required": False},
        },
    )
    def comfy_save_config(self, params):
        parent = self.parent
        if parent._active_workflow_name is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first"}

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
            "paste": {
                "name":       parent._active_config["paste"]["name"],
                "batch_mode": parent._active_config["paste"]["batch_mode"],
            },
        }
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
