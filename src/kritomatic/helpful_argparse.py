"""
helpful_argparse.py — print a help menu instead of a terse "required
argument missing" error when that argument is a subcommand group.

Why
---
By default, argparse's only response to a missing required positional
argument is a one-line error message, e.g.:

    kritomatic layer: error: the following arguments are required: subcommand

That is technically correct but unhelpful: the user was clearly on the
way to typing a valid command and just needs to see what the next token
can be. This module intercepts exactly that case and prints the offending
parser's own help screen instead.

Every other argparse error (missing flag, invalid choice, unknown option,
etc.) is passed through unchanged.

Subcommand argument summaries
-----------------------------
argparse's stock help lists a subcommand group's choices by name only;
each subcommand's own flags appear on a separate `--help` screen. That
means a user typing just `kritomatic text` sees `dump` and `load` but not
`--pattern`, `--output`, or `--input`. This module enriches the group's
help so each subcommand line carries its own flags below the description:

    positional arguments:
      {dump,load}
        dump       Emit full metadata for every text layer
                   --pattern PATTERN --output OUTPUT
        load       Push records from JSON back into Krita
                   --input INPUT

The flag list is wrapped by us, not by the terminal, at exactly the
column width argparse gives the help text.  Every wrapped line is
placed by argparse at the same indent as the first line, so alignment
is preserved no matter how many flags a subcommand has or how narrow
the terminal is.  A single `--flag METAVAR` pair is never split across
lines: if it would not fit, it goes on a line of its own even if that
line overflows the width.

The enrichment is generic: any parser subclassing HelpfulArgumentParser
gets it, for any subcommand group, with no changes to the calling code.

Colour
------
Help output is coloured when the target stream is a TTY.  Section
headings are bold, flag invocations are cyan, metavars are yellow, and
the "usage:" prefix is bold.  Honours NO_COLOR and FORCE_COLOR.
Colouring is a substitution pass over the fully-formatted string: it
inserts escape sequences around existing tokens and never rebuilds a
line, so argparse's own width and padding arithmetic and the exact
whitespace the formatter produced are both preserved.

Propagation
-----------
`argparse.ArgumentParser.add_subparsers()` calls
`kwargs.setdefault('parser_class', type(self))`, so as long as the
top-level parser is a `HelpfulArgumentParser`, every subparser and
sub-subparser it creates is one too — no per-subparser wiring required,
even when the tree is built dynamically in a loop from a schema.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import textwrap


# ----------------------------------------------------------------------
# Colour palette and detection
# ----------------------------------------------------------------------

_ESC_BOLD   = '\033[1m'
_ESC_CYAN   = '\033[36m'
_ESC_YELLOW = '\033[33m'
_ESC_RESET  = '\033[0m'


def _color_enabled() -> bool:
    """NO_COLOR set → no colour.  FORCE_COLOR set → colour.  Otherwise
    colour iff stdout or stderr is a TTY."""
    if os.environ.get('NO_COLOR') is not None:
        return False
    if os.environ.get('FORCE_COLOR'):
        return True
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and stream.isatty():
                return True
        except Exception:
            pass
    return False


# ----------------------------------------------------------------------
# Formatter
# ----------------------------------------------------------------------

class SubcommandHelpFormatter(argparse.HelpFormatter):
    """HelpFormatter with two customizations:

      - Real newlines inside a help string survive formatting, so the
        subcommand-flag enrichment can put its list on its own line
        below the description.

      - A line that reads as a flag summary is re-wrapped by us, at
        argparse's own help-text width, unit by unit.  A unit is
        either `--flag` or `--flag METAVAR`; units are placed on a
        line as long as they fit and never split across lines.  This
        lets every emitted line be placed by argparse at the same
        indent, without relying on the terminal's own wrap.
    """

    # A line that consists solely of `--flag [METAVAR]` repetitions:
    # starts with a dash, and never contains a token that is neither a
    # flag nor the value of a preceding flag.
    _FLAG_LINE_RE = re.compile(r'^\s*-{1,2}\S+(?:\s+[^\s-]\S*)?'
                               r'(?:\s+-{1,2}\S+(?:\s+[^\s-]\S*)?)*$')

    def _split_lines(self, text, width):
        # The stock implementation collapses newlines and wraps the
        # whole thing as one paragraph.  When the help text contains
        # our enrichment newline, we treat each segment on its own.
        if '\n' not in text:
            return super()._split_lines(text, width)

        out = []
        for raw in text.split('\n'):
            stripped = raw.strip()
            if not stripped:
                out.append('')
                continue
            if self._FLAG_LINE_RE.match(stripped):
                out.extend(self._wrap_flag_line(stripped, width))
            else:
                out.extend(
                    textwrap.wrap(
                        stripped,
                        width,
                        break_long_words=False,
                        break_on_hyphens=False,
                    ) or ['']
                )
        return out

    # -- flag-line wrapping --------------------------------------------

    @staticmethod
    def _units(flag_line):
        """Split a flag-summary line into units, each of which is either
        `--flag` (a value-less flag) or `--flag METAVAR` (a flag and
        the one token that follows it, if that token is not itself a
        flag).  Units are the smallest pieces we are willing to put on
        a line.
        """
        tokens = flag_line.split()
        units = []
        i = 0
        while i < len(tokens):
            if i + 1 < len(tokens) and not tokens[i + 1].startswith('-'):
                units.append(tokens[i] + ' ' + tokens[i + 1])
                i += 2
            else:
                units.append(tokens[i])
                i += 1
        return units

    def _wrap_flag_line(self, flag_line, width):
        units = self._units(flag_line)
        if not units:
            return ['']

        lines = []
        current = ''
        for unit in units:
            if not current:
                current = unit
                continue
            if len(current) + 1 + len(unit) <= width:
                current += ' ' + unit
            else:
                lines.append(current)
                current = unit
        if current:
            lines.append(current)
        return lines


# ----------------------------------------------------------------------
# Help-string enrichment: build the per-subcommand flag summary
# ----------------------------------------------------------------------

def _format_subcommand_flags(subparser):
    """Return the flag list for a subparser as a single space-separated
    string.  Positional arguments and the automatic -h/--help are
    skipped: the subcommand's own name is already on the line above,
    and -h is universally understood.
    """
    parts = []
    for action in subparser._actions:
        if not action.option_strings:
            continue
        if all(o in ('-h', '--help') for o in action.option_strings):
            continue

        opt = next(
            (o for o in action.option_strings if o.startswith('--')),
            action.option_strings[0],
        )

        if action.nargs == 0:
            parts.append(opt)
            continue

        metavar = action.metavar
        if metavar is None:
            metavar = str(action.dest).upper()
        elif isinstance(metavar, tuple):
            metavar = metavar[0]
        parts.append(f'{opt} {metavar}')

    return ' '.join(parts)


# ----------------------------------------------------------------------
# Colourisation
# ----------------------------------------------------------------------
#
# Everything below is a substitution over the fully-formatted string.
# No line is ever split, joined, or otherwise rebuilt: escape sequences
# are inserted around existing tokens, and every other byte — indent,
# internal whitespace, trailing newline — passes through unchanged.

_SECTION_RE = re.compile(r'^([^\s].*?:)$')
_USAGE_RE   = re.compile(r'^(usage:)(.*)$')

# indent | invocation | 2+ spaces | help text
_LINE_WITH_HELP_RE = re.compile(r'^(\s+)(\S.*?\S)(\s{2,})(\S.*)$')

# indent | invocation-only line
_LINE_INVOCATION_ONLY_RE = re.compile(
    r'^(\s+)((?:-{1,2}\S.*)|(?:\{.*\}))$'
)

# `--flag` optionally followed by whitespace and a metavar token.
# The metavar must not start with a dash, so `--a --b` reads as two
# bare flags, not one flag with a value.
_FLAG_WITH_OPTIONAL_VALUE_RE = re.compile(
    r'(--[A-Za-z][A-Za-z0-9_-]*)(?:([ \t]+)([^\s-][^\s]*))?'
)


def _looks_like_subcommand_name(text: str) -> bool:
    return bool(re.match(r'^[a-z][a-z0-9_-]*$', text))


def _is_flag_line(line: str) -> bool:
    """True for a line that reads as a flag summary: first non-space
    character is a dash, and every whitespace-separated token is either
    a flag (starts with '-') or a value attached to a preceding flag.
    """
    stripped = line.lstrip()
    if not stripped.startswith('-'):
        return False
    tokens = stripped.split()
    i = 0
    while i < len(tokens):
        if not tokens[i].startswith('-'):
            return False
        i += 1
        if i < len(tokens) and not tokens[i].startswith('-'):
            i += 1
    return True


def _colorize_flag_line(line: str) -> str:
    def repl(m):
        flag = m.group(1)
        sep  = m.group(2)
        val  = m.group(3)
        out = f'{_ESC_CYAN}{flag}{_ESC_RESET}'
        if val is not None:
            out += f'{sep}{_ESC_YELLOW}{val}{_ESC_RESET}'
        return out
    return _FLAG_WITH_OPTIONAL_VALUE_RE.sub(repl, line)


def _colorize_help(text: str) -> str:
    if not _color_enabled():
        return text

    out = []
    for line in text.split('\n'):
        if _SECTION_RE.match(line):
            out.append(f'{_ESC_BOLD}{line}{_ESC_RESET}')
            continue

        m = _USAGE_RE.match(line)
        if m:
            out.append(f'{_ESC_BOLD}{m.group(1)}{_ESC_RESET}{m.group(2)}')
            continue

        # Flag line — detect before the invocation rules, because a
        # flag line has no help text after it and would otherwise be
        # mistaken for a lone invocation line.
        if _is_flag_line(line):
            out.append(_colorize_flag_line(line))
            continue

        m = _LINE_WITH_HELP_RE.match(line)
        if m:
            indent, invocation, spaces, help_text = m.groups()
            if (invocation.startswith('-')
                    or invocation.startswith('{')
                    or _looks_like_subcommand_name(invocation)):
                colour = _ESC_YELLOW if invocation.startswith('{') else _ESC_CYAN
                out.append(
                    f'{indent}{colour}{invocation}{_ESC_RESET}'
                    f'{spaces}{help_text}'
                )
                continue

        m = _LINE_INVOCATION_ONLY_RE.match(line)
        if m:
            indent, invocation = m.groups()
            colour = _ESC_YELLOW if invocation.startswith('{') else _ESC_CYAN
            out.append(f'{indent}{colour}{invocation}{_ESC_RESET}')
            continue

        out.append(line)

    return '\n'.join(out)


# ----------------------------------------------------------------------
# Parser
# ----------------------------------------------------------------------

class HelpfulArgumentParser(argparse.ArgumentParser):
    _MISSING_ARGS_PREFIX = "the following arguments are required:"
    _EXIT_CODE = 2

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('formatter_class', SubcommandHelpFormatter)
        super().__init__(*args, **kwargs)

    def error(self, message: str) -> None:
        if message.startswith(self._MISSING_ARGS_PREFIX):
            missing = message[len(self._MISSING_ARGS_PREFIX):].strip()
            if self._missing_is_subcommand(missing):
                self.print_help(sys.stderr)
                sys.exit(self._EXIT_CODE)
        super().error(message)

    def _missing_is_subcommand(self, missing: str) -> bool:
        for action in self._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            if missing == action.dest:
                return True
            if action.metavar is not None and missing == str(action.metavar):
                return True
            if missing in str(list(action.choices)):
                return True
        return False

    def format_help(self):
        """Enrich each subcommand entry with its flags, then colourize.

        The originals of every modified help string are restored before
        this method returns, so subsequent use of the parser is
        unaffected.
        """
        saved = []
        for action in self._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue
            for choice_action in action._choices_actions:
                name = choice_action.dest
                sub = action._name_parser_map.get(name)
                if sub is None:
                    continue

                flags = _format_subcommand_flags(sub)
                if not flags:
                    continue

                original = choice_action.help or ''
                combined = f'{original}\n{flags}' if original else flags

                # argparse %-formats every help string; double literal %.
                combined = combined.replace('%', '%%')

                saved.append((choice_action, choice_action.help))
                choice_action.help = combined

        try:
            text = super().format_help()
        finally:
            for choice_action, original in saved:
                choice_action.help = original

        return _colorize_help(text)
