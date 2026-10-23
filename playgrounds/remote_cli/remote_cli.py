#!/usr/bin/env python3
"""
remote_cli — command-line client for pgkit playgrounds.

    python remote_cli.py --url ws://127.0.0.1:8773/remote [COMMAND ...]
    python remote_cli.py --target sam3 [COMMAND ...]
    python remote_cli.py [COMMAND ...]           # uses default_url

Without a subcommand, opens an interactive REPL.

Run `python remote_cli.py --help` for the full command list.

Config
======
`remote_cli/config.json`, next to this file. Ships with the CLI, is
checked into the repo, and is edited in place. There is no dotfile
dance and no cwd fallback — the file next to this script is always
what gets read.

A missing or unparseable config falls back to built-in defaults with
a warning on stderr. The CLI keeps working either way.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# --- sys.path: make `pgkit` and `remote_cli` importable regardless of
#     the working directory the script is run from.
_HERE = Path(__file__).resolve().parent
_PARENT = _HERE.parent
for _p in (_HERE, _PARENT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from pgkit.remote_cli_client import (  # noqa: E402
    RemoteCliClient,
    RemoteCliError,
    RemoteTimeout,
    RemoteNotConnected,
    RemoteCommandFailed,
    parse_value,
    set_verbose,
)
from remote_cli import rc_script  # noqa: E402
from remote_cli import rc_repl    # noqa: E402


# ============================================================================
#  Config
# ============================================================================

# The config lives in this file's directory. It ships with the CLI, is
# checked into the repo, and is edited in place. There is no dotfile
# dance and no cwd fallback — if you are running `remote_cli.py` from
# anywhere, it reads `remote_cli/config.json` next to this file.
CONFIG_PATH = _HERE / "config.json"

DEFAULT_CONFIG = {
    "default_url": "ws://127.0.0.1:8773/remote",
    "targets": {
        "sam3":       "ws://127.0.0.1:8773/remote",
        "pair":       "ws://127.0.0.1:8767/remote",
        "cone":       "ws://127.0.0.1:8766/remote",
        "board":      "ws://127.0.0.1:8770/remote",
        "text_board": "ws://127.0.0.1:8771/remote",
    },
    "timeout": 10.0,
}


def _load_config() -> dict:
    """Load `remote_cli/config.json`, merged onto DEFAULT_CONFIG.

    Missing file, unreadable file, or invalid JSON all fall back to
    DEFAULT_CONFIG with a one-line warning on stderr. A malformed
    config should not stop the CLI; it should tell the user where the
    problem is and continue with the built-in defaults.

    User-supplied `targets` are merged onto the built-in targets rather
    than replacing them. A config that adds one new playground should
    not silently erase `sam3`, `pair`, and the rest.
    """
    if not CONFIG_PATH.is_file():
        return dict(DEFAULT_CONFIG)

    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(
            f"warning: could not read {CONFIG_PATH}: {e}; "
            f"using built-in defaults",
            file=sys.stderr,
        )
        return dict(DEFAULT_CONFIG)

    if not isinstance(data, dict):
        print(
            f"warning: {CONFIG_PATH} is not a JSON object; "
            f"using built-in defaults",
            file=sys.stderr,
        )
        return dict(DEFAULT_CONFIG)

    merged = dict(DEFAULT_CONFIG)
    merged.update(data)

    # Merge targets rather than replace, so removing a line from the
    # user config does not erase that target from the CLI.
    targets = dict(DEFAULT_CONFIG["targets"])
    targets.update(data.get("targets") or {})
    merged["targets"] = targets

    return merged


def resolve_url(target: Optional[str], config: dict) -> str:
    if not target:
        return config["default_url"]
    if target.startswith(("ws://", "wss://")):
        return target
    targets = config.get("targets") or {}
    if target in targets:
        return targets[target]
    raise ValueError(
        f"unknown alias {target!r}; known aliases: {sorted(targets)}"
    )


# ============================================================================
#  Value parsing for `set`
# ============================================================================

def parse_set_args(args: list) -> dict:
    """Turn command-line args into a values dict.

    Supports two forms:
      - `NAME=VALUE NAME=VALUE ...` (all args contain '=')
      - `NAME VALUE NAME VALUE ...` (even arg count, no '=')
    Mixing the two is an error.
    """
    if not args:
        return {}
    has_equals = [("=" in a) for a in args]
    if all(has_equals):
        out = {}
        for a in args:
            k, _, v = a.partition("=")
            out[k] = parse_value(v)
        return out
    if any(has_equals):
        raise ValueError(
            "mixed forms: use all NAME=VALUE or all NAME VALUE"
        )
    if len(args) % 2 != 0:
        raise ValueError(
            f"odd number of args for NAME VALUE pairs: {len(args)}"
        )
    out = {}
    for i in range(0, len(args), 2):
        out[args[i]] = parse_value(args[i + 1])
    return out


# ============================================================================
#  Output helpers
# ============================================================================

def _print_json(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _truncate(s: str, n: int) -> str:
    s = str(s)
    return s if len(s) <= n else s[:n - 1] + "…"


# ============================================================================
#  Command implementations
# ============================================================================

def cmd_ls(client: RemoteCliClient, args) -> int:
    values = client.values()
    schema = client.schema()
    names = sorted(values.keys())
    if args.prefix:
        names = [n for n in names if n.startswith(args.prefix)]
    if args.json:
        _print_json({n: values[n] for n in names})
        return 0
    if not names:
        print("(no fields)")
        return 0
    width = max(len(n) for n in names)
    for n in names:
        meta = schema.get(n) or {}
        kind = meta.get("kind", "?")
        val = values[n]
        print(f"{n:<{width}}  {_truncate(json.dumps(val, default=str), 60)}"
              f"   [{kind}]")
    return 0


def cmd_show(client: RemoteCliClient, args) -> int:
    values = client.values()
    schema = client.schema()
    name = args.name
    if name not in values:
        print(f"unknown field {name!r}", file=sys.stderr)
        return 1
    if args.json:
        _print_json({
            "name": name,
            "value": values[name],
            "schema": schema.get(name),
        })
        return 0
    print(f"name:  {name}")
    print(f"value: {json.dumps(values[name], default=str)}")
    meta = schema.get(name) or {}
    for k in sorted(meta):
        print(f"  {k}: {json.dumps(meta[k], default=str)}")
    return 0


def cmd_schema(client: RemoteCliClient, args) -> int:
    schema = client.schema()
    names = sorted(schema.keys())
    if args.prefix:
        names = [n for n in names if n.startswith(args.prefix)]
    if args.json:
        _print_json({n: schema[n] for n in names})
        return 0
    if not names:
        print("(no schema)")
        return 0
    for n in names:
        meta = schema[n] or {}
        kind = meta.get("kind", "?")
        label = meta.get("label", "")
        extra = ""
        if meta.get("options"):
            extra = f" options={meta['options']}"
        elif meta.get("min") is not None or meta.get("max") is not None:
            extra = f" range=[{meta.get('min')}, {meta.get('max')}]"
        print(f"{n:<32} {kind:<8} {label}{extra}")
    return 0


def cmd_actions(client: RemoteCliClient, args) -> int:
    schema = client.schema()
    names = sorted(
        n for n, m in schema.items()
        if (m or {}).get("kind") == "action"
    )
    if args.prefix:
        names = [n for n in names if n.startswith(args.prefix)]
    if args.json:
        _print_json({n: schema[n] for n in names})
        return 0
    if not names:
        print("(no actions)")
        return 0
    for n in names:
        meta = schema[n] or {}
        label = meta.get("label", "")
        scope = meta.get("scope", "global")
        level = meta.get("level")
        line = f"{n:<32} {label:<24} ({scope}"
        if level:
            line += f" on {level}"
        line += ")"
        print(line)
        req = meta.get("requires") or []
        for r in req:
            print(f"    requires: {r.get('name')} ({r.get('kind')})")
    return 0


def cmd_entities(client: RemoteCliClient, args) -> int:
    entities = client.entities() or {}
    order = client.entity_order()
    if args.json:
        _print_json({"levels": entities, "order": order})
        return 0
    if not entities:
        print("(no entities)")
        return 0
    for level in order:
        e = entities.get(level) or {}
        label = e.get("label", level)
        sel = e.get("selected")
        print(f"{label}:")
        for item in e.get("items") or []:
            mark = " [selected]" if item.get("name") == sel else ""
            extra = item.get("extra")
            extra_str = ""
            if extra:
                extra_str = "  " + json.dumps(extra, default=str)
            print(f"  {item.get('name')}  {item.get('label', '')}"
                  f"{mark}{extra_str}")
    return 0


def cmd_presence(client: RemoteCliClient, args) -> int:
    p = client.presence()
    if args.json:
        _print_json(p)
        return 0
    print(f"browser={p.get('browser', 0)}  remote={p.get('remote', 0)}")
    return 0


def cmd_state(client: RemoteCliClient, args) -> int:
    data = {
        "values": client.values(),
        "schema": client.schema(),
        "entities": client.entities(),
        "presence": client.presence(),
    }
    _print_json(data)
    return 0


def cmd_set(client: RemoteCliClient, args) -> int:
    try:
        values = parse_set_args(args.pairs)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if not values:
        print("error: no values given", file=sys.stderr)
        return 2
    applied = client.patch_and_verify(values)
    if args.json:
        changed = [k for k, v in values.items() if applied.get(k) != v]
        _print_json({
            "requested": values,
            "applied": applied,
            "changed": changed,
        })
        return 0
    for k, v in values.items():
        if applied.get(k) != v:
            print(f"  {k}: requested {v!r} → applied {applied.get(k)!r}")
        else:
            print(f"  {k} = {v!r}")
    return 0


def cmd_action(client: RemoteCliClient, args) -> int:
    payload = None
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as e:
            print(f"error: invalid payload JSON: {e}", file=sys.stderr)
            return 2
        if not isinstance(payload, dict):
            print("error: payload must be a JSON object", file=sys.stderr)
            return 2
    try:
        result = client.command(args.kind, payload, timeout=args.timeout)
    except RemoteCommandFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RemoteTimeout as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if args.json:
        _print_json(result)
    else:
        print(f"ok: {args.kind}")
        if result.get("result") is not None:
            print(f"  result: {json.dumps(result['result'], default=str)}")
    return 0


def cmd_select(client: RemoteCliClient, args) -> int:
    try:
        client.command("select",
                       {"level": args.level, "name": args.name},
                       timeout=args.timeout)
    except RemoteCommandFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"ok: selected {args.level}/{args.name}")
    return 0


def cmd_deselect(client: RemoteCliClient, args) -> int:
    try:
        client.command("deselect", {}, timeout=args.timeout)
    except RemoteCommandFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print("ok: deselected")
    return 0


def cmd_raw(client: RemoteCliClient, args) -> int:
    try:
        msg = json.loads(args.message)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON: {e}", file=sys.stderr)
        return 2
    if not isinstance(msg, dict):
        print("error: message must be a JSON object", file=sys.stderr)
        return 2
    client.raw_send(msg)
    if args.json:
        print("{}")
    else:
        print(f"sent: {json.dumps(msg, default=str)}")
    return 0


def cmd_validate(client: RemoteCliClient, args) -> int:
    return rc_script.run_validate(client, args.script, args.json)


def cmd_script(client: RemoteCliClient, args) -> int:
    return rc_script.run_script(
        client, args.script,
        stop_on_error=(not args.continue_on_error),
        plan_only=args.plan,
        verbose=args.verbose,
        json_output=args.json,
    )


# ============================================================================
#  CLI wiring
# ============================================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="remote_cli",
        description="Command-line client for pgkit playgrounds.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  remote_cli --url ws://127.0.0.1:8773/remote ls\n"
            "  remote_cli --target sam3 set settings.threshold=0.55\n"
            "  remote_cli --target sam3 action action.segmentAll\n"
            "  remote_cli --target sam3 script flow.json\n"
            "  remote_cli --target sam3\n"
        ),
    )
    p.add_argument("--url", default=None,
                   help="WebSocket URL (ws://host:port/remote) or alias")
    p.add_argument("--target", default=None,
                   help="Target alias from config (sam3, pair, cone, ...)")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="Print every message sent/received")
    p.add_argument("--json", action="store_true",
                   help="Machine-readable JSON output where applicable")
    p.add_argument("--timeout", type=float, default=None,
                   help="Default wait/command timeout in seconds")

    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    ls = sub.add_parser("ls", help="List fields with values")
    ls.add_argument("--prefix", default=None)
    ls.set_defaults(fn=cmd_ls)

    show = sub.add_parser("show", help="Show one field's value and metadata")
    show.add_argument("name")
    show.set_defaults(fn=cmd_show)

    schema = sub.add_parser("schema", help="List field metadata")
    schema.add_argument("--prefix", default=None)
    schema.set_defaults(fn=cmd_schema)

    actions = sub.add_parser("actions", help="List registered actions")
    actions.add_argument("--prefix", default=None)
    actions.set_defaults(fn=cmd_actions)

    entities = sub.add_parser("entities", help="List entity levels and items")
    entities.set_defaults(fn=cmd_entities)

    presence = sub.add_parser("presence", help="Show presence counts")
    presence.set_defaults(fn=cmd_presence)

    state = sub.add_parser("state", help="Dump full state as JSON")
    state.set_defaults(fn=cmd_state)

    setp = sub.add_parser("set", help="Set one or more field values")
    setp.add_argument("pairs", nargs="+",
                      metavar="NAME=VALUE",
                      help="NAME=VALUE pairs, or NAME VALUE pairs")
    setp.set_defaults(fn=cmd_set)

    action = sub.add_parser("action",
                            help="Fire an action with an optional payload")
    action.add_argument("kind")
    action.add_argument("payload", nargs="?", default=None,
                        help="Optional JSON payload")
    action.add_argument("--timeout", type=float, default=30.0)
    action.set_defaults(fn=cmd_action)

    select = sub.add_parser("select", help="Select an entity item")
    select.add_argument("level")
    select.add_argument("name")
    select.add_argument("--timeout", type=float, default=30.0)
    select.set_defaults(fn=cmd_select)

    deselect = sub.add_parser("deselect", help="Deselect the current item")
    deselect.add_argument("--timeout", type=float, default=30.0)
    deselect.set_defaults(fn=cmd_deselect)

    raw = sub.add_parser("raw", help="Send an arbitrary JSON message")
    raw.add_argument("message")
    raw.set_defaults(fn=cmd_raw)

    validate = sub.add_parser("validate",
                              help="Validate a script against the live schema")
    validate.add_argument("script")
    validate.set_defaults(fn=cmd_validate)

    script = sub.add_parser("script",
                            help="Execute a JSON script of steps")
    script.add_argument("script")
    script.add_argument("--continue-on-error", action="store_true",
                        help="Keep running after a failed step")
    script.add_argument("--plan", action="store_true",
                        help="Print the wire plan, do not send anything")
    script.set_defaults(fn=cmd_script)

    return p


# Subcommands that can run without a browser tab connected.
_NO_BROWSER_REQUIRED = {
    "presence", "raw", "state", "ls", "show",
    "schema", "actions", "entities",
}


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    config = _load_config()

    if args.timeout is not None:
        config["timeout"] = args.timeout

    if args.verbose:
        set_verbose(True)

    try:
        url = resolve_url(args.target or args.url, config)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    # No subcommand → REPL
    if args.cmd is None:
        return rc_repl.run(url, config, verbose=args.verbose)

    require_browser = args.cmd not in _NO_BROWSER_REQUIRED

    client = RemoteCliClient(url)
    try:
        client.connect(require_browser=require_browser)
    except RemoteCliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    try:
        return args.fn(client, args)
    except RemoteCommandFailed as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RemoteTimeout as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RemoteNotConnected as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    except RemoteCliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
