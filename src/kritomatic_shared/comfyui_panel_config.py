"""
comfyui_panel_config.py — pure logic for the panel's per-workflow config.

Shared between the CLI (`kritomatic comfyui panel ...`) and the daemon
(autoload on `comfy_load`, save via `comfy_save_config`). No file I/O
beyond `read_config_file`/`write_config_file`, no third-party deps,
stdlib `json` only.

Why JSON instead of TOML
========================
The daemon runs in Krita's bundled Python, which does not ship
`tomllib` (3.11+) nor have `tomlkit` installed. Requiring downstream
users to `pip install --target ~/.local/share/krita/pykrita tomlkit`
before the panel works is the kind of friction that makes "install
kritomatic and have it work" false. `json` is stdlib everywhere.

The `_notes` field in generated templates is the replacement for
TOML's comments: a top-level list of strings that nothing reads but
humans can edit. Not as ergonomic as `# ...` on every line, but
survives every round-trip and doesn't need a comment-preserving
parser.

Schema
======
    {
      "host": "127.0.0.1",
      "port": 8188,
      "workflow": "NEWTekakutli54",
      "exclusive": true,
      "watch": false,
      "timeout": 55.0,
      "count": 1,
      "seed": null,
      "resources": {
        "@thecanvas": {"source": "layer", "layer_name": "thecanvas"},
        ...
      },
      "params": {"prompt": "...", "denoise": 0.7, ...},
      "crop": {"context": "entire_image", "padding": 0},
      "paste": {"x": 0, "y": 0, "name": "", "batch_mode": "default",
                "resize_canvas": false},
      "_notes": ["..."]
    }

`host`/`port`/`workflow`/`exclusive`/`watch`/`timeout`/`count`/`seed`
are CLI-only: `panel run` uses them for the batch's connection and
generate commands. The panel itself only reads/writes
`resources`/`params`/`crop`/`paste`, and only those are autoloaded on
`comfy_load`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# ============================================================================
#  Paths
# ============================================================================

# <repo>/src/kritomatic_shared/comfyui_panel_config.py
#   .parent          → .../kritomatic_shared
#   .parent.parent   → .../src
#   .parent.parent.parent → <repo>
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent

PANEL_DIR = _REPO_ROOT / "data" / "comfy_panel"
WORKFLOW_DIR = PANEL_DIR / "workflows"
CONFIG_DIR = PANEL_DIR / "panel_configs"
PRESET_DIR = PANEL_DIR / "presets"


# ============================================================================
#  Dataclasses
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

    Populated by `parse_config_dict(dict)`. Structural validation
    only — graph-aware validation (does this @-name exist in the
    workflow?) happens daemon-side at `comfy_apply_config` time.
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
#  File I/O
# ============================================================================

def read_config_file(path: Path) -> dict:
    """Read a JSON panel config file. Raises ValueError on failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"{path}: invalid JSON: {e}") from e
    except OSError as e:
        raise ValueError(f"Could not read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(
            f"{path}: expected a JSON object, got {type(data).__name__}"
        )
    return data


def write_config_file(path: Path, data: dict) -> None:
    """Write a JSON panel config file atomically, creating parent dirs.

    Writes to a sibling temp file, fsyncs, then os.replace()s onto the
    target.  os.replace is atomic on POSIX (guaranteed same filesystem
    by the sibling temp file) and on Windows.  A crash, full disk, or
    Ctrl-C mid-write leaves the original file intact — never a
    half-written JSON that fails to parse on the next comfy_load
    autoload.

    The original implementation used path.write_text, which truncates
    the target first and writes second; a crash between those two steps
    could leave the user's saved config at zero bytes.  This is the
    standard write-to-temp-then-rename pattern.
    """
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None
    finally:
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ============================================================================
#  Parsing / validation
# ============================================================================

