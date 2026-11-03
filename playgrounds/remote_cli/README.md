# remote_cli

A terminal client for pgkit playgrounds. Speaks the same WebSocket
protocol as `remote_panel`; anything the browser panel can do to a
running playground, `remote_cli` can do from a shell or a Python
script.

## Discovery

Playgrounds register themselves with the pgkit hub at startup. The
hub's `/__playgrounds` endpoint returns the current registry —
`{name, port, role, pid, html_file, connected_at}` for each — and
`remote_cli` derives every URL it needs from that registry.

There is no per-playground map in the config. One hub URL and an
optional default target are the whole configuration; a new playground
that registers with the hub is targetable by name with zero CLI
changes.

Three ways to name a target:

| form | result |
|------|--------|
| `--target NAME` | `ws://<hub>/NAME/remote` (through the hub proxy) |
| `--target NAME --direct` | `ws://<hub-host>:<NAME-port>/remote` (bypass the proxy) |
| `--url URL` | literal, no discovery — for standalone playgrounds |

`--direct` still asks the hub for the port, so the hub must be
reachable. `--url` is the only mode that works without a hub.

## Install

`remote_cli` reads `pgkit` from a sibling directory. From
`playgrounds/`:

```bash
cd playgrounds
python remote_cli/remote_cli.py --help
```

The CLI's dependencies are the stdlib plus `websockets >= 12`
(already required by the playgrounds). `remote_cli` is not packaged;
invoke it in place.

## Config

`remote_cli/config.json`, next to `remote_cli.py`. Ships with the CLI,
is checked into the repo, and is edited in place.

```json
{
  "hub": "ws://127.0.0.1:8780",
  "default_target": "sam3",
  "timeout": 10.0
}
```

- `hub` — the hub's WebSocket base. The `/__playgrounds` HTTP query
  and the `/__hub_ws` management channel are both derived from it.
- `default_target` — used when no `--target`/`--url` is given.
- `timeout` — default command/wait timeout, in seconds.

A missing or unparseable config falls back to the same defaults with
a one-line warning on stderr; the CLI keeps working either way.

## Common invocations

```bash
# List what is registered right now (one-shot HTTP, no WS)
python remote_cli/remote_cli.py list-targets

# Inspect a playground
python remote_cli/remote_cli.py --target sam3 ls
python remote_cli/remote_cli.py --target sam3 schema
python remote_cli/remote_cli.py --target sam3 actions
python remote_cli/remote_cli.py --target sam3 entities

# Set fields and run actions
python remote_cli/remote_cli.py --target sam3 set settings.threshold=0.55
python remote_cli/remote_cli.py --target sam3 action action.segmentAll
python remote_cli/remote_cli.py --target sam3 select masks mask.0

# Scripted sequences with waits and asserts
python remote_cli/remote_cli.py --target sam3 script flow.json
python remote_cli/remote_cli.py --target sam3 validate flow.json

# Bypass the hub proxy, still using the registry
python remote_cli/remote_cli.py --target board --direct ls

# Standalone (no hub running)
python remote_cli/remote_cli.py --url ws://127.0.0.1:8773/remote ls
```

`--json` produces machine-readable output on the commands that have a
structured form (`ls`, `show`, `schema`, `actions`, `entities`,
`presence`, `state`, `action`, `list-targets`).

## Interactive REPL

Running `remote_cli` with no subcommand enters a readline shell:

```bash
python remote_cli/remote_cli.py --target sam3
sam3> ls
sam3> set settings.threshold 0.55
sam3> action action.segmentAll
sam3> entities
sam3> quit
```

The prompt shows the target name when entered via `--target`, or the
full URL when entered via `--url`.

Tab completion covers field names (from `schema`), action kinds,
level names, and item names (from `entities`).

Verbs mirror the CLI subcommands (`ls`, `show`, `schema`, `actions`,
`entities`, `presence`, `state`, `set`, `action`, `select`, `deselect`,
`raw`). Colon commands (`:help`, `:refresh`, `:schema`, `:entities`,
`:quit`) do the obvious thing.

### Hub management from the REPL

Two verbs operate on the hub, not on the connected playground. Both
are one-shot — they open a short-lived WS to `/__hub_ws`, act, and
close — so they never leave a dangling panel connection behind.

```
sam3> pg-list                # list every playground registered with the hub
sam3> kill board             # cooperative shutdown (WS shutdown message)
sam3> kill board --force     # escalate to SIGTERM if cooperative fails
```

`pg-list` also populates the tab-completion cache for `kill`'s target
argument.

## Python API

For anything more complex than the REPL, import the client directly:

```python
from pgkit.remote_cli_client import RemoteCliClient

with RemoteCliClient("ws://127.0.0.1:8780/sam3/remote") as client:
    client.bundle(
        set={
            "settings.threshold":        0.55,
            "settings.refineIterations": 2,
            "prompt.text":               "hair, woman",
        },
        actions=[
            {"kind": "action.segmentAll"},
            {"kind": "action.run"},
        ],
        wait_for_running=True,
    )
    for m in client.entities()["masks"]["items"]:
        print(m["label"])
```

`pgkit.remote_cli_client` is the same client the CLI uses; anything
the CLI can do, an external script can do. See its module docstring
for the full method list — `values`, `schema`, `entities`, `command`,
`patch_and_verify`, `wait_value`, `wait_change`, `bundle`, and the
`on` / `off` event hooks.

## Command reference

| command | what it does |
|---------|--------------|
| `ls [--prefix P]` | List fields with current values |
| `show NAME` | One field's value and metadata |
| `schema [--prefix P]` | Field metadata |
| `actions [--prefix P]` | Registered actions |
| `entities` | Entity levels, items, and selection |
| `presence` | Browser / remote counts for the target |
| `state` | Full state dump as JSON |
| `set NAME=VALUE ...` | Set one or more fields |
| `action KIND [JSON]` | Fire an action with optional payload |
| `select LEVEL NAME` | Select an entity item |
| `deselect` | Deselect the current item |
| `raw JSON` | Send an arbitrary message over the WS |
| `validate SCRIPT` | Validate a script against the live schema |
| `script SCRIPT` | Execute a JSON script of steps |
| `list-targets` | List playgrounds registered with the hub |
| (no subcommand) | Enter the interactive REPL |

## Errors

- **Hub unreachable** (no `--url`, and `/__playgrounds` doesn't
  answer): exit code 2 with the hub URL and the underlying reason.
  Use `--url` for a standalone playground.
- **Unknown target** (name not in the registry): exit code 2, listing
  the currently-registered playground names.
- **Target disconnected mid-command**: exit code 1 with the
  disconnect reason (a `RemoteNotConnected` from the client).

Scripted runs (`script`, `validate`) return exit code 1 on the first
failing step unless `--continue-on-error` is passed.
