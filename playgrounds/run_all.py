#!/usr/bin/env python3
"""
run_all.py — launch all six playgrounds in one shared Python process.

    python playgrounds/run_all.py
    python playgrounds/run_all.py --only board,cone
    python playgrounds/run_all.py --browser
    python playgrounds/run_all.py --shutdown-hub

Six playgrounds, one interpreter, one Ctrl+C.  See
pgkit/launcher.py's docstring for the shared-process model and its
costs.

Participants and ports
======================
    board       8770
    cone        8766
    inpaint     8774
    segment     8773
    text_boxes  8771
    text_wall   8767

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

Standalone vs grouped
=====================
Each of the six remains independently runnable:

    python playgrounds/board/board_playground.py

In that mode the playground registers with the hub under its own
name, with `group=None`.  Killing it via the hub only shuts down
that one process.  Grouped mode wires `group` so the hub knows
killing one member takes down all six; the interactive panel warns
about that before the kill.

sys.path setup
==============
Three directories must be importable:

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
import subprocess
import sys


_HERE = os.path.dirname(os.path.abspath(__file__))
_SHARED_CABLE_ROOM = os.path.join(_HERE, "shared_cable_room")
for _p in (_HERE, _SHARED_CABLE_ROOM):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pgkit.launcher import serve_group

from board      import board_html, board_server
from cone       import cone_html, cone_kra
from inpaint    import inpaint_html, inpaint_server
from segment    import segment_html, segment_server
from text_boxes import text_boxes_html, text_boxes_server
from text_wall  import text_wall_html, text_wall_kra


GROUP_NAME = "core"


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
    ]


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


def main():
    p = argparse.ArgumentParser(prog="run_all.py")
    p.add_argument("--only", default=None,
                   help="Comma-separated playground names.")
    p.add_argument("--browser", action="store_true",
                   help="Open each playground in the browser on start.")
    p.add_argument("--shutdown-hub", action="store_true",
                   help="Ask a running pgkit hub to exit, then exit.")
    args = p.parse_args()

    if args.shutdown_hub:
        _shutdown_hub()
        return

    only = None
    if args.only:
        only = {n.strip() for n in args.only.split(",") if n.strip()}

    serve_group(
        GROUP_NAME,
        _descriptors(),
        open_browser=args.browser,
        only=only,
    )


if __name__ == "__main__":
    main()
