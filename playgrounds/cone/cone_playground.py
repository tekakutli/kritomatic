"""
cone_playground.py — look at the inside of a cone.

    python cone_playground.py     → writes cone_playground.html and
                                    serves it (via kit_serve)

The module_sources dict built by `cone_html.module_sources()` is
passed to both `page()` (for syntax checking) and `kit_serve()` (for
serving at /modules/).  kit_serve additionally wires the `/remote`
WebSocket, so a remote client (via `remote_panel_playground.py`) can
drive the cone from another tab — same shape as board/pair/text_board.

`cone_server.py` is deleted; its only remaining job — the
`/generate-kra` route — is now a single dict entry passed to
`kit_serve(routes=...)`.

sys.path setup
==============
Two directories must be importable:

  _PLAYGROUNDS         playgrounds/
                       → `from cone import cone_html, cone_kra`
                         resolves here.  This file is a script
                         (`python cone_playground.py`), so its
                         `__name__` is `__main__` and relative
                         imports would fail; the launcher therefore
                         uses an absolute package import.

  _SHARED_CABLE_ROOM   playgrounds/shared_cable_room/
                       → `cone/pg_state.py` does
                         `from playground_fonts import …`, and
                         `playground_fonts.py` lives here.  Importing
                         `cone_html` transitively imports `pg_state`,
                         which fails if this directory is not on
                         sys.path.

`_HERE` (this directory) is *removed* from sys.path so a stray
`import pg_base` cannot resolve to a sibling under a top-level name
— that would collide with every other playground's `pg_base` when
the shared-process launcher is used.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PLAYGROUNDS = os.path.dirname(_HERE)
_SHARED_CABLE_ROOM = os.path.join(_PLAYGROUNDS, "shared_cable_room")

try:
    sys.path.remove(_HERE)
except ValueError:
    pass
for _p in (_PLAYGROUNDS, _SHARED_CABLE_ROOM):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from cone import cone_html, cone_kra

from pgkit.server import serve as kit_serve


HTML_FILE    = "cone_playground.html"
PORT         = 8766
SERVE        = True
OPEN_BROWSER = True


def main():
    html = cone_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  band split   : cone (upper) + parameter space (lower)")
    print(f"  interactions : drag apex · scroll depth · "
          f"drag patches in either band · shift+click a patch to "
          f"drop a square · drag squares from either band · "
          f"H collapses the panel")
    print(f"  remote api   : ws://127.0.0.1:{PORT}/remote")

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            routes         = {"/generate-kra": cone_kra.generate},
            module_sources = cone_html.module_sources(),
            hub_name       = "cone",
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
