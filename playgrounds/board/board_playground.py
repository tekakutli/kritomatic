"""
board_playground.py — lay the final image of every file the board
knows about on a single canvas.

    python board_playground.py     → writes board_playground.html and serves it

The module_sources dict built by `board_html.module_sources()` is
passed to both `page()` and `kit_serve()`.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import board_html
import board_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "board_playground.html"
PORT         = 8770
SERVE        = True
OPEN_BROWSER = True


def main():
    html = board_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  interactions : drag a card to move it · drag a corner to resize ·")
    print(f"                 hold Shift while resizing to keep the aspect ratio ·")
    print(f"                 click to select · double-click to open/activate ·")
    print(f"                 scroll to zoom · drag empty space to pan ·")
    print(f"                 R refresh · G grid · F fit · O open all ·")
    print(f"                 P project overlaps · H toggle panel")
    print(f"  routes       : {', '.join(sorted(list(board_server.ROUTES) + list(board_server.RAW_ROUTES)))}")

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            routes         = board_server.ROUTES,
            raw_routes     = board_server.RAW_ROUTES,
            module_sources = board_html.module_sources(),
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
