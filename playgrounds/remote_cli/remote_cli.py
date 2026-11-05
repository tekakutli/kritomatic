#!/usr/bin/env python3
"""
remote_cli — command-line client for pgkit playgrounds.

    python remote_cli.py --target segment [COMMAND ...]
    python remote_cli.py --url ws://127.0.0.1:8773/remote [COMMAND ...]
    python remote_cli.py [COMMAND ...]           # uses default_target

Without a subcommand, opens an interactive REPL.  Without --target and
without --url, opens a hub-only REPL (target-bound verbs disabled, hub
verbs available).

Run `python remote_cli.py --help` for the full command list.

DISCOVERY, NOT CONFIGURATION
============================
Playgrounds register themselves with the pgkit hub at startup. The
hub's `/__playgrounds` endpoint returns `{name, port, role, pid,
html_file, group, available}` for each registration.

    --target NAME          ws://<hub>/NAME/remote
                           (through the hub proxy)

    --target NAME --direct ws://<hub-host>:<NAME-port>/remote
                           (bypass the hub proxy; the hub is still
                           queried, but only for the port)

    --url URL              literal; no discovery. Use this for
                           standalone playgrounds when the hub is
                           not running.

`list-targets` does the same `/__playgrounds` query and prints the
result — no target connect, no WS traffic.

LAUNCHER COMMANDS
=================
When a launcher (the shared-process runner) is present, the CLI can
start/stop individual playgrounds from its control channel:

    start NAME     ask the launcher to start NAME in-process
    stop NAME      ask the launcher to stop just NAME (group stays up)
    restart NAME   stop, then start
    available      list playgrounds that a launcher can start

These all run one-shot against the hub's management WS and do not
require a target connection.  They can be used from scripts, and are
mirrored as REPL verbs.

`kill NAME` still shuts down the whole group when NAME is grouped.
`stop NAME` is the "shut down only this one" alternative.

ROLE FILTER
===========
The hub's registry includes panels (remote_panel registers with
`role="panel"`) and launchers (`role="launcher"`). Neither accepts
the playground remote protocol. `resolve_target` therefore filters
the lookup to `role == "playground"` and produces a distinct error
message when the name matches a panel or launcher.

EXIT CODES
==========
    0  success
    1  runtime failure after connecting (a command the target
       refused, a mid-command disconnect, a script step that
       failed, or a launcher operation the hub refused)
    2  usage failure before connecting (bad flag, unknown target,
       or the hub was required and could not be reached)

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
import re
import sys
import uuid
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
#  Exceptions
# ============================================================================

class HubUnreachable(Exception):
    """The hub's /__playgrounds endpoint could not be queried."""


class UnknownTarget(Exception):
    """The named target is not in the hub's registry."""


# ============================================================================
#  Config
# ============================================================================

CONFIG_PATH = _HERE / "config.json"

DEFAULT_CONFIG = {
    "hub": "ws://127.0.0.1:8780",
    "default_target": "segment",
    "timeout": 10.0,
}


def _load_config() -> dict:
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

    if not merged.get("default_target"):
        old_url = merged.get("default_url") or ""
        m = re.match(r"^wss?://[^/]+/([^/]+)/remote/?$", old_url)
        if m:
            merged["default_target"] = m.group(1)

    return merged


# ============================================================================
#  Hub registry query
# ============================================================================

def _fetch_registry(config: dict):
    hub_ws = config.get("hub", "ws://127.0.0.1:8780")
    hub_http = hub_ws.replace("ws://", "http://") \
                     .replace("wss://", "https://")
    url = hub_http.rstrip("/") + "/__playgrounds"

    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        raise HubUnreachable(
            f"hub not reachable at {hub_http}: {e}"
        ) from e

    return (data.get("playgrounds") or []), hub_http


def _hub_host(config: dict) -> str:
    from urllib.parse import urlparse
    hub_ws = config.get("hub", "ws://127.0.0.1:8780")
    return urlparse(hub_ws).hostname or "127.0.0.1"


# ============================================================================
#  Target resolution
# ============================================================================

