"""
boxes_html.py — assembles the HTML/JS bundle from boxes_panel +
boxes_live.

Concatenation order (all inside one <script>):

    1. HTML_HEAD       — the panel markup, empty canvas, light base CSS
    2. const FONT_*    — from playground_fonts.py
    3. const GEOMETRY  — the geometry dict, JSON-encoded
    4. LIVE_JS         — the concatenation from boxes_live.py, which
                         starts with the model (bx_core.CORE_JS)
    5. BOOT_JS         — wires button onclick handlers, kicks off load
    6. HTML_TAIL       — closes the script tag and body

The @font-face block is spliced into <head> by inject_head; the
FONT_MONO / FONT_SANS / FONT_FACE_CSS constants are declared by
FONT_JS before any module reads them.
"""

import json

from boxes_panel      import HTML_HEAD, BOOT_JS, HTML_TAIL
from boxes_live       import LIVE_JS
from playground_fonts import FONT_JS, inject_head


def render(geom):
    return (
        inject_head(HTML_HEAD)
        + "<script>\n"
        + '"use strict";\n'
        + FONT_JS
        + "const GEOMETRY = "
        + json.dumps(geom, separators=(",", ":"))
        + ";\n"
        + LIVE_JS
        + BOOT_JS
        + HTML_TAIL
    )
