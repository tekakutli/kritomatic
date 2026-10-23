"""
floor_plan_geometry.py — read the two room sidecars into the geom dict
the floor-plan drawer consumes.

room.py writes two files, both at playgrounds/shared/:

    room_walls.json        the built geometry — surfaces, planFaces,
                           columns, steps, openings
    room_dimensions.json   the declared annotations — dimension
                           entries and notes-block lines

DEFAULT_WALLS_FILE and DEFAULT_DIMENSIONS_FILE below resolve to those
paths via `_SHARED` computed from this module's own location, so the
reader works from any working directory.

The drawer reads both.  It does not read room.step and does not
import build123d.
"""

import json
import os

from floor_plan_i18n import T


_HERE   = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.normpath(os.path.join(_HERE, "..", "shared"))
DEFAULT_WALLS_FILE      = os.path.join(_SHARED, "room_walls.json")
DEFAULT_DIMENSIONS_FILE = os.path.join(_SHARED, "room_dimensions.json")


def _signed_area(ring):
    n = len(ring)
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def _all_holes(plan_faces):
    """Every hole ring, from every face.  The drawer probes these to
    decide inwardness for a dimension — a probe point that lands in a
    hole is inside a room interior; a probe that lands in wall
    material is not."""
    out = []
    for face in plan_faces:
        for hole in face.get("holes", []):
            if len(hole) >= 3:
                out.append(hole)
    return out


def extract_from_json(walls_path=None, dimensions_path=None):
    if walls_path is None:
        walls_path = DEFAULT_WALLS_FILE
    if dimensions_path is None:
        dimensions_path = DEFAULT_DIMENSIONS_FILE

    if not os.path.exists(walls_path):
        raise SystemExit(T("errWallsMissing")(walls_path))

    with open(walls_path, "r", encoding="utf-8") as f:
        wdata = json.load(f)

    dims  = []
    notes = []
    unit_mm = float(wdata.get("unit_mm", 10.0))
    if os.path.exists(dimensions_path):
        with open(dimensions_path, "r", encoding="utf-8") as f:
            ddata = json.load(f)
        dims    = ddata.get("dimensions", [])
        notes   = ddata.get("notes", [])
        unit_mm = float(ddata.get("unit_mm", unit_mm))

    plan_faces = wdata.get("planFaces", [])

    return {
        "planFaces":         plan_faces,
        "columns":           wdata.get("columns", []),
        "steps":             wdata.get("steps", []),
        "openings":          wdata.get("openings", []),
        "dimensions":        dims,
        "notes":             notes,
        "holes":             _all_holes(plan_faces),
        "unit_mm":           unit_mm,
        "wall_height_mm":    float(wdata.get("wall_height_mm", 0.0)),
        "wall_thickness_mm": float(wdata.get("wall_thickness_mm", 0.0)),
    }
