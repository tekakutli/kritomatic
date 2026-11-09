"""
config.py — panel config file manipulation.

Pure helpers: TOML rendering, workflow-interface extraction, config
resolution, preset application, and — since Stage 3 — parsing of the
new `[resources]` / `[params]` / `[crop]` / `[paste]` schema into a
typed `PanelConfig`.

No daemon or network here; `cli.py` supplies the runtime around these
functions.

Two schema generations coexist
==============================
* `build_commands(cfg)` — the legacy path. Reads `[canvas]` /
  `[selection]` / `[params]` (with `ETN_Parameter.name` keys) and
  produces the batch the old generation pipeline consumes. Unchanged
  by Stage 3.

* `parse_panel_config(path)` + `build_batch(cfg, workflow_ref)` — the
  native-v2 path. Reads `[resources]` / `[params]` / `[crop]` /
  `[paste]` and produces `comfy_apply_config` + `comfy_generate`.
  Added in Stage 3, wired into `cmd_run` in Stage 5.

The CLI dispatches by inspecting the workflow: a graph with `ETN_*`
nodes uses the legacy path; a native graph uses the v2 path. Stage 6
migrates workflows and deletes the legacy path.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import tomllib
except ImportError:
    print("This command requires Python 3.11+ (for tomllib).",
          file=sys.stderr)
    sys.exit(2)

try:
    import tomlkit
except ImportError:
    print(
        "This command requires the 'tomlkit' package:\n"
        "    pip install tomlkit",
        file=sys.stderr,
    )
    sys.exit(2)

from .paths import CONFIG_DIR, PRESET_DIR, WORKFLOW_DIR


# ============================================================================
#  TOML helpers
# ============================================================================

def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if v is None:
        return '""'
    s = str(v)
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def _toml_key(k: str) -> str:
    if k and all(c.isalnum() or c in "-_" for c in k):
        return k
    return _toml_value(k)


# ============================================================================
#  Path-vs-slug detection
# ============================================================================

def _looks_like_path(s: str) -> bool:
    if not s:
        return False
    return (
        "/" in s
        or "\\" in s
        or s.startswith("~")
        or s.startswith(".")
        or s.lower().endswith(".json")
    )


# ============================================================================
#  Workflow interface extraction (used by legacy template rendering)
# ============================================================================

def extract_workflow(workflow: dict) -> dict:
    params: dict = {}
    has_canvas = False
    selection = None
    image_layers: list[str] = []
    mask_layers: list[str] = []

    for node in workflow.values():
        cls = node.get("class_type", "")
        ins = node.get("inputs", {}) or {}

        if cls == "ETN_Parameter":
            name = ins.get("name", "")
            ptype = ins.get("type", "auto")
            default = ins.get("default", "")
            if name:
                params[name] = (ptype, default)
        elif cls == "ETN_KritaCanvas":
            has_canvas = True
        elif cls == "ETN_KritaSelection":
            selection = (
                ins.get("context", "entire_image"),
                int(ins.get("padding", 0) or 0),
            )
        elif cls == "ETN_KritaImageLayer":
            n = ins.get("name", "")
            if n:
                image_layers.append(str(n))
        elif cls == "ETN_KritaMaskLayer":
            n = ins.get("name", "")
            if n:
                mask_layers.append(str(n))

    return {
        "params": params,
        "has_canvas": has_canvas,
        "selection": selection,
        "image_layers": sorted(set(image_layers)),
        "mask_layers": sorted(set(mask_layers)),
    }


# ============================================================================
#  Native-v2 interface extraction (used by template rendering)
# ============================================================================

def extract_native_workflow(workflow: dict) -> dict:
    """Walk the graph for native slots: @-names, Primitive titles,
    SaveImage nodes. Mirrors the daemon-side `_extract_*` functions in
    comfyui_inspect.py, kept local so the CLI has no import dependency
    on the daemon package.
    """
    resource_names: list[str] = []
    resource_kinds: dict[str, str] = {}
    param_titles: dict[str, str] = {}
    output_node_ids: list[str] = []

    _RESOURCE_CLASSES = {"LoadImage": "image", "LoadImageMask": "mask"}
    _PARAM_CLASSES = {
        "PrimitiveFloat": "float",
        "PrimitiveInt": "int",
        "PrimitiveString": "string",
        "PrimitiveBoolean": "boolean",
    }

    for node in workflow.values():
        cls = node.get("class_type", "")
        ins = node.get("inputs", {}) or {}

        kind = _RESOURCE_CLASSES.get(cls)
        if kind:
            val = ins.get("image")
            if isinstance(val, str) and val.startswith("@"):
                resource_names.append(val)
                resource_kinds[val] = kind
            continue

        pkind = _PARAM_CLASSES.get(cls)
        if pkind:
            title = (node.get("_meta") or {}).get("title", "")
            if isinstance(title, str) and title.strip():
                param_titles[title] = pkind
            continue

        if cls == "SaveImage":
            # Node id is a dict key in the API format; but we're
            # iterating values here. Look up by class-specific default
            # inputs for the template — we don't need node ids for
            # rendering, just the fact that SaveImage exists.
            output_node_ids.append(cls)

    return {
        "resource_names": sorted(set(resource_names)),
        "resource_kinds": resource_kinds,
        "param_titles": param_titles,
        "has_output_node": "SaveImage" in output_node_ids,
    }


# ============================================================================
#  Template blocks
# ============================================================================

def _params_block(params: dict,
                  heading: str = "ETN_Parameter values, keyed by each "
                                 "node's `name` widget.") -> list[str]:
    lines = [f"# {heading}", "[params]"]
    if params:
        for name, (ptype, default) in params.items():
            suffix = f"   # {ptype}" if ptype and ptype != "auto" else ""
            lines.append(f"{_toml_key(name)} = {_toml_value(default)}{suffix}")
    else:
        lines.append("# (no ETN_Parameter nodes in this workflow)")
    return lines


def _layer_aliases_block(image_layers: list[str],
                         mask_layers: list[str]) -> list[str]:
    lines = [
        "# ---- Layer aliases ----------------------------------------",
        "#",
        "# Keys are the layer names the workflow declares. Values are the",
        "# layers the daemon reads from your document. Change a value to",
        "# use a document layer with a different name; the key stays as",
        "# the workflow wrote it.",
        "#",
    ]
    entries: list[tuple[str, str]] = []
    for name in mask_layers:
        entries.append((name, "ETN_KritaMaskLayer"))
    for name in image_layers:
        entries.append((name, "ETN_KritaImageLayer"))

    if not entries:
        lines.append("# This workflow does not reference named layers.")
        return lines

    lines.append("[layer_aliases]")
    for name, kind in entries:
        lines.append(
            f"{_toml_key(name)} = {_toml_value(name)}   # {kind}"
        )
    return lines


def _canvas_selection_block(info: dict) -> list[str]:
    has_canvas = info.get("has_canvas", False)
    has_selection = info.get("has_selection", False)
    sel_context = info.get("selection_context") or "entire_image"
    sel_padding = info.get("selection_padding", 0)

    lines = ["# ---- Canvas and selection --------------------------------",
             "#"]
    if has_canvas:
        lines.append("# This workflow uses ETN_KritaCanvas. The daemon "
                     "reads the")
        lines.append("# selected source below and ships it as the canvas "
                     "image.")
    else:
        lines.append("# This workflow does not use ETN_KritaCanvas.")
    if has_selection:
        lines.append(f"# It also uses ETN_KritaSelection "
                     f"(context=\"{sel_context}\", padding={sel_padding}).")
    else:
        lines.append("# It does not use ETN_KritaSelection.")
    return lines


def _batch_block() -> list[str]:
    return [
        "# ---- Batch generation -------------------------------------",
        "#",
        "# count > 1 runs the workflow that many times, one run per",
        "# consecutive seed, and pastes each result. The workflow must",
        "# declare an ETN_Parameter named exactly 'seed' wired to the",
        "# sampler's seed input, or wire the ETN_KritaCanvas 'seed'",
        "# output; otherwise the daemon clamps count to 1.",
        "#",
        "# seed is the base for a batch: run i uses seed+i. Blank means",
        "# the daemon picks a random base. One history record is written",
        "# per comfy_generate call, regardless of count.",
    ]


def _resources_block(resource_names: list[str],
                     resource_kinds: dict[str, str]) -> list[str]:
    """The `[resources]` section for the native-v2 template."""
    lines = [
        "# ---- Resources -------------------------------------------",
        "#",
        "# @-prefixed values in LoadImage / LoadImageMask widgets are",
        "# substituted at generate time. The daemon reads the document,",
        "# uploads via POST /api/v2/assets, and injects a core/ASSET",
        "# reference into the graph.",
        "#",
        "# Source options:",
        "#   source = 'active'   — composite of all visible layers",
        "#   source = 'global'   — Krita's active selection (needs [crop])",
        "#   source = 'layer'    — one named paint or mask layer",
        "#",
    ]
    if not resource_names:
        lines.append("# This workflow has no @-prefixed resource inputs.")
        return lines
    lines.append("[resources]")
    for name in resource_names:
        kind = resource_kinds.get(name, "image")
        if kind == "mask":
            # Mask-shaped resource: almost always the selection.
            lines.append(
                f'"{name}" = {{ source = "global" }}   '
                f'# mask'
            )
        else:
            lines.append(
                f'"{name}" = {{ source = "active" }}   '
                f'# image'
            )
    return lines


def _crop_block() -> list[str]:
    return [
        "# ---- Crop -----------------------------------------------",
        "#",
        "# How to crop the canvas when a resource has source='global'",
        "# (i.e. reads Krita's selection). 'entire_image' skips cropping;",
        "# 'automatic' crops to the selection bbox + padding;",
        "# 'mask_bounds' crops to the tight selection bbox.",
        "[crop]",
        'context = "entire_image"',
        "padding = 0",
    ]


def _paste_block() -> list[str]:
    return [
        "# ---- Paste ----------------------------------------------",
        "#",
        "# Where and how to paste SaveImage outputs. Matches the old",
        "# ETN_KritaOutput widget values (x, y, name, batch_mode,",
        "# resize_canvas).",
        "[paste]",
        "x = 0",
        "y = 0",
        'name = ""',
        'batch_mode = "default"   # default | layers | animation',
        "resize_canvas = false",
    ]


def _native_params_block(param_titles: dict[str, str]) -> list[str]:
    """The `[params]` section for the native-v2 template."""
    lines = [
        "# ---- Parameters -----------------------------------------",
        "#",
        "# Keys are Primitive*._meta.title values from the loaded",
        "# graph. Set _meta.title on each Primitive node in ComfyUI's",
        "# browser (double-click the node title), then save the graph",
        "# in API format. Type is enforced at comfy_apply_config time.",
        "",
    ]
    if not param_titles:
        lines.append("# This workflow has no titled Primitive nodes.")
        return lines
    lines.append("[params]")
    for title in sorted(param_titles):
        kind = param_titles[title]
        default = _native_param_default(kind)
        lines.append(f"{_toml_key(title)} = {_toml_value(default)}   # {kind}")
    return lines


def _native_param_default(kind: str) -> Any:
    return {"float": 0.0, "int": 0, "string": "", "boolean": False}[kind]


# ============================================================================
#  Template rendering
# ============================================================================

def _render_file_template(workflow_ref: str, workflow: dict) -> str:
    """Render a template for a file-sourced workflow.

    Detects the schema generation from the graph: a graph with no
    `ETN_*` nodes is treated as native-v2 (renders `[resources]` /
    `[crop]` / `[params]` / `[paste]`); otherwise legacy (renders
    `[canvas]` / `[selection]` / `[layer_aliases]` / `[params]` with
    `ETN_Parameter` keys).
    """
    has_etn = any(
        str(node.get("class_type", "")).startswith("ETN_")
        for node in workflow.values()
    )

    if not has_etn:
        return _render_native_file_template(workflow_ref, workflow)

    # Legacy template — unchanged from before Stage 3.
    info = extract_workflow(workflow)

    lines: list[str] = [
        f'host = "127.0.0.1"',
        f'port = 8188',
        f'workflow = "{workflow_ref}"',
        '# exclusive=true means the daemon loads this file and ignores',
        '# any graph concurrently published by a ComfyUI browser tab.',
        '# Without it, a browser republish between load and generate',
        '# silently replaces the file\'s graph. Set to false to follow',
        '# the browser instead.',
        'exclusive = true',
        'watch = true',
        '',
    ]
    lines += _canvas_selection_block({
        "has_canvas": info["has_canvas"],
        "has_selection": info["selection"] is not None,
        "selection_context": (info["selection"][0]
                              if info["selection"] else "entire_image"),
        "selection_padding": (info["selection"][1]
                              if info["selection"] else 0),
    })
    lines.append('canvas = "active"    # active | ["layer", "Name"]')
    if info["selection"]:
        lines.append('# selection = "global"     # see notes above')
        lines.append('# selection = ["layer", "areaToRMBG", '
                     '"mask_bounds", 0]')
    else:
        lines.append('# selection = "global"     # workflow does not use '
                     'ETN_KritaSelection')
    lines.append('')
    lines.append('timeout = 55.0')
    lines.append('output_name = ""    # paste only this ETN_KritaOutput '
                 'node; blank = all')
    lines.append('')
    lines += _batch_block()
    lines.append('count = 1')
    lines.append('seed = ""          # base seed for a batch; blank = auto')
    lines.append('')
    lines += _layer_aliases_block(info["image_layers"],
                                  info["mask_layers"])
    lines.append('')
    lines += _params_block(info["params"])
    lines.append('')
    lines.append("# To bypass the settings above and write the full chain "
                 "yourself,")
    lines.append("# delete everything and use an array of inline tables:")
    lines.append("#")
    lines.append("# commands = [")
    lines.append("#     { type = \"comfy_connect\", host = \"127.0.0.1\", "
                 "port = 8188, check = true, watch = true },")
    lines.append(f"#     {{ type = \"comfy_load\", name = "
                 f"\"{workflow_ref}\", exclusive = true }},")
    lines.append("#     { type = \"comfy_bind_layer_alias\", "
                 "logical_name = \"areaToRMBG\", "
                 "actual_name = \"face\" },")
    lines.append("#     { type = \"comfy_bind_canvas\", source = "
                 "\"active\" },")
    lines.append("#     { type = \"comfy_set_param\", name = "
                 "\"segmentFurther\", value = \"person\" },")
    lines.append("#     { type = \"comfy_generate\", timeout = 55.0, "
                 "count = 3 },")
    lines.append("# ]")

    return "\n".join(lines) + "\n"


def _render_native_file_template(workflow_ref: str, workflow: dict) -> str:
    """Render a template for a native-v2 workflow (no ETN_* nodes)."""
    info = extract_native_workflow(workflow)

    lines: list[str] = [
        f'host = "127.0.0.1"',
        f'port = 8188',
        f'workflow = "{workflow_ref}"',
        '# exclusive=true means the daemon loads this file and ignores',
        '# any graph concurrently published by a ComfyUI browser tab.',
        'exclusive = true',
        'watch = true',
        '',
        '# ---- Batch generation -----------------------------------',
        'timeout = 55.0',
        'count = 1',
        'seed = ""          # base seed for a batch; blank = auto',
        '',
    ]
    lines += _resources_block(info["resource_names"],
                              info["resource_kinds"])
    lines.append('')
    lines += _crop_block()
    lines.append('')
    lines += _native_params_block(info["param_titles"])
    lines.append('')
    lines += _paste_block()
    return "\n".join(lines) + "\n"


def _render_browser_template_body(status: dict,
                                  signature: Optional[str]) -> list[str]:
    params_list = status.get("workflow_parameters") or []
    params: dict = {}
    for entry in params_list:
        name = entry.get("name")
        if not name:
            continue
        params[str(name)] = (
            str(entry.get("type", "auto")),
            entry.get("default", ""),
        )

    layer_reqs = status.get("workflow_layer_requirements") or {}
    image_layers = list(layer_reqs.get("image_layers") or [])
    mask_layers = list(layer_reqs.get("mask_layers") or [])
    canvas_info = status.get("workflow_canvas_info") or {}

    lines: list[str] = []
    if signature:
        lines.append(f'signature = "{signature}"')
    lines += [
        'host = "127.0.0.1"',
        'port = 8188',
        "# Empty: the workflow is followed from ComfyUI's browser.",
        'workflow = ""',
        'watch = true',
        '',
    ]
    lines += _canvas_selection_block(canvas_info)
    lines.append('canvas = "active"    # active | ["layer", "Name"]')
    if canvas_info.get("has_selection"):
        lines.append('# selection = "global"     # see notes above')
        lines.append('# selection = ["layer", "areaToRMBG", '
                     '"mask_bounds", 0]')
    else:
        lines.append('# selection = "global"     # workflow does not use '
                     'ETN_KritaSelection')
    lines.append('')
    lines.append('timeout = 55.0')
    lines.append('output_name = ""    # paste only this ETN_KritaOutput '
                 'node; blank = all')
    lines.append('')
    lines += _batch_block()
    lines.append('count = 1')
    lines.append('seed = ""          # base seed for a batch; blank = auto')
    lines.append('')
    lines += _layer_aliases_block(image_layers, mask_layers)
    lines.append('')
    lines += _params_block(params)
    return lines


def build_file_template(workflow_ref: str) -> Optional[str]:
    """Read a workflow JSON and render the template string."""
    if _looks_like_path(workflow_ref):
        path = Path(workflow_ref).expanduser()
        if not path.is_file():
            print(f"Error: workflow file not found: {path}",
                  file=sys.stderr)
            return None
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            pass
        toml_ref = str(path)
    else:
        path = WORKFLOW_DIR / f"{workflow_ref}.json"
        if not path.is_file():
            print(f"Error: workflow file not found: {path}",
                  file=sys.stderr)
            if WORKFLOW_DIR.is_dir():
                avail = sorted(p.stem for p in WORKFLOW_DIR.glob("*.json"))
                if avail:
                    print(f"Available: {', '.join(avail)}",
                          file=sys.stderr)
            return None
        toml_ref = workflow_ref

    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: {path} is not valid JSON: {e}", file=sys.stderr)
        return None

    if "nodes" in workflow or "links" in workflow:
        print(f"Error: {path} is in ComfyUI UI format; export as API "
              f"format, or run `kritomatic comfyui convert` on it first.",
              file=sys.stderr)
        return None

    return _render_file_template(toml_ref, workflow)


def build_browser_template(status: dict,
                           signature: Optional[str]) -> str:
    lines = _render_browser_template_body(status, signature)
    lines.append('')
    lines.append("# This config was generated from a graph published by")
    lines.append("# ComfyUI's browser. It is keyed by the signature above, "
                 "so")
    lines.append("# reloading the same workflow in the browser will find "
                 "this")
    lines.append("# file again.")
    return "\n".join(lines) + "\n"


# ============================================================================
#  File system helpers
# ============================================================================

def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


def existing_configs() -> list[Path]:
    if not CONFIG_DIR.is_dir():
        return []
    return sorted(CONFIG_DIR.glob("*.toml"))


def find_config_by_signature(sig: str) -> Optional[Path]:
    if not sig or not CONFIG_DIR.is_dir():
        return None
    for p in sorted(CONFIG_DIR.glob("*.toml")):
        try:
            with p.open("rb") as f:
                cfg = tomllib.load(f)
        except Exception:
            continue
        if cfg.get("signature") == sig:
            return p
    return None


# ============================================================================
#  Legacy command building (unchanged)
# ============================================================================

def _stringify(v) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return str(v)


def _canvas_cmd(canvas) -> dict:
    if isinstance(canvas, str):
        return {"type": "comfy_bind_canvas", "source": canvas}
    if isinstance(canvas, list) and len(canvas) == 2:
        return {"type": "comfy_bind_canvas",
                "source": str(canvas[0]), "layer_name": str(canvas[1])}
    raise ValueError(f"canvas must be a string, a 2-element list, or "
                     f"None; got {canvas!r}")


def _selection_cmd(selection) -> dict:
    if isinstance(selection, str):
        return {"type": "comfy_bind_selection", "source": selection}
    if isinstance(selection, list) and len(selection) == 4:
        return {"type": "comfy_bind_selection",
                "source": str(selection[0]), "layer_name": str(selection[1]),
                "context": str(selection[2]), "padding": int(selection[3])}
    raise ValueError(
        f"selection must be a string, a 4-element list, or None; "
        f"got {selection!r}"
    )


def build_commands(cfg: dict) -> list:
    """Legacy path: build the batch for an ETN_* workflow."""
    if "commands" in cfg:
        cmds = cfg["commands"]
        if not isinstance(cmds, list):
            raise ValueError("commands must be an array of tables")
        for i, c in enumerate(cmds):
            if not isinstance(c, dict) or "type" not in c:
                raise ValueError(f"commands[{i}] must have a 'type' key")
        return cmds

    watch = bool(cfg.get("watch", True))

    cmds: list = [
        {"type": "comfy_connect",
         "host": str(cfg.get("host", "127.0.0.1")),
         "port": int(cfg.get("port", 8188)),
         "check": True,
         "watch": watch},
    ]

    workflow = cfg.get("workflow") or ""
    if workflow:
        load_cmd: dict = {"type": "comfy_load", "name": str(workflow)}
        if bool(cfg.get("exclusive", True)):
            load_cmd["exclusive"] = True
        cmds.append(load_cmd)

    for logical, actual in (cfg.get("layer_aliases") or {}).items():
        cmds.append({
            "type": "comfy_bind_layer_alias",
            "logical_name": str(logical),
            "actual_name": str(actual),
        })

    canvas = cfg.get("canvas", "active")
    if canvas is not None:
        cmds.append(_canvas_cmd(canvas))

    selection = cfg.get("selection", None)
    if selection is not None:
        cmds.append(_selection_cmd(selection))

    for name, value in (cfg.get("params") or {}).items():
        cmds.append({
            "type": "comfy_set_param",
            "name": name,
            "value": _stringify(value),
        })

    generate_cmd: dict = {
        "type": "comfy_generate",
        "timeout": float(cfg.get("timeout", 55.0)),
    }
    output_name = cfg.get("output_name") or ""
    if output_name:
        generate_cmd["output_name"] = str(output_name)

    count_val = cfg.get("count", 1)
    try:
        count = int(count_val)
    except (TypeError, ValueError):
        count = 1
    if count > 1:
        generate_cmd["count"] = count

    seed_val = cfg.get("seed", "")
    if seed_val not in ("", None):
        try:
            generate_cmd["seed"] = int(seed_val)
        except (TypeError, ValueError):
            raise ValueError(
                f"seed must be an integer or blank, got {seed_val!r}"
            )

    cmds.append(generate_cmd)
    return cmds


# ============================================================================
#  Native-v2: PanelConfig + parse_panel_config + build_batch (Stage 3)
# ============================================================================

@dataclass
class ResourceSpec:
    source: str  # "active" | "global" | "layer"
    layer_name: Optional[str] = None


@dataclass
class PasteSpec:
    x: int = 0
    y: int = 0
    name: str = ""
    batch_mode: str = "default"
    resize_canvas: bool = False


@dataclass
class PanelConfig:
    """Parsed native-v2 panel config.

    Populated by `parse_panel_config(path)`. Structural validation only —
    graph-aware validation (does this @-name exist in the workflow?)
    happens daemon-side at `comfy_apply_config` time.
    """
    host: str
    port: int
    workflow: str
    exclusive: bool
    watch: bool
    timeout: float
    count: int
    seed: Optional[int]
    resources: dict[str, ResourceSpec] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    crop_context: str = "entire_image"
    crop_padding: int = 0
    paste: PasteSpec = field(default_factory=PasteSpec)
    # Set when parsing a browser-sourced config; None for file-sourced.
    signature: Optional[str] = None


def parse_panel_config(path: Path) -> PanelConfig:
    """Read a TOML panel config and return a `PanelConfig`.

    Structural validation only: raises ValueError on malformed sections
    or invalid enum values. Graph-aware validation happens later, when
    `comfy_apply_config` is called against a loaded workflow.

    Old-style configs (no `[resources]` / `[crop]` / `[paste]`) parse
    cleanly with empty `resources`/`params` — the config then validates
    against a native workflow that has no slots. That is not what the
    old-style config authors meant, but it is not this function's job
    to guess: the CLI decides old-vs-new based on the workflow's node
    classes (see `cmd_run`), and old workflows keep using
    `build_commands`, never this function.
    """
    try:
        cfg = load_config(path)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: invalid TOML: {e}") from e

    # -- core fields --
    try:
        host = str(cfg.get("host", "127.0.0.1"))
        port = int(cfg.get("port", 8188))
        workflow = str(cfg.get("workflow", ""))
        exclusive = bool(cfg.get("exclusive", True))
        watch = bool(cfg.get("watch", True))
        timeout = float(cfg.get("timeout", 55.0))
        count = int(cfg.get("count", 1))
    except (TypeError, ValueError) as e:
        raise ValueError(f"{path}: invalid core field: {e}") from e

    if count < 1:
        raise ValueError(f"{path}: count must be >= 1, got {count}")
    if timeout <= 0:
        raise ValueError(f"{path}: timeout must be > 0, got {timeout}")

    seed: Optional[int] = None
    seed_raw = cfg.get("seed", "")
    if seed_raw not in ("", None):
        try:
            seed = int(seed_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"{path}: seed must be an integer or blank, got "
                f"{seed_raw!r}"
            )

    signature = cfg.get("signature")
    if signature is not None:
        signature = str(signature)

    # -- [resources] --
    resources: dict[str, ResourceSpec] = {}
    raw_resources = cfg.get("resources", {}) or {}
    if not isinstance(raw_resources, dict):
        raise ValueError(f"{path}: [resources] must be a table")
    for name, spec in raw_resources.items():
        if not isinstance(name, str) or not name.startswith("@"):
            raise ValueError(
                f"{path}: [resources] key {name!r} must start with '@'"
            )
        if not isinstance(spec, dict):
            raise ValueError(
                f"{path}: [resources].{name} must be a table with a "
                f"'source' field"
            )
        source = spec.get("source")
        if source not in ("active", "global", "layer"):
            raise ValueError(
                f"{path}: [resources].{name}.source must be "
                f"active/global/layer, got {source!r}"
            )
        layer_name = spec.get("layer_name")
        if source == "layer":
            if not isinstance(layer_name, str) or not layer_name:
                raise ValueError(
                    f"{path}: [resources].{name}.layer_name is "
                    f"required when source='layer'"
                )
        else:
            layer_name = None
        resources[name] = ResourceSpec(source=source,
                                       layer_name=layer_name)

    # -- [params] --
    params: dict[str, Any] = dict(cfg.get("params") or {})
    if not isinstance(params, dict):
        raise ValueError(f"{path}: [params] must be a table")

    # -- [crop] --
    crop = cfg.get("crop", {}) or {}
    if not isinstance(crop, dict):
        raise ValueError(f"{path}: [crop] must be a table")
    crop_context = str(crop.get("context", "entire_image"))
    if crop_context not in ("entire_image", "automatic", "mask_bounds"):
        raise ValueError(
            f"{path}: [crop].context must be entire_image/automatic/"
            f"mask_bounds, got {crop_context!r}"
        )
    try:
        crop_padding = int(crop.get("padding", 0))
    except (TypeError, ValueError):
        raise ValueError(
            f"{path}: [crop].padding must be an integer, got "
            f"{crop.get('padding')!r}"
        )
    if crop_padding < 0:
        raise ValueError(
            f"{path}: [crop].padding must be >= 0, got {crop_padding}"
        )

    # -- [paste] --
    paste_cfg = cfg.get("paste", {}) or {}
    if not isinstance(paste_cfg, dict):
        raise ValueError(f"{path}: [paste] must be a table")
    batch_mode = str(paste_cfg.get("batch_mode", "default"))
    if batch_mode not in ("default", "layers", "animation"):
        raise ValueError(
            f"{path}: [paste].batch_mode must be default/layers/animation, "
            f"got {batch_mode!r}"
        )
    try:
        paste = PasteSpec(
            x=int(paste_cfg.get("x", 0)),
            y=int(paste_cfg.get("y", 0)),
            name=str(paste_cfg.get("name") or ""),
            batch_mode=batch_mode,
            resize_canvas=bool(paste_cfg.get("resize_canvas", False)),
        )
    except (TypeError, ValueError) as e:
        raise ValueError(f"{path}: invalid [paste] field: {e}") from e

    return PanelConfig(
        host=host,
        port=port,
        workflow=workflow,
        exclusive=exclusive,
        watch=watch,
        timeout=timeout,
        count=count,
        seed=seed,
        resources=resources,
        params=params,
        crop_context=crop_context,
        crop_padding=crop_padding,
        paste=paste,
        signature=signature,
    )


def build_batch(config: PanelConfig) -> dict:
    """Build the batch the daemon consumes for a native-v2 workflow.

    Produces:

        comfy_connect       — establish connection, start proxy if needed
        comfy_load          — load the workflow graph
        comfy_apply_config  — validate + store resources/params/crop/paste
        comfy_generate      — run the pipeline (Stage 5 consumes the config)

    The `comfy_apply_config` args are JSON strings because the daemon's
    dispatch hands strings to the handler (they are typed `str` in the
    schema for argparse compatibility). The handler parses them and
    rejects on any structural problem, so the daemon is still the
    authority for graph-aware validation.
    """
    resources_json = json.dumps({
        name: {
            "source": spec.source,
            **({"layer_name": spec.layer_name}
               if spec.layer_name else {}),
        }
        for name, spec in config.resources.items()
    })
    params_json = json.dumps(config.params)
    crop_json = json.dumps({
        "context": config.crop_context,
        "padding": config.crop_padding,
    })
    paste_json = json.dumps({
        "x": config.paste.x,
        "y": config.paste.y,
        "name": config.paste.name,
        "batch_mode": config.paste.batch_mode,
        "resize_canvas": config.paste.resize_canvas,
    })

    load_cmd: dict = {"type": "comfy_load", "name": config.workflow}
    if config.exclusive:
        load_cmd["exclusive"] = True

    generate_cmd: dict = {
        "type": "comfy_generate",
        "timeout": config.timeout,
    }
    if config.count > 1:
        generate_cmd["count"] = config.count
    if config.seed is not None:
        generate_cmd["seed"] = config.seed

    return {
        "id": "comfy_panel_v2",
        "commands": [
            {"type": "comfy_connect",
             "host": config.host,
             "port": config.port,
             "check": True,
             "watch": config.watch},
            load_cmd,
            {"type": "comfy_apply_config",
             "resources": resources_json,
             "params": params_json,
             "crop": crop_json,
             "paste": paste_json},
            generate_cmd,
        ],
    }


# ============================================================================
#  Presets
# ============================================================================

_PRESETTABLE_SECTIONS = ("params", "layer_aliases",
                         "resources", "crop", "paste")


def list_presets() -> list[str]:
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.toml"))


def resolve_preset_path(name: str) -> Optional[Path]:
    if not name:
        print("Preset name is required.", file=sys.stderr)
        return None
    if "/" in name or "\\" in name:
        print(f"Preset name must be a bare filename, not a path: "
              f"{name!r}", file=sys.stderr)
        print(f"Presets live in {PRESET_DIR}.", file=sys.stderr)
        return None
    if not name.endswith(".toml"):
        name = name + ".toml"
    path = PRESET_DIR / name
    if not path.is_file():
        avail = list_presets()
        msg = f"Preset {name!r} not found in {PRESET_DIR}."
        if avail:
            msg += "\nAvailable presets: " + ", ".join(avail)
        print(msg, file=sys.stderr)
        return None
    return path


def apply_preset(preset_path: Path, config_path: Path,
                 dry_run: bool = False) -> int:
    """Splice a preset's changes into a config file.

    Extended in Stage 3 to also handle `[resources]` / `[crop]` /
    `[paste]` sections. Same semantics as before: values spliced
    section-by-section, unknown keys skipped, `[delete]` removes by
    name, comments and ordering preserved via tomlkit.
    """
    try:
        preset = tomlkit.parse(preset_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Preset {preset_path} is not valid TOML: {e}",
              file=sys.stderr)
        return 1

    try:
        config = tomlkit.parse(config_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"Config {config_path} is not valid TOML: {e}",
              file=sys.stderr)
        return 1

    applied: list[str] = []
    skipped: list[str] = []
    deleted: list[str] = []
    delete_skipped: list[str] = []

    for section_name in _PRESETTABLE_SECTIONS:
        if section_name not in preset:
            continue
        section = preset[section_name]
        try:
            section_keys = list(section.keys())
        except AttributeError:
            continue
        if section_name not in config:
            for key in section_keys:
                skipped.append(f"{section_name}.{key}")
            continue
        target = config[section_name]
        for key in section_keys:
            if key in target:
                target[key] = section[key]
                applied.append(f"{section_name}.{key}")
            else:
                skipped.append(f"{section_name}.{key}")

    if "delete" in preset:
        delete_section = preset["delete"]
        try:
            delete_sections = list(delete_section.keys())
        except AttributeError:
            delete_sections = []
        for section_name in delete_sections:
            if section_name not in _PRESETTABLE_SECTIONS:
                continue
            keys = delete_section[section_name]
            if not isinstance(keys, list):
                continue
            if section_name not in config:
                for key in keys:
                    delete_skipped.append(f"{section_name}.{key}")
                continue
            target = config[section_name]
            for key in keys:
                key_str = str(key)
                if key_str in target:
                    del target[key_str]
                    deleted.append(f"{section_name}.{key_str}")
                else:
                    delete_skipped.append(f"{section_name}.{key_str}")

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
    if delete_skipped:
        print(f"Delete skipped ({len(delete_skipped)}) — not present in "
              f"target:")
        for k in delete_skipped:
            print(f"  {k}")

    if not (applied or deleted):
        print()
        print("This preset matches nothing in the target config.")
        print("Check that it is meant for this workflow.")

    if dry_run:
        print()
        print("(dry run; file not modified)")
        return 0

    config_path.write_text(tomlkit.dumps(config), encoding="utf-8")
    print()
    print(f"Wrote {config_path}")
    return 0