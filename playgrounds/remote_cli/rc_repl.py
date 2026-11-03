"""
rc_repl — interactive readline shell for a pgkit playground.

    remote_cli --target segment      # enter REPL

Verbs mirror the one-shot CLI subcommands. Tab completion covers
field names (from `schema`), action kinds, level names and item names
(from `entities`), so a user can explore the playground without
memorizing names.

Built-in colon commands:
    :help / :?   print this help
    :refresh     request fresh state from the browser
    :schema      dump schema
    :entities    dump entities
    :quit / :q   exit

Hub-management verbs (available from any REPL; they act on the hub,
not on the connected playground):
    pg-list                list every playground registered with the hub
    kill NAME [--force]    shut down a playground (cooperative, or SIGTERM
                           escalation with --force)
    shutdown-hub           ask the hub to exit (takes every playground
                           with it)

All three are one-shot: they open a short-lived connection to the
hub's management WS, act, and close.  No polling.

TAG
===
The prompt shows the target name (`segment>`) when the REPL was entered
via `--target segment`; it shows the full URL when entered via `--url`.
`main()` passes the name through `target_name=`, so the REPL does not
need to reverse-look it up.
"""

from __future__ import annotations

import json
import shlex
import sys
from typing import Optional

try:
    import readline  # noqa: F401 — importing enables line editing
    _READLINE = True
except ImportError:
    _READLINE = False

from pgkit.remote_cli_client import (
    RemoteCliClient,
    RemoteCliError,
    RemoteCommandFailed,
    RemoteTimeout,
    parse_value,
)


HELP_TEXT = """\
Commands:
  ls [PREFIX]                  List fields with values
  show NAME                    Show one field's value and metadata
  schema [PREFIX]              List field metadata
  actions [PREFIX]             List registered actions
  entities                     List entity levels and items
  presence                     Show presence counts
  state                        Dump full state as JSON
  set NAME=VALUE [NAME=VALUE …] Set field values
  action KIND [PAYLOAD_JSON]   Fire an action
  select LEVEL NAME            Select an entity item
  deselect                     Deselect the current item
  raw JSON                     Send an arbitrary JSON message

Hub management (one-shot, event-driven, no polling):
  pg-list                      List playgrounds registered with the hub
  kill NAME [--force]          Shut down a playground (cooperative;
                               --force escalates to SIGTERM)
  shutdown-hub                 Ask the hub to exit (takes every
                               playground with it)

Colon commands:
  :refresh                     Request fresh state from the browser
  :schema / :entities          Dump schema / entities as JSON
  :quit / :q                   Exit
  :help / :?                   Print this help

Tab completes field names, action kinds, and entity items.
"""


# ============================================================================
#  Completion
# ============================================================================

_completion_source = {
    "fields":  [],
    "actions": [],
    "levels":  [],
    "items":   {},   # level -> [item names]
    "verbs":   [],
}


def _refresh_completion(client: RemoteCliClient) -> None:
    schema = client.schema()
    entities = client.entities() or {}
    order = client.entity_order()

    fields = sorted(schema.keys())
    actions = sorted(n for n, m in schema.items()
                     if (m or {}).get("kind") == "action")
    items = {}
    for level in order:
        e = entities.get(level) or {}
        items[level] = [it.get("name")
                        for it in (e.get("items") or [])
                        if it.get("name")]

    _completion_source.update({
        "fields":  fields,
        "actions": actions,
        "levels":  order,
        "items":   items,
        "verbs": [
            "ls", "show", "schema", "actions", "entities",
            "presence", "state", "set", "action",
            "select", "deselect", "raw",
            "pg-list", "kill", "shutdown-hub",
            "help", "quit", "exit",
        ],
    })


def _make_completer():
    def _complete(text: str, state: int):
        try:
            tokens = readline.get_line_buffer().split()
        except Exception:
            tokens = []
        completions = _compute_completions(tokens, text)
        try:
            return completions[state]
        except IndexError:
            return None
    return _complete


