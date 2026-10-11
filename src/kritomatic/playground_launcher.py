"""
playground_launcher.py — start one of the interactive playgrounds.

The playgrounds under <repo>/playgrounds/ each expose a `main()` that
writes their HTML bundle and starts an HTTP server.  This module
registers them as a `playgrounds` subcommand group and launches the
selected one by loading its entry-point module and calling `main()`.

The list of playgrounds is explicit — nothing scans the playgrounds/
directory at runtime.  PLAYGROUNDS below is the single source of truth:
registry.py imports it to build the CLI's `playgrounds` subparser, and
run_playground_command() imports it to build its own matching parser.
Adding a playground is a one-file edit — append an entry to the list.
"""

import importlib.util
import sys
from pathlib import Path

from kritomatic.helpful_argparse import HelpfulArgumentParser


# Each entry:
#   name    — the token typed after `kritomatic playgrounds`
#   subdir  — directory under <repo>/playgrounds/
#   module  — entry-point module inside that directory, whose main()
#             launches the playground
#   help    — one-line description shown in the menu
PLAYGROUNDS = [
    {
        'name':   'board',
        'subdir': 'board',
        'module': 'board_playground',
        'help':   'Arrange open .kra documents as cards; project a '
                  'card\'s overlap into other documents as a new layer.',
    },
    {
        'name':   'cone',
        'subdir': 'cone',
        'module': 'cone_playground',
        'help':   'Sketch patches on a cone\'s interior and export the '
                  'result as a .kra with warped vector text.',
    },
    {
        'name':   'text_board',
        'subdir': 'text_board',
        'module': 'text_playground',
        'help':   'Edit every text shape across open Krita documents '
                  'from one canvas.',
    },
]


def _repo_root() -> Path:
    # src/kritomatic/playground_launcher.py
    #   → src/kritomatic   → src   → repo root
    return Path(__file__).resolve().parent.parent.parent


def _load(entry):
    """Import a playground's entry-point module and return it."""
    subdir      = entry['subdir']
    module_name = entry['module']

    playground_dir = _repo_root() / 'playgrounds' / subdir
    entry_file     = playground_dir / f'{module_name}.py'

    if not entry_file.is_file():
        raise FileNotFoundError(
            f'Playground entry point not found: {entry_file}'
        )

    # The playgrounds import their own siblings by bare module name
    # (`from pg_base import ...`), so the subdirectory must be on
    # sys.path before the entry point is loaded.
    if str(playground_dir) not in sys.path:
        sys.path.insert(0, str(playground_dir))

    spec   = importlib.util.spec_from_file_location(module_name, entry_file)
    module = importlib.util.module_from_spec(spec)
    # Register before exec so any intra-module import of the same
    # name resolves to this module.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _build_parser():
    """Build the same parser registry.py builds for the `playgrounds`
    group.  Kept in sync with registry.py by sharing PLAYGROUNDS."""
    parser = HelpfulArgumentParser(
        prog='kritomatic playgrounds',
        description='Launch an interactive browser-based playground '
                    'that drives Krita through the daemon.',
    )
    sub = parser.add_subparsers(
        dest='playground', metavar='PLAYGROUND',
        required=True, help='Playgrounds',
    )
    for pg in PLAYGROUNDS:
        sub.add_parser(pg['name'], help=pg['help'])
    return parser


def run_playground_command():
    """Dispatch `kritomatic playgrounds <name>` to the matching
    playground's main().  A bare `kritomatic playgrounds` prints this
    group's help, formatted identically to `kritomatic layer` and the
    other subcommand groups."""
    parser = _build_parser()
    args   = parser.parse_args(sys.argv[2:])

    name  = args.playground
    entry = next((pg for pg in PLAYGROUNDS if pg['name'] == name), None)
    if entry is None:
        # argparse already validated the name against the subparsers,
        # so this is defensive only.
        print(f"Unknown playground: {name}", file=sys.stderr)
        sys.exit(2)

    try:
        module = _load(entry)
    except FileNotFoundError as e:
        print(f'Error: {e}', file=sys.stderr)
        sys.exit(1)

    if not hasattr(module, 'main'):
        print(f"Playground {name!r} entry point has no main()",
              file=sys.stderr)
        sys.exit(1)

    module.main()
