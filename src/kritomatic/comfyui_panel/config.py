"""
config.py — panel config file manipulation.

Pure helpers: TOML rendering, workflow-interface extraction, config
resolution, preset application, and parsing of the `[resources]` /
`[params]` / `[crop]` / `[paste]` schema into a typed `PanelConfig`.

No daemon or network here; `cli.py` supplies the runtime around these
functions.

Since Stage 7 all supported workflows are native-v2 (no ETN_* nodes).
The legacy `build_commands` / `extract_workflow` helpers are gone; a
config either parses as `PanelConfig` or it doesn't.
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
#  Workflow interface extraction
# ============================================================================

def extract_native_workflow(workflow: dict) -> dict:
    """Walk the graph for native slots: @-names, Primitive titles,
    SaveImage nodes. Mirrors the daemon-side extractors in
    comfyui_inspect.py, kept local so the CLI has no import dependency
    on the daemon package.
    """
    resource_names: list[str] = []
    resource_kinds: dict[str, str] = {}
    param_titles: dict[str, str] = {}
    has_output_node = False

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
            has_output_node = True

    return {
        "resource_names": sorted(set(resource_names)),
        "resource_kinds": resource_kinds,
        "param_titles": param_titles,
        "has_output_node": has_output_node,
    }


# ============================================================================
#  Template blocks
# ============================================================================

def _resources_block(resource_names: list[str],
                     resource_kinds: dict[str, str]) -> list[str]:
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
            lines.append(
                f'"{name}" = {{ source = "global" }}   # mask'
            )
        else:
            lines.append(
                f'"{name}" = {{ source = "active" }}   # image'
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
        "# Where and how to paste SaveImage outputs.",
        "[paste]",
        "x = 0",
        "y = 0",
        'name = ""',
        'batch_mode = "default"   # default | layers | animation',
        "resize_canvas = false",
    ]


def _native_params_block(param_titles: dict[str, str]) -> list[str]:
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
        default = {"float": 0.0, "int": 0, "string": "", "boolean": False}[kind]
        lines.append(f"{_toml_key(title)} = {_toml_value(default)}   # {kind}")
    return lines


# ============================================================================
#  Template rendering
# ============================================================================

def _render_file_template(workflow_ref: str, workflow: dict) -> str:
    """Render a template for a native-v2 workflow.

    A legacy ETN_* workflow would produce empty slot sections; since
    Stage 7 the daemon refuses to load such graphs (no `expand_custom`),
    so the template doesn't attempt to distinguish.
    """
    info = extract_native_workflow(workflow)

    lines: list[str] = [
        f'host = "127.0.0.1"',
        f'port = 8188',
        f'workflow = "{workflow_ref}"',
        '# exclusive=true keeps the graph pinned even though browser',
        '# publishing no longer exists after Stage 6; kept for clarity.',
        'exclusive = true',
        'watch = false',
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


# ============================================================================
#  PanelConfig + parse_panel_config + build_batch
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


def parse_panel_config(path: Path) -> PanelConfig:
    """Read a TOML panel config and return a `PanelConfig`.

    Structural validation only. Graph-aware validation happens later,
    when `comfy_apply_config` is called against a loaded workflow.
    """
    try:
        cfg = load_config(path)
    except tomllib.TOMLDecodeError as e:
        raise ValueError(f"{path}: invalid TOML: {e}") from e

    try:
        host = str(cfg.get("host", "127.0.0.1"))
        port = int(cfg.get("port", 8188))
        workflow = str(cfg.get("workflow", ""))
        exclusive = bool(cfg.get("exclusive", True))
        watch = bool(cfg.get("watch", False))
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

    params: dict[str, Any] = dict(cfg.get("params") or {})
    if not isinstance(params, dict):
        raise ValueError(f"{path}: [params] must be a table")

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
    )


def build_batch(config: PanelConfig) -> dict:
    """Build the batch the daemon consumes for a native-v2 workflow."""
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
             "check": True},
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

    Handles `[params]`, `[resources]`, `[crop]`, `[paste]`, and legacy
    `[layer_aliases]` (harmless no-op now that the pipeline ignores it).
    Same semantics: values spliced section-by-section, unknown keys
    skipped, `[delete]` removes by name, comments preserved via tomlkit.
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