def _compute_completions(tokens, text):
    src = _completion_source
    verb = tokens[0] if tokens else ""

    if len(tokens) <= 1 and not text.startswith("-"):
        return _filter(src["verbs"], text)

    if verb == "set":
        return _filter(src["fields"], text)

    if verb == "action":
        return _filter(src["actions"], text)

    if verb == "show":
        return _filter(src["fields"], text)

    if verb == "select":
        if len(tokens) == 2:
            return _filter(src["levels"], text)
        if len(tokens) >= 3:
            level = tokens[1]
            return _filter(src["items"].get(level, []), text)

    if verb == "kill":
        # Complete from the cached hub registry (populated by pg-list).
        if len(tokens) == 2:
            return _filter(_hub_pg_names, text)

    return []


def _filter(items, prefix):
    return [i for i in items if i.startswith(prefix)]


def _install_completion():
    if not _READLINE:
        return
    try:
        readline.set_completer(_make_completer())
        readline.parse_and_bind("tab: complete")
    except Exception:
        pass


# ============================================================================
#  Hub helpers (one-shot WS to /__hub_ws; no persistent connection)
# ============================================================================

# Cached from the last `pg-list`, used only for tab completion on `kill`.
_hub_pg_names = []


def _hub_ws_url(config: dict) -> str:
    hub = config.get("hub", "ws://127.0.0.1:8780")
    return hub.rstrip("/") + "/__hub_ws"


def _hub_one_shot(config: dict, send_msg: dict,
                  wait_for: str, timeout: float = 5.0):
    """Open a one-shot management WS, send one message, wait for a
    reply of type `wait_for`, close.  Returns the reply dict or None.

    The hub pushes an initial `playgrounds` event on every connect;
    `send_msg` may be None to just read that first event.
    """
    try:
        from websockets.sync.client import connect as _ws_connect
    except ImportError:
        print("error: 'websockets' library is required for hub actions",
              file=sys.stderr)
        return None

    url = _hub_ws_url(config)
    try:
        ws = _ws_connect(url, open_timeout=timeout, close_timeout=2.0)
    except Exception as e:
        print(f"error: could not connect to hub at {url}: {e}",
              file=sys.stderr)
        return None

    try:
        ws.send(json.dumps({"type": "hello", "role": "panel"}))
        if send_msg is not None:
            ws.send(json.dumps(send_msg))

        import time as _time
        deadline = _time.monotonic() + timeout
        while True:
            remaining = deadline - _time.monotonic()
            if remaining <= 0:
                print("error: hub did not respond in time", file=sys.stderr)
                return None
            try:
                raw = ws.recv(timeout=min(remaining, 1.0))
            except TimeoutError:
                continue
            except Exception as e:
                print(f"error: hub connection failed: {e}", file=sys.stderr)
                return None
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("type") == wait_for:
                return msg
            # Store the initial playground list for later completion.
            if msg.get("type") == "playgrounds":
                _hub_pg_names[:] = [
                    p.get("name") for p in (msg.get("playgrounds") or [])
                    if p.get("name")
                ]
                # If we only wanted the playgrounds event, return now.
                if wait_for == "playgrounds":
                    return msg
    finally:
        try:
            ws.close()
        except Exception:
            pass


# ============================================================================
#  Dispatch
# ============================================================================

