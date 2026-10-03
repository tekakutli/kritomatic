"""
cable_playground.py — plan cable / pipe routes in a room.

    1. run room.py                → produces room.step (+ room_walls.json)
    2. run cable_playground.py    → writes cable_playground.html and serves it.
    3. open the URL that this script prints.

This module is the entry point.  The heavy lifting lives in:

    cable_geometry     — STEP + room_walls.json → a geometry dict
    cable_html         — the HTML/JS bundle, rendered with the geometry
    cable_server       — a tiny threaded HTTP server with /save
    quiet              — stderr suppression around build123d calls

Data model (used by the browser-side JS)
----------------------------------------
Anchor: a point in the room.  Two spaces:
    floor     : { x, y }  (+ optional gridId/gridAxis)
    wall-edge : { segIdx, t, v }

Cable: an ordered list of anchor ids.  Sharing an anchor between cables is
what "connecting" means; drag a vertex, every cable referencing that anchor
re-renders from the same source.

Junction graph (derived, computed once at load in the browser):
    Nodes      — physical room corners.
    Terminals  — (segIdx, side) pairs, side ∈ {0, 1}.
    Co-located when their plan XY is within 3 mm.

Every "does X share a physical point with Y" question routes through this
graph: corner-drag height sync, true-cable grouping pass 2, focus-mode
neighbours, route planner chain resolution.

Anchor reuse policy
-------------------
A plain click during drawing always creates a fresh vertex, even if it
lands on top of an existing anchor.  Exceptions:
    - the anchor is already part of the drawing in progress, or
    - the anchor's owning cable is in the OTHER view (cross-view
      connection: floor cable snapping onto a wall footprint).
Everything else requires Alt: Alt+click near an existing endpoint reuses
its anchor, and Alt+click on another cable's endpoint adopts/merges into
it explicitly.

Save format v3
--------------
wall-edge anchors persist as { t, v, segRef: {a,b}, world: [x,y] }, so they
re-bind to whichever new segment occupies the same footprint at load,
independently of how room_walls.json re-orders segments on the next build.
Legacy 'wall' anchors from v1/v2/v3 saves are upgraded to wall-edge on load.
"""

import os

import cable_geometry
import cable_html
import cable_server


HTML_FILE    = "cable_playground.html"
STATE_FILE   = "room_layout.json"
PORT         = 8765
SERVE        = True
OPEN_BROWSER = True


def main():
    geom = cable_geometry.extract_geometry()

    html = cable_html.render(geom)
    with open(HTML_FILE, "w", encoding="utf-8") as f:
        f.write(html)

    b  = geom["bounds"]
    uw = geom["unfoldedWall"]
    n_step = sum(1 for s in uw["segments"] if s.get("kind") == "step")
    n_col  = sum(1 for s in uw["segments"] if s.get("kind") == "column")

    print(f"Wrote {HTML_FILE}")
    print(f"  wall faces extracted  : {len(geom['faces'])}")
    print(f"  step areas extracted  : {len(geom['stepFaces'])}")
    print(f"  bounds (mm)           : "
          f"x [{b['minX']:.1f}, {b['maxX']:.1f}]  "
          f"y [{b['minY']:.1f}, {b['maxY']:.1f}]")
    print(f"  wall cut              : z = {geom['cutZ']:.0f} mm")
    print(f"  unfolded wall         : {len(uw['segments'])} segment(s) "
          f"({n_col} column, {n_step} step), "
          f"perimeter {uw['totalU']:.0f} mm, height {uw['height']:.0f} mm, "
          f"{'CCW' if uw['ccw'] else 'CW'}")
    print(f"  columns               : {len(geom.get('columns', []))}")
    print(f"  openings              : {len(geom.get('openings', []))}")
    print(f"  state file            : {STATE_FILE}")

    if SERVE:
        print()
        cable_server.serve(
            port         = PORT,
            open_browser = OPEN_BROWSER,
            html_file    = HTML_FILE,
            state_file   = STATE_FILE,
        )
    else:
        print(f"  open                  : file://{os.path.abspath(HTML_FILE)}")


if __name__ == "__main__":
    main()
