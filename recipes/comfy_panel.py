#!/usr/bin/env python3
"""
comfy_panel.py — edit a per-workflow panel config, run it against the
                 daemon.

    python comfy_panel.py                 uses the config for the daemon's
                                          active workflow
    python comfy_panel.py <name>          uses a named config
    python comfy_panel.py --reset         back up and rewrite the template
    python comfy_panel.py --print         dump a template to stdout

Layout
------
    recipes/
    ├── comfy_panel.py                       this script
    └── comfy_panel/
        ├── workflows/                       JSON workflow definitions
        │                                    (only present if injected from disk)
        ├── panel_configs/                   the .toml files this panel edits
        └── history/                         generation records

Batch generation
----------------
When `count > 1`, the daemon runs the workflow that many times, each
run with seed `seed + i` for run `i`, and pastes each result with the
seed in the layer name. This requires the workflow to expose an
`ETN_Parameter` named exactly `seed` wired to the sampler's seed input;
without it, the daemon clamps count to 1 and reports the reason.

The `seed` field is the base seed for a batch. Blank means the daemon
picks a random base. One history record is written per `comfy_generate`
call, regardless of count, with a `runs` list carrying each run's seed.
"""

from pathlib import Path
import argparse
import sys
import time

try:
    import tomllib
except ImportError:
    print("This script requires Python 3.11+ (for tomllib).", file=sys.stderr)
    sys.exit(2)

_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE / "src", _HERE.parent / "src"):
    if (_cand / "kritomatic").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

try:
    from kritomatic.batch import BatchExecutor
    from kritomatic.client import KritaClient
except ImportError:
    print("Error: the 'kritomatic' Python package is not importable.", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
#  Directories
# ---------------------------------------------------------------------------
#
# Everything the panel owns lives under PANEL_DIR, named after this
# script. The three subdirectories are fixed; the code below creates the
# ones it writes to as needed.

PANEL_DIR = _HERE / "comfy_panel"
WORKFLOW_DIR = PANEL_DIR / "workflows"
CONFIG_DIR = PANEL_DIR / "panel_configs"

WORKFLOW_WAIT = 3.0


# ---------------------------------------------------------------------------
#  Daemon interaction
# ---------------------------------------------------------------------------

def _make_client() -> KritaClient | None:
    client = KritaClient()
    if not client.connect():
        return None
    return client


def daemon_status() -> dict | None:
    """Fetch the daemon's comfyui state, or None if unreachable."""
    client = _make_client()
    if client is None:
        return None
    try:
        resp = client.execute("comfy_status")
    finally:
        client.close()
    if not resp or resp.get("status") != "success":
        return None
    return resp.get("data") or {}


def ensure_daemon_ready() -> dict | None:
    """Bring the daemon to a state where it can report a workflow.

    Always issues `comfy_connect`. The daemon caches the ComfyUI
    client from the last successful connect, so `connected_to` in
    `comfy_status` can be truthy even when ComfyUI is currently
    unreachable. Only a fresh `comfy_connect` actually pings it.

    Bails out with a clean message when the environment is not ready:

      - the daemon (Krita's Kritomatic plugin) is not reachable
      - ComfyUI cannot be contacted
      - ComfyUI is reachable but the tooling-nodes package is missing

    All are conditions the user has to fix outside the panel, so
    continuing into the batch would only produce a cascade of
    downstream errors that obscure the real one.
    """
    client = KritaClient()
    if not client.connect():
        raise SystemExit(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.\n"
            "Start Krita with the Kritomatic Daemon plugin enabled, "
            "then re-run."
        )

    try:
        print("Connecting to ComfyUI ...")
        connect_resp = client.execute("comfy_connect", watch=True)
        if not connect_resp or connect_resp.get("status") != "success":
            msg = (connect_resp or {}).get("message", "unknown error")
            raise SystemExit(
                f"ComfyUI is not reachable.\n  {msg}\n"
                "Start ComfyUI and re-run."
            )
        cr_data = connect_resp.get("data") or {}
        if cr_data.get("watching") and not cr_data.get("subscribed"):
            print("  watcher started, subscription pending")

        deadline = time.monotonic() + WORKFLOW_WAIT
        while time.monotonic() < deadline:
            resp = client.execute("comfy_status")
            status = (resp or {}).get("data") or {}
            if status.get("workflow"):
                return status
            time.sleep(0.25)

        resp = client.execute("comfy_status")
        return (resp or {}).get("data") or status

    finally:
        client.close()


# ---------------------------------------------------------------------------
#  TOML helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
#  Workflow interface extraction (for file-sourced workflows; the daemon
#  exposes the same for browser-sourced ones via comfy_status)
# ---------------------------------------------------------------------------

def extract_workflow(workflow: dict) -> dict:
    params = {}
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


# ---------------------------------------------------------------------------
#  Template blocks
# ---------------------------------------------------------------------------

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
    """The [layer_aliases] table.

    Keys are the layer names the workflow declares; values are the
    layer names the daemon should actually read from the document.
    Defaults are the keys themselves, so an unedited table is the
    identity mapping.
    """
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
        "# sampler's seed input; otherwise the daemon clamps count to 1.",
        "#",
        "# seed is the base for a batch: run i uses seed+i. Blank means",
        "# the daemon picks a random base. One history record is written",
        "# per comfy_generate call, regardless of count, with each run's",
        "# seed in its runs list.",
    ]


