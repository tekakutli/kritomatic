#!/usr/bin/env python3
"""
run_all.py — launch playgrounds in one shared Python process.

    python playgrounds/run_all.py                       # start every discovered playground
    python playgrounds/run_all.py --only board,cone     # start two
    python playgrounds/run_all.py --browser             # open tabs too

    python playgrounds/run_all.py -i                    # all, then REPL
    python playgrounds/run_all.py -i --only ""          # start nothing, REPL only
    python playgrounds/run_all.py -i --only board,cone  # two, then REPL

    python playgrounds/run_all.py --shutdown-hub        # kill a running hub

Two modes:

  Fixed set (default)   Start the initial set, block until Ctrl+C,
                        stop everything, exit.  The behavior described
                        in `pgkit/launcher.py`'s docstring.

  Interactive (-i)      Start the initial set, then drop into a REPL
                        where you can `start <name>` / `stop <name>`
                        at runtime, all still in this one process, all
                        still registering under the same group.

        REPL commands:
            list              — running playgrounds and their URLs
            available         — all known playgrounds, * marks running
            start <name>      — start one playground
            stop <name>       — stop one playground
            restart <name>    — stop, then start
            start-all         — start every not-yet-running
            stop-all          — stop every running
            help / ?          — this list
            quit / Ctrl+D     — shut down the group and exit

        Ctrl+C at the REPL prompt shuts down the whole group cleanly,
        same as it does in fixed-set mode.

Playground discovery
====================
A "playground" is any subpackage `<name>/` of this directory that
contains a submodule `<name>/<name>_playground.py` exposing a
callable `descriptor()`.  That function returns the fields the
launcher needs:

    name           hub registration name
    port           preferred TCP port
    html_file      file the launcher renders and serves
    html_render    zero-argument callable returning the HTML string
    routes         JSON POST routes
    raw_routes     binary POST routes (file drops)
    module_sources module manifest served at /modules/

Adding a new playground therefore requires only creating the
subpackage and exposing `descriptor()`.  Nothing in this file needs
to change.  Discovery is tolerant: a playground whose entry module
raises on import is skipped with a stderr line, and packages without
a `<name>_playground` module (pgkit, pg_comfy, `shared_cable_room`,
`__pycache__`) are silently ignored.

Note that `cable`, `remote_panel`, `room`, `kritomatic_nodes`, and
`remote_cli` are not subpackages of `playgrounds/` in the Python
sense — they have no `__init__.py` — so `pkgutil.iter_modules`
never yields them and discovery never reaches their import path.
They remain independently runnable via their own entry scripts.

The `--shutdown-hub` path does not invoke discovery, so it keeps
working even when a playground is broken.

Standalone vs grouped
=====================
Each playground remains independently runnable, e.g.:

    python playgrounds/board/board_playground.py

In that mode it registers with the hub under its own name, with
`group=None`.  Killing it via the hub only shuts down that one
process.  Grouped mode wires `group` so the hub knows killing one
member takes down the whole group.

sys.path setup
==============
Two directories must be importable:

  _HERE                playgrounds/
                       → `from pgkit.launcher import …`, discovery,
                         and each playground's `<name>_playground`
                         module all resolve here.

  _SHARED_CABLE_ROOM   playgrounds/shared_cable_room/
                       → `cone/pg_state.py` does
                         `from playground_fonts import …`, and
                         `playground_fonts.py` lives here.  Without
                         this on sys.path, importing `cone_html` (which
                         imports `pg_state`) raises ModuleNotFoundError.
                         Discovery surfaces this as a per-playground
                         stderr line rather than a fatal error.

The parent of `playgrounds/` (the repo root) does not need to be on
sys.path: nothing imported from here uses it, and each playground's
own `_find_repo_src()` helpers walk up from `__file__` to locate
`src/kritomatic/` independently.
"""

import argparse
import os
import queue
import subprocess
import sys
import threading


_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED_CABLE_ROOM = os.path.join(_HERE, "shared_cable_room")
for _p in (_HERE, _SHARED_CABLE_ROOM):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pgkit.launcher import Launcher, serve_group


GROUP_NAME = "core"


# ============================================================================
#  Playground discovery
# ============================================================================

