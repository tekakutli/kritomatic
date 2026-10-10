"""
text_board_html.py — assembles the HTML/JS bundle.

Concatenation order mirrors board_html.py:

    CORE_JS       canvas, view, item model, format signature
    VIEW_JS       drawBoardView — the text cards and word boxes
    KRITA_JS      fetch glue for the /krita endpoints
    PANEL_JS      editor, item list, button bindings
    SCENE_JS      board save / load
    DISPATCH_JS   draw() + canvas event listeners
    BOOT_JS       first resize() and first pull from Krita
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from tb_base     import HTML_HEAD, HTML_TAIL, BOOT_JS
from tb_core     import CORE_JS
from tb_view     import VIEW_JS
from tb_krita    import KRITA_JS
from tb_panel    import PANEL_JS
from tb_scene    import SCENE_JS
from tb_dispatch import DISPATCH_JS


def render():
    return (
        HTML_HEAD
        + "<script>\n"
        + '"use strict";\n'
        + CORE_JS
        + VIEW_JS
        + KRITA_JS
        + PANEL_JS
        + SCENE_JS
        + DISPATCH_JS
        + BOOT_JS
        + HTML_TAIL
    )
