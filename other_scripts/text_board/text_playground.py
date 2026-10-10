"""
text_playground.py — lay every text shape Krita knows about on a
single canvas.

    python text_playground.py     → writes text_board.html and serves it

The board pulls every text shape from every open Krita document.
Each shape becomes a card.  Word-level bounding boxes are drawn inside
each card and toggled with W.  The panel edits the selected shape in
place via the daemon.  The + Add files… button opens a native file
picker; picked .kra files are added to the board and opened in Krita.
Nothing is polled.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import text_board_html
import text_server


HTML_FILE    = "text_board.html"
PORT         = 8771
SERVE        = True
OPEN_BROWSER = True


def main():
    html = text_board_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  interactions : drag a card to move it · click to select ·")
    print(f"                 double-click to activate its document ·")
    print(f"                 scroll to zoom · drag empty space to pan ·")
    print(f"                 R refresh · G grid · F fit · W word boxes ·")
    print(f"                 O open all picked files · H toggle panel")

    if SERVE:
        print()
        text_server.serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
