"""
cli.py — command-line interface for the panel.

    kritomatic comfyui panel run    [<workflow>]
    kritomatic comfyui panel reset  [<workflow>]
    kritomatic comfyui panel print  [<workflow>] [--output FILE]
    kritomatic comfyui panel preset apply <name> [<workflow>] [--dry-run]
    kritomatic comfyui panel preset list
    kritomatic comfyui panel queue
    kritomatic comfyui panel cancel [<batch_id>] [--all]
    kritomatic comfyui panel publishers
    kritomatic comfyui panel pin        [<key>]
    kritomatic comfyui panel unpin

`run` requires Krita's daemon and a reachable ComfyUI; `queue`,
`cancel`, `publishers`, `pin`, and `unpin` require the daemon only,
and can be used from any shell while other processes are running
`comfyui panel run` — all of them share the single daemon, so its
state is the union of every running batch and every known publisher.

`cancel` is a request, not a synchronous kill: the daemon sets the
target batch's cancel flag and the batch winds itself down at its next
check, marking unfinished runs as cancelled. Already-pasted layers stay
in the document.

`queue`, `publishers`, and their `--json` variants emit JSON on stdout
so they pipe cleanly into `jq` or a polling loop.

`reset` and `print` require the daemon only when the workflow is not
named explicitly; `preset apply` with an explicit workflow touches
neither; `preset list` reads the presets directory.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional

from kritomatic.batch import BatchExecutor
from kritomatic.client import KritaClient
from kritomatic.helpful_argparse import HelpfulArgumentParser

from .config import (
    apply_preset,
    build_browser_template,
    build_commands,
    build_file_template,
    existing_configs,
    find_config_by_signature,
    list_presets,
    load_config,
    resolve_preset_path,
)
from .paths import CONFIG_DIR, PRESET_DIR, WORKFLOW_DIR


WORKFLOW_WAIT = 3.0


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
    """Fail hard if the daemon socket is not open.

    Does not require ComfyUI to be up. Used by `reset`, `print`, and
    `preset apply` when they need the daemon to identify the active
    workflow but have no reason to touch ComfyUI.
    """
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
    """Full readiness: daemon up, ComfyUI reachable, and (if possible)
    a workflow published. Used by `run`.
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


# ============================================================================
#  Config resolution
# ============================================================================

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
                "command or run `kritomatic comfyui comfy_watch`."
            )
    else:
        lines.append(
            "  The daemon is not connected to ComfyUI. Make sure "
            "ComfyUI is running, then re-run."
        )
    lines.append("")
    lines.append("Alternatively, name a workflow file directly:")
    lines.append("  kritomatic comfyui panel <command> <workflow-name>")
    return "\n".join(lines)


def _resolve_config_path(workflow_name: Optional[str]
                         ) -> tuple[Path, str, Optional[str]]:
    """Return (path, label, signature).

    Explicit name -> path resolved without contacting the daemon.
    No name -> daemon is consulted for the active workflow; if the
    daemon is unreachable, fall back to the on-disk configs when there
    is exactly one.
    """
    if workflow_name:
        return (CONFIG_DIR / f"{workflow_name}.toml",
                f"explicit name {workflow_name!r}", None)

    status = _daemon_status()
    if status is None:
        existing = existing_configs()
        if len(existing) == 1:
            return (existing[0],
                    "daemon unreachable, only config on disk", None)
        if not existing:
            raise SystemExit(
                "Daemon unreachable and no config on disk.\n"
                "Name a workflow explicitly, or start Krita."
            )
        names = ", ".join(p.stem for p in existing)
        raise SystemExit(
            f"Daemon unreachable and multiple configs on disk.\n"
            f"Pick one:  kritomatic comfyui panel run <name>\n"
            f"Available: {names}"
        )

    source = status.get("workflow_source")
    wf_name = status.get("workflow") or ""
    sig = status.get("workflow_signature") or ""

    if source == "file" and wf_name:
        return (CONFIG_DIR / f"{wf_name}.toml",
                f"file workflow {wf_name!r}", None)

    if source == "browser":
        existing = find_config_by_signature(sig)
        if existing is not None:
            return existing, f"browser workflow, matched by signature {sig}", sig
        target = CONFIG_DIR / f"browser_{sig or 'unknown'}.toml"
        return target, f"browser workflow, new signature {sig}", sig

    existing = existing_configs()
    if len(existing) == 1:
        return existing[0], "no daemon workflow, only config on disk", None
    raise SystemExit(_explain_no_workflow(status))


