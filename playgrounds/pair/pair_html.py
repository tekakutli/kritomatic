"""
pair_html.py — assembles the HTML/JS bundle.

Concatenation order:

    CORE_JS             canvas, view, layout, pair model, geometry
    CANVAS_VIEW_JS      drawCanvasView — top band
    SEGMENTS_VIEW_JS    drawSegmentsView — bottom band
    SCENE_JS            save / load
    EXPORT_JS           visual-state JSON dump
    KRA_JS              .kra export (segment texts, warped affines)
    PANEL_JS            panel primitives + fields + lists + scrubs +
                        bindings, assembled from the six pg_panel_*
                        files
    DISPATCH_JS         draw() + canvas events
    BOOT_JS             first resize()

KRA_JS is placed before PANEL_JS because the panel's Generate .kra
button binds generateKraFromScene, which KRA_JS defines.

PANEL_JS is assembled by pg_panel.py from:

    pg_panel_core.py        flashStatus, _installScrubInput
    pg_panel_fields.py      _pairInclinationDeg, _syncPairFieldInputs
    pg_panel_lists.py       syncPairList
    pg_panel_scrubs.py      the six _installScrubInput(...) calls
    pg_panel_bindings.py    the sliders, buttons, help, keydown

This file just imports the assembled PANEL_JS; the split lives in
pg_panel.py.
"""

from pg_base           import HTML_HEAD, HTML_TAIL, BOOT_JS
from pg_core           import CORE_JS
from pg_view_canvas    import CANVAS_VIEW_JS
from pg_view_segments  import SEGMENTS_VIEW_JS
from pg_scene          import SCENE_JS
from pg_export         import EXPORT_JS
from pg_kra            import KRA_JS
from pg_panel          import PANEL_JS
from pg_dispatch       import DISPATCH_JS


def render():
    return (
        HTML_HEAD
        + "<script>\n"
        + '"use strict";\n'
        + CORE_JS
        + CANVAS_VIEW_JS
        + SEGMENTS_VIEW_JS
        + SCENE_JS
        + EXPORT_JS
        + KRA_JS
        + PANEL_JS
        + DISPATCH_JS
        + BOOT_JS
        + HTML_TAIL
    )
