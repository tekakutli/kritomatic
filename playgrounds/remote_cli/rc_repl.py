"""
rc_repl — interactive readline shell for a pgkit playground.

    remote_cli --target segment      # enter REPL bound to a playground
    remote_cli                       # enter hub-only REPL (no target)

Verbs mirror the one-shot CLI subcommands. Tab completion covers
field names (from `schema`), action kinds, level names and item names
(from `entities`), and playground names (from the hub's `playgrounds`
broadcast, running + available-to-start).

Built-in colon commands:
    :help / :?   print this help
    :refresh     request fresh state from the browser
    :schema      dump schema
    :entities    dump entities
    :quit / :q   exit

Hub-management verbs (available from any REPL; they act on the hub,
not on the connected playground):

    pg-list                list every playground registered with the hub
    available              list playgrounds that a launcher can start
    kill NAME [--force]    shut down a playground (cooperative, or SIGTERM
                           escalation with --force).  For a grouped
                           playground, this takes down its whole group.
    start NAME             ask the launcher to start NAME in-process
    stop NAME              ask the launcher to stop NAME (only NAME;
                           the launcher and other members keep running)
    restart NAME           stop, then start
    shutdown-hub           ask the hub to exit (takes every playground
                           with it)

All hub-management verbs are one-shot: they open a short-lived
connection to the hub's management WS, act, and close.  No polling.

HUB-ONLY MODE
=============
`remote_cli` with no `--target` / `--url` starts the REPL with no
client bound.  Target-bound verbs (`ls`, `show`, `schema`, `actions`,
`entities`, `presence`, `state`, `set`, `action`, `select`,
`deselect`, `raw`) print a hint and return.  Hub verbs work normally.
This is the right starting point for a fresh session where nothing is
running yet and the first thing you want to do is `start board`.

TAG
===
The prompt shows the target name (`segment>`) when the REPL was
entered via `--target segment`; it shows `<hub>` when entered with no
target; and it shows the full URL when entered via `--url`.
`main()` passes the name through `target_name=`, so the REPL does not
need to reverse-look it up.
"""

from __future__ import annotations

import json
import shlex
import sys
import uuid
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
Target-bound commands:
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
  pg-list                      List every registration (running,
                               launchers with their available list,
                               panels)
  available                    List playgrounds a launcher can start
  kill NAME [--force]          Shut down a playground (for a grouped
                               playground this takes down its whole
                               group; use `stop` to shut down one
                               member without touching the rest)
  start NAME                   Ask the launcher to start NAME
  stop NAME                    Ask the launcher to stop just NAME
  restart NAME                 Stop, then start
  shutdown-hub                 Ask the hub to exit (takes every
                               playground with it)

Colon commands:
  :refresh                     Request fresh state from the browser
  :schema / :entities          Dump schema / entities as JSON
  :quit / :q                   Exit
  :help / :?                   Print this help

Tab completes field names, action kinds, entity items, and playground
names (for kill / start / stop / restart).
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
            "pg-list", "available", "kill",
            "start", "stop", "restart", "shutdown-hub",
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

    if verb in ("kill", "start", "stop", "restart"):
        # Complete from the cached hub registry (running + available,
        # populated by `pg-list` or by the initial pre-populate).
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

# Cached from the last `pg-list`; also pre-populated on session start
# so `start <TAB>` works before the user has run anything.  Includes
# running names and launcher-available names.
_hub_pg_names = []


def _set_hub_pg_names_from_playgrounds(pgs):
    names = set()
    for p in pgs:
        n = p.get("name")
        if n and p.get("role") in ("playground", "launcher"):
            names.add(n)
        if p.get("role") == "launcher":
            for a in (p.get("available") or []):
                if a:
                    names.add(a)
    _hub_pg_names[:] = sorted(names)


def _hub_ws_url(config: dict) -> str:
    hub = config.get("hub", "ws://127.0.0.1:8780")
    return hub.rstrip("/") + "/__hub_ws"


