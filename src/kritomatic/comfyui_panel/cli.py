"""
cli.py — command-line interface for the panel.

    kritomatic comfyui panel run    [<workflow>]
    kritomatic comfyui panel reset  [<workflow>]
    kritomatic comfyui panel print  [<workflow>] [--output FILE]
    kritomatic comfyui panel preset apply <name> [<workflow>] [--dry-run]
    kritomatic comfyui panel preset list
    kritomatic comfyui panel queue
    kritomatic comfyui panel cancel [<batch_id>] [--all]

Config files are JSON (`.json`). The daemon autoloads them on
`comfy_load` and saves them via `comfy_save_config`. TOML is no
longer read or written anywhere.

If the source file is in ComfyUI UI format, it is automatically
converted to API format before the daemon reads it, via the same
mechanism as `kritomatic comfyui convert`. The converted file lands
under `<repo>/data/convert/<source_name>` and is what the panel config
references; the source JSON is not modified or copied.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from kritomatic.batch import BatchExecutor
from kritomatic.client import KritaClient
from kritomatic.comfy_convert.cli import (
    ConversionError,
    convert_if_ui,
)
from kritomatic.helpful_argparse import HelpfulArgumentParser

from .config import (
    apply_preset,
    build_batch,
    build_file_template,
    existing_configs,
    list_presets,
    load_config,
    parse_panel_config,
    resolve_preset_path,
)
from .paths import CONFIG_DIR, PRESET_DIR, WORKFLOW_DIR


# ============================================================================
#  Daemon interaction
# ============================================================================

def _daemon_status() -> Optional[dict]:
    """Fetch the daemon's comfyui state, or None if unreachable."""
    client = KritaClient()
    if not client.connect():
        return None
    try:
        resp = client.execute("comfy_status")
    finally:
        client.close()
    if not resp or resp.get("status") != "success":
        return None
    return resp.get("data") or {}


def _ensure_daemon_reachable() -> bool:
    """Fail hard if the daemon socket is not open."""
    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        print("Start Krita with the Kritomatic Daemon plugin enabled.",
              file=sys.stderr)
        return False
    client.close()
    return True


def _ensure_daemon_ready() -> Optional[dict]:
    """Full readiness: daemon up and ComfyUI reachable."""
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
        connect_resp = client.execute("comfy_connect")
        if not connect_resp or connect_resp.get("status") != "success":
            msg = (connect_resp or {}).get("message", "unknown error")
            raise SystemExit(
                f"ComfyUI is not reachable.\n  {msg}\n"
                "Start ComfyUI and re-run."
            )
    finally:
        client.close()

    resp_status = _daemon_status() or {}
    return resp_status


# ============================================================================
#  Workflow argument normalization
# ============================================================================

def _path_like(s: str) -> bool:
    if not s:
        return False
    return (
        "/" in s
        or "\\" in s
        or s.startswith("~")
        or s.startswith(".")
        or s.lower().endswith(".json")
    )


def _normalize_workflow_arg(
    workflow_arg: Optional[str],
) -> tuple[Optional[str], Optional[Path]]:
    """Split the raw `<workflow>` argument into (slug, path)."""
    if not workflow_arg:
        return None, None
    if not _path_like(workflow_arg):
        return workflow_arg, None

    p = Path(workflow_arg).expanduser()
    try:
        p = p.resolve(strict=False)
    except (OSError, RuntimeError):
        pass
    return p.stem, p


# ============================================================================
#  Conversion (UI format → API format)
# ============================================================================

def _prepare_workflow_ref(
    slug: Optional[str],
    source_path: Optional[Path],
    *,
    comfy_url: Optional[str] = None,
) -> tuple[Optional[str], bool]:
    """Resolve the workflow reference the panel config should store."""
    if slug is None:
        return None, True

    if source_path is not None:
        source = source_path
        is_slug = False
    else:
        source = WORKFLOW_DIR / f"{slug}.json"
        is_slug = True

    if not source.is_file():
        return (slug if is_slug else str(source)), True

    try:
        converted, records, was_converted = convert_if_ui(
            source, url=comfy_url or "http://127.0.0.1:8188",
            annotate=False,
        )
    except ConversionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return None, False

    if not was_converted:
        return (slug if is_slug else str(source)), True

    print(f"Converted UI-format workflow → {converted}")
    if records:
        tokens = ", ".join(r["token"] for r in records)
        print(f"  {len(records)} handle(s): {tokens}")

    return str(converted), True


