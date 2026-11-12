#!/usr/bin/env python3
"""
run_all.py — launch playgrounds in one shared Python process.

    python playgrounds/run_all.py                       # start all seven
    python playgrounds/run_all.py --only board,cone     # start two
    python playgrounds/run_all.py --browser             # open tabs too

    python playgrounds/run_all.py -i                    # all seven, then REPL
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
                        still registering under the same group.  Use
                        this when you want to add a playground after
                        the fact — for instance, start with board only,
                        then add cone, inpaint, segment as you need
                        them, and stop the ones you are done with.

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
        same as it does in fixed-set mode.  There is no "cancel current
        command" gesture; the underlying operations are sub-second.

Participants and ports
======================
    board        8770
    cone         8766
    inpaint      8774
    interceptor  8775
    segment      8773
    text_boxes   8771
    text_wall    8767
    multimask    8776

Each descriptor below supplies:
    name           hub registration name
    module         python package name
    port           preferred TCP port (nearby free one is used if
                   taken)
    html_file      file the launcher renders and serves
    html_render    zero-argument callable returning the HTML string
    routes         JSON POST routes
    raw_routes     binary POST routes (file drops)
    module_sources module manifest served at /modules/

Every panel's routes are the same ones the standalone launcher
registers; the two entry points diverge only in how the HTTP server
is started (blocking vs threaded).

The interceptor playground is off by default (its `--only` default is
unchanged); when the inpaint panel's "Route via interceptor" checkbox
is on, its payload flows through interceptor at 8775.  The two are
independent — inpaint works alone with the checkbox off, interceptor
sits idle alone with no source feeding it.

Standalone vs grouped
=====================
Each of the seven remains independently runnable:

    python playgrounds/board/board_playground.py
    python playgrounds/interceptor/interceptor_playground.py

In that mode the playground registers with the hub under its own
name, with `group=None`.  Killing it via the hub only shuts down
that one process.  Grouped mode wires `group` so the hub knows
killing one member takes down all seven; the interactive panel warns
about that before the kill.

sys.path setup
==============
Two directories must be importable:

  _HERE                playgrounds/
                       → `from board import …`, `from pgkit.launcher
                         import …` all resolve here.

  _SHARED_CABLE_ROOM   playgrounds/shared_cable_room/
                       → `cone/pg_state.py` does
                         `from playground_fonts import …`, and
                         `playground_fonts.py` lives here.  Without
                         this on sys.path, the mere act of importing
                         `cone_html` (which imports `pg_state`)
                         raises ModuleNotFoundError, and because this
                         file imports all six at module load, one
                         broken import takes down every `--only`
                         selection too.

The parent of `playgrounds/` (the repo root) does not need to be on
sys.path: nothing imported from here uses it, and the playgrounds'
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

from board       import board_html, board_server
from cone        import cone_html, cone_kra
from inpaint     import inpaint_html, inpaint_server
from interceptor import interceptor_html, interceptor_server
from segment     import segment_html, segment_server
from text_boxes  import text_boxes_html, text_boxes_server
from text_wall   import text_wall_html, text_wall_kra
from multimask   import multimask_html, multimask_server


GROUP_NAME = "core"


# ============================================================================
#  Descriptors
# ============================================================================

def _descriptors():
    return [
        {
            "name":           "board",
            "module":         "board",
            "port":           8770,
            "html_file":      "board_playground.html",
            "html_render":    board_html.render,
            "routes":         board_server.ROUTES,
            "raw_routes":     board_server.RAW_ROUTES,
            "module_sources": board_html.module_sources(),
        },
        {
            "name":           "cone",
            "module":         "cone",
            "port":           8766,
            "html_file":      "cone_playground.html",
            "html_render":    cone_html.render,
            "routes":         {"/generate-kra": cone_kra.generate},
            "raw_routes":     {},
            "module_sources": cone_html.module_sources(),
        },
        {
            "name":           "inpaint",
            "module":         "inpaint",
            "port":           8774,
            "html_file":      "inpaint.html",
            "html_render":    inpaint_html.render,
            "routes":         inpaint_server.ROUTES,
            "raw_routes":     inpaint_server.RAW_ROUTES,
            "module_sources": inpaint_html.module_sources(),
        },
        {
            "name":           "interceptor",
            "module":         "interceptor",
            "port":           8775,
            "html_file":      "interceptor.html",
            "html_render":    interceptor_html.render,
            "routes":         interceptor_server.ROUTES,
            "raw_routes":     {},
            "module_sources": interceptor_html.module_sources(),
        },
        {
            "name":           "segment",
            "module":         "segment",
            "port":           8773,
            "html_file":      "segment.html",
            "html_render":    segment_html.render,
            "routes":         segment_server.ROUTES,
            "raw_routes":     segment_server.RAW_ROUTES,
            "module_sources": segment_html.module_sources(),
        },
        {
            "name":           "text_boxes",
            "module":         "text_boxes",
            "port":           8771,
            "html_file":      "text_boxes.html",
            "html_render":    text_boxes_html.render,
            "routes":         text_boxes_server.ROUTES,
            "raw_routes":     {},
            "module_sources": text_boxes_html.module_sources(),
        },
        {
            "name":           "text_wall",
            "module":         "text_wall",
            "port":           8767,
            "html_file":      "text_wall_playground.html",
            "html_render":    text_wall_html.render,
            "routes":         {"/generate-kra": text_wall_kra.generate},
            "raw_routes":     {},
            "module_sources": text_wall_html.module_sources(),
        },
        {
            "name":           "multimask",
            "module":         "multimask",
            "port":           8776,
            "html_file":      "multimask.html",
            "html_render":    multimask_html.render,
            "routes":         multimask_server.ROUTES,
            "raw_routes":     multimask_server.RAW_ROUTES,
            "module_sources": multimask_html.module_sources(),
        },
    ]


# ============================================================================
#  Hub shutdown (unchanged from previous revision)
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

    Using a dedicated thread rather than `select()` avoids the
    buffered-stdin race (Python's `sys.stdin` may hold bytes that
    `select` cannot see).  The cost is one thread for the whole
    session, not one per prompt.
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
