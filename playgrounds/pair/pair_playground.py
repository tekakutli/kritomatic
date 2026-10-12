"""
pair_playground.py — pairs of parallel lines with 1D segment layouts.

    python pair_playground.py

The canvas shows a set of PAIRS.  A pair is two parallel lines that
run from one edge of the viewport to the other, together with a 1D
layout of SEGMENTS along the pair's abstract axis.  Each pair has
its own direction (angle / inclination), gap (perpendicular distance
between the two lines), offset (shift of line B along the direction
of line A), and a per-line metric (metricA on line A, metricB on
line B).

A segment is an abstract interval [a, b].  On the canvas it becomes
a trapezoid whose top edge is that interval drawn on line A (scaled
by metricA) and whose bottom edge is the same interval drawn on line
B (scaled by metricB, shifted by offset).  When the two metrics are
equal and the offset is zero, the trapezoid degenerates into a
parallelogram; when they differ it narrows or widens across the gap.

The abstract segment axis is periodic with period SEG_X_SPAN.  A
segment [a, b] and every [a + k·SEG_X_SPAN, b + k·SEG_X_SPAN] are
the same physical segment; both views draw every wrapped copy that
intersects their own visible region, so a segment dragged past one
end reappears on the other — on the segment view and, correspondingly,
at the opposite end of the pair of lines.

The bottom band is the SEGMENT VIEW.  It shows one row per pair.
Each row is a 1D axis; a segment is a filled bar along that axis.
Drag a segment's edges to widen or shrink it; drag its body to
translate every segment in the pair together; click Draw Segment to
place a new segment anywhere in a row.

The panel also carries a KRA export section:  each segment (filled
or empty) becomes a text label, and each filled segment also emits
a filled trapezoid.  The label's affine is derived from the
trapezoid's own local Jacobian, so it warps as if it were painted
on the trapezoid's plane — matching cone_playground's text-shear
export.

IMPORT PATH
===========
This script imports sibling modules (pair_html, pair_server, and the
pg_* family through pair_html).  To make those imports resolve no
matter how the script is invoked — plain `python pair_playground.py`,
`python /abs/path/pair_playground.py`, or with PYTHONSAFEPATH=1 —
the script's own directory is inserted into sys.path before the
first sibling import.  See the top of the module.
"""

import os
import sys

# Put this script's directory on sys.path before any sibling import.
# Idempotent: if the directory is already present (the common case),
# nothing is inserted twice.  This lets the script be run from any
# working directory, and survives PYTHONSAFEPATH=1 / python -P.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import pair_html
import pair_server


HTML_FILE    = "pair_playground.html"
PORT         = 8767
SERVE        = True
OPEN_BROWSER = True


def main():
    html = pair_html.render()
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"Wrote {HTML_FILE}")
    print("  band split   : canvas (upper) + segment view (lower)")
    print("  interactions : drag pairs in the canvas · drag segment "
          "edges or bodies in the segment view · H collapses the panel")

    if SERVE:
        print()
        pair_server.serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
        )
    else:
        print(f"  open : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
