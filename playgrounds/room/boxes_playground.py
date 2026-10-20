"""
boxes_playground.py — plan-view box placement playground.

    1. run room.py             → produces room_walls.json
    2. run boxes_playground.py → writes boxes_playground.html and serves it
    3. open the URL the script prints.

The heavy lifting lives in:

    boxes_geometry    room_walls.json → a geometry dict
    boxes_html        the HTML/JS bundle, rendered with the geometry
    boxes_server      a tiny threaded HTTP server with /save and /print
    boxes_live        aggregator for the JS modules
    boxes_panel       the dark panel's HTML/CSS/JS

The plan geometry is reconstructed from room_walls.json, NOT from
room.step.  Walls are the room's inner boundary surfaces extruded
outward by wall_thickness_mm; columns are taken verbatim from the
columns array; steps are taken verbatim from the new steps array that
room.py emits alongside surfaces.

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
"""

import os

import boxes_geometry
import boxes_html
import boxes_server


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
        boxes_server.serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
            boxes_file   = BOXES_FILE,
        )
    else:
        print(f"  open                     : "
              f"file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