def resolve_target(config: dict, target_spec: Optional[str],
                   direct: bool = False) -> str:
    if not target_spec:
        target_spec = config.get("default_target") or ""
    if not target_spec:
        raise ValueError(
            "no target specified; pass --target NAME or --url URL, "
            "or set 'default_target' in config.json"
        )

    if target_spec.startswith(("ws://", "wss://")):
        return target_spec

    pgs, hub_http = _fetch_registry(config)

    entry = next(
        (p for p in pgs
         if p.get("name") == target_spec
         and p.get("role") == "playground"),
        None,
    )
    if entry is None:
        panel_match = next(
            (p for p in pgs
             if p.get("name") == target_spec
             and p.get("role") in ("panel", "launcher")),
            None,
        )
        names = sorted(
            p.get("name") for p in pgs
            if p.get("role") == "playground" and p.get("name")
        )
        if panel_match is not None:
            role = panel_match.get("role")
            raise UnknownTarget(
                f"{target_spec!r} is a {role}, not a playground. "
                f"Targetable playgrounds: {names or '(none)'}."
            )
        raise UnknownTarget(
            f"playground {target_spec!r} is not registered with the "
            f"hub at {hub_http}. "
            f"Registered playgrounds: {names or '(none)'}."
        )

    if direct:
        host = _hub_host(config)
        port = entry.get("port")
        if not port:
            raise UnknownTarget(
                f"playground {target_spec!r} has no port in its "
                f"hub registration; cannot construct a direct URL"
            )
        return f"ws://{host}:{port}/remote"

    hub_ws = config.get("hub", "ws://127.0.0.1:8780").rstrip("/")
    return f"{hub_ws}/{target_spec}/remote"


# ============================================================================
#  Value parsing for `set`
# ============================================================================

def parse_set_args(args: list) -> dict:
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
#  Hub commands (list-targets, available, start, stop, restart,
#  shutdown-hub)
# ============================================================================
#
# These do not require a target connection.  Their exit code follows
# the same convention as target-bound operations: 2 for "the hub was
# required and could not be reached", 1 for "hub reachable but the
# operation failed", 0 for success.

def _list_targets_cli(config: dict, json_output: bool = False) -> int:
    try:
        pgs, hub_http = _fetch_registry(config)
    except HubUnreachable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if json_output:
        _print_json({"hub": hub_http, "playgrounds": pgs})
        return 0

    if not pgs:
        print(f"hub at {hub_http}: no playgrounds registered")
        return 0

    running = [p for p in pgs if p.get("role") == "playground"]
    launchers = [p for p in pgs if p.get("role") == "launcher"]
    panels = [p for p in pgs if p.get("role") == "panel"]
    running_names = {p.get("name") for p in running}

    print(f"hub at {hub_http}:")
    if running:
        print("  Running:")
        for pg in sorted(running, key=lambda p: p.get("name", "")):
            line = f"    {pg.get('name', '?'):<16}"
            line += f" :{pg.get('port', '?'):<6}"
            if pg.get("group"):
                line += f" group={pg['group']!r}"
            if pg.get("pid"):
                line += f"  pid {pg['pid']}"
            print(line)
    if launchers:
        print("  Launchers:")
        for lc in sorted(launchers, key=lambda p: p.get("name", "")):
            grp = lc.get("group") or lc.get("name", "?")
            avail = [n for n in (lc.get("available") or [])
                     if n not in running_names]
            print(f"    {lc.get('name', '?'):<16} group={grp!r}")
            if avail:
                print(f"      available: {', '.join(avail)}")
    if panels:
        print("  Panels:")
        for pn in sorted(panels, key=lambda p: p.get("name", "")):
            print(f"    {pn.get('name', '?'):<16} :{pn.get('port', '?')}")
    return 0


def _available_cli(config: dict, json_output: bool = False) -> int:
    try:
        pgs, hub_http = _fetch_registry(config)
    except HubUnreachable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    running_names = {p.get("name") for p in pgs
                     if p.get("role") == "playground"}
    available = []
    for lc in pgs:
        if lc.get("role") != "launcher":
            continue
        for name in (lc.get("available") or []):
            if name and name not in running_names:
                available.append({"name": name, "group": lc.get("group")})

    if json_output:
        _print_json({"hub": hub_http, "available": available})
        return 0

    if not available:
        print(f"hub at {hub_http}: nothing available to start")
        return 0
    print(f"hub at {hub_http}:")
    for item in sorted(available, key=lambda x: x["name"]):
        suffix = (f"  (group={item['group']!r})" if item.get("group")
                  else "")
        print(f"  {item['name']}{suffix}")
    return 0


def _launcher_cmd_cli(config: dict, kind: str, name: str,
                      json_output: bool) -> int:
    if not name:
        print(f"error: missing NAME for {kind}", file=sys.stderr)
        return 2

    try:
        _fetch_registry(config)
    except HubUnreachable as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    msg = rc_repl._hub_one_shot(
        config,
        send_msg={
            "type":       f"launcher_{kind}",
            "name":       name,
            "request_id": uuid.uuid4().hex,
        },
        wait_for="launcher_result",
        timeout=15.0,
    )
    if msg is None:
        if json_output:
            print(json.dumps({
                "success": False,
                "message": "no response from hub",
            }))
        return 1

    if json_output:
        print(json.dumps(msg))
    else:
        if msg.get("success"):
            print(f"ok: {msg.get('message', kind + ' ' + name)}")
        else:
            print(f"error: {msg.get('message', 'operation failed')}",
                  file=sys.stderr)

    return 0 if msg.get("success") else 1


