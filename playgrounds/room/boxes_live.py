"""
boxes_live.py — aggregator for the boxes playground's JS half.

Eight submodules, each a Python string, concatenated into one <script>
scope.  Same flat-scope discipline as the cable project's cable_live.py:
same functions, same names, same call sites as a monolithic file.

Order matters.  bx_i18n declares the translation table and the T()
accessor every other module reads.  bx_core declares the state objects
(placedBoxes, selectedBox, view, mouse, canvas, ctx, viewFloor,
viewWall, layout, router, …) that every later module reads at load
time.  bx_base extends viewFloor / viewWall and declares the palette
every renderer reads; it must therefore come after bx_core.  Everything
else can be in any order because those modules only declare functions
and IIFEs.

    bx_i18n.py       the translation table and the T() accessor
    bx_core.py       model, storage, view state, transforms
    bx_base.py       palette, constants, textures, panel-fade state
    bx_view.py       plan-band rendering: walls, steps, boxes, handles
    bx_edit.py       box lifecycle — place, drag, resize, rotate, delete
    bx_png.py        black-and-white print exporter
    bx_dispatch.py   draw() + canvas/window event listeners
    boxes_panel.py   the panel's installVerticalMenu + dark stylesheet +
                     conditional BOX inspector
"""

from bx_i18n     import I18N_JS
from bx_core     import CORE_JS
from bx_base     import BASE_JS
from bx_view     import VIEW_JS
from bx_edit     import EDIT_JS
from bx_png      import PNG_JS
from bx_dispatch import DISPATCH_JS
from boxes_panel import PANEL_JS


LIVE_JS = (
      I18N_JS
    + CORE_JS
    + BASE_JS
    + VIEW_JS
    + EDIT_JS
    + PNG_JS
    + DISPATCH_JS
    + PANEL_JS
)
