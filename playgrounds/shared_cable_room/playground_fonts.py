"""
playground_fonts.py — single source of truth for every font the
cable and boxes playgrounds use, on-screen and in the printable
exports.

Edit CONFIGURATION below.  Everything else follows: the panel chrome,
the canvas readouts, the ruler's dimension labels, the printable
exporter's title block, dimensions, legends, and the preview page.

How it works
------------
This module exports three Python strings:

    FONT_FAMILY_MONO   the CSS font-family stack for the mono font
    FONT_FAMILY_SANS   the CSS font-family stack for the sans font
    FONT_FACE_CSS      the @font-face declarations, base64-inlined

and one JS block:

    FONT_JS            `const FONT_MONO = ...;`
                       `const FONT_SANS = ...;`
                       `const FONT_FACE_CSS = ...;`

Every module that draws text references FONT_MONO / FONT_SANS —
either as `"700 12px " + FONT_MONO` or as `${FONT_MONO}` inside a
template literal.  Every panel stylesheet references `${FONT_MONO}` /
`${FONT_SANS}` inside its own JS template literal.  Every entry
point calls `inject_head(HTML_HEAD)` so the @font-face block lands
in the document's <head>.

There is no string rewriting anywhere.  The source itself reads as
`"700 12px " + FONT_MONO`, which is what a maintainer wants to see.
"""

import base64
import json
import os


# ============================================================================
# CONFIGURATION — the single place to change a font
# ============================================================================

# ---- monospace -----------------------------------------------------------
# Every panel button, canvas readout, status line, ruler value, and
# printable-exporter number is drawn in this font.

MONO_FONT_PATH   = "/usr/share/fonts/WOFF2/FiraCode-Regular.woff2"
MONO_FONT_FAMILY = "Fira Code"
MONO_FONT_WEIGHT = 400
MONO_FALLBACK    = "ui-monospace, Menlo, Consolas, monospace"

# ---- sans-serif ----------------------------------------------------------
# The printable export's title line and section headers, and the
# popover body.

# SANS_FONT_PATH   = (
#     "/home/tekakutli/.local/share/fonts/elevatia-font/"
#     "Elevatia-nA2AP.ttf"
# )
# SANS_FONT_FAMILY = "Elevatia"
# SANS_FONT_WEIGHT = 400
# SANS_FALLBACK    = "-apple-system, system-ui, Segoe UI, sans-serif"

# SANS_FONT_PATH   = "/home/tekakutli/.local/share/fonts/Chewy/Chewy-Regular.ttf"
# SANS_FONT_FAMILY = "Chewy"
SANS_FONT_PATH   = "/usr/share/fonts/WOFF2/FiraCode-Regular.woff2"
SANS_FONT_FAMILY = "Fira Code"
SANS_FONT_WEIGHT = 400
SANS_FALLBACK    = "-apple-system, system-ui, Segoe UI, sans-serif"

# ============================================================================
# Derived constants — nothing below needs editing
# ============================================================================

def _stack(name, path, fallback):
    """Prepend the loaded font name to the fallback stack only when
    the file exists and can be embedded."""
    if path and os.path.exists(path):
        return f"'{name}', {fallback}"
    return fallback


FONT_FAMILY_MONO = _stack(MONO_FONT_FAMILY, MONO_FONT_PATH, MONO_FALLBACK)
FONT_FAMILY_SANS = _stack(SANS_FONT_FAMILY, SANS_FONT_PATH, SANS_FALLBACK)


_MIME_BY_EXT = {
    ".ttf":   ("truetype", "font/ttf"),
    ".otf":   ("opentype", "font/otf"),
    ".woff":  ("woff",     "font/woff"),
    ".woff2": ("woff2",    "font/woff2"),
}


def _font_face(name, path, weight):
    """Return the @font-face block for `path` under `name`, or an
    empty string if the file is missing / unreadable / unsupported."""
    if not path:
        return ""
    if not os.path.exists(path):
        print(f"  note: font not found at {path} — using fallback stack")
        return ""
    fmt_mime = _MIME_BY_EXT.get(os.path.splitext(path)[1].lower())
    if fmt_mime is None:
        print(f"  note: unsupported font format for {path}")
        return ""
    fmt, mime = fmt_mime
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode("ascii")
    return (
        "@font-face {\n"
        f"  font-family: '{name}';\n"
        f"  src: url('data:{mime};base64,{data}') format('{fmt}');\n"
        f"  font-weight: {weight};\n"
        "  font-style: normal;\n"
        "  font-display: block;\n"
        "}\n"
    )


FONT_FACE_CSS = (
    _font_face(MONO_FONT_FAMILY, MONO_FONT_PATH, MONO_FONT_WEIGHT)
    + _font_face(SANS_FONT_FAMILY, SANS_FONT_PATH, SANS_FONT_WEIGHT)
)


# ============================================================================
# JS injection — one block prepended to every bundle
# ============================================================================
#
# json.dumps produces a valid JS string literal (JSON is a subset of
# JS), correctly escaping the quotes and newlines inside FONT_FACE_CSS.

FONT_JS = (
    "/* ==== playground_fonts.py — centralised font stacks ==== */\n"
    f"const FONT_MONO = {json.dumps(FONT_FAMILY_MONO)};\n"
    f"const FONT_SANS = {json.dumps(FONT_FAMILY_SANS)};\n"
    f"const FONT_FACE_CSS = {json.dumps(FONT_FACE_CSS)};\n"
)


def inject_head(html):
    """Splice the @font-face block into <head>.  Idempotent — a
    second call on an already-injected document is a no-op."""
    if not FONT_FACE_CSS:
        return html
    marker = '<style id="playgroundFonts">'
    if marker in html:
        return html
    style = marker + "\n" + FONT_FACE_CSS + "</style>\n"
    if "</head>" in html:
        return html.replace("</head>", style + "</head>", 1)
    return style + html