def _discover_playgrounds():
    """Scan this directory for playground subpackages.

    A playground is any subpackage `<name>/` whose entry module
    `<name>/<name>_playground.py` exposes a callable `descriptor()`.
    The descriptor carries the fields the launcher expects.

    Import failures split two ways:

      · Not a playground — pgkit, pg_comfy, `shared_cable_room`,
        `__pycache__`, etc. do not have a `<name>_playground.py`, so
        `import <name>.<name>_playground` raises and we silently skip.
        Directories without an `__init__.py` (cable, remote_panel,
        room, kritomatic_nodes, remote_cli) are not yielded by
        `pkgutil.iter_modules` in the first place, so they never
        reach this branch either.

      · A playground whose entry module is broken — the file exists
        but its imports (or any module-scope statement it runs on
        load) fail.  Printed to stderr; skipped.

    Either way, discovery itself never raises, and one broken
    playground does not take down the others.  The exception class is
    deliberately `Exception`, not `ImportError`: a properly-packaged
    playground can fail its import for reasons other than a missing
    module (`FileNotFoundError` on a module-scope JSON read,
    `KeyError` on a config dict, `SyntaxError` in a sibling), and
    those must not propagate out of discovery.
    """
    import importlib
    import pkgutil

    out = []
    for _, name, ispkg in pkgutil.iter_modules([_HERE]):
        if not ispkg:
            continue

        module_path = f"{name}.{name}_playground"
        try:
            mod = importlib.import_module(module_path)
        except Exception as e:
            if _has_playground_entry(name):
                print(f"  [{name}] could not import {module_path}: {e}",
                      file=sys.stderr)
            continue

        descriptor_fn = getattr(mod, "descriptor", None)
        if not callable(descriptor_fn):
            continue

        try:
            out.append(descriptor_fn())
        except Exception as e:
            print(f"  [{name}] descriptor() failed: {e}", file=sys.stderr)

    out.sort(key=lambda d: d.get("name", ""))
    return out


def _has_playground_entry(name):
    """True iff `name` is a directory containing `name_playground.py`.

    Distinguishes "not a playground" (silent skip) from "a playground
    whose entry module failed to import" (warn on stderr).  The check
    is a cheap file-existence probe — no imports.
    """
    entry = os.path.join(_HERE, name, f"{name}_playground.py")
    return os.path.isfile(entry)


def _descriptors():
    return _discover_playgrounds()


# ============================================================================
#  Hub shutdown
# ============================================================================

def _shutdown_hub() -> None:
    """Ask a running pgkit hub to exit.

    Runs the hub's own shutdown entry point in a child process.  The
    child is a fresh interpreter and inherits the parent's *environment*,
    not the parent's in-memory `sys.path` — so `python -m pgkit.hub`
    would fail with ModuleNotFoundError unless `pgkit` is installed
    system-wide or `PYTHONPATH` is already set.  Augmenting the child's
    `PYTHONPATH` with `_HERE` is the same technique
    `pgkit/hub_client.py`'s detached spawn uses.

    Called from `main()` before discovery; no playground is imported
    on this path.  That is deliberate: the command exists to be
    reachable when a playground misbehaves, and must not fail on the
    misbehaving playground's behalf.
    """
    env = os.environ.copy()
    prev = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = _HERE + (os.pathsep + prev if prev else "")
    subprocess.run(
        [sys.executable, "-m", "pgkit.hub", "--shutdown"],
        env=env,
    )


# ============================================================================
#  REPL — one stdin reader thread per session, no leaks
# ============================================================================

class _StdinReader:
    """Read lines from stdin in a background thread.

    One instance per REPL session.  `readline(abort_event)` polls the
    internal queue with a 0.2s timeout so a signal handler that sets
    `abort_event` breaks the block — but the reader thread itself
    stays blocked in `sys.stdin.readline()`, which is fine: it is a
    daemon, and it dies when the launcher process exits.
    """

    def __init__(self):
        self._q = queue.Queue()
        self._thread = threading.Thread(
            target=self._run, name="run_all-stdin", daemon=True,
        )
        self._thread.start()

    def _run(self):
        while True:
            try:
                line = sys.stdin.readline()
            except Exception:
                self._q.put(None)
                return
            if not line:
                self._q.put(None)
                return
            self._q.put(line)

    def readline(self, abort_event):
        """Return one line (without trailing newline), or None on EOF
        or abort.  "" means "Ctrl+C — treat as empty input"."""
        while not abort_event.is_set():
            try:
                line = self._q.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                return None
            return line.rstrip("\r\n")
        return None


