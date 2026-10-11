"""
xremap_launcher.py — run helper scripts from the kritomatic_xremap
subproject.

The subproject at <repo>/kritomatic_xremap/ ships helper scripts that
sit outside the daemon's command surface: they edit the xremap YAML,
inject new bindings, and otherwise maintain file-level state the
daemon does not own.  They are invoked through the same CLI as
everything else, via `kritomatic xremap <command>`.

Each command runs its script as-is, in a fresh subprocess, with
stdin, stdout, and stderr inherited from the parent.  That matters
for scripts that prompt interactively: inject_script.py's input mode
reads a line from the terminal, and capturing its streams would
swallow the prompt.

The list below is the single source of truth for *which* scripts are
reachable.  registry.py imports it to build the CLI's `xremap`
submenu, and run_xremap_command() imports it to dispatch.  Adding a
command is a one-file edit — append an entry with a name, a help
string, and the path of the script to run.  Nothing scans the
subproject directory at runtime; the list is explicit on purpose, so
that any script reachable through the CLI has been deliberately
listed here.

FLAG DISCOVERY: CLI_SPEC
------------------------
The launcher does not hardcode each script's command-line flags.  It
reads them from the script's own source, via the module-level
`CLI_SPEC` constant.  A script that declares `CLI_SPEC` gets its
flags surfaced in the CLI menu — `kritomatic xremap <name> --help`
lists them, with the same help text the script itself would print.
A script that does not declare `CLI_SPEC` still runs, but the
launcher cannot show its flags in the menu; in that case
`kritomatic xremap <name> --help` forwards `--help` to the script,
so the script's own help is what the user sees.

CLI_SPEC is a Python-source convention.  Only scripts written in
Python can declare one; a shell script has no declaration site and
is treated the same as a Python script that omits the constant:
pass-through mode, no warning.  This is why `setup_links.sh` — which
is bash — does not produce a "could not find CLI_SPEC" message.  The
discovery step itself is skipped for non-Python files, before any
parsing is attempted, so no bash file is ever fed to `ast.parse`.

CLI_SPEC is a list of dicts, one per argument, in the same order the
script's own argparse adds them:

    CLI_SPEC = [
        {'name': 'input', 'nargs': '?',
         'help': '...'},
        {'name': '--remove', 'action': 'store_true',
         'help': '...'},
        {'name': '--output', 'metavar': 'PATH',
         'help': '...'},
    ]

Supported keys: `name`, `nargs`, `action` (only `store_true` and
`store_false`), `metavar`, `choices` (used for help display only,
not for validation), `default`, `help`.  A `type` key is accepted
and ignored by the launcher — type coercion is the script's job.
Keys the launcher does not recognise are also ignored, so the spec
can carry script-only fields without breaking the menu.

The launcher reads CLI_SPEC via ast.literal_eval — it never imports
or execs the script.  A script with top-level side effects (network
calls on import, file writes) is therefore safe to register.  The
constant must be a literal: no calls, no name lookups, no
expressions.  If it is absent, the launcher silently falls back to
pass-through mode for that entry.  If it is present but malformed,
the launcher prints a warning to stderr and falls back to
pass-through mode for that entry — the script still runs, but the
author is told the declaration did not parse.

The script is expected to build its own argparse from the same
`CLI_SPEC`, so the two cannot drift.  A worked example of that
pattern lives in inject_script.py: `_build_argparser` there reads
`CLI_SPEC` and produces the parser main() uses.

Argument forwarding
-------------------
Everything after the subcommand name is forwarded verbatim to the
underlying script, including flags.  This is why the launcher uses
parse_known_args rather than parse_args: parse_args rejects any flag
the launcher's own parser does not declare, and the flags are meant
for the script, which is the only thing that knows what they are.

For scripts that declare CLI_SPEC, the launcher declares the same
flags on its own subparser, so the help menu can show them.  The
values the launcher's parser collects are discarded — the launcher's
parser is a documentation surface, not the authoritative one.  What
actually runs is the script's own parser, on the same argv the user
typed.  This is why the launcher forwards `sys.argv[3:]` verbatim
rather than reconstructing argv from its own parse tree.

Handling of `--help` splits on whether CLI_SPEC was declared:

    CLI_SPEC present:
        The subparser has add_help=True.  `kritomatic xremap <name>
        --help` is intercepted by the launcher, which prints its own
        help screen showing the CLI_SPEC-declared flags.  That
        screen is the one source of truth for the script's flags.

    CLI_SPEC absent:
        The subparser has add_help=False.  `--help` is not
        recognised by the launcher and falls through
        parse_known_args into the argv the launcher forwards to the
        script.  The script's own help is what the user sees,
        because the launcher has no way to show it and pretending
        otherwise would hide the flags.

Interpreter selection
---------------------
The launcher picks the interpreter by file extension.  `.py` runs
under the same Python that runs the CLI (sys.executable), so a
pipx-installed `kritomatic` uses its own interpreter.  `.sh` runs
under bash.  Anything else is executed directly, which requires the
file to be marked executable.

Error paths
-----------
A script named in XREMAP_COMMANDS but missing from disk is a hard
error: the launcher refuses to run and prints the missing path.  A
CLI_SPEC that fails to parse is a soft error: the launcher warns
once on stderr and continues in pass-through mode, so a broken
declaration never prevents a script from being run.
"""

