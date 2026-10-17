"""
pair_html.py — assemble the pair playground's HTML page.

The shared page shell — head, CSS wrapper, panel scaffolding,
status line, restore button, "Controlled remotely" banner, `<canvas>`,
and boot script — comes from `pgkit.html_shell.page()`.  This module
supplies the two project-specific pieces (the panel body from
`pg_base.PAIR_PANEL_BODY`, the project CSS from `pg_base.PAIR_CSS`)
and the script bundle.

SCRIPT CONCATENATION ORDER
==========================
    CORE_JS            canvas, layout, view, pair model, geometry
    REMOTE_CLIENT_JS   pgkit's WebSocket client and field registry
    CANVAS_VIEW_JS     top band
    SEGMENTS_VIEW_JS   bottom band
    SCENE_JS           save / load
    EXPORT_JS          visual-state JSON dump
    KRA_JS             .kra export
    PANEL_JS           pgkit's panel_core, plus pair's fields,
                       lists, scrubs, and bindings (assembled by
                       pg_panel.py)
    DISPATCH_JS        draw() + canvas event listeners

The kit's `PANEL_CORE_JS` (flashStatus and _installScrubInput) and
`REMOTE_CLIENT_JS` (pgkitRegisterField and the WebSocket) are the
only kit-provided JS in the bundle; everything else is pair's.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from pg_base           import PAIR_CSS, PAIR_PANEL_BODY
from pg_core           import CORE_JS
from pg_view_canvas    import CANVAS_VIEW_JS
from pg_view_segments  import SEGMENTS_VIEW_JS
from pg_scene          import SCENE_JS
from pg_export         import EXPORT_JS
from pg_kra            import KRA_JS
from pg_panel          import PANEL_JS
from pg_dispatch       import DISPATCH_JS

from pgkit.html_shell   import page
from pgkit.remote_client import REMOTE_CLIENT_JS


def render():
    script = (
        CORE_JS
        + REMOTE_CLIENT_JS
        + CANVAS_VIEW_JS
        + SEGMENTS_VIEW_JS
        + SCENE_JS
        + EXPORT_JS
        + KRA_JS
        + PANEL_JS
        + DISPATCH_JS
    )
    return page(
        title         = "Pairs — viewer",
        panel_title   = "Pairs",
        panel_body    = PAIR_PANEL_BODY,
        script        = script,
        extra_css     = PAIR_CSS,
        status_text   = "drag pairs &middot; drag segment edges below",
        boot_sync_fn  = "syncPairList",
    )
