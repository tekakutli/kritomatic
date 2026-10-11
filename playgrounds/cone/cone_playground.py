"""
cone_playground.py — look at the inside of a cone.

    python cone_playground.py     → writes cone_playground.html and serves it

The apex is a draggable world point; the base circle is fixed at the
origin.  Dragging the apex tilts the cone.  Scrolling changes depth,
which widens or narrows the base ring.

Patches are quadrilaterals bound to the cone's lateral surface as
rectangles in the (phi, s) parameter space.  The lower band shows
that parameter space as a flat sheet; a patch appears there as an
actual rectangle and on the cone as a curved trapezoid narrowing
toward the apex along its two meridian edges.

Floating squares live on each patch's plane, not on the cone, and
are editable from either band.
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
    print(f"  band split   : cone (upper) + parameter space (lower)")
    print(f"  interactions : drag apex · scroll depth · "
          f"drag patches in either band · shift+click a patch to "
          f"drop a square · drag squares from either band · "
          f"H collapses the panel")

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