def _shutdown_hub_cli(config: dict, json_output: bool = False) -> int:
    try:
        _fetch_registry(config)
    except HubUnreachable as e:
        if json_output:
            print(json.dumps({"success": False, "message": str(e)}))
        else:
            print(f"error: {e}", file=sys.stderr)
        return 2

    msg = rc_repl._hub_one_shot(
        config,
        send_msg={"type": "shutdown_hub"},
        wait_for="shutdown_ack",
        timeout=5.0,
    )
    if msg is None:
        if json_output:
            print(json.dumps({
                "success": False,
                "message": "hub reachable but did not ack shutdown",
            }))
        return 1

    if json_output:
        print(json.dumps({"success": True, "ack": msg}))
    else:
        print("ok: hub shutdown requested (all registered "
              "playgrounds will exit)")
    return 0


# ============================================================================
#  Target-bound command implementations
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
            "  remote_cli --target segment ls\n"
            "  remote_cli --target segment set settings.threshold=0.55\n"
            "  remote_cli --target segment action action.segmentAll\n"
            "  remote_cli --target segment script flow.json\n"
            "  remote_cli --target board --direct ls     # bypass hub proxy\n"
            "  remote_cli --url ws://127.0.0.1:8773/remote ls  # standalone\n"
            "  remote_cli list-targets\n"
            "  remote_cli available\n"
            "  remote_cli start segment\n"
            "  remote_cli stop  board\n"
            "  remote_cli restart cone\n"
            "  remote_cli shutdown-hub\n"
            "  remote_cli --target segment                # target-bound REPL\n"
            "  remote_cli                                 # hub-only REPL\n"
        ),
    )
    p.add_argument("--url", default=None,
                   help="Literal WebSocket URL (ws://host:port/remote). "
                        "Bypasses hub discovery. Use this for standalone "
                        "playgrounds when the hub is not running.")
    p.add_argument("--target", default=None,
                   help="Playground name; resolved through the hub's "
                        "registry (e.g. board, cone, sam3). Panels and "
                        "launchers are listed by `list-targets` but "
                        "cannot be targeted.")
    p.add_argument("--direct", action="store_true",
                   help="With --target: connect to the playground's own "
                        "port instead of through the hub proxy. The hub "
                        "is still queried for the port, so it must be "
                        "reachable.")
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

    listtgt = sub.add_parser(
        "list-targets",
        help="List playgrounds registered with the hub (one-shot HTTP)",
    )
    listtgt.set_defaults(fn=None)

    avail = sub.add_parser(
        "available",
        help="List playgrounds a launcher can start (one-shot HTTP)",
    )
    avail.set_defaults(fn=None)

    start = sub.add_parser(
        "start",
        help="Ask the launcher to start a playground",
    )
    start.add_argument("name")
    start.set_defaults(fn=None)

    stop = sub.add_parser(
        "stop",
        help="Ask the launcher to stop a playground (keeps the group up)",
    )
    stop.add_argument("name")
    stop.set_defaults(fn=None)

    restart = sub.add_parser(
        "restart",
        help="Stop then start a playground",
    )
    restart.add_argument("name")
    restart.set_defaults(fn=None)

    shutdown_hub = sub.add_parser(
        "shutdown-hub",
        help="Ask the hub to shut down (and every playground it manages)",
    )
    shutdown_hub.set_defaults(fn=None)

    return p


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

    # Hub commands that do not need a target connection.
    if args.cmd == "list-targets":
        return _list_targets_cli(config, args.json)
    if args.cmd == "available":
        return _available_cli(config, args.json)
    if args.cmd == "start":
        return _launcher_cmd_cli(config, "start", args.name, args.json)
    if args.cmd == "stop":
        return _launcher_cmd_cli(config, "stop", args.name, args.json)
    if args.cmd == "restart":
        return _launcher_cmd_cli(config, "restart", args.name, args.json)
    if args.cmd == "shutdown-hub":
        return _shutdown_hub_cli(config, args.json)

    target_spec = args.target or args.url

    # No subcommand → REPL.  If no target is given, enter hub-only
    # mode rather than erroring; hub verbs (start/stop/available/
    # kill/shutdown-hub/pg-list) work without a target.
    if args.cmd is None:
        if not target_spec:
            return rc_repl.run(None, config,
                               target_name=None,
                               verbose=args.verbose)
        try:
            url = resolve_target(config, target_spec, direct=args.direct)
        except (ValueError, HubUnreachable, UnknownTarget) as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return rc_repl.run(url, config,
                           target_name=args.target,
                           verbose=args.verbose)

    # A target-bound subcommand.  Resolve first, then connect.
    try:
        url = resolve_target(config, target_spec, direct=args.direct)
    except (ValueError, HubUnreachable, UnknownTarget) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

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
