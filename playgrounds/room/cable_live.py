"""
cable_live.py — aggregator for the cable playground's live half.

Everything the playground's UX actually is: rendering decisions, snap
producers, hop transitions, the wormhole route planner, drawing
lifecycle, the Alt-click reuse policy, event handlers, status text, and
the ruler.

This file is the aggregator.  The interactive half has been split into
thirteen modules by concern, mirroring the split pg_arrows.py and
pg_export.py introduced for the escape-arrow field and the printable
diagram exporter:

    pg_i18n.py        the translation table and the T() accessor —
                      every user-visible string in the playground
    pg_base.py        palette, sheet constants, textures, primitives,
                      the shared extended view state, and the small
                      cross-cutting drawing atoms
    pg_snap.py        snap spec producers for both views
    pg_route.py       hop arcs, the wormhole route planner, floor→wall
                      projection, and merge-target detection
    pg_view_cables.py cable drawing on both views
    pg_view_floor.py  the plan band
    pg_view_wall.py   the unfolded-wall band, its rulers, and step
                      dimension chains
    pg_view_sheet.py  the drafting-sheet decoration layer
    pg_edit.py        cable lifecycle: draw, delete, drag, hit test,
                      merge, adopt, garbage collect
    pg_navigation.py  layout split, zoom, pan, resize
    pg_panel.py       the UI panel, dark stylesheet, title block,
                      status line, cursor, popover, panel fade
    pg_ruler.py       the entire ruler tool, self-contained
    pg_dispatch.py    the draw() orchestrator and the canvas event
                      listeners

Each module is a Python string.  The aggregator concatenates them into
one <script> tag, so the JS lives in a single flat scope — same
functions, same names, same call sites as before the split.  Only the
Python packaging changed; there is no semantic difference.

I18N_JS is prepended so T() is available at parse time to every later
module.  pg_export.py — concatenated after this file by cable_html.py —
sees the same T() and no longer carries its own table; its strings
live in pg_i18n.py alongside the rest of the playground's translated
text.
"""

from pg_i18n       import I18N_JS
from pg_base       import BASE_JS
from pg_snap       import SNAP_JS
from pg_route      import ROUTE_JS
from pg_view_cables import CABLES_VIEW_JS
from pg_view_floor  import FLOOR_VIEW_JS
from pg_view_wall   import WALL_VIEW_JS
from pg_view_sheet  import SHEET_VIEW_JS
from pg_edit        import CABLE_EDIT_JS
from pg_navigation  import NAVIGATION_JS
from pg_panel       import PANEL_JS
from pg_ruler       import RULER_JS
from pg_dispatch    import DISPATCH_JS


LIVE_JS = (
      I18N_JS
    + BASE_JS
    + SNAP_JS
    + ROUTE_JS
    + CABLES_VIEW_JS
    + FLOOR_VIEW_JS
    + WALL_VIEW_JS
    + SHEET_VIEW_JS
    + CABLE_EDIT_JS
    + NAVIGATION_JS
    + PANEL_JS
    + RULER_JS
    + DISPATCH_JS
)