# ============================================================================
#  Template helpers
# ============================================================================

def _write_fresh_template(path: Path, workflow_name: Optional[str],
                          signature: Optional[str]) -> int:
    """Write a fresh template to `path`. Used by `run` (when the config
    does not exist yet) and by `reset`.
    """
    if signature is not None:
        status = _daemon_status() or {}
        template = build_browser_template(status, signature)
    else:
        if not workflow_name:
            print("Error: cannot determine workflow name.", file=sys.stderr)
            return 1
        template = build_file_template(workflow_name)
        if template is None:
            return 1

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(template, encoding="utf-8")
    print(f"Wrote template to {path}")
    print("Edit it, then run this command again.")
    return 0


def _get_template_string(workflow_name: Optional[str]
                         ) -> Optional[str]:
    """Return a rendered template string, or None on error.

    Explicit workflow -> read workflows/<name>.json and render.
    No workflow -> consult the daemon for the active workflow.
    """
    if workflow_name:
        return build_file_template(workflow_name)

    if not _ensure_daemon_reachable():
        return None
    status = _daemon_status() or {}
    source = status.get("workflow_source")
    sig = status.get("workflow_signature") or ""

    if source == "browser":
        return build_browser_template(status, sig)
    if source == "file":
        name = status.get("workflow") or ""
        if not name:
            print("Error: daemon reports a file workflow with no name.",
                  file=sys.stderr)
            return None
        return build_file_template(name)

    print(_explain_no_workflow(status), file=sys.stderr)
    return None


# ============================================================================
#  Subcommand implementations
# ============================================================================

def cmd_run(args) -> int:
    """Run the batch described by the panel config for the active (or
    named) workflow.
    """
    _ensure_daemon_ready()

    try:
        path, label, signature = _resolve_config_path(args.workflow)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    if not path.exists():
        return _write_fresh_template(path, args.workflow, signature)

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

    per_run = float(cfg.get("timeout", 55.0))
    try:
        count = int(cfg.get("count", 1))
    except (TypeError, ValueError):
        count = 1
    if count < 1:
        count = 1
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


def cmd_reset(args) -> int:
    """Regenerate the panel config from the workflow template, without
    a backup. The old file is deleted first; the template writer then
    creates a fresh one.
    """
    if not _ensure_daemon_reachable():
        return 1

    try:
        path, _label, signature = _resolve_config_path(args.workflow)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    if path.exists():
        path.unlink()

    return _write_fresh_template(path, args.workflow, signature)


def cmd_print(args) -> int:
    """Render the config template for the active (or named) workflow.
    Prints to stdout by default; writes to `--output PATH` if given.
    The config file on disk is not read or written.
    """
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
    """Apply a preset to a panel config.

    Explicit workflow -> direct path, no daemon. No workflow -> daemon
    is consulted to identify the active workflow's config.
    """
    preset_path = resolve_preset_path(args.name)
    if preset_path is None:
        return 1

    if args.workflow:
        config_path = CONFIG_DIR / f"{args.workflow}.toml"
    else:
        if not _ensure_daemon_reachable():
            return 1
        try:
            config_path, _label, _sig = _resolve_config_path(None)
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
#  queue
# ----------------------------------------------------------------------------

def cmd_queue(args) -> int:
    """Print the daemon's current comfyui batch queue as JSON.

    A batch is a `comfyui panel run` call that has not yet returned.
    Because every panel run goes through the same daemon, a `queue`
    invocation from any shell sees the union of every batch currently
    in flight — including batches launched by other processes.

    Read-only: it sends one `comfy_batches` command and prints the
    reply verbatim as JSON. It does not touch the batches themselves,
    and it is safe to run alongside them.

    Output is always JSON, on stdout, so the command pipes cleanly into
    `jq` or a polling loop. On success the process exits 0; on
    connection failure or a non-success reply it prints a diagnostic on
    stderr and exits 1, leaving stdout empty.
    """
    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        print(
            "Start Krita with the Kritomatic Daemon plugin enabled.",
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


# ----------------------------------------------------------------------------
#  cancel
# ----------------------------------------------------------------------------

def cmd_cancel(args) -> int:
    """Request cancellation of a running batch, or every running batch.

    Three modes, selected by the arguments:

      (no args)         cancel the "current" batch — the oldest active
                        batch, which is the one ComfyUI is executing or
                        is next up to execute. If nothing is running
                        this is an error, not a silent no-op.

      <batch_id>        cancel the batch with this id. The id must be
                        one the daemon currently has registered; an
                        unknown id is an error.

      --all             cancel every active batch, and any async
                        generates the daemon is also tracking.

    Cancel is a request, not a synchronous kill. The daemon sets each
    target batch's cancel event and returns immediately; the batch
    loop notices at its next check and winds itself down, marking the
    runs it did not finish as cancelled. Runs that already pasted
    layers keep those layers — cancellation is not transactional.

    Exit codes: 0 on a cancel request that the daemon accepted, 1 on
    a connection failure or a daemon-side refusal (unknown batch id,
    nothing to cancel), 2 on a local argument conflict.
    """
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
        print(
            "Start Krita with the Kritomatic Daemon plugin enabled.",
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
    """Cancel the oldest active batch.

    "Current" is defined as the head of the queue: the batch with the
    smallest `started_at` among the daemon's active batches. Because
    ComfyUI executes prompts sequentially on a single worker, and the
    client's single-flight lock guarantees only one phase 2 at a time,
    the oldest batch is the one currently on ComfyUI — or, if the
    executing slot is momentarily empty, the one next up. Either way
    it is the batch a user means when they say "the current one".

    The lookup goes through `comfy_batches` first so this function can
    print which batch it is about to cancel. That extra round trip is
    cheap and it makes the command's effect legible in the shell,
    which matters when several batches are in flight at once.
    """
    resp = client.execute("comfy_batches")
    if not resp or resp.get("status") != "success":
        msg = (resp or {}).get("message", "no response from daemon")
        print(f"Could not query the batch queue: {msg}", file=sys.stderr)
        return 1

    batches = list(((resp.get("data") or {}).get("batches")) or [])
    if not batches:
        print("Nothing to cancel: no active batches.", file=sys.stderr)
        print(
            "  Run `kritomatic comfyui panel queue` for the current "
            "queue.",
            file=sys.stderr,
        )
        return 1

    # `comfy_batches` sorts oldest-first.
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
    """Cancel one batch, by id."""
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
    """Cancel every active batch, and any async generates."""
    cancel = client.execute("comfy_cancel")
    if cancel and cancel.get("status") == "success":
        print(cancel.get("message")
              or "Cancel requested for all batches")
        return 0

    msg = (cancel or {}).get("message", "no response from daemon")
    print(f"Cancel failed: {msg}", file=sys.stderr)
    return 1


# ----------------------------------------------------------------------------
#  publishers / pin / unpin
# ----------------------------------------------------------------------------

def cmd_publishers(args) -> int:
    """Print every browser publisher the daemon has heard from, as JSON.

    A publisher is a ComfyUI browser tab that has published a graph
    containing an ETN_KritaOutput node. Each tab gets its own key
    (ComfyUI's per-page-load client id), so two tabs of the same
    workflow show up as two entries.

    Read-only: it sends one `comfy_list_publishers` command and prints
    the reply verbatim as JSON. Output is always JSON, on stdout, so
    the command pipes cleanly into `jq` or a polling loop. On success
    the process exits 0; on connection failure or a non-success reply
    it prints a diagnostic on stderr and exits 1, leaving stdout empty.
    """
    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        print(
            "Start Krita with the Kritomatic Daemon plugin enabled.",
            file=sys.stderr,
        )
        return 1

    try:
        resp = client.execute("comfy_list_publishers")
    finally:
        client.close()

    if not resp or resp.get("status") != "success":
        msg = (resp or {}).get("message", "no response from daemon")
        print(f"Could not fetch publishers: {msg}", file=sys.stderr)
        return 1

    data = resp.get("data") or {}
    print(json.dumps(data, indent=2))
    return 0


def cmd_pin(args) -> int:
    """Pin the active workflow to a browser publisher.

    Without a key, pins whichever publisher is currently active — the
    one whose graph is driving the handler right now. With a key, pins
    that specific one. While pinned, publishes from other browser
    instances are tracked but do not replace the active workflow;
    publishes from the pinned instance still update it.
    """
    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        print(
            "Start Krita with the Kritomatic Daemon plugin enabled.",
            file=sys.stderr,
        )
        return 1

    try:
        if args.key:
            resp = client.execute("comfy_pin_publisher", key=args.key)
        else:
            resp = client.execute("comfy_pin_publisher")
    finally:
        client.close()

    if resp and resp.get("status") == "success":
        print(resp.get("message") or "Pinned")
        return 0

    msg = (resp or {}).get("message", "no response from daemon")
    print(f"Pin failed: {msg}", file=sys.stderr)
    if "comfy_list_publishers" not in msg:
        print(
            "  Run `kritomatic comfyui panel publishers` to list "
            "available keys.",
            file=sys.stderr,
        )
    return 1


def cmd_unpin(args) -> int:
    """Clear the publisher pin."""
    client = KritaClient()
    if not client.connect():
        print(
            f"Krita's Kritomatic daemon is not reachable at "
            f"{client.host}:{client.port}.",
            file=sys.stderr,
        )
        print(
            "Start Krita with the Kritomatic Daemon plugin enabled.",
            file=sys.stderr,
        )
        return 1

    try:
        resp = client.execute("comfy_unpin_publisher")
    finally:
        client.close()

    if resp and resp.get("status") == "success":
        print(resp.get("message") or "Unpinned")
        return 0

    msg = (resp or {}).get("message", "no response from daemon")
    print(f"Unpin failed: {msg}", file=sys.stderr)
    return 1


# ============================================================================
#  Parser
# ============================================================================

def populate_panel_subcommands(sub) -> None:
    """Populate the panel's subcommands into a subparsers object.

    Called by `build_parser()` below (top-level entry point) and by
    `registry.py` (to show `panel` in `kritomatic comfyui --help` and
    `kritomatic --help`). The two paths share this function so the
    subcommand tree is declared in exactly one place.
    """
    run_p = sub.add_parser(
        'run',
        help='Run the panel config for the active (or named) workflow',
    )
    run_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name; uses panel_configs/<name>.toml. Defaults to '
             'the daemon-reported active workflow.',
    )
    run_p.set_defaults(func=cmd_run)

    reset_p = sub.add_parser(
        'reset',
        help='Regenerate the panel config from the workflow template '
             '(no backup)',
    )
    reset_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name; uses panel_configs/<name>.toml. Defaults to '
             'the daemon-reported active workflow.',
    )
    reset_p.set_defaults(func=cmd_reset)

    print_p = sub.add_parser(
        'print',
        help='Print the panel config template for the active (or named) '
             'workflow',
    )
    print_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name. Defaults to the daemon-reported active '
             'workflow.',
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
        help='Preset name (bare filename in presets/, .toml optional).',
    )
    apply_p.add_argument(
        'workflow', nargs='?', default=None,
        help='Workflow name; uses panel_configs/<name>.toml. Defaults to '
             'the daemon-reported active workflow.',
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
        help='Cancel every active batch, and any async generates the '
             'daemon is tracking.',
    )
    cancel_p.set_defaults(func=cmd_cancel)

    publishers_p = sub.add_parser(
        'publishers',
        help='Print every ComfyUI browser publisher the daemon has '
             'heard from, as JSON',
    )
    publishers_p.set_defaults(func=cmd_publishers)

    pin_p = sub.add_parser(
        'pin',
        help='Pin the active workflow to one browser publisher '
             '(no argument: pin the currently active one)',
    )
    pin_p.add_argument(
        'key', nargs='?', default=None,
        help='Publisher key from `panel publishers`. Omit to pin '
             'whichever publisher is currently active.',
    )
    pin_p.set_defaults(func=cmd_pin)

    unpin_p = sub.add_parser(
        'unpin',
        help='Clear the publisher pin; return to last-publish-wins',
    )
    unpin_p.set_defaults(func=cmd_unpin)


def build_parser() -> HelpfulArgumentParser:
    """Top-level parser for `kritomatic comfyui panel`. Used by
    `run_panel_command()` (the runtime entry point).
    """
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
    """Entry point for the client-side `comfyui panel` dispatch.

    Invoked from `main.py` when `sys.argv[1:3] == ['comfyui', 'panel']`.
    Parses `sys.argv[3:]` (everything after `comfyui panel`).
    """
    parser = build_parser()
    args = parser.parse_args(sys.argv[3:])
    return args.func(args)