# ============================================================================
#  Config resolution
# ============================================================================

def _resolve_config_path(
    workflow_slug: Optional[str],
    json_path: Optional[Path] = None,
) -> tuple[Path, str]:
    """Return (config_path, label). Config files are `<slug>.json`."""
    if workflow_slug:
        config_path = CONFIG_DIR / f"{workflow_slug}.json"
        if json_path is not None:
            label = f"file {json_path}"
        else:
            label = f"explicit name {workflow_slug!r}"
        return config_path, label

    existing = existing_configs()
    if len(existing) == 1:
        return existing[0], "only config on disk"
    if not existing:
        raise SystemExit(
            "No config on disk and no workflow named.\n"
            "Name a workflow explicitly, or run `kritomatic comfyui panel "
            "reset <workflow>` to generate one."
        )
    names = ", ".join(p.stem for p in existing)
    raise SystemExit(
        f"Multiple configs on disk.\n"
        f"Pick one:  kritomatic comfyui panel run <name>\n"
        f"Available: {names}"
    )


# ============================================================================
#  Template helpers
# ============================================================================

def _write_fresh_template(
    path: Path,
    workflow_ref: Optional[str],
) -> int:
    if not workflow_ref:
        print("Error: cannot determine workflow name.",
              file=sys.stderr)
        return 1
    template = build_file_template(workflow_ref)
    if template is None:
        return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    print(f"Wrote template to {path}")
    print("Edit it, then run this command again.")
    return 0


def _get_template_string(workflow_arg: Optional[str]) -> Optional[str]:
    slug, source_path = _normalize_workflow_arg(workflow_arg)
    if slug is None:
        print("Error: no workflow named.", file=sys.stderr)
        return None

    workflow_ref, ok = _prepare_workflow_ref(slug, source_path)
    if not ok:
        return None
    return build_file_template(workflow_ref)


# ============================================================================
#  Subcommand implementations
# ============================================================================

def cmd_run(args) -> int:
    """Run the batch described by the panel config for the named workflow."""
    _ensure_daemon_ready()

    slug, source_path = _normalize_workflow_arg(args.workflow)
    if slug is None:
        try:
            path, label = _resolve_config_path(None)
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            return 1
        try:
            cfg = parse_panel_config(path)
        except ValueError as e:
            print(f"Error in {path}: {e}", file=sys.stderr)
            return 1
    else:
        workflow_ref, ok = _prepare_workflow_ref(slug, source_path)
        if not ok:
            return 1

        path, label = _resolve_config_path(slug, source_path)

        if not path.exists():
            return _write_fresh_template(path, workflow_ref)

        try:
            cfg = parse_panel_config(path)
        except ValueError as e:
            print(f"Error in {path}: {e}", file=sys.stderr)
            return 1

        if workflow_ref is not None:
            cfg.workflow = workflow_ref

    try:
        batch = build_batch(cfg)
    except ValueError as e:
        print(f"Error in {path}: {e}", file=sys.stderr)
        return 1

    print(f"Config:   {path}   ({label})")
    print(f"Commands: {len(batch['commands'])}\n")

    per_run = float(cfg.timeout)
    read_timeout = per_run * (cfg.count + 1) + 60.0
    print(f"Client read timeout: {read_timeout:.0f}s "
          f"({cfg.count} run(s) × {per_run:.0f}s + margin)\n")

    executor = BatchExecutor()
    try:
        results = executor.execute(batch, timeout=read_timeout)
        failed = [r for r in results if r["status"] == "error"]
        if failed:
            print(f"\n{len(failed)} command(s) failed:")
            for r in failed:
                print(f"  ✗ {r['command']}: {r['message']}")
            return 1
        return 0
    finally:
        executor.close()