def parse_config_dict(data: dict) -> PanelConfig:
    """Parse a dict (already loaded from JSON) into a PanelConfig.

    Structural validation only. Graph-aware validation happens later,
    when `comfy_apply_config` is called against a loaded workflow.
    Raises ValueError on any structural problem.
    """
    if not isinstance(data, dict):
        raise ValueError(
            f"config must be a JSON object, got {type(data).__name__}"
        )

    try:
        host = str(data.get("host", "127.0.0.1"))
        port = int(data.get("port", 8188))
        workflow = str(data.get("workflow", ""))
        exclusive = bool(data.get("exclusive", True))
        watch = bool(data.get("watch", False))
        timeout = float(data.get("timeout", 55.0))
        count = int(data.get("count", 1))
    except (TypeError, ValueError) as e:
        raise ValueError(f"invalid core field: {e}") from e

    if count < 1:
        raise ValueError(f"count must be >= 1, got {count}")
    if timeout <= 0:
        raise ValueError(f"timeout must be > 0, got {timeout}")

    seed: Optional[int] = None
    seed_raw = data.get("seed")
    if seed_raw not in (None, ""):
        try:
            seed = int(seed_raw)
        except (TypeError, ValueError):
            raise ValueError(
                f"seed must be an integer or null, got {seed_raw!r}"
            )

    resources: dict[str, ResourceSpec] = {}
    raw_resources = data.get("resources", {}) or {}
    if not isinstance(raw_resources, dict):
        raise ValueError("'resources' must be an object")
    for name, spec in raw_resources.items():
        if not isinstance(name, str) or not name.startswith("@"):
            raise ValueError(
                f"resource key {name!r} must start with '@'"
            )
        if not isinstance(spec, dict):
            raise ValueError(
                f"resource {name!r} must be an object with a 'source' field"
            )
        source = spec.get("source")
        if source not in ("active", "global", "layer"):
            raise ValueError(
                f"resource {name!r}.source must be active/global/layer, "
                f"got {source!r}"
            )
        layer_name = spec.get("layer_name")
        if source == "layer":
            if not isinstance(layer_name, str) or not layer_name:
                raise ValueError(
                    f"resource {name!r}.layer_name is required when "
                    f"source='layer'"
                )
        else:
            layer_name = None
        resources[name] = ResourceSpec(source=source, layer_name=layer_name)

    params: dict[str, Any] = dict(data.get("params") or {})
    if not isinstance(params, dict):
        raise ValueError("'params' must be an object")

    crop = data.get("crop", {}) or {}
    if not isinstance(crop, dict):
        raise ValueError("'crop' must be an object")
    crop_context = str(crop.get("context", "entire_image"))
    if crop_context not in ("entire_image", "automatic", "mask_bounds"):
        raise ValueError(
            f"crop.context must be entire_image/automatic/mask_bounds, "
            f"got {crop_context!r}"
        )
    try:
        crop_padding = int(crop.get("padding", 0))
    except (TypeError, ValueError):
        raise ValueError(
            f"crop.padding must be an integer, got {crop.get('padding')!r}"
        )
    if crop_padding < 0:
        raise ValueError(
            f"crop.padding must be >= 0, got {crop_padding}"
        )

    paste_cfg = data.get("paste", {}) or {}
    if not isinstance(paste_cfg, dict):
        raise ValueError("'paste' must be an object")
    batch_mode = str(paste_cfg.get("batch_mode", "default"))
    if batch_mode not in ("default", "layers", "animation"):
        raise ValueError(
            f"paste.batch_mode must be default/layers/animation, "
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
        raise ValueError(f"invalid 'paste' field: {e}") from e

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


def parse_config_file(path: Path) -> PanelConfig:
    """Read + parse a config file in one call. Raises ValueError."""
    return parse_config_dict(read_config_file(path))


# ============================================================================
#  Serialization
# ============================================================================

def serialize_config(cfg: PanelConfig) -> dict:
    """Convert a PanelConfig back to a JSON-serializable dict."""
    return {
        "host": cfg.host,
        "port": cfg.port,
        "workflow": cfg.workflow,
        "exclusive": cfg.exclusive,
        "watch": cfg.watch,
        "timeout": cfg.timeout,
        "count": cfg.count,
        "seed": cfg.seed,
        "resources": {
            name: {
                "source": spec.source,
                **({"layer_name": spec.layer_name}
                   if spec.layer_name else {}),
            }
            for name, spec in cfg.resources.items()
        },
        "params": dict(cfg.params),
        "crop": {
            "context": cfg.crop_context,
            "padding": cfg.crop_padding,
        },
        "paste": {
            "x": cfg.paste.x,
            "y": cfg.paste.y,
            "name": cfg.paste.name,
            "batch_mode": cfg.paste.batch_mode,
            "resize_canvas": cfg.paste.resize_canvas,
        },
    }


# ============================================================================
#  Workflow interface extraction
# ============================================================================

def extract_native_workflow(workflow: dict) -> dict:
    """Walk the graph for native slots: @-names, Primitive titles,
    SaveImage nodes.

    Delegates to the three shared extractors in
    `kritomatic_shared.comfyui_inspect` — the same functions the
    daemon's `comfy_load` and the playground's `handle_inpaint` use.
    Keeping the flat-name projection here (rather than the shared
    module) lets the CLI template render without importing node ids,
    which a human-edited template cannot use anyway.

    Raises `ValueError` on a duplicate `@`-name or `_meta.title`,
    matching the daemon's behaviour: a graph the CLI cannot render a
    coherent template for is a graph the daemon will refuse to drive
    via the native pipeline.
    """
    from .comfyui_inspect import (
        extract_output_nodes,
        extract_param_slots,
        extract_resource_slots,
    )

    resources = extract_resource_slots(workflow)
    params = extract_param_slots(workflow)
    outputs = extract_output_nodes(workflow)

    return {
        "resource_names": sorted(resources.keys()),
        "resource_kinds": {
            name: slot["kind"] for name, slot in resources.items()
        },
        "param_titles": {
            title: slot["kind"] for title, slot in params.items()
        },
        "has_output_node": bool(outputs),
    }


# ============================================================================
#  Template rendering
# ============================================================================

def _render_template_data(workflow_ref: str, workflow: dict) -> dict:
    """Build the JSON dict for a fresh template."""
    info = extract_native_workflow(workflow)

    resources: dict[str, dict] = {}
    for name in info["resource_names"]:
        kind = info["resource_kinds"].get(name, "image")
        if kind == "mask":
            resources[name] = {"source": "global"}
        else:
            resources[name] = {"source": "active"}

    params: dict[str, Any] = {}
    for title, kind in info["param_titles"].items():
        default = {"float": 0.0, "int": 0, "string": "", "boolean": False}[kind]
        params[title] = default

    return {
        "_notes": [
            "Autogenerated by `kritomatic comfyui panel reset`.",
            "Resources: source='layer' reads one named layer; "
            "'active' reads the composite; 'global' reads the selection.",
            "Crop: 'entire_image' skips cropping; 'automatic' crops to "
            "selection bbox + padding.",
            "The panel never reads _notes; edit freely.",
        ],
        "host": "127.0.0.1",
        "port": 8188,
        "workflow": workflow_ref,
        "exclusive": True,
        "watch": False,
        "timeout": 55.0,
        "count": 1,
        "seed": None,
        "resources": resources,
        "params": params,
        "crop": {"context": "entire_image", "padding": 0},
        "paste": {
            "x": 0,
            "y": 0,
            "name": "",
            "batch_mode": "default",
            "resize_canvas": False,
        },
    }


def render_template_string(workflow_ref: str, workflow: dict) -> str:
    """Render the full JSON template string for a workflow."""
    return json.dumps(
        _render_template_data(workflow_ref, workflow),
        indent=2,
        ensure_ascii=False,
    ) + "\n"


def build_file_template(workflow_ref: str) -> Optional[str]:
    """Read a workflow JSON and render its template string.

    `workflow_ref` is either a slug (resolved against WORKFLOW_DIR) or
    a path. Returns None and prints an error on failure.
    """
    import sys

    if _looks_like_path(workflow_ref):
        path = Path(workflow_ref).expanduser()
        if not path.is_file():
            print(
                f"Error: workflow file not found: {path}", file=sys.stderr
            )
            return None
        try:
            path = path.resolve()
        except (OSError, RuntimeError):
            pass
        json_ref = str(path)
    else:
        path = WORKFLOW_DIR / f"{workflow_ref}.json"
        if not path.is_file():
            print(
                f"Error: workflow file not found: {path}", file=sys.stderr
            )
            if WORKFLOW_DIR.is_dir():
                avail = sorted(
                    p.stem for p in WORKFLOW_DIR.glob("*.json")
                )
                if avail:
                    print(
                        f"Available: {', '.join(avail)}", file=sys.stderr
                    )
            return None
        json_ref = workflow_ref

    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: {path} is not valid JSON: {e}", file=sys.stderr)
        return None

    if "nodes" in workflow or "links" in workflow:
        print(
            f"Error: {path} is in ComfyUI UI format; export as API "
            f"format, or run `kritomatic comfyui convert` on it first.",
            file=sys.stderr,
        )
        return None

    try:
        return render_template_string(json_ref, workflow)
    except ValueError as e:
        print(f"Error: {path}: {e}", file=sys.stderr)
        return None


# ============================================================================
#  Batch construction (CLI-only; daemon uses comfy_apply_config)
# ============================================================================

def build_batch(config: PanelConfig) -> dict:
    """Build the batch `panel run` sends to the daemon."""
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

_PRESETTABLE_SECTIONS = ("params", "resources", "crop", "paste")


def list_presets() -> list[str]:
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.json"))


def resolve_preset_path(name: str) -> Optional[Path]:
    import sys

    if not name:
        print("Preset name is required.", file=sys.stderr)
        return None
    if "/" in name or "\\" in name:
        print(
            f"Preset name must be a bare filename, not a path: {name!r}",
            file=sys.stderr,
        )
        print(f"Presets live in {PRESET_DIR}.", file=sys.stderr)
        return None
    if not name.endswith(".json"):
        name = name + ".json"
    path = PRESET_DIR / name
    if not path.is_file():
        avail = list_presets()
        msg = f"Preset {name!r} not found in {PRESET_DIR}."
        if avail:
            msg += "\nAvailable presets: " + ", ".join(avail)
        print(msg, file=sys.stderr)
        return None
    return path


def apply_preset_dict(preset: dict, config: dict) -> tuple[list, list, list]:
    """Splice `preset` into `config` in place. Returns
    (applied_keys, skipped_keys, deleted_keys)."""
    applied: list[str] = []
    skipped: list[str] = []
    deleted: list[str] = []

    for section in _PRESETTABLE_SECTIONS:
        if section not in preset:
            continue
        preset_section = preset[section]
        if not isinstance(preset_section, dict):
            continue
        target = config.setdefault(section, {})
        for key, value in preset_section.items():
            if key in target:
                target[key] = value
                applied.append(f"{section}.{key}")
            else:
                skipped.append(f"{section}.{key}")

    if "delete" in preset and isinstance(preset["delete"], dict):
        for section, keys in preset["delete"].items():
            if section not in _PRESETTABLE_SECTIONS:
                continue
            if not isinstance(keys, list):
                continue
            target = config.get(section, {})
            if not isinstance(target, dict):
                continue
            for key in keys:
                key_str = str(key)
                if key_str in target:
                    del target[key_str]
                    deleted.append(f"{section}.{key_str}")

    return applied, skipped, deleted
