"""
text_playground.py — lay every text shape Krita knows about on a
single canvas.

    python text_playground.py     → writes text_board.html and serves it

The module_sources dict built by `text_board_html.module_sources()`
is passed to both `page()` (for syntax checks) and `kit_serve()` (for
serving at /modules/).
"""

import os
import socket
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import text_board_html
import text_server

from pgkit.server import serve as kit_serve


HTML_FILE    = "text_board.html"
PORT         = 8771
SERVE        = True
OPEN_BROWSER = True


def _probe_daemon():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        s.connect(("127.0.0.1", 12346))
        s.close()
        print("  \u2713 Kritomatic daemon reachable on 127.0.0.1:12346")
        return True
    except OSError as e:
        print(f"  \u2717 Kritomatic daemon NOT reachable: {e}")
        print(f"    - Is Krita running?")
        print(f"    - Is the Kritomatic Daemon plugin enabled?")
        return False


def main():
    html = text_board_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  routes       : {', '.join(text_server.ROUTES.keys())}")

    _probe_daemon()

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            routes         = text_server.ROUTES,
            module_sources = text_board_html.module_sources(),
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