def cmd_reset(args) -> int:
    """Regenerate the panel config from the workflow template."""
    if not _ensure_daemon_reachable():
        return 1

    slug, source_path = _normalize_workflow_arg(args.workflow)
    if slug is None:
        print("Error: no workflow named.", file=sys.stderr)
        return 1

    workflow_ref, ok = _prepare_workflow_ref(slug, source_path)
    if not ok:
        return 1

    path, _label = _resolve_config_path(slug, source_path)

    if path.exists():
        path.unlink()

    return _write_fresh_template(path, workflow_ref)


def cmd_print(args) -> int:
    template = _get_template_string(args.workflow)
    if template is None:
        return 1

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(template, encoding="utf-8")
        print(f"Wrote {out_path}")
    else:
        print(template, end="")
    return 0


def cmd_preset_apply(args) -> int:
    preset_path = resolve_preset_path(args.name)
    if preset_path is None:
        return 1

    if not _ensure_daemon_reachable():
        return 1

    if args.workflow:
        slug, _ = _normalize_workflow_arg(args.workflow)
        config_path = CONFIG_DIR / f"{slug}.json"
    else:
        try:
            config_path, _label = _resolve_config_path(None)
        except SystemExit as e:
            print(str(e), file=sys.stderr)
            return 1

    if not config_path.exists():
        print(f"Config {config_path} does not exist yet.", file=sys.stderr)
        print(
            "Run `kritomatic comfyui panel run` once for this workflow, "
            "or `kritomatic comfyui panel reset`, to create it.",
            file=sys.stderr,
        )
        return 1

    return apply_preset(preset_path, config_path, args.dry_run)


def cmd_preset_list(args) -> int:
    names = list_presets()
    if not names:
        print(f"No presets in {PRESET_DIR}")
        return 0
    for name in names:
        print(name)
    return 0


# ----------------------------------------------------------------------------
#  queue / cancel
# ----------------------------------------------------------------------------

def cmd_queue(args) -> int:
    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        return 1

    try:
        resp = client.execute("comfy_batches")
    finally:
        client.close()

    if not resp or resp.get("status") != "success":
        msg = (resp or {}).get("message", "no response from daemon")
        print(f"Could not fetch the batch queue: {msg}", file=sys.stderr)
        return 1

    data = resp.get("data") or {}
    print(json.dumps(data, indent=2))
    return 0


def cmd_cancel(args) -> int:
    if args.all and args.batch_id:
        print(
            "Pick one: pass a batch id, or --all, or neither "
            "(which cancels the current batch).",
            file=sys.stderr,
        )
        return 2

    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        return 1

    try:
        if args.all:
            return _cancel_all(client)
        if args.batch_id:
            return _cancel_specific(client, args.batch_id)
        return _cancel_current(client)
    finally:
        client.close()


def _cancel_current(client) -> int:
    resp = client.execute("comfy_batches")
    if not resp or resp.get("status") != "success":
        msg = (resp or {}).get("message", "no response from daemon")
        print(f"Could not query the batch queue: {msg}", file=sys.stderr)
        return 1

    batches = list(((resp.get("data") or {}).get("batches")) or [])
    if not batches:
        print("Nothing to cancel: no active batches.", file=sys.stderr)
        return 1

    head = batches[0]
    batch_id = head.get("batch_id") or ""
    if not batch_id:
        print(
            "Daemon returned a batch with no id; cannot target it.",
            file=sys.stderr,
        )
        return 1

    workflow = head.get("workflow", "(unnamed)")
    phase = head.get("phase", "?")
    finished = head.get("finished_runs", 0)
    total = head.get("total_runs", 0)

    cancel = client.execute("comfy_cancel", batch_id=batch_id)
    if cancel and cancel.get("status") == "success":
        print(
            f"Cancelling current batch {batch_id} "
            f"({workflow}, phase: {phase}, progress: "
            f"{finished}/{total})"
        )
        return 0

    msg = (cancel or {}).get("message", "no response from daemon")
    print(f"Cancel failed: {msg}", file=sys.stderr)
    return 1


