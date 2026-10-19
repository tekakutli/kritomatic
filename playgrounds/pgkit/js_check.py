"""
pgkit.js_check — syntax-check a JS module before serving it.

`check_js(source, label)` writes `source` to a temp file and runs
`node --check` on it.  The temp file gets `.mjs` suffix so node
parses `import`/`export` as ES module syntax; a `.js` suffix would
require `--experimental-modules` on older nodes.

Non-tool failures (node missing, subprocess error, timeout) do not
raise — the check is a diagnostic, not a build requirement.
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
    def __init__(self, label: str, lineno: Optional[int],
                 message: str, raw: str):
        self.label = label
        self.lineno = lineno
        self.message = message
        self.raw = raw
        loc = f"{label}:{lineno}" if lineno is not None else label
        super().__init__(f"{loc}: {message}")


def check_js(source: str, *, label: str = "bundle") -> None:
    """Raise JsSyntaxError if `source` is not valid JS module source."""
    node = shutil.which("node")
    if node is None:
        _warn_once(
            "pgkit.js_check: `node` not found on PATH — "
            "skipping JS syntax check. Install node to enable it.",
        )
        return

    fd, path = tempfile.mkstemp(suffix=".mjs", prefix="pgkit_check_")
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
    for line in raw.splitlines():
        m = _LINENO_RE.match(line.strip())
        if m:
            try:
                return int(m.group(2))
            except ValueError:
                continue
    return None


def _parse_message(raw: str) -> str:
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
