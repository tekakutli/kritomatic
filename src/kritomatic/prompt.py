"""
prompt.py — interactive multi-line JSON input for the CLI.

When a command needs the user to paste a JSON payload (a batch bundle,
a preset, etc.), shell quoting turns into a fight: single quotes protect
against inner double quotes but not against embedded single quotes;
heredocs work but are clunky; the shell strips nothing, so the JSON
arrives verbatim only if the user is careful. The result is that a
straightforward `kritomatic batch run` ends up requiring the human to
wrap a small document in the correct quote style, which is friction
the tool should remove.

This module implements a small prompt loop that reads lines from
/dev/tty until the accumulated text parses as JSON. The user pastes,
hits Enter twice, and the parsed value comes back. No quoting
gymnastics, no escaped quotes, no heredoc.

Pattern borrowed from the mcp.py router's interactive mode.

Semantics:

    - Reads from /dev/tty, falling back to stdin if unavailable
      (e.g. on platforms without /dev/tty, or if the tty cannot be
      opened for any reason).
    - Writes the prompt to /dev/tty, falling back to stdout.
    - A blank line forces an early parse attempt — an escape hatch
      for the case where the accumulated text is expected to be
      complete but the loop has not yet recognized it as such.
    - A single wrapping markdown code fence is stripped before parsing.
      Same tolerance the batch loader applies elsewhere; a fenced paste
      is accepted without any extra work by the caller.
    - Reads at most `max_lines` lines (default 200). A runaway paste
      aborts cleanly rather than looping forever.
    - After a successful parse, pending input is drained with a short
      select() timeout, so trailing prose (if the user pasted a
      response that included commentary after the JSON) does not leak
      into the next thing the caller does.

Return value:

    The parsed JSON value on success. The caller receives whatever
    json.loads produces — dict, list, scalar, whatever.

    None if the user typed nothing before EOF or before a
    blank-line-forced parse. This is the clean-abort signal: the user
    changed their mind, or the input stream closed, and the caller
    should exit without an error message.

Exceptions:

    json.JSONDecodeError if the accumulated text is not valid JSON.
    Typically fires when the user forces a parse (via a blank line)
    while the paste is still incomplete, or when EOF arrives with
    partial content that does not parse. The caller is expected to
    surface this to the user; the module does not print on its own
    because the caller may want to route the message through its own
    output-suppression logic.
"""

import json
import re
import select
import sys


# Matches a single markdown code fence wrapping its whole body:
#   ```json\n{...}\n```
#   ```\n{...}\n```
# The language tag (json, JSON, or absent) is optional. Anything else
# around the JSON — prose, multiple fences, partial wrapping — is not
# stripped, because recognizing it would require guessing. See the
# module docstring for the rationale.
_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(?P<body>.*?)\n?```\s*$",
    re.DOTALL,
)


def _strip_fences(text):
    """Unwrap a single markdown code fence around `text`, if present."""
    m = _FENCE_RE.match(text)
    if m:
        return m.group("body")
    return text


def _is_complete_json(text):
    """True iff `text` (after fence-strip tolerance) is a valid JSON
    value.

    This is a *liveness* check for the read loop, not a validator. It
    never raises; failure just means "keep reading". The authoritative
    parse happens once the loop has finished accumulating, and errors
    are reported to the caller then.
    """
    try:
        json.loads(_strip_fences(text).strip())
    except (json.JSONDecodeError, ValueError):
        return False
    return True


def prompt_for_json(prompt_text="json> ", max_lines=200):
    """
    Prompt on /dev/tty and read lines until the accumulated text parses
    as JSON. See the module docstring for the full semantics.
    """
    # Prefer /dev/tty: the prompt must appear on the terminal even when
    # stdin/stdout are being redirected. Fall back to standard streams
    # if /dev/tty is unavailable (Windows, exotic environments, or a
    # tty that cannot be opened for any reason).
    try:
        tty_in = open("/dev/tty", "r")
    except OSError:
        tty_in = sys.stdin
    try:
        tty_out = open("/dev/tty", "w")
    except OSError:
        tty_out = sys.stdout

    try:
        lines = []
        tty_out.write(prompt_text)
        tty_out.flush()

        while True:
            line = tty_in.readline()
            if line == "":
                # EOF on the tty (Ctrl-D). Take whatever we have as
                # final; the caller will attempt a parse.
                break
            line = line.rstrip("\n")
            lines.append(line)
            accumulated = "\n".join(lines)

            # Blank line = "I'm done, parse what I have." Only fires
            # if there is already non-whitespace content — a blank
            # line as the very first input is just an empty line.
            if line == "" and accumulated.strip():
                break

            # Same tolerance as the final parse. If the accumulator is
            # already complete JSON, we are done without needing a
            # blank-line terminator.
            if _is_complete_json(accumulated):
                break

            if len(lines) > max_lines:
                tty_out.write(
                    f"\n[aborted: {len(lines)} lines without parseable JSON]\n"
                )
                tty_out.flush()
                return None

        accumulated = "\n".join(lines)
        if not accumulated.strip():
            # Nothing typed, or only whitespace. Silent abort.
            return None

        # Drain any pending input (trailing prose after valid JSON) so
        # it does not leak into the caller's subsequent interaction.
        # select() has a short timeout; on platforms where the stream
        # is not selectable, this silently does nothing.
        try:
            while select.select([tty_in], [], [], 0.05)[0]:
                if not tty_in.readline():
                    break
        except (OSError, ValueError):
            pass

        tty_out.write("\n")
        tty_out.flush()

        return json.loads(_strip_fences(accumulated).strip())
    finally:
        if tty_in is not sys.stdin:
            tty_in.close()
        if tty_out is not sys.stdout:
            tty_out.close()
