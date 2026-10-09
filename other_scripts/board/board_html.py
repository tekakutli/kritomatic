"""
board_html.py — assembles the HTML/JS bundle.

Concatenation order:

    CORE_JS             canvas, view, board state, document model
    VIEW_BOARD_JS       drawBoardView — the cards and the grid
    KRITA_JS            fetch glue for the /krita endpoints
    PANEL_JS            slider + list + button bindings
    SCENE_JS            board save / load
    DISPATCH_JS         draw() orchestrator + canvas event listeners
    BOOT_JS             first resize() and first pull from Krita
"""

import os
import sys

# Make this file's own directory importable as a plain top-level
# directory, so `from pg_base import ...` and the other sibling
# imports resolve regardless of how Python was invoked or what the
# surrounding environment sets.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from pg_base       import HTML_HEAD, HTML_TAIL, BOOT_JS
from pg_core       import CORE_JS
from pg_view_board import VIEW_BOARD_JS
from pg_krita      import KRITA_JS
from pg_panel      import PANEL_JS
from pg_scene      import SCENE_JS
from pg_dispatch   import DISPATCH_JS


def render():
    return (
        HTML_HEAD
        + "<script>\n"
        + '"use strict";\n'
        + CORE_JS
        + VIEW_BOARD_JS
        + KRITA_JS
        + PANEL_JS
        + SCENE_JS
        + DISPATCH_JS
        + BOOT_JS
        + HTML_TAIL
    )
