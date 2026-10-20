"""
boxes_playground.py — plan-view box placement playground.

    1. run room.py             → produces room_walls.json
    2. run boxes_playground.py → writes boxes_playground.html and serves it
    3. open the URL the script prints.

The heavy lifting lives in:

    boxes_geometry    room_walls.json → a geometry dict
    boxes_html        the HTML/JS bundle, rendered with the geometry
    pgkit.server      the HTTP + /modules/ server, plus /remote

The plan geometry is reconstructed from room_walls.json, NOT from
room.step.  Walls are the room's inner boundary surfaces; columns
are taken verbatim from the columns array; steps are taken verbatim
from the steps array that room.py emits alongside surfaces.

Data model (used by the browser-side JS)
----------------------------------------
Box: an axis-aligned rectangle rotated by `rot` degrees about its own
centre.  A box is "valid" when its footprint does not overlap any wall
or column polygon.  Two valid boxes may overlap each other; they are
then painted in the overlap accent, but stay valid and draggable.

Save format (room_boxes.json) — unchanged from the previous revision
--------------------------------------------------------------------
    { "version": 1,
      "boxes": [ {"name":"Kitchen","x":500,"y":1200,
                  "w":2400,"h":1800,"rot":0}, ... ] }

    A bare array [ {...}, {...} ] is also accepted.

Deleted
-------
`boxes_server.py` was a bespoke ThreadingHTTPServer with `/save` and
`/print` endpoints.  It is gone; `kit_serve` (from pgkit.server)
handles the HTTP layer, the module serving at `/modules/`, and the
`/remote` WebSocket for the remote panel.  The `/save` and `/print`
endpoints do not exist in the pgkit model — those are replaced by the
panel's own Save action (which downloads `room_boxes.json` as a
file) and List boxes action (which POSTs to a per-project route if
one is added later, or falls back to a browser console dump).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_HERE)
for _p in (_HERE, _PARENT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import boxes_geometry
import boxes_html

from pgkit.server import serve as kit_serve


HTML_FILE    = "boxes_playground.html"
BOXES_FILE   = "room_boxes.json"
PORT         = 8766
SERVE        = True
OPEN_BROWSER = True


def main():
    geom = boxes_geometry.extract_from_json()

    html = boxes_html.render(geom)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    b = geom["bounds"]
    print(f"Wrote {HTML_FILE}")
    print(f"  wall faces reconstructed : {len(geom['faces'])}")
    print(f"  step areas reconstructed : {len(geom['stepFaces'])}")
    print(f"  bounds (mm)              : "
          f"x [{b['minX']:.1f}, {b['maxX']:.1f}]  "
          f"y [{b['minY']:.1f}, {b['maxY']:.1f}]")
    print(f"  wall cut                 : z = {geom['cutZ']:.0f} mm")
    print(f"  box file                 : {BOXES_FILE}")

    if SERVE:
        print()
        kit_serve(
            port           = PORT,
            open_browser   = OPEN_BROWSER,
            html_file      = HTML_FILE,
            module_sources = boxes_html.module_sources(geom),
        )
    else:
        print(f"  open                     : "
              f"file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
