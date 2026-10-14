"""
config.py — panel config file manipulation.

Pure helpers: TOML rendering, workflow-interface extraction, config
resolution, preset application. No daemon or network here; `cli.py`
supplies the runtime around these functions.
"""

from __future__ import annotations

import json
import sys
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
#  Workflow interface extraction
# ============================================================================

def extract_workflow(workflow: dict) -> dict:
    """Pull the interface of a workflow: declared params, canvas and
    selection use, and named-layer requirements."""
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
        lines.append("# Uncomment a selection line below to choose what "
                     "the daemon")
        lines.append("# reads for it.")
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


# ============================================================================
#  Template rendering
# ============================================================================

def _render_file_template(workflow_name: str, workflow: dict) -> str:
    info = extract_workflow(workflow)

    lines: list[str] = [
        f'host = "127.0.0.1"',
        f'port = 8188',
        f'workflow = "{workflow_name}"',
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
                 f"\"{workflow_name}\" }},")
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


def build_file_template(workflow_name: str) -> Optional[str]:
    """Read workflows/<name>.json, render the template string.

    Returns None (and prints a diagnostic on stderr) if the workflow
    file is missing, is not valid JSON, or is in the ComfyUI UI format
    rather than the API format.
    """
    path = WORKFLOW_DIR / f"{workflow_name}.json"
    if not path.is_file():
        print(f"Error: workflow file not found: {path}", file=sys.stderr)
        if WORKFLOW_DIR.is_dir():
            avail = sorted(p.stem for p in WORKFLOW_DIR.glob("*.json"))
            if avail:
                print(f"Available: {', '.join(avail)}", file=sys.stderr)
        return None

    try:
        workflow = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: {path} is not valid JSON: {e}", file=sys.stderr)
        return None

    if "nodes" in workflow or "links" in workflow:
        print(f"Error: {path} is in ComfyUI UI format; export as API "
              f"format.", file=sys.stderr)
        return None

    return _render_file_template(workflow_name, workflow)


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
#  Command building (from a loaded config dict)
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
        cmds.append({"type": "comfy_load", "name": str(workflow)})

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
#  Presets
# ============================================================================

_PRESETTABLE_SECTIONS = ("params", "layer_aliases")


def list_presets() -> list[str]:
    """Return the names (without the .toml suffix) of every preset in
    the presets directory."""
    if not PRESET_DIR.is_dir():
        return []
    return sorted(p.stem for p in PRESET_DIR.glob("*.toml"))


def resolve_preset_path(name: str) -> Optional[Path]:
    """Return a preset file path, or None with a diagnostic on stderr.

    Strict: the argument must be a bare filename. Paths with directory
    separators are rejected so lookup cannot escape `presets/`. The
    `.toml` extension is optional.
    """
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

    Preset format:

        [params]
        "cutSelection?" = false
        segmentFurther = "person"

        [layer_aliases]
        areaToRMBG = "face"

        [delete]
        params = ["simpleSegmentation?"]
        layer_aliases = ["thecanvas"]

    Only `[params]` and `[layer_aliases]` are consulted for value
    assignments. Unknown sections and unknown keys within known
    sections are skipped silently. `[delete]` removes keys by section;
    a key not present is skipped. If a key appears in both a value
    section and `[delete]`, the delete wins.

    Comments, key order, and unrelated values in the target are
    preserved: tomlkit parses into a document that retains them, and
    only the changed entries are perturbed on write.
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
