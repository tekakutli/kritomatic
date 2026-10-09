"""
board_playground.py — lay the final image of every open Krita document
on a single canvas.

    python board_playground.py     → writes board_playground.html and serves it

The board is an infinite pan/zoom surface.  Each Krita document the
daemon knows about gets a rectangle on it, showing the document's
flattened thumbnail at its true aspect ratio.  Dragging a rectangle
moves it on the board; clicking it activates the matching document in
Krita; pressing R pulls fresh thumbnails from the daemon.

All Krita interaction goes through the local HTTP server, which
forwards to the running Kritomatic daemon over the socket Krita's
plugin exposes.
"""

import os
import sys

# Make this script's own directory importable as a plain top-level
# directory, so `import board_html` and `import board_server` resolve
# to the sibling files regardless of how Python was invoked or what
# the surrounding environment sets.
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
    print(f"  interactions : drag a card to move it · click to select ·")
    print(f"                 double-click to activate in Krita ·")
    print(f"                 scroll to zoom · drag empty space to pan ·")
    print(f"                 R refresh · G grid · F fit · H toggle panel")

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