def _dispatch(client: RemoteCliClient, config: dict, line: str) -> bool:
    """Run one line of input. Returns False to exit."""
    line = line.strip()
    if not line:
        return True

    if line.startswith(":"):
        verb = line[1:].split()[0] if line[1:] else ""
        if verb in ("q", "quit", "exit"):
            return False
        if verb in ("h", "help", "?"):
            print(HELP_TEXT, end="")
            return True
        if verb == "refresh":
            try:
                client.request_state_and_wait(timeout=5.0)
                _refresh_completion(client)
                print("refreshed")
            except RemoteTimeout:
                print("refresh timed out", file=sys.stderr)
            return True
        if verb == "schema":
            print(json.dumps(client.schema(), indent=2, default=str))
            return True
        if verb == "entities":
            print(json.dumps({
                "levels": client.entities() or {},
                "order": client.entity_order(),
            }, indent=2, default=str))
            return True
        print(f"unknown command :{verb}", file=sys.stderr)
        return True

    try:
        tokens = shlex.split(line)
    except ValueError as e:
        print(f"parse error: {e}", file=sys.stderr)
        return True

    if not tokens:
        return True

    verb, rest = tokens[0], tokens[1:]

    try:
        if verb in ("q", "quit", "exit"):
            return False
        if verb in ("h", "help", "?"):
            print(HELP_TEXT, end="")
            return True
        if verb == "ls":
            prefix = rest[0] if rest else None
            _print_ls(client, prefix)
        elif verb == "show":
            if not rest:
                print("usage: show NAME", file=sys.stderr)
                return True
            _print_show(client, rest[0])
        elif verb == "schema":
            _print_schema(client, rest[0] if rest else None)
        elif verb == "actions":
            _print_actions(client, rest[0] if rest else None)
        elif verb == "entities":
            _print_entities(client)
        elif verb == "presence":
            p = client.presence()
            print(f"browser={p['browser']}  remote={p['remote']}")
        elif verb == "state":
            print(json.dumps({
                "values": client.values(),
                "presence": client.presence(),
            }, indent=2, default=str))
        elif verb == "set":
            values = _parse_set_args(rest)
            if not values:
                print("usage: set NAME=VALUE [NAME=VALUE ...]",
                      file=sys.stderr)
                return True
            applied = client.patch_and_verify(values)
            _refresh_completion(client)
            for k, v in values.items():
                if applied.get(k) != v:
                    print(f"  {k}: requested {v!r} → applied "
                          f"{applied.get(k)!r}")
                else:
                    print(f"  {k} = {v!r}")
        elif verb == "action":
            if not rest:
                print("usage: action KIND [PAYLOAD_JSON]", file=sys.stderr)
                return True
            kind = rest[0]
            payload = None
            if len(rest) > 1:
                try:
                    payload = json.loads(rest[1])
                except json.JSONDecodeError as e:
                    print(f"invalid payload JSON: {e}", file=sys.stderr)
                    return True
            result = client.command(kind, payload)
            print(f"ok: {kind}")
            if result.get("result") is not None:
                print(f"  {json.dumps(result['result'], default=str)}")
        elif verb == "select":
            if len(rest) < 2:
                print("usage: select LEVEL NAME", file=sys.stderr)
                return True
            client.command("select", {"level": rest[0], "name": rest[1]})
            print(f"ok: selected {rest[0]}/{rest[1]}")
        elif verb == "deselect":
            client.command("deselect", {})
            print("ok: deselected")
        elif verb == "raw":
            if not rest:
                print("usage: raw JSON", file=sys.stderr)
                return True
            try:
                msg = json.loads(rest[0])
            except json.JSONDecodeError as e:
                print(f"invalid JSON: {e}", file=sys.stderr)
                return True
            client.raw_send(msg)
            print("sent")
        elif verb == "pg-list":
            _repl_pg_list(config)
        elif verb == "kill":
            if not rest:
                print("usage: kill NAME [--force]", file=sys.stderr)
                return True
            name = rest[0]
            hard = "--force" in rest[1:] or "-f" in rest[1:]
            _repl_kill(config, name, hard)
        elif verb == "shutdown-hub":
            _repl_shutdown_hub(config)
        else:
            print(f"unknown command: {verb}", file=sys.stderr)

    except RemoteCommandFailed as e:
        print(f"error: {e}", file=sys.stderr)
    except RemoteTimeout as e:
        print(f"error: {e}", file=sys.stderr)
    except RemoteCliError as e:
        print(f"error: {e}", file=sys.stderr)

    return True


def _repl_pg_list(config: dict):
    msg = _hub_one_shot(config, send_msg=None, wait_for="playgrounds")
    if msg is None:
        return
    pgs = msg.get("playgrounds") or []
    if not pgs:
        print("(no playgrounds registered)")
        return
    for pg in pgs:
        line = (f"  {pg.get('name', '?'):<16}"
                f" {pg.get('role', '?'):<10}"
                f" :{pg.get('port', '?')}")
        if pg.get("pid"):
            line += f"  pid {pg['pid']}"
        print(line)