# ---------------------------------------------------------------------------
#  Template builders
# ---------------------------------------------------------------------------

def build_template(workflow_name: str, workflow: dict,
                   signature: str | None = None,
                   source: str = "file") -> str:
    """Render a fresh config file from a workflow dict on disk."""
    info = extract_workflow(workflow)

    lines: list[str] = []
    if signature:
        lines.append(f'signature = "{signature}"')
    lines += [
        f'host = "127.0.0.1"',
        f'port = 8188',
    ]
    if source == "browser":
        lines += [
            "# Empty: the workflow is followed from ComfyUI's browser.",
            'workflow = ""',
        ]
    else:
        lines.append(f'workflow = "{workflow_name}"')
    lines.append('watch = true')
    lines.append('')

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
    if source == "browser":
        lines.append("#     # no comfy_load: the browser supplies the graph")
    else:
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


def _build_browser_template_body(status: dict,
                                 signature: str | None) -> list[str]:
    """Shared body for the two browser-template writers."""
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


def write_template_from_file(workflow_name: str, path: Path) -> int:
    """Write a template for a file-sourced workflow."""
    wf_path = WORKFLOW_DIR / f"{workflow_name}.json"
    if not wf_path.is_file():
        print(f"Error: workflow file not found: {wf_path}", file=sys.stderr)
        avail = sorted(p.stem for p in WORKFLOW_DIR.glob("*.json"))
        if avail:
            print(f"Available: {', '.join(avail)}", file=sys.stderr)
        return 1

    import json
    try:
        workflow = json.loads(wf_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"Error: {wf_path} is not valid JSON: {e}", file=sys.stderr)
        return 1

    if "nodes" in workflow or "links" in workflow:
        print(f"Error: {wf_path} is in ComfyUI UI format; export as "
              f"API format.", file=sys.stderr)
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_template(workflow_name, workflow, source="file"),
                    encoding="utf-8")
    print(f"Wrote template to {path}")
    print("Edit it, then run this script again.")
    return 0


def write_template_from_status(path: Path, status: dict,
                               signature: str | None) -> int:
    """Write a template for a browser-sourced workflow."""
    lines = _build_browser_template_body(status, signature)
    lines.append('')
    lines.append("# This config was generated from a graph published by")
    lines.append("# ComfyUI's browser. It is keyed by the signature above, "
                 "so")
    lines.append("# reloading the same workflow in the browser will find "
                 "this")
    lines.append("# file again.")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote template to {path}")
    print("Edit it, then run this script again.")
    return 0


def print_template_for_status(status: dict, signature: str | None) -> int:
    """Print a browser-workflow template to stdout, no file writes."""
    lines = _build_browser_template_body(status, signature)
    print("\n".join(lines) + "\n", end="")
    return 0


# ---------------------------------------------------------------------------
#  Config loading and command building
# ---------------------------------------------------------------------------

def load_config(path: Path) -> dict:
    with path.open("rb") as f:
        return tomllib.load(f)


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


# ---------------------------------------------------------------------------
#  Config resolution
# ---------------------------------------------------------------------------

def _find_config_by_signature(sig: str) -> Path | None:
    if not sig:
        return None
    if not CONFIG_DIR.is_dir():
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


def existing_configs() -> list[Path]:
    if not CONFIG_DIR.is_dir():
        return []
    return sorted(CONFIG_DIR.glob("*.toml"))


def _explain_no_workflow(status: dict) -> str:
    connected = status.get("connected_to")
    watching = status.get("watching")
    lines = ["No workflow is loaded in the daemon."]
    if connected:
        lines.append(f"  The daemon is connected to {connected}.")
        if watching:
            lines.append(
                "  The browser watcher is running, but no graph has "
                "been published."
            )
            lines.append(
                "  Open a workflow in ComfyUI's browser that contains "
                "an ETN_KritaOutput node, and make any edit (drag a "
                "node, change a widget) to trigger a publish."
            )
        else:
            lines.append(
                "  The browser watcher is not running. Re-run this "
                "script or run `kritomatic comfyui comfy_watch`."
            )
    else:
        lines.append(
            "  The daemon is not connected to ComfyUI. Make sure "
            "ComfyUI is running, then re-run this script."
        )
    lines.append("")
    lines.append("Alternatively, load a workflow file directly:")
    lines.append("  python comfy_panel.py <workflow-name>")
    return "\n".join(lines)