def _cancel_specific(client, batch_id: str) -> int:
    cancel = client.execute("comfy_cancel", batch_id=batch_id)
    if cancel and cancel.get("status") == "success":
        print(f"Cancelling batch {batch_id}")
        return 0

    msg = (cancel or {}).get("message", "no response from daemon")
    print(f"Cancel failed: {msg}", file=sys.stderr)

    if "No active batch" in msg:
        print(
            "  Run `kritomatic comfyui panel queue` to list active "
            "batch ids.",
            file=sys.stderr,
        )
    return 1


def _cancel_all(client) -> int:
    cancel = client.execute("comfy_cancel")
    if cancel and cancel.get("status") == "success":
        print(cancel.get("message")
              or "Cancel requested for all batches")
        return 0

    msg = (cancel or {}).get("message", "no response from daemon")
    print(f"Cancel failed: {msg}", file=sys.stderr)
    return 1


# ============================================================================
#  Parser
# ============================================================================

def populate_panel_subcommands(sub) -> None:
    """Populate the panel's subcommands into a subparsers object."""
    run_p = sub.add_parser(
        'run',
        help='Run the panel config for the named workflow',
    )
    run_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name, or a path to a workflow .json file. With a '
             'name, uses panel_configs/<name>.json and '
             'workflows/<name>.json. With a path, reads the JSON from '
             'where it is. UI-format sources are auto-converted to API '
             'format. Defaults to the only config on disk if there is '
             'exactly one.',
    )
    run_p.set_defaults(func=cmd_run)

    reset_p = sub.add_parser(
        'reset',
        help='Regenerate the panel config from the workflow template '
             '(no backup)',
    )
    reset_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name, or a path to a workflow .json file. '
             'UI-format sources are auto-converted to API format.',
    )
    reset_p.set_defaults(func=cmd_reset)

    print_p = sub.add_parser(
        'print',
        help='Print the panel config template for the named workflow',
    )
    print_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name, or a path to a workflow .json file. '
             'UI-format sources are auto-converted to API format.',
    )
    print_p.add_argument(
        '--output', '-o', default=None, metavar='PATH',
        help='Write the template to this file instead of stdout.',
    )
    print_p.set_defaults(func=cmd_print)

    preset_p = sub.add_parser(
        'preset',
        help='Preset operations on panel configs',
    )
    preset_sub = preset_p.add_subparsers(
        dest='preset_command', metavar='PRESET_COMMAND',
        required=True, help='Preset commands',
    )

    apply_p = preset_sub.add_parser(
        'apply',
        help='Apply a preset to a panel config',
    )
    apply_p.add_argument(
        'name',
        help='Preset name (bare filename in presets/, .json optional).',
    )
    apply_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name, or a path to a workflow .json file. '
             'Defaults to the only config on disk if there is exactly '
             'one.',
    )
    apply_p.add_argument(
        '--dry-run', action='store_true',
        help='Print what would change, do not modify the config file.',
    )
    apply_p.set_defaults(func=cmd_preset_apply)

    list_p = preset_sub.add_parser(
        'list',
        help='List available presets in presets/',
    )
    list_p.set_defaults(func=cmd_preset_list)

    queue_p = sub.add_parser(
        'queue',
        help='Print the daemon\'s current comfyui batch queue as JSON',
    )
    queue_p.set_defaults(func=cmd_queue)

    cancel_p = sub.add_parser(
        'cancel',
        help='Request cancellation of a running batch, or all of them',
    )
    cancel_p.add_argument(
        'batch_id', nargs='?', default=None,
        help='Id of the batch to cancel, as reported by '
             '`panel queue`. Omit (and do not pass --all) to cancel '
             'the current batch: the oldest still-running one.',
    )
    cancel_p.add_argument(
        '--all', action='store_true',
        help='Cancel every active batch.',
    )
    cancel_p.set_defaults(func=cmd_cancel)


def build_parser() -> HelpfulArgumentParser:
    parser = HelpfulArgumentParser(
        prog='kritomatic comfyui panel',
        description='Edit and run per-workflow panel configs.',
    )
    sub = parser.add_subparsers(
        dest='panel_command', metavar='PANEL_COMMAND',
        required=True, help='Panel commands',
    )
    populate_panel_subcommands(sub)
    return parser


def run_panel_command() -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[3:])
    return args.func(args)