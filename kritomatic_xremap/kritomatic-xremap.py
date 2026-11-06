#!/usr/bin/env python3
"""
kritomatic-xremap — render and launch the xremap config from
capabilities.yaml + ~/.config/kritomatic/bindings.yaml.

Subcommands:
    init        seed ~/.config/kritomatic/bindings.yaml from
                bindings.example.yaml (no-op if it already exists)
    render      print the rendered xremap YAML to stdout
    validate    render, then check for issues; exit non-zero if any
    run         render and launch xremap (this is what systemd calls)

Two files drive this tool:

    capabilities.yaml                 (shipped, maintainer-owned)
    ~/.config/kritomatic/bindings.yaml (per-user, user-owned)

See the header of capabilities.yaml and bindings.example.yaml for format.
"""

from __future__ import annotations

import argparse
import ast
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml


# ---------------------------------------------------------------------------
# Locations
# ---------------------------------------------------------------------------

SCRIPT_DIR          = Path(__file__).resolve().parent
CAPABILITIES_PATH   = SCRIPT_DIR / "capabilities.yaml"
USER_BINDINGS_PATH  = Path.home() / ".config" / "kritomatic" / "bindings.yaml"
BINDINGS_EXAMPLE    = SCRIPT_DIR / "bindings.example.yaml"


# ---------------------------------------------------------------------------
# kritomatic CLI resolution
# ---------------------------------------------------------------------------

def resolve_kritomatic_cli(bindings: dict) -> list[str]:
    """Resolve the command prefix that invokes the `kritomatic` CLI.

    Priority:
      1. bindings.yaml -> kritomatic.cli (explicit user override)
      2. `kritomatic` on PATH
      3. relative discovery from repo layout (../src/kritomatic/main.py)

    Returning a list rather than a string, because the relative case
    requires a 4-element prefix (`/usr/bin/env`, `PYTHONPATH=...`,
    `python`, `.../main.py`) — same form the old merger used.
    """
    explicit = (bindings.get("kritomatic") or {}).get("cli")
    if explicit:
        return list(explicit)

    on_path = shutil.which("kritomatic")
    if on_path:
        return [on_path]

    src   = SCRIPT_DIR.parent / "src"
    main  = src / "kritomatic" / "main.py"
    if main.is_file():
        return ["/usr/bin/env", f"PYTHONPATH={src}", "python", str(main)]

    raise RuntimeError(
        "Cannot resolve the `kritomatic` CLI. Install it on PATH, place "
        "this script inside a kritomatic checkout that contains "
        "../src/kritomatic/main.py, or set `kritomatic.cli` in bindings.yaml."
    )


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------

def load_capabilities() -> dict:
    if not CAPABILITIES_PATH.is_file():
        raise RuntimeError(f"capabilities file not found: {CAPABILITIES_PATH}")
    with CAPABILITIES_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_bindings() -> dict:
    if not USER_BINDINGS_PATH.is_file():
        raise RuntimeError(
            f"bindings file not found: {USER_BINDINGS_PATH}\n"
            f"Seed one with:\n"
            f"  {Path(__file__).name} init\n"
            f"or manually:\n"
            f"  mkdir -p {USER_BINDINGS_PATH.parent}\n"
            f"  cp {BINDINGS_EXAMPLE} {USER_BINDINGS_PATH}"
        )
    with USER_BINDINGS_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# File-existence checks
# ---------------------------------------------------------------------------

def _check_script_arg(name: str, script: str, source: str,
                      script_dir: Path, issues: list[str]) -> None:
    if not isinstance(script, str):
        return
    if script.startswith("/"):
        full = Path(script)
    else:
        full = script_dir / script
    if not full.is_file():
        issues.append(f"{source}: {name!r} script not found: {script} "
                      f"(resolved to {full})")


def _check_one_command(name: str, cmd: list, source: str,
                       script_dir: Path) -> list[str]:
    """Inspect a single capability's `command:` array for missing files.

    Patterns recognized and checked:
      ["python", "scripts/foo.py", ...]      -> scripts/foo.py exists
      ["python3", "/abs/foo.py"]             -> /abs/foo.py exists
      ["bash", "scripts/foo.sh"]             -> scripts/foo.sh exists
      ["/usr/bin/env", "python", "foo.py"]   -> foo.py exists
      ["/abs/path/foo.sh"]                   -> /abs/path/foo.sh exists

    Patterns recognized and skipped (can't be checked as files):
      ["kritomatic", ...]                    -> expanded at render time
      ["kitty", "Chat2API"]                  -> arbitrary binary + args

    Any other head element is not checked; this keeps the checker from
    false-positiving on commands that are genuinely not file paths.
    """
    issues: list[str] = []
    if not cmd:
        return issues
    head = cmd[0]
    if not isinstance(head, str):
        return issues

    # `kritomatic` is a CLI subcommand, not a file path
    if head == "kritomatic":
        return issues

    # Bare script path (e.g. ["/home/me/bin/foo.sh"])
    if head.startswith("/") and (head.endswith(".py") or head.endswith(".sh")):
        if not Path(head).is_file():
            issues.append(f"{source}: {name!r} missing file: {head}")
        return issues

    # interpreter + script
    if head in ("python", "python3", "bash", "sh"):
        if len(cmd) < 2:
            issues.append(f"{source}: {name!r} has interpreter {head!r} "
                          f"but no script argument")
            return issues
        _check_script_arg(name, cmd[1], source, script_dir, issues)
        return issues

    # /usr/bin/env + interpreter + script
    if head == "/usr/bin/env":
        if len(cmd) < 3:
            issues.append(f"{source}: {name!r} has /usr/bin/env but "
                          f"insufficient arguments")
            return issues
        _check_script_arg(name, cmd[2], source, script_dir, issues)
        return issues

    return issues


def check_capability_files(caps: dict, source_label: str,
                           script_dir: Path) -> list[str]:
    """Verify that every script file referenced by a capability exists.

    `caps` is a name -> spec mapping (either capabilities.yaml's
    `capabilities:` block or bindings.yaml's `user_capabilities:` block).
    `kritomatic` subcommands and arbitrary binaries are skipped — only
    interpreter-plus-script patterns and bare script paths are checked.
    """
    issues: list[str] = []
    for name, spec in caps.items():
        if not isinstance(spec, dict):
            continue
        cmd = spec.get("command") or []
        issues.extend(_check_one_command(name, cmd, source_label, script_dir))
    return issues


def _find_router() -> Path | None:
    """Locate scripts/router.py relative to this launcher."""
    for c in (SCRIPT_DIR / "scripts" / "router.py",
              SCRIPT_DIR / "router.py"):
        if c.is_file():
            return c
    return None


def check_router_files() -> list[str]:
    """Parse router.py's TOOLS dict and verify every referenced file exists.

    Read-only, non-destructive: uses ast.literal evaluation, never
    imports router.py (which would execute its module-level code).
    """
    router = _find_router()
    if router is None:
        return [f"router.py not found (searched from {SCRIPT_DIR})"]

    try:
        tree = ast.parse(router.read_text(encoding="utf-8"), filename=str(router))
    except SyntaxError as e:
        return [f"router.py has syntax error: {e}"]

    issues: list[str] = []
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if not (isinstance(target, ast.Name) and target.id == "TOOLS"):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for k, v in zip(node.value.keys, node.value.values):
                if not (isinstance(k, ast.Constant) and isinstance(v, ast.Dict)):
                    continue
                tool_name = k.value
                for kk, vv in zip(v.keys, v.values):
                    if (isinstance(kk, ast.Constant) and kk.value == "file"
                            and isinstance(vv, ast.Constant)):
                        file_path = router.parent / vv.value
                        if not file_path.is_file():
                            issues.append(
                                f"router.py: tool {tool_name!r} references "
                                f"missing file: {vv.value}"
                            )
    return issues


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def resolve_capability(name: str,
                       capabilities: dict,
                       user_caps: dict,
                       kritomatic_cli: list[str]) -> list[str]:
    """Turn a capability name into a fully-qualified launch array.

    User-defined capabilities (bindings.yaml -> user_capabilities) shadow
    shipped ones by name. `command[0] == "kritomatic"` gets expanded to
    `kritomatic_cli`.
    """
    spec = user_caps.get(name) or (capabilities.get("capabilities") or {}).get(name)
    if spec is None:
        raise KeyError(
            f"unknown capability: {name!r}\n"
            f"  declared in capabilities.yaml:      {sorted((capabilities.get('capabilities') or {}).keys())}\n"
            f"  declared in user_capabilities:      {sorted(user_caps.keys())}"
        )
    cmd = list(spec["command"])
    if cmd and cmd[0] == "kritomatic":
        cmd = list(kritomatic_cli) + cmd[1:]
    return cmd


def _resolve_path_in_command(cmd: list[str]) -> list[str]:
    """Make relative script paths absolute against SCRIPT_DIR.

    The shipped commands use repo-relative paths like
    "scripts/clipboard2krita.py". xremap spawns its children from
    whatever CWD it has, so relative paths would fail. Resolve here.
    """
    out = []
    for i, part in enumerate(cmd):
        # Only touch the first real path-like argument, i.e. the one right
        # after the interpreter. `python scripts/foo.py` -> resolve part 1.
        if i > 0 and ("/" in part) and not part.startswith("/"):
            out.append(str((SCRIPT_DIR / part).resolve()))
        else:
            out.append(part)
    return out


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------

def render(bindings: dict, capabilities: dict) -> tuple[dict, list[str]]:
    """Render bindings + capabilities into xremap-format YAML.

    Returns (rendered_keymap, warnings).
    """
    warnings: list[str] = []

    user_caps      = bindings.get("user_capabilities") or {}
    devices_cfg    = bindings.get("devices") or {}
    listen_devices = list(devices_cfg.get("listen") or [])
    default_device = devices_cfg.get("default")
    kritomatic_cli = resolve_kritomatic_cli(bindings)

    if not listen_devices:
        raise ValueError("bindings.devices.listen is empty; xremap would listen to nothing")

    out_keymap: list[dict] = []

    for block in (bindings.get("keymap") or []):
        name = block.get("name") or "<unnamed>"

        # Per-block device override, or inherit default.
        block_devices = block.get("devices")
        if block_devices is None:
            if not default_device:
                raise ValueError(
                    f"block {name!r} has no `devices:` and bindings.devices.default is unset"
                )
            block_devices = [default_device]
        elif isinstance(block_devices, str):
            block_devices = [block_devices]

        # Warn if the block filters on a device xremap isn't listening to.
        for d in block_devices:
            if d not in listen_devices:
                warnings.append(
                    f"block {name!r} filters on device {d!r}, which is NOT in "
                    f"devices.listen {listen_devices} — this block will never fire"
                )

        # Resolve remap entries.
        out_remap: dict[str, dict] = {}
        for key, value in (block.get("remap") or {}).items():
            if isinstance(value, str):
                out_remap[key] = {
                    "launch": _resolve_path_in_command(
                        resolve_capability(value, capabilities, user_caps, kritomatic_cli)
                    )
                }
            elif isinstance(value, dict) and "launch" in value:
                out_remap[key] = {"launch": list(value["launch"])}
            elif isinstance(value, dict) and "cap" in value:
                raise NotImplementedError(
                    "capability-with-arguments is not supported in Phase 1; "
                    "define a dedicated capability in capabilities.yaml or "
                    "user_capabilities instead"
                )
            else:
                raise ValueError(
                    f"block {name!r}, key {key!r}: unrecognized binding value {value!r}"
                )

        out_block: dict = {
            "name":   name,
            "device": {"only": block_devices},
            "remap":  out_remap,
        }
        if "application" in block:
            out_block["application"] = block["application"]
        if "window" in block:
            out_block["window"] = block["window"]
        out_keymap.append(out_block)

    return {"keymap": out_keymap}, warnings


# ---------------------------------------------------------------------------
# Device wait
# ---------------------------------------------------------------------------

def wait_for_devices(devices: list[str], timeout: float = 5.0) -> None:
    """Block briefly until every device name appears in /proc/bus/input/devices.

    Non-fatal: if a device is missing after the timeout, warn and continue.
    xremap itself may still work if the device appears a moment later, and
    systemd's Restart=always will retry on the next restart if not.
    """
    if not devices:
        return

    deadline = time.time() + timeout
    missing  = list(devices)
    while time.time() < deadline and missing:
        try:
            text = Path("/proc/bus/input/devices").read_text().lower()
        except OSError:
            text = ""
        missing = [d for d in missing if d.lower() not in text]
        if missing:
            time.sleep(0.1)

    if missing:
        print(f"⚠️  waiting for device(s) timed out: {missing}", file=sys.stderr)
    else:
        print(f"✨ device(s) present: {devices}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------

def cmd_init(args: argparse.Namespace) -> int:
    if USER_BINDINGS_PATH.exists() and not args.force:
        print(f"⚠️  {USER_BINDINGS_PATH} already exists.", file=sys.stderr)
        print( "   Use --force to overwrite (destroys current bindings).",
              file=sys.stderr)
        return 1
    USER_BINDINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(BINDINGS_EXAMPLE, USER_BINDINGS_PATH)
    print(f"✓ Created {USER_BINDINGS_PATH}")
    print(f"  Edit it to add your devices and bindings, then run setup_links.py.")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    capabilities = load_capabilities()
    bindings     = load_bindings()
    rendered, warnings = render(bindings, capabilities)

    for w in warnings:
        print(f"⚠️  {w}", file=sys.stderr)

    text = yaml.safe_dump(rendered, sort_keys=False)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"✓ rendered to {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    capabilities = load_capabilities()
    bindings     = load_bindings()
    rendered, warnings = render(bindings, capabilities)

    # Structural checks (hard errors): every referenced script must exist
    # on disk. `kritomatic` subcommands and arbitrary binaries are skipped
    # by the checker — only interpreter-plus-script patterns and bare
    # script paths are verified.
    file_issues: list[str] = []
    file_issues.extend(check_capability_files(
        capabilities.get("capabilities") or {},
        "capabilities.yaml", SCRIPT_DIR,
    ))
    file_issues.extend(check_capability_files(
        bindings.get("user_capabilities") or {},
        "user_capabilities", SCRIPT_DIR,
    ))

    if args.router:
        file_issues.extend(check_router_files())

    # Print warnings (soft) and issues (hard) in that order.
    for w in warnings:
        print(f"⚠️  {w}", file=sys.stderr)
    for e in file_issues:
        print(f"❌ {e}", file=sys.stderr)

    if file_issues:
        print(f"\n{len(file_issues)} file-existence issue(s). "
              f"Bindings will not work until fixed.", file=sys.stderr)
        return 1

    n_blocks   = len(rendered["keymap"])
    n_bindings = sum(len(b["remap"]) for b in rendered["keymap"])
    extra      = "" if not warnings else f" ({len(warnings)} warning(s))"
    print(f"✓ bindings valid: {n_blocks} block(s), {n_bindings} binding(s){extra}",
          file=sys.stderr)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    capabilities = load_capabilities()
    bindings     = load_bindings()
    rendered, warnings = render(bindings, capabilities)

    for w in warnings:
        print(f"⚠️  {w}", file=sys.stderr)

    listen_devices = list((bindings.get("devices") or {}).get("listen") or [])
    wait_for_devices(listen_devices)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as tmp:
        yaml.safe_dump(rendered, tmp, sort_keys=False)
        tmp_path = Path(tmp.name)

    try:
        import os
        env = os.environ.copy()
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
        env.setdefault("XAUTHORITY",     str(Path.home() / ".Xauthority"))
        env.setdefault("DISPLAY",        env.get("DISPLAY", ":0"))

        cmd = ["xremap", str(tmp_path)]
        for d in listen_devices:
            cmd.extend(["--device", d])

        print(f"🚀 launching: {' '.join(cmd)}", file=sys.stderr)
        proc = subprocess.Popen(cmd, env=env)
        try:
            return proc.wait()
        except KeyboardInterrupt:
            proc.terminate()
            return proc.wait()
    finally:
        tmp_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        prog="kritomatic-xremap",
        description="Render and launch the xremap config from capabilities "
                    "and the user's bindings.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init",
                            help="seed ~/.config/kritomatic/bindings.yaml")
    p_init.add_argument("--force", action="store_true",
                        help="overwrite an existing bindings.yaml")
    p_init.set_defaults(func=cmd_init)

    p_render = sub.add_parser("render", help="print rendered xremap YAML")
    p_render.add_argument("--out", metavar="FILE", default=None,
                          help="write to FILE instead of stdout")
    p_render.set_defaults(func=cmd_render)

    p_validate = sub.add_parser("validate", help="render and warn on issues")
    p_validate.add_argument("--router", action="store_true",
                            help="also verify router.py TOOLS files exist")
    p_validate.set_defaults(func=cmd_validate)

    p_run = sub.add_parser("run", help="render and launch xremap")
    p_run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    try:
        return args.func(args)
    except (RuntimeError, ValueError, KeyError, NotImplementedError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
