"""
text_wall_playground.py — pairs of parallel lines with 1D segment layouts.

    python text_wall_playground.py

The module_sources dict built by `text_wall_html.module_sources()` is
passed to both `page()` (for syntax checking) and `kit_serve()`
(for serving at /modules/).
"""

import os
import sys

_HERE   = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import text_wall_html
import text_wall_kra

from pgkit.server import serve as kit_serve


HTML_FILE    = "text_wall_playground.html"
PORT         = 8767
SERVE        = True
OPEN_BROWSER = True


def main():
    html = text_wall_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print("  band split   : canvas (upper) + segment view (lower)")
    print("  interactions : drag pairs in the canvas · drag segment "
          "edges or bodies in the segment view · H collapses the panel")
    print(f"  remote api   : ws://127.0.0.1:{PORT}/remote")

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            routes         = {"/generate-kra": text_wall_kra.generate},
            module_sources = text_wall_html.module_sources(),
            hub_name       = "text_wall",
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
