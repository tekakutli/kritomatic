# remote_cli

A terminal client for pgkit playgrounds. Speaks the same WebSocket
protocol as `remote_panel`; anything the browser panel can do to a
running playground, `remote_cli` can do from a shell or a Python
script.

## Install

`remote_cli` reads `pgkit` from a sibling directory. From
`playgrounds/`:

```bash
cd playgrounds
python remote_cli/remote_cli.py --help