import ast
import subprocess
import sys
from pathlib import Path

from kritomatic.helpful_argparse import HelpfulArgumentParser


# Each entry:
#   name    — the token typed after `kritomatic xremap`
#   script  — path of the script, relative to <repo>/kritomatic_xremap/
#   help    — one-line description shown in the menu
XREMAP_COMMANDS = [
    {
        'name':   'inject',
        'script': 'inject_script.py',
        'help':   'Add or remove an entry in merger.py\'s SCRIPTS dict, '
                  'and drop a commented binding stub into '
                  'kritomatic_xremap.yaml.  Runs inject_script.py in its '
                  'interactive input mode; extra args are forwarded to '
                  'the script.',
    },
    {
        'name':   'setup',
        'script': 'setup_links.sh',
        'help':   'Refresh the systemd units and restart the merger, so '
                  'the current repo location and environment are picked '
                  'up.  Run after moving the checkout or editing the '
                  'xremap YAML.',
    },
]


def _repo_root() -> Path:
    # src/kritomatic/xremap_launcher.py
    #   → src/kritomatic   → src   → repo root
    return Path(__file__).resolve().parent.parent.parent


def _xremap_root() -> Path:
    return _repo_root() / 'kritomatic_xremap'


def _interpreter_for(script_path: Path):
    """Return the argv prefix used to run `script_path`, or None to
    execute the file directly (requires the executable bit)."""
    suffix = script_path.suffix.lower()
    if suffix == '.py':
        # Same interpreter that is running the CLI.  Under a pipx
        # install this is the app's own venv Python, which is the
        # right choice for a script that only needs the standard
        # library.
        return [sys.executable]
    if suffix == '.sh':
        return ['bash']
    return None


# ----------------------------------------------------------------------
#  CLI_SPEC discovery
# ----------------------------------------------------------------------

def _read_cli_spec(script_path: Path):
    """Return the script's CLI_SPEC list, or None.

    The first check is on the file's extension.  CLI_SPEC is a
    Python-source convention, and `ast.parse` — the parser used here —
    understands only Python.  Feeding a bash script to it produces a
    SyntaxError that means nothing to the author of the bash script
    (they never wrote anything resembling a Python declaration), so
    non-Python files return None before any parse is attempted.  That
    is also the same fallback used for Python files with no CLI_SPEC:
    pass-through mode, no warning.

    Two outcomes are distinguished by the caller, not here:

      list      — CLI_SPEC was declared and parsed.  Even an empty
                  list is a positive declaration ("this script has
                  no flags, I promise").

      None      — CLI_SPEC was absent, or the file is not Python.
                  The launcher falls back to pass-through mode
                  silently; the absence of the declaration is a
                  supported state, not an error.

    On a malformed CLI_SPEC in a Python file — syntax error,
    non-literal value, wrong shape — the function prints a warning on
    stderr and returns None.  The script is still runnable; only the
    flag display is lost.  The warning makes the declaration bug
    visible without turning it into a hard failure: a broken help
    menu should not prevent a working script from running.
    """
    if script_path.suffix.lower() != '.py':
        return None

    try:
        source = script_path.read_text(encoding='utf-8')
    except OSError as e:
        print(f"xremap_launcher: cannot read {script_path}: {e}",
              file=sys.stderr)
        return None

    try:
        tree = ast.parse(source, filename=str(script_path))
    except SyntaxError as e:
        print(f"xremap_launcher: {script_path}: syntax error, "
              f"skipping CLI_SPEC discovery ({e})", file=sys.stderr)
        return None

    cli_spec_node = None
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == 'CLI_SPEC':
                cli_spec_node = node.value
                break
        if cli_spec_node is not None:
            break

    if cli_spec_node is None:
        # Absent: supported state.  No message.
        return None

    try:
        spec = ast.literal_eval(cli_spec_node)
    except (ValueError, SyntaxError) as e:
        print(f"xremap_launcher: {script_path}: CLI_SPEC is not a "
              f"literal and could not be read ({e}); flag display "
              f"will be skipped", file=sys.stderr)
        return None

    if not isinstance(spec, list):
        print(f"xremap_launcher: {script_path}: CLI_SPEC must be a "
              f"list of dicts, got {type(spec).__name__}; flag display "
              f"will be skipped", file=sys.stderr)
        return None

    for i, entry in enumerate(spec):
        if not isinstance(entry, dict) or 'name' not in entry:
            print(f"xremap_launcher: {script_path}: CLI_SPEC[{i}] must "
                  f"be a dict with at least a 'name' key; flag display "
                  f"will be skipped", file=sys.stderr)
            return None

    return spec


# ----------------------------------------------------------------------
#  Parser construction
# ----------------------------------------------------------------------