def _hub_one_shot(config: dict, send_msg: dict,
                  wait_for: str, timeout: float = 5.0):
    """Open a one-shot management WS, send one message, wait for a
    reply of type `wait_for`, close.  Returns the reply dict or None.

    The hub pushes an initial `playgrounds` event on every connect;
    that event is used to update `_hub_pg_names` for completion, then
    the loop continues waiting for `wait_for` unless `wait_for` is
    `"playgrounds"` itself.
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
            if msg.get("type") == "playgrounds":
                _set_hub_pg_names_from_playgrounds(
                    msg.get("playgrounds") or []
                )
                if wait_for == "playgrounds":
                    return msg
    finally:
        try:
            ws.close()
        except Exception:
            pass


def _populate_hub_pg_names(config: dict) -> None:
    """Best-effort: fetch the playground list once so tab-completion
    works before the user has run `pg-list`."""
    _hub_one_shot(config, send_msg=None, wait_for="playgrounds",
                  timeout=3.0)


# ============================================================================
#  Hub verbs
# ============================================================================

def _repl_pg_list(config: dict):
    msg = _hub_one_shot(config, send_msg=None, wait_for="playgrounds")
    if msg is None:
        return
    pgs = msg.get("playgrounds") or []
    if not pgs:
        print("(no playgrounds registered)")
        return

    running = [p for p in pgs if p.get("role") == "playground"]
    launchers = [p for p in pgs if p.get("role") == "launcher"]
    panels = [p for p in pgs if p.get("role") == "panel"]
    running_names = {p.get("name") for p in running}

    if running:
        print("Running:")
        for pg in sorted(running, key=lambda p: p.get("name", "")):
            line = f"  {pg.get('name', '?'):<16}"
            line += f" :{pg.get('port', '?'):<6}"
            if pg.get("group"):
                line += f" group={pg['group']!r}"
            if pg.get("pid"):
                line += f"  pid {pg['pid']}"
            print(line)

    if launchers:
        print()
        print("Launchers:")
        for lc in sorted(launchers, key=lambda p: p.get("name", "")):
            grp = lc.get("group") or lc.get("name", "?")
            avail = [n for n in (lc.get("available") or [])
                     if n not in running_names]
            print(f"  {lc.get('name', '?'):<16} group={grp!r}")
            if avail:
                print(f"    available: {', '.join(avail)}")

    if panels:
        print()
        print("Panels:")
        for pn in sorted(panels, key=lambda p: p.get("name", "")):
            print(f"  {pn.get('name', '?'):<16} :{pn.get('port', '?')}")


def _repl_available(config: dict):
    msg = _hub_one_shot(config, send_msg=None, wait_for="playgrounds")
    if msg is None:
        return
    pgs = msg.get("playgrounds") or []
    running_names = {p.get("name") for p in pgs
                     if p.get("role") == "playground"}
    available = []
    for lc in pgs:
        if lc.get("role") != "launcher":
            continue
        for name in (lc.get("available") or []):
            if name and name not in running_names:
                available.append((name, lc.get("group")))
    if not available:
        print("(nothing available to start)")
        return
    for name, grp in sorted(available):
        suffix = f"  (group={grp!r})" if grp else ""
        print(f"  {name}{suffix}")


def _repl_launcher_cmd(config: dict, kind: str, name: str):
    """Send launcher_<kind> for `name` and print the result.

    kind ∈ {"start", "stop", "restart"}.
    """
    if not name:
        print(f"usage: {kind} NAME", file=sys.stderr)
        return
    msg = _hub_one_shot(
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
        return
    if msg.get("success"):
        print(f"ok: {msg.get('message', kind + ' ' + name)}")
    else:
        reason = msg.get("message", f"{kind} failed")
        print(f"error: {reason}", file=sys.stderr)
        if "no launcher manages" in reason:
            print(f"  ({name!r} is standalone; use `kill {name}` to shut it "
                  f"down, but note that on a grouped playground `kill` "
                  f"takes down its whole group)", file=sys.stderr)


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


# ============================================================================
#  Target-bound verbs (need a client)
# ============================================================================

def _require_client(client) -> bool:
    if client is not None:
        return True
    print("no target bound to this session; pass --target NAME at start. "
          "For hub-only work use pg-list / available / start / stop / kill.",
          file=sys.stderr)
    return False


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
#  Dispatch
# ============================================================================

def _dispatch(client, config, line):
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
            if not _require_client(client): return True
            try:
                client.request_state_and_wait(timeout=5.0)
                _refresh_completion(client)
                print("refreshed")
            except RemoteTimeout:
                print("refresh timed out", file=sys.stderr)
            return True
        if verb == "schema":
            if not _require_client(client): return True
            print(json.dumps(client.schema(), indent=2, default=str))
            return True
        if verb == "entities":
            if not _require_client(client): return True
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

        # ---- hub verbs (work with or without a target) ----
        if verb == "pg-list":
            _repl_pg_list(config)
        elif verb == "available":
            _repl_available(config)
        elif verb == "kill":
            if not rest:
                print("usage: kill NAME [--force]", file=sys.stderr)
                return True
            name = rest[0]
            hard = "--force" in rest[1:] or "-f" in rest[1:]
            _repl_kill(config, name, hard)
        elif verb == "start":
            _repl_launcher_cmd(config, "start",
                               rest[0] if rest else "")
        elif verb == "stop":
            _repl_launcher_cmd(config, "stop",
                               rest[0] if rest else "")
        elif verb == "restart":
            _repl_launcher_cmd(config, "restart",
                               rest[0] if rest else "")
        elif verb == "shutdown-hub":
            _repl_shutdown_hub(config)

        # ---- target-bound verbs ----
        elif verb == "ls":
            if not _require_client(client): return True
            _print_ls(client, rest[0] if rest else None)
        elif verb == "show":
            if not _require_client(client): return True
            if not rest:
                print("usage: show NAME", file=sys.stderr)
                return True
            _print_show(client, rest[0])
        elif verb == "schema":
            if not _require_client(client): return True
            _print_schema(client, rest[0] if rest else None)
        elif verb == "actions":
            if not _require_client(client): return True
            _print_actions(client, rest[0] if rest else None)
        elif verb == "entities":
            if not _require_client(client): return True
            _print_entities(client)
        elif verb == "presence":
            if not _require_client(client): return True
            p = client.presence()
            print(f"browser={p['browser']}  remote={p['remote']}")
        elif verb == "state":
            if not _require_client(client): return True
            print(json.dumps({
                "values": client.values(),
                "presence": client.presence(),
            }, indent=2, default=str))
        elif verb == "set":
            if not _require_client(client): return True
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
            if not _require_client(client): return True
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
            if not _require_client(client): return True
            if len(rest) < 2:
                print("usage: select LEVEL NAME", file=sys.stderr)
                return True
            client.command("select", {"level": rest[0], "name": rest[1]})
            print(f"ok: selected {rest[0]}/{rest[1]}")
        elif verb == "deselect":
            if not _require_client(client): return True
            client.command("deselect", {})
            print("ok: deselected")
        elif verb == "raw":
            if not _require_client(client): return True
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
        else:
            print(f"unknown command: {verb}", file=sys.stderr)

    except RemoteCommandFailed as e:
        print(f"error: {e}", file=sys.stderr)
    except RemoteTimeout as e:
        print(f"error: {e}", file=sys.stderr)
    except RemoteCliError as e:
        print(f"error: {e}", file=sys.stderr)

    return True


# ============================================================================
#  Entry point
# ============================================================================

def run(url, config, *, target_name=None, verbose=False):
    """Enter the REPL.

    `url=None` means hub-only mode: no target client, hub verbs only.
    Target-bound verbs print a hint on use.  The prompt shows `<hub>`.
    """
    client = None
    if url is not None:
        client = RemoteCliClient(url)
        try:
            client.connect(require_browser=False)
        except RemoteCliError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        _refresh_completion(client)

    # Best-effort pre-population of tab-completion sources for hub
    # verbs.  If the hub is unreachable this is a no-op; the user can
    # still use `pg-list` after the hub comes up.
    try:
        _populate_hub_pg_names(config)
    except Exception:
        pass

    _install_completion()

    tag = target_name or ("<hub>" if url is None else url)
    if client is not None:
        presence = client.presence()
        if presence["browser"] == 0:
            print(f"connected to {tag} (no browser connected)")
            print("Waiting for a browser tab; :quit to exit.",
                  file=sys.stderr)
        else:
            print(f"connected to {tag} "
                  f"({presence['browser']} browser, "
                  f"{presence['remote']} remote)")
        def on_presence(msg):
            _refresh_completion(client)
        client.on("presence", on_presence)
    else:
        print("Hub-only session.  'pg-list', 'available', 'start NAME', "
              "'stop NAME', 'kill NAME', 'shutdown-hub'.")
        print("For a target-bound REPL, start with --target NAME.")
        print()

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

    if client is not None:
        client.close()
    return 0
