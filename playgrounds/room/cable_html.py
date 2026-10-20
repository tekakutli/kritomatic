"""
cable_html.py — assembles the HTML/JS bundle from pg_core + cable_live
+ pg_arrows + pg_export.

pg_core.py    holds everything that defines the model, the storage format,
              and the passive infrastructure.
cable_live.py    holds everything interactive.
pg_arrows.py  holds the wall→plan escape-arrow field.
pg_export.py  holds the printable cable-run diagram exporter.

This file concatenates them, injects the geometry dict as
`const GEOMETRY`, splices the @font-face block into the head, and
prepends the FONT_MONO / FONT_SANS / FONT_FACE_CSS declarations that
every module below reads.  See playground_fonts.py for the single
knob that drives both playgrounds' fonts.
"""

import json

from pg_core          import HTML_HEAD, CORE_JS, BOOT_JS, HTML_TAIL
from cable_live       import LIVE_JS
from pg_arrows        import ARROWS_JS
from pg_export        import EXPORT_JS
from playground_fonts import FONT_JS, inject_head


def render(geom):
    """Return the final HTML document with `geom` baked in."""
    return (
        inject_head(HTML_HEAD)
        + "<script>\n"
        + '"use strict";\n'
        + FONT_JS
        + "const GEOMETRY = "
        + json.dumps(geom, separators=(",", ":"))
        + ";\n"
        + CORE_JS
        + LIVE_JS
        + ARROWS_JS
        + EXPORT_JS
        + BOOT_JS
        + HTML_TAIL
    )
