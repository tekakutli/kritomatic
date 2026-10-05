"""
cone_playground.py — look at the inside of a cone.

    python cone_playground.py     → writes cone_playground.html and serves it

The apex is a point you can drag across the upper band; scroll changes
its depth, which widens or narrows the visible cone. The lower band is
left empty on purpose — it is the slot that once held the unfolded
wall, reserved for whatever surface we map onto the cone later.
"""

import os
import cone_html
import cone_server


HTML_FILE    = "cone_playground.html"
PORT         = 8766
SERVE        = True
OPEN_BROWSER = True


def main():
    html = cone_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print(f"  band split  : cone (upper) + reserved (lower)")
    print(f"  interactions: drag apex · scroll depth · H collapses panel")

    if SERVE:
        print()
        cone_server.serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
