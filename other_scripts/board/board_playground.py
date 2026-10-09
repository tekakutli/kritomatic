"""
board_playground.py — lay the final image of every file the board
knows about on a single canvas.

    python board_playground.py     → writes board_playground.html and serves it

The board persists a list of file paths — the .kra files the user
wants on the board — and lays one card per file.  Loading a board
opens the missing files in Krita; refreshing asks Krita which of the
board's files are currently open, and updates the cards accordingly.
Files closed in Krita stay on the board, marked closed, ready to be
re-opened from the panel.

No polling anywhere.  Every state transition is a manual action:
open the browser, press R, click a card, click the panel button.
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import board_html
import board_server


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

    if SERVE:
        print()
        board_server.serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
