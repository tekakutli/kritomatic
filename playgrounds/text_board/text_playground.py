"""
text_playground.py — lay every text shape Krita knows about on a
single canvas.

    python text_playground.py     → writes text_board.html and serves it

STARTUP DAEMON PROBE
====================
Before the HTTP server starts, `_probe_daemon()` opens a TCP connection
to the Kritomatic daemon (localhost:12346).  If it fails, the terminal
shows a clear diagnosis before the browser opens — otherwise the only
symptom is "Refresh from Krita" failing in the browser, with the cause
buried three layers down.

This does NOT abort startup.  The server still starts, so the page
loads and the browser console can be inspected.  The probe is
diagnostic, not a hard gate.
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
    """Probe the daemon's command socket.  Report, do not abort."""
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
        print(f"    - Is the Kritomatic Daemon plugin enabled "
              f"(Settings \u2192 Configure Krita \u2192 Plugin Manager)?")
        print(f"    - Check with:  ss -tlnp | grep 12346")
        return False


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
    print(f"  routes       : {', '.join(text_server.ROUTES.keys())}")

    _probe_daemon()

    if SERVE:
        print(f"  remote api   : ws://127.0.0.1:{PORT}/remote")
        print()
        kit_serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
            routes       = text_server.ROUTES,
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
