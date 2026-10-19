"""
pgkit.js_check — syntax-check a JS bundle before serving it.

Every playground serves JS assembled from N Python-string segments.
A syntax error anywhere in that assembly is catastrophic in a way a
per-module error is not: the browser's parser rejects the *entire*
script, "use strict" and every function declaration in it never
execute, and the playground loads as a panel-shaped shell with no
behavior.  Buttons don't fire.  The canvas never draws.  The console
shows a single `Uncaught SyntaxError` whose position is a character
offset into the concatenated bundle, not a line into any file the
developer actually edited.

The bug this module was written for was a Python implicit-string-
concatenation that slipped into a JS payload:

    "Opens the native file picker. "
    "Use action.addFilesWithPaths…"

Adjacent string literals are valid Python and a SyntaxError in JS.
Because the offending segment lived inside a bundle of about a hundred
thousand characters, the browser could say no more than "Unexpected
string at 1:48219"; finding which of the six panel files actually
contained the problem required bisecting the bundle.

`check_js()` runs `node --check` on the assembled string.  `node
--check` parses without executing, exits non-zero on a syntax error,
and prints `path:line` — the line number into the string the caller
passed, which the caller maps back to a source file using an index it
built during concatenation.  `JsSyntaxError` carries both the line
number and the label so callers do not have to re-parse node's stderr.

Non-tool failures (node missing, subprocess error, timeout) do not
raise.  The check is a diagnostic, not a build requirement — a
developer without node installed can still run their playground, and
a false positive from a broken tool is far worse than a missed check.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Optional


_LINENO_RE = re.compile(r'^(.+?):(\d+)(?::\d+)?$')


class JsSyntaxError(Exception):
    """A syntax error in a checked JS bundle.

    Attributes:
        label     the caller-supplied label (usually a source filename).
        lineno    1-based line number into the string passed to check_js,
                  or None if node's output could not be parsed.
        message   the human-readable SyntaxError line from node.
        raw       node's stderr verbatim.
    """

    def __init__(self, label: str, lineno: Optional[int],
                 message: str, raw: str):
        self.label = label
        self.lineno = lineno
        self.message = message
        self.raw = raw
        loc = f"{label}:{lineno}" if lineno is not None else label
        super().__init__(f"{loc}: {message}")


def check_js(source: str, *, label: str = "bundle") -> None:
    """Raise JsSyntaxError if `source` is not valid JavaScript.

    No-op when node is unavailable; warns once per process so a
    developer who has not installed node sees why the check is silent
    but is not blocked by it.
    """
    node = shutil.which("node")
    if node is None:
        _warn_once(
            "pgkit.js_check: `node` not found on PATH — "
            "skipping JS syntax check. Install node to enable it.",
        )
        return

    # A temp file, not stdin: `node --check` opens the path itself,
    # and the error format for a piped source differs across node
    # versions.
    fd, path = tempfile.mkstemp(suffix=".js", prefix="pgkit_check_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(source)
        try:
            proc = subprocess.run(
                [node, "--check", path],
                capture_output=True, text=True, timeout=10.0,
            )
        except subprocess.TimeoutExpired:
            _warn_once(
                f"pgkit.js_check: `node --check` timed out for {label}",
            )
            return
        except OSError as e:
            _warn_once(
                f"pgkit.js_check: could not run node for {label}: {e}",
            )
            return

        if proc.returncode == 0:
            return

        raw = (proc.stderr or proc.stdout or "").strip()
        lineno = _parse_lineno(raw)
        message = _parse_message(raw) or f"syntax error in {label}"
        raise JsSyntaxError(label, lineno, message, raw)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _parse_lineno(raw: str) -> Optional[int]:
    """Extract `path:line` from node's stderr.

    node's error header is `path:line` or `path:line:col` on its own
    line, followed by a code excerpt and the `SyntaxError:` line.
    Non-greedy `(.+?)` handles paths with colons because it backtracks
    until `:\\d+` matches — for `/tmp/weird:file.js:3` it correctly
    returns 3, not a `ValueError` from trying to parse `file.js` as
    an integer.
    """
    for line in raw.splitlines():
        m = _LINENO_RE.match(line.strip())
        if m:
            try:
                return int(m.group(2))
            except ValueError:
                continue
    return None


def _parse_message(raw: str) -> str:
    """Return the `SyntaxError: …` line from node's stderr."""
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("SyntaxError:"):
            return s
    for line in raw.splitlines():
        if line.strip():
            return line.strip()
    return ""


_warned: set = set()


def _warn_once(msg: str) -> None:
    if msg in _warned:
        return
    _warned.add(msg)
    print(msg, file=sys.stderr)
