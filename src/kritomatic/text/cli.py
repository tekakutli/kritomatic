"""
CLI for viewing and editing vector text layers via the daemon.

Two subcommands:

    kritomatic text dump [--pattern GLOB] [--output FILE]
        Emit a JSON array of full records, one per text shape.
        A layer with several text shapes yields several records,
        each distinguished by its text_index field.

    kritomatic text load [--input FILE]
        Read a JSON array of records (or a single record) and push
        each one's fields back to its shape.  Fields omitted from a
        record are preserved as-is on the shape.  Records are matched
        to shapes by (layer_name, text_index).

No daemon-side change beyond the three commands the handler exposes:
list_text_layers, get_layer_text_metadata, patch_layer_text.
"""

import argparse
import fnmatch
import json
import sys
from pathlib import Path

from kritomatic.client import KritaClient
from kritomatic.helpful_argparse import HelpfulArgumentParser


# Fields that can be pushed; everything else in a record is read-only.
_PUSHABLE = {
    'text', 'font_family', 'font_size', 'color', 'x', 'y',
    'alignment', 'rotation_deg',
}


def _matches(pattern, name):
    """Glob match if the pattern contains wildcard characters, exact
    match otherwise."""
    if any(c in pattern for c in '*?['):
        return fnmatch.fnmatch(name, pattern)
    return name == pattern


def _daemon_call(client, cmd_type, **kwargs):
    resp = client.execute(cmd_type, **kwargs)
    if not resp or resp.get('status') != 'success':
        msg = resp.get('message') if resp else 'no response'
        print(f"  ! {cmd_type}: {msg}", file=sys.stderr)
        return None
    return resp.get('data', {})


def cmd_dump(client, args):
    data = _daemon_call(client, 'list_text_layers')
    if data is None:
        return 1
    names = data.get('layers', [])

    if args.pattern:
        names = [n for n in names if _matches(args.pattern, n)]

    records = []
    for name in names:
        recs = _daemon_call(client, 'get_layer_text_metadata',
                            layer_name=name)
        if recs is None:
            continue
        # The daemon returns a list — one record per text shape in the
        # layer.  Older daemon builds returned a single dict; accept
        # both so an un-reloaded daemon still produces useful output.
        if isinstance(recs, list):
            records.extend(recs)
        elif isinstance(recs, dict):
            records.append(recs)

    output = json.dumps(records, indent=2)
    if args.output:
        Path(args.output).expanduser().write_text(output)
        print(f"Wrote {len(records)} record(s) to {args.output}",
              file=sys.stderr)
    else:
        print(output)
    return 0


def cmd_load(client, args):
    if args.input:
        raw = Path(args.input).expanduser().read_text()
    else:
        raw = sys.stdin.read()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1

    if isinstance(data, dict):
        records = [data]
    elif isinstance(data, list):
        records = data
    else:
        print("Expected a record or an array of records", file=sys.stderr)
        return 1

    succeeded = 0
    failed = 0
    for rec in records:
        if not isinstance(rec, dict):
            failed += 1
            continue
        layer_name = rec.get('layer_name')
        if not layer_name:
            print("  ! record without layer_name, skipped", file=sys.stderr)
            failed += 1
            continue

        try:
            text_index = int(rec.get('text_index', 0))
        except (TypeError, ValueError):
            text_index = 0

        patch = {k: v for k, v in rec.items()
                 if k not in ('layer_name', 'text_index') and k in _PUSHABLE}
        if not patch:
            succeeded += 1
            continue

        resp = client.execute('patch_layer_text',
                              layer_name=layer_name,
                              text_index=text_index,
                              patch=json.dumps(patch))
        if resp and resp.get('status') == 'success':
            succeeded += 1
        else:
            failed += 1
            msg = resp.get('message') if resp else 'no response'
            print(f"  ✗ {layer_name} [{text_index}]: {msg}",
                  file=sys.stderr)

    print(f"Loaded {succeeded} record(s), {failed} failed", file=sys.stderr)
    return 0 if failed == 0 else 1


def run_text_command():
    parser = HelpfulArgumentParser(
        prog='kritomatic text',
        description='View and edit vector text layers in the running Krita',
    )
    sub = parser.add_subparsers(dest='text_command', metavar='TEXT_COMMAND',
                                required=True)

    p_dump = sub.add_parser('dump',
                            help='Emit full metadata for every text shape')
    p_dump.add_argument('--pattern', default=None,
                        help='Only layers matching this glob')
    p_dump.add_argument('--output', '-o', default=None,
                        help='Write JSON to this file (default: stdout)')

    p_load = sub.add_parser('load',
                            help='Push records from JSON back into Krita')
    p_load.add_argument('--input', '-i', default=None,
                        help='Read JSON from this file (default: stdin)')

    args = parser.parse_args(sys.argv[2:])

    client = KritaClient()
    if not client.connect():
        print("Error: cannot connect to the Kritomatic daemon. "
              "Is Krita running with the plugin enabled?", file=sys.stderr)
        sys.exit(1)

    try:
        if args.text_command == 'dump':
            sys.exit(cmd_dump(client, args))
        elif args.text_command == 'load':
            sys.exit(cmd_load(client, args))
    finally:
        client.close()