# Keys the launcher forwards from a CLI_SPEC entry to argparse.  Any
# other key in the spec (notably `type`) is ignored by the launcher on
# purpose: type coercion and validation are the script's job, not the
# menu's.  Forwarding `type` here would make the launcher reject a
# value the script might have been happy to receive as a string, which
# is exactly the kind of gatekeeping the launcher must not do.
#
# `choices` is also deliberately absent from this list.  It would make
# the launcher reject an out-of-range value before the script got a
# chance to see it.  When the spec includes `choices`, its display
# form is reconstructed as an explicit metavar instead (see below), so
# the help screen still shows `{a,b,c}` without the launcher gaining
# any validation authority.
_LAUNCHER_ARG_KEYS = ('action', 'nargs', 'default', 'help', 'metavar')


def _add_arg_from_spec(subparser, spec):
    """Add one CLI_SPEC entry to `subparser`, in the display-only way
    the launcher uses."""
    kwargs = {}
    for key in _LAUNCHER_ARG_KEYS:
        if key in spec:
            kwargs[key] = spec[key]

    # Reconstruct the display form of choices when no explicit metavar
    # was given.  This is what argparse itself would have produced, so
    # the help text matches what the script's own parser prints.
    if 'metavar' not in kwargs and 'choices' in spec:
        kwargs['metavar'] = (
            '{' + ','.join(str(c) for c in spec['choices']) + '}'
        )

    subparser.add_argument(spec['name'], **kwargs)


def _build_parser():
    """Build the launcher's parser, one subparser per XREMAP_COMMANDS
    entry, each populated with the corresponding script's CLI_SPEC when
    one is declared.

    The same parser serves both the top-level `kritomatic xremap`
    menu (built by registry.py from XREMAP_COMMANDS) and the direct
    invocation path (run_xremap_command below).  Keeping them equal
    means the menu and the dispatcher cannot disagree about what
    `kritomatic xremap <name>` accepts.
    """
    parser = HelpfulArgumentParser(
        prog='kritomatic xremap',
        description='Run helper scripts from the kritomatic_xremap '
                    'subproject.',
    )
    sub = parser.add_subparsers(
        dest='xremap_command', metavar='XREMAP_COMMAND',
        required=True, help='xremap commands',
    )

    xremap_root = _xremap_root()

    for entry in XREMAP_COMMANDS:
        script_path = xremap_root / entry['script']
        cli_spec = _read_cli_spec(script_path) if script_path.is_file() else None

        # add_help splits on whether the script declared CLI_SPEC.
        # See the module docstring, section "Handling of --help".
        subparser = sub.add_parser(
            entry['name'],
            help=entry['help'],
            add_help=(cli_spec is not None),
        )

        if cli_spec is not None:
            for spec in cli_spec:
                _add_arg_from_spec(subparser, spec)

    return parser


# ----------------------------------------------------------------------
#  Dispatch
# ----------------------------------------------------------------------

def run_xremap_command():
    """Dispatch `kritomatic xremap <name> [args...]` to the matching
    script.  Extra args after the name are forwarded verbatim.  A bare
    `kritomatic xremap` prints this group's help, formatted identically
    to `kritomatic playgrounds` and the other subcommand groups."""
    parser = _build_parser()

    # parse_known_args, not parse_args: the launcher's parser is a
    # documentation surface for the flags declared in CLI_SPEC, but
    # the script is the authoritative parser.  Whatever the launcher
    # consumes into `args` is thrown away — what actually reaches the
    # script is `sys.argv[3:]`, verbatim.  parse_known_args is used
    # instead of parse_args so that a script without CLI_SPEC can
    # still receive its own flags: any flag the launcher does not
    # declare (which is all of them, in that case) is handed back in
    # `extra` and would be lost if we only forwarded `extra`.
    #
    # `--help`, when CLI_SPEC was declared, is intercepted here by the
    # subparser's auto-added help action and exits cleanly before this
    # line ever returns.  When CLI_SPEC was absent, add_help=False
    # kept that interception from happening and `--help` flows onward
    # to the script.
    args, _extra = parser.parse_known_args(sys.argv[2:])

    name = args.xremap_command
    entry = next((e for e in XREMAP_COMMANDS if e['name'] == name), None)
    if entry is None:
        # argparse already validated the name against the subparsers,
        # so this is defensive only.
        print(f"Unknown xremap command: {name}", file=sys.stderr)
        sys.exit(2)

    script_path = _xremap_root() / entry['script']
    if not script_path.is_file():
        print(f"Error: script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    prefix = _interpreter_for(script_path)
    if prefix is None:
        base = [str(script_path)]
    else:
        base = prefix + [str(script_path)]

    # Forward everything after `xremap <name>` verbatim.  Slicing
    # sys.argv directly, rather than re-serialising `args` + `extra`,
    # is what preserves the user's own quoting and ordering, and it is
    # the same argv the script's own parser will see when it runs.
    script_args = sys.argv[3:]
    argv = base + script_args

    # stdin/stdout/stderr are inherited on purpose: scripts that prompt
    # interactively (inject_script.py) must be able to read from the
    # terminal they were launched in.  Capturing the streams would
    # swallow the prompt.
    result = subprocess.run(argv)
    sys.exit(result.returncode)
