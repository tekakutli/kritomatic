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
import sys


class HelpfulArgumentParser(argparse.ArgumentParser):
    # argparse's exact wording for the "required argument missing" error.
    # Kept as a class attribute so a future Python version (or a subclass)
    # can override it in one place if the stdlib wording ever changes.
    _MISSING_ARGS_PREFIX = "the following arguments are required:"

    # Exit code used when help is substituted for an error. Matches
    # argparse's own convention (2) so shell pipelines still notice a
    # problem. Set to 0 if you'd rather "half a command" be a clean
    # success.
    _EXIT_CODE = 2

    def error(self, message: str) -> None:
        # Only intercept the specific "a required positional is missing"
        # case. Anything else (unknown flag, invalid choice, missing
        # --value, ...) falls straight through to the standard behaviour.
        if message.startswith(self._MISSING_ARGS_PREFIX):
            missing = message[len(self._MISSING_ARGS_PREFIX):].strip()

            if self._missing_is_subcommand(missing):
                # self is the parser whose subcommand the user failed to
                # provide — so printing *its* help is exactly what the
                # user needs to see.
                self.print_help(sys.stderr)
                sys.exit(self._EXIT_CODE)

        super().error(message)

    def _missing_is_subcommand(self, missing: str) -> bool:
        """
        Return True iff the missing required argument corresponds to the
        subparser action on *this* parser.

        argparse reports the missing argument using whichever of the
        following is set (in preference order):

          - the action's `metavar`   (rarely set for subparsers)
          - the action's `dest`      (e.g. "command", "subcommand")
          - the choices list         (as a last-ditch fallback)

        We match all three so this works no matter how the calling code
        configured `add_subparsers()`.
        """
        for action in self._actions:
            if not isinstance(action, argparse._SubParsersAction):
                continue

            if missing == action.dest:
                return True

            if action.metavar is not None and missing == str(action.metavar):
                return True

            # Fallback: argparse occasionally renders the choices list.
            if missing in str(list(action.choices)):
                return True

        return False
