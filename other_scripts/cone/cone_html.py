"""
cone_html.py — assembles the HTML/JS bundle.

Concatenation order:

    CORE_JS             canvas, layout, view, cone state, patch model
    CONE_VIEW_JS        drawConeView — everything above the divider
    FLAT_VIEW_JS        drawFlatView — the parameter-space view
    FLOAT_SQUARES_JS    shapes on each patch's plane
    EXPORT_JS           the visual-state JSON export
    PANEL_JS            slider + list + button bindings
    DISPATCH_JS         draw() orchestrator + canvas event listeners
    BOOT_JS             first resize()
"""

from pg_base         import HTML_HEAD, HTML_TAIL, BOOT_JS
from pg_core         import CORE_JS
from pg_view_cone    import CONE_VIEW_JS
from pg_view_flat    import FLAT_VIEW_JS
from pg_view_squares import FLOAT_SQUARES_JS
from pg_export       import EXPORT_JS
from pg_panel        import PANEL_JS
from pg_dispatch     import DISPATCH_JS


def render():
    return (
        HTML_HEAD
        + "<script>\n"
        + '"use strict";\n'
        + CORE_JS
        + CONE_VIEW_JS
        + FLAT_VIEW_JS
        + FLOAT_SQUARES_JS
        + EXPORT_JS
        + PANEL_JS
        + DISPATCH_JS
        + BOOT_JS
        + HTML_TAIL
    )
