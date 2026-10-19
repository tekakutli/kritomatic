"""
board_html.py — assemble the board's HTML page.

The shared page shell (head, CSS wrapper, panel scaffolding, status
line, restore button, "controlled remotely" banner, `<canvas>`, boot
script) comes from `pgkit.html_shell.page()`.  This module supplies
the two project-specific pieces (the panel body from
`pg_base.PG_PANEL_BODY`, the project CSS from `pg_base.PG_CSS`) and
the segment list that page() assembles.

SEGMENT LIST
============
`page(segments=...)` is the multi-segment form (see
`pgkit/html_shell.py`).  Each `(label, js)` pair becomes its own
`<script>` block with `//# sourceURL=<label>.js`; DevTools then
attributes a runtime error or uncaught exception to the specific
source file, not to `board_playground.html`.  A syntax error is
caught by `page()`'s pre-flight check before any HTML is produced.

The list order is the concatenation order the file always used:

    CORE_JS            canvas, view, board state, item model,
                       rotation-aware geometry, projection transform
    REMOTE_CLIENT_JS   pgkit's WebSocket client and field registry
    VIEW_BOARD_JS      drawBoardView — cards and grid
    KRITA_JS           fetch glue for /krita/*, drop overlay install
    PANEL_SEGMENTS     pgkit's panel_core + four board panel files
    SCENE_JS           save / load
    DISPATCH_JS        draw() + canvas event listeners

BOOT SYNC
=========
`boardBootSync` is called once by page()'s boot block, after
installShellControls and before resize().  It runs the initial
syncPanelSliders, syncFileList, and refreshFromKrita.

SYNTAX ERRORS
=============
If `page()` raises `JsSyntaxError`, this module catches it, prints
a diagnostic that names the offending source file and the line
within that file, and exits with status 1.  No HTML file is written;
the server does not start.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base       import PG_CSS, PG_PANEL_BODY
from pg_core       import CORE_JS
from pg_view_board import VIEW_BOARD_JS
from pg_krita      import KRITA_JS
from pg_panel      import PANEL_SEGMENTS
from pg_scene      import SCENE_JS
from pg_dispatch   import DISPATCH_JS

from pgkit.html_shell    import page
from pgkit.remote_client import REMOTE_CLIENT_JS
from pgkit.js_check      import JsSyntaxError


_SEGMENTS = (
    [
        ("pg_core.py",             CORE_JS),
        ("pgkit/remote_client.py", REMOTE_CLIENT_JS),
        ("pg_view_board.py",       VIEW_BOARD_JS),
        ("pg_krita.py",            KRITA_JS),
    ]
    + list(PANEL_SEGMENTS)
    + [
        ("pg_scene.py",            SCENE_JS),
        ("pg_dispatch.py",         DISPATCH_JS),
    ]
)


def _report_and_exit(err: JsSyntaxError) -> None:
    """Print a diagnostic naming the source file and line, then exit.

    `page()` raises `JsSyntaxError` from two places: a per-segment
    check (where `err.label` is one of the segments and `err.lineno`
    is 1-based against that segment's body plus `"use strict";`), and
    the concatenation check (where `err.label` is `"bundle"`).
    """
    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    if err.label and err.label != "bundle":
        offset = (err.lineno - 1) if err.lineno is not None else None
        if offset is not None and offset >= 1:
            print(f"JS syntax error in {err.label}, "
                  f"line {offset} of the file:", file=sys.stderr)
        else:
            print(f"JS syntax error in {err.label}:", file=sys.stderr)
    elif err.lineno is not None:
        line = 2
        located = None
        for label, js in _SEGMENTS:
            body = js if js.endswith("\n") else js + "\n"
            n_lines = body.count("\n")
            start = line
            end = line + n_lines - 1
            if start <= err.lineno <= end:
                located = (label, err.lineno - start + 1)
                break
            line = end + 1

        if located is not None:
            label, offset = located
            print(f"JS syntax error in {label}, "
                  f"line {offset} of the file "
                  f"(bundle line {err.lineno}):", file=sys.stderr)
        else:
            print(f"JS syntax error at bundle line {err.lineno} "
                  f"(outside any tracked segment — likely in the "
                  f"boot script):", file=sys.stderr)
    else:
        print(f"JS syntax error (no line info): {err.message}",
              file=sys.stderr)

    print(file=sys.stderr)
    print(f"  {err.message}", file=sys.stderr)
    print(file=sys.stderr)
    print("Offending line (from node's output):", file=sys.stderr)
    print(file=sys.stderr)
    for line in err.raw.splitlines():
        print(f"  {line}", file=sys.stderr)
    print(file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    sys.exit(1)


def render():
    try:
        return page(
            title        = "Board — files on a canvas",
            panel_title  = "Board",
            panel_body   = PG_PANEL_BODY,
            segments     = _SEGMENTS,
            extra_css    = PG_CSS,
            status_text  = "ready",
            boot_sync_fn = "boardBootSync",
        )
    except JsSyntaxError as err:
        _report_and_exit(err)