def _repl_kill(config: dict, name: str, hard: bool):
    msg = _hub_one_shot(
        config,
        send_msg={"type": "kill", "name": name, "hard": hard},
        wait_for="kill_result",
        timeout=10.0 if hard else 6.0,
    )
    if msg is None:
        return
    if msg.get("success"):
        print(f"ok: {msg.get('message', name + ' shut down')}")
    else:
        print(f"error: {msg.get('message', 'kill failed')}",
              file=sys.stderr)


def _repl_shutdown_hub(config: dict):
    msg = _hub_one_shot(
        config,
        send_msg={"type": "shutdown_hub"},
        wait_for="shutdown_ack",
        timeout=5.0,
    )
    if msg is None:
        return
    print("ok: hub shutdown requested (all playgrounds will exit)")


def _print_ls(client, prefix):
    values = client.values()
    schema = client.schema()
    names = sorted(values.keys())
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    if not names:
        print("(no fields)")
        return
    width = max(len(n) for n in names)
    for n in names:
        meta = schema.get(n) or {}
        kind = meta.get("kind", "?")
        val = json.dumps(values[n], default=str)
        if len(val) > 60:
            val = val[:59] + "…"
        print(f"{n:<{width}}  {val}   [{kind}]")


def _print_show(client, name):
    values = client.values()
    schema = client.schema()
    if name not in values:
        print(f"unknown field {name!r}", file=sys.stderr)
        return
    print(f"name:  {name}")
    print(f"value: {json.dumps(values[name], default=str)}")
    for k, v in sorted((schema.get(name) or {}).items()):
        print(f"  {k}: {json.dumps(v, default=str)}")


def _print_schema(client, prefix):
    schema = client.schema()
    names = sorted(schema.keys())
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    for n in names:
        meta = schema[n] or {}
        kind = meta.get("kind", "?")
        label = meta.get("label", "")
        print(f"{n:<32} {kind:<8} {label}")


def _print_actions(client, prefix):
    schema = client.schema()
    names = sorted(n for n, m in schema.items()
                   if (m or {}).get("kind") == "action")
    if prefix:
        names = [n for n in names if n.startswith(prefix)]
    for n in names:
        meta = schema[n] or {}
        label = meta.get("label", "")
        scope = meta.get("scope", "global")
        print(f"{n:<32} {label:<24} ({scope})")


def _print_entities(client):
    entities = client.entities() or {}
    order = client.entity_order()
    for level in order:
        e = entities.get(level) or {}
        print(f"{e.get('label', level)}:")
        for item in e.get("items") or []:
            mark = " [selected]" if item.get("name") == e.get("selected") else ""
            print(f"  {item.get('name')}  {item.get('label', '')}{mark}")


def _parse_set_args(args):
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
        raise ValueError("mixed forms: use all NAME=VALUE or all NAME VALUE")
    if len(args) % 2 != 0:
        raise ValueError(f"odd number of args: {len(args)}")
    out = {}
    for i in range(0, len(args), 2):
        out[args[i]] = parse_value(args[i + 1])
    return out


# ============================================================================
#  Entry point
# ============================================================================

def run(url: str, config: dict, *, target_name: Optional[str] = None,
        verbose: bool = False) -> int:
    client = RemoteCliClient(url)
    try:
        client.connect(require_browser=False)
    except RemoteCliError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    _refresh_completion(client)
    _install_completion()

    tag = target_name or url
    presence = client.presence()
    if presence["browser"] == 0:
        print(f"connected to {tag} (no browser connected)")
        print("Waiting for a browser tab; :quit to exit.", file=sys.stderr)
    else:
        print(f"connected to {tag} "
              f"({presence['browser']} browser, "
              f"{presence['remote']} remote)")

    def on_presence(msg):
        _refresh_completion(client)
    client.on("presence", on_presence)

    while True:
        try:
            line = input(f"{tag}> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            if not _dispatch(client, config, line):
                break
        except Exception as e:
            print(f"error: {e}", file=sys.stderr)

    client.close()
    return 0