def resolve_path(name: str | None) -> tuple[Path, str, str | None]:
    """Decide which config file to use. Returns (path, label, signature)."""
    if name:
        return CONFIG_DIR / f"{name}.toml", f"explicit name {name!r}", None

    status = daemon_status()
    if status is None:
        existing = existing_configs()
        if len(existing) == 1:
            return existing[0], "daemon unreachable, only config on disk", None
        if not existing:
            raise SystemExit(
                "Daemon unreachable and no config on disk.\n"
                "Start Krita with the plugin, or run:  "
                "python comfy_panel.py <workflow-name>"
            )
        names = ", ".join(p.stem for p in existing)
        raise SystemExit(
            f"Daemon unreachable and multiple configs on disk.\n"
            f"Pick one:  python comfy_panel.py <name>\n"
            f"Available: {names}"
        )

    source = status.get("workflow_source")
    wf_name = status.get("workflow") or ""
    sig = status.get("workflow_signature") or ""

    if source == "file" and wf_name:
        return (CONFIG_DIR / f"{wf_name}.toml",
                f"file workflow {wf_name!r}", None)

    if source == "browser":
        existing = _find_config_by_signature(sig)
        if existing is not None:
            return existing, f"browser workflow, matched by signature {sig}", sig
        target = CONFIG_DIR / f"browser_{sig or 'unknown'}.toml"
        return target, f"browser workflow, new signature {sig}", sig

    existing = existing_configs()
    if len(existing) == 1:
        return existing[0], "no daemon workflow, only config on disk", None
    raise SystemExit(_explain_no_workflow(status))


# ---------------------------------------------------------------------------
#  Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(prog="comfy_panel")
    p.add_argument("workflow", nargs="?",
                   help="Workflow name; uses panel_configs/<name>.toml. "
                        "Defaults to whatever workflow the daemon reports "
                        "as active.")
    p.add_argument("--reset", action="store_true",
                   help="Back up the config and write a fresh template.")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="Print a template to stdout. No files touched.")
    p.add_argument("--no-connect", action="store_true",
                   help="Skip the auto-connect step.")
    args = p.parse_args()

    if args.print_only:
        if not args.no_connect:
            ensure_daemon_ready()
        if args.workflow:
            wf_path = WORKFLOW_DIR / f"{args.workflow}.json"
            if not wf_path.is_file():
                print(f"Error: workflow file not found: {wf_path}",
                      file=sys.stderr)
                return 1
            import json
            workflow = json.loads(wf_path.read_text(encoding="utf-8"))
            print(build_template(args.workflow, workflow, source="file"),
                  end="")
            return 0

        status = daemon_status()
        if status is None:
            print("Error: daemon unreachable.", file=sys.stderr)
            return 1
        source = status.get("workflow_source")
        sig = status.get("workflow_signature") or ""
        if source == "browser":
            return print_template_for_status(status, sig)
        if source == "file":
            name = status.get("workflow") or ""
            wf_path = WORKFLOW_DIR / f"{name}.json"
            if not wf_path.is_file():
                print(f"Error: workflow file not found: {wf_path}",
                      file=sys.stderr)
                return 1
            import json
            workflow = json.loads(wf_path.read_text(encoding="utf-8"))
            print(build_template(name, workflow, source="file"), end="")
            return 0
        print(_explain_no_workflow(status), file=sys.stderr)
        return 1

    if not args.no_connect:
        ensure_daemon_ready()

    try:
        path, label, signature = resolve_path(args.workflow)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    if args.reset and path.exists():
        backup = path.with_name(path.name + ".bak")
        if backup.exists():
            backup.unlink()
        path.replace(backup)
        print(f"Moved existing config to {backup}")

    if not path.exists():
        if signature is not None:
            status = daemon_status() or {}
            return write_template_from_status(path, status, signature)
        return write_template_from_file(args.workflow or path.stem, path)

    try:
        cfg = load_config(path)
    except Exception as e:
        print(f"Error reading {path}: {e}", file=sys.stderr)
        return 1

    try:
        commands = build_commands(cfg)
    except ValueError as e:
        print(f"Error in {path}: {e}", file=sys.stderr)
        return 1

    print(f"Config:   {path}   ({label})")
    print(f"Commands: {len(commands)}\n")

    # Size the client's per-read timeout to the longest single command
    # the batch can contain. `comfy_generate` with a count > 1 runs
    # several generations in sequence before it emits its own progress
    # line; the client must be willing to wait that long.
    per_run = float(cfg.get("timeout", 55.0))
    try:
        count = int(cfg.get("count", 1))
    except (TypeError, ValueError):
        count = 1
    if count < 1:
        count = 1
    # Each run can take up to `per_run`; add one more `per_run` of
    # margin so a slow first run does not eat into the wait for the
    # last one, and a fixed overhead for connection and setup.
    read_timeout = per_run * (count + 1) + 60.0
    print(f"Client read timeout: {read_timeout:.0f}s "
          f"({count} run(s) × {per_run:.0f}s + margin)\n")

    executor = BatchExecutor()
    try:
        results = executor.execute(
            {"id": "comfy_panel", "commands": commands},
            timeout=read_timeout,
        )
        failed = [r for r in results if r["status"] == "error"]
        if failed:
            print(f"\n{len(failed)} command(s) failed:")
            for r in failed:
                print(f"  ✗ {r['command']}: {r['message']}")
            return 1
        return 0
    finally:
        executor.close()


if __name__ == "__main__":
    sys.exit(main())
