"""
pgkit.js_check — syntax-check a JS module before serving it.

`check_js(source, label)` writes `source` to a temp file and runs
`node --check` on it.  The temp file gets `.mjs` suffix so node
parses `import`/`export` as ES module syntax; a `.js` suffix would
require `--experimental-modules` on older nodes.

Non-tool failures (node missing, subprocess error, timeout) do not
raise — the check is a diagnostic, not a build requirement.

CACHE
=====
`node --check` is a subprocess, and every playground runs it once per
JS module at page-build time.  In the shared-process launcher this is
~120–240 subprocess spawns per `run_all.py` invocation — several
seconds of wall clock, dwarfing the interpreter-startup savings that
justify the shared process in the first place.

The cache eliminates that cost on every run after the first (or after
any JS source edit).  Each entry is keyed by::

    sha256(
        cache_version,
        sys.platform,
        node_path,
        node_version,
        source,
    )

and stored as a small JSON file under `~/.pgkit/js_check_cache/`.
A cache hit is a fast file read (~50 µs); a cache miss is the full
`node --check` subprocess (100–300 ms).  On a warm cache, the whole
`run_all.py` startup shaves several seconds.

The cache invalidates automatically when the source changes, the
node binary moves or is upgraded, or the platform changes (a
`node --check` that succeeded on one OS can fail on another only
pathologically, but the extra safety costs one line of hash input).

Two environment variables affect behavior:

    PGKIT_NO_JS_CACHE=1     disable the cache entirely (always
                            re-run `node --check`).  Use this if you
                            suspect a stale cache entry.
    PGKIT_JS_CACHE_DIR=…    relocate the cache directory.  Useful
                            when sharing a scratch directory across
                            checkouts, or isolating it in CI.

Cached failures replay verbatim — same `JsSyntaxError` with the same
lineno, message, and raw output.  The raw output has the ephemeral
temp path rewritten to the module label so the entry is portable
across runs and does not point at a file the OS has deleted.

The cache is a dev artifact.  Entries are ~200 B for a success and
~1 KB for a failure; even 10,000 entries stay under ~2 MB.  There is
no evictor.  If it ever matters, `rm -rf ~/.pgkit/js_check_cache/`
is the fallback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
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


# ============================================================================
#  Node discovery (cached per process)
# ============================================================================

_node_path: Optional[str] = None
_node_path_set = False

def _get_node() -> Optional[str]:
    global _node_path, _node_path_set
    if not _node_path_set:
        _node_path = shutil.which("node")
        _node_path_set = True
    return _node_path


_node_version: Optional[str] = None
_node_version_set = False

def _get_node_version(node_path: str) -> Optional[str]:
    global _node_version, _node_version_set
    if not _node_version_set:
        _node_version_set = True
        try:
            _node_version = subprocess.check_output(
                [node_path, "--version"], text=True, timeout=2,
            ).strip()
        except Exception:
            _node_version = None
    return _node_version


# ============================================================================
#  Cache directory + key
# ============================================================================

_CACHE_VERSION = "1"

def _cache_enabled() -> bool:
    return os.environ.get("PGKIT_NO_JS_CACHE", "").lower() not in (
        "1", "true", "yes",
    )

def _cache_dir() -> Path:
    override = os.environ.get("PGKIT_JS_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".pgkit" / "js_check_cache"


def _cache_key(node_path: str, node_version: Optional[str],
               source: str) -> str:
    h = hashlib.sha256()
    h.update(_CACHE_VERSION.encode("ascii"))
    h.update(b"\0")
    h.update(sys.platform.encode("utf-8", "replace"))
    h.update(b"\0")
    h.update(node_path.encode("utf-8", "replace"))
    h.update(b"\0")
    h.update((node_version or "").encode("utf-8", "replace"))
    h.update(b"\0")
    h.update(source.encode("utf-8", "replace"))
    return h.hexdigest()


def _load_cache(key: str) -> Optional[dict]:
    if not _cache_enabled():
        return None
    try:
        with open(_cache_dir() / (key + ".json"),
                  "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _store_cache(key: str, value: dict) -> None:
    if not _cache_enabled():
        return
    d = _cache_dir()
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    fd = None
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(
            dir=str(d), prefix=key + "-", suffix=".json",
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f)
        fd = None  # closed by the context manager
        os.replace(tmp_path, d / (key + ".json"))
        tmp_path = None
    except OSError:
        pass
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if tmp_path is not None:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ============================================================================
#  The public check
# ============================================================================

def check_js(source: str, *, label: str = "bundle") -> None:
    """Raise JsSyntaxError if `source` is not valid JS module source."""
    node = _get_node()
    if node is None:
        _warn_once(
            "pgkit.js_check: `node` not found on PATH — "
            "skipping JS syntax check. Install node to enable it.",
        )
        return

    version = _get_node_version(node)
    key = _cache_key(node, version, source)

    cached = _load_cache(key)
    if cached is not None:
        if cached.get("ok"):
            return
        raise JsSyntaxError(
            label,
            cached.get("lineno"),
            cached.get("message", "syntax error"),
            cached.get("raw", ""),
        )

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
            _store_cache(key, {"ok": True})
            return

        raw = (proc.stderr or proc.stdout or "").strip()
        # The temp path changes on every invocation; rewrite it to the
        # module label so a cached failure is portable across runs and
        # does not point a reader at a file the OS has already deleted.
        display_label = label or "source"
        if path:
            raw = raw.replace(path, display_label)
        lineno = _parse_lineno(raw)
        message = _parse_message(raw) or f"syntax error in {label}"
        _store_cache(key, {
            "ok":      False,
            "lineno":  lineno,
            "message": message,
            "raw":     raw,
        })
        raise JsSyntaxError(label, lineno, message, raw)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ============================================================================
#  Helpers (unchanged)
# ============================================================================

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
