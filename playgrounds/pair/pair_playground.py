"""
pair_playground.py — pairs of parallel lines with 1D segment layouts.

    python pair_playground.py

The canvas shows a set of PAIRS.  A pair is two parallel lines that
run from one edge of the viewport to the other, together with a 1D
layout of SEGMENTS along the pair's abstract axis.  Each pair has
its own direction, gap, offset, per-line metric, whole-pair scale,
and loopback gap; see pg_core.py for the model.

The bottom band is the SEGMENT VIEW: one row per pair, each row an
axis along which the pair's segments are laid out as bars.  Edges
drag to widen or shrink; bodies drag to translate every segment in
the pair together (or, with Shift held, to build a growing set of
segments and drag them together).

The panel is a shared shell provided by `pgkit` — shared CSS, panel
primitives, HTML scaffolding, the WebSocket server, and the
browser-side remote client.  The project supplies its model, views,
scene format, KRA export, and — in `pg_panel_scrubs.py` and
`pg_panel_bindings.py` — the registration of its value fields with
`pgkitRegisterField`.  See `pgkit/MIGRATION.md`.

REMOTE API
==========
Every value field declared in `pg_panel_scrubs.py` and
`pg_panel_bindings.py` is published over the WebSocket at `/remote`.
A remote client — a CLI, another browser tab, a script — reads the
current state on connect and can write any registered field by name.
When at least one remote client is connected, the panel hides and
the "Controlled remotely" banner appears.  There is no polling: a
value is published only when its own write path calls
`pgkitNotifyLocal` or when a wholesale model change calls
`pgkitNotifyAll`.

THIS FILE
=========
The entry point.  It sets up `sys.path` so both the playground's
own modules and the sibling `pgkit` package resolve, writes the
HTML page (assembled by `pair_html.py`), and starts the shared
HTTP+WebSocket server with the one pair-specific route — the KRA
export at `/generate-kra`.

There is no separate `pair_server.py`.  After the pgkit migration
the server side of this project is one `pgkit.server.serve(...)`
call with one route; a whole module for that call would be a
wrapper around a wrapper.
"""

import os
import sys

# Both this directory and its parent (which contains pgkit/) need to
# be importable, so the project's own modules and the shared package
# resolve regardless of the working directory or of
# PYTHONSAFEPATH / `python -P`.
_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pair_html
import pair_kra

from pgkit.server import serve as kit_serve


HTML_FILE    = "pair_playground.html"
PORT         = 8767
SERVE        = True
OPEN_BROWSER = True


def main():
    html = pair_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print("  band split   : canvas (upper) + segment view (lower)")
    print("  interactions : drag pairs in the canvas · drag segment "
          "edges or bodies in the segment view · H collapses the panel")
    print(f"  remote api   : ws://127.0.0.1:{PORT}/remote")

    if SERVE:
        print()
        kit_serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
            routes       = {"/generate-kra": pair_kra.generate},
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