def _dispatch(launcher, line):
    """Handle one REPL command line.  Returns False to exit the REPL."""
    parts = line.split(None, 1)
    if not parts:
        return True
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("quit", "exit", "q"):
        return False

    if cmd in ("help", "?", "h"):
        print("Commands:")
        print("  list              — running playgrounds and their URLs")
        print("  available         — all known playgrounds, * marks running")
        print("  start <name>      — start one playground")
        print("  stop <name>       — stop one playground")
        print("  restart <name>    — stop, then start")
        print("  start-all         — start every not-yet-running")
        print("  stop-all          — stop every running")
        print("  quit / Ctrl+D     — shut down the group and exit")
        return True

    if cmd in ("list", "ls"):
        running = launcher.running()
        if not running:
            print("  (no playgrounds running)")
        else:
            for name in launcher.names():
                if name in running:
                    print(f"  {name:<12} {running[name]}")
        return True

    if cmd in ("available", "avail"):
        running = launcher.running()
        for name in launcher.names():
            mark = "*" if name in running else " "
            print(f"  {mark} {name}")
        return True

    if cmd == "start":
        if not arg:
            print("usage: start <name>")
            return True
        url = launcher.start(arg)
        if url is None:
            print(f"  could not start {arg!r} "
                  f"(already running, or unknown)")
        return True

    if cmd == "stop":
        if not arg:
            print("usage: stop <name>")
            return True
        if not launcher.stop(arg):
            print(f"  {arg!r} is not running")
        return True

    if cmd == "restart":
        if not arg:
            print("usage: restart <name>")
            return True
        launcher.stop(arg)
        launcher.start(arg)
        return True

    if cmd in ("start-all", "startall"):
        for name in launcher.names():
            if not launcher.is_running(name):
                launcher.start(name)
        return True

    if cmd in ("stop-all", "stopall"):
        launcher.stop_all()
        return True

    print(f"  unknown command: {cmd!r} (type 'help')")
    return True


def _run_interactive(group, playgrounds, *, open_browser=False, only=None):
    """Start the initial set, then drop into a REPL."""
    launcher = Launcher(group, playgrounds, open_browser=open_browser)
    initial = (list(only) if only is not None
               else [p["name"] for p in playgrounds])
    for name in initial:
        launcher.start(name)
    launcher.install_signal_handlers()

    print()
    print(f"Group '{group}' — {len(launcher.running())} running, "
          f"{len(launcher.names())} total.")
    print("Type 'help' for commands, 'quit' or Ctrl+D to exit.")

    prompt = f"({group}) "
    reader = _StdinReader()
    try:
        while not launcher._shutdown_event.is_set():
            sys.stdout.write(prompt)
            sys.stdout.flush()
            line = reader.readline(launcher._shutdown_event)
            if line is None:
                break
            if not _dispatch(launcher, line):
                break
    finally:
        launcher.stop_all()
        print("Stopped.")


# ============================================================================
#  Entry point
# ============================================================================

def main():
    p = argparse.ArgumentParser(prog="run_all.py")
    p.add_argument("--only", default=None,
                   help="Comma-separated playground names.  With -i, "
                        "an empty value (--only \"\") starts nothing "
                        "and opens the REPL.")
    p.add_argument("--browser", action="store_true",
                   help="Open each playground in the browser when it "
                        "starts.")
    p.add_argument("-i", "--interactive", action="store_true",
                   help="After the initial set starts, enter a REPL "
                        "for start/stop at runtime.")
    p.add_argument("--shutdown-hub", action="store_true",
                   help="Ask a running pgkit hub to exit, then exit.")
    args = p.parse_args()

    # Short-circuit before any playground is imported.  See
    # `_shutdown_hub` and the module docstring.
    if args.shutdown_hub:
        _shutdown_hub()
        return

    only = None
    if args.only is not None:
        only = {n.strip() for n in args.only.split(",") if n.strip()}

    if args.interactive:
        _run_interactive(
            GROUP_NAME,
            _descriptors(),
            open_browser=args.browser,
            only=only,
        )
    else:
        serve_group(
            GROUP_NAME,
            _descriptors(),
            open_browser=args.browser,
            only=only,
        )


if __name__ == "__main__":
    main()
