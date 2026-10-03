"""
boxes_geometry.py — reconstruct plan geometry from room_walls.json.

Reads the sidecar that room.py writes and returns the `geom` dict the
rest of the boxes playground reads:

    {
        "faces":     [ {"outer": [[x,y], ...], "holes": [...]}, ... ],
        "stepFaces": [ {"outer": [[x,y], ...], "holes": []},   ... ],
        "bounds":    {"minX":..., "minY":..., "maxX":..., "maxY":...},
        "cutZ":      <mm>,
    }

Primary path — `planFaces`
--------------------------
room.py emits a `planFaces` field: the actual 2D section of the room
solid at PLAN_CUT_Z, taken by build123d's own section operator while
the solid is still in hand.  That is the authoritative plan-view
footprint.  It carries:

    • walls and columns as a single connected region, with the corner
      joins resolved (a 90° turn fills correctly, not as two rectangles
      meeting at a point),
    • openings (doors, windows) as holes in the wall region,
    • small-room walls merged with the main-room walls where they meet.

The cable playground's cable_geometry.py takes exactly this approach,
except it computes the section on the fly from room.step.  By moving
the section into room.py's output, the boxes playground gets the same
geometry without depending on the STEP file at runtime.

Fallback path — surfaces + columns + steps
------------------------------------------
A room_walls.json produced by an earlier room.py (before `planFaces`
existed) carries only the surfaces array, the columns array, and the
steps array.  In that case the geometry is reconstructed by extruding
each wall surface outward by wall_thickness_mm.  This approximates
the wall region but does NOT join wall strips at their corners; a
v4-before-planFaces sidecar will therefore look slightly worse in the
corners than a v4-with-planFaces sidecar.  That is why `planFaces`
exists.
"""

import json
import os


WALLS_FILE = "room_walls.json"
DEFAULT_CUT_Z = 1200.0


# ---------------------------------------------------------------------------
# Primary path — read the pre-computed plan section
# ---------------------------------------------------------------------------

def _faces_from_plan_faces(raw):
    """Turn the raw planFaces array into the geom['faces'] shape.

    Returns an empty list if the input is unusable, which the caller
    treats as "fall back to reconstruction"."""
    faces = []
    if not isinstance(raw, list):
        return faces
    for f in raw:
        if not isinstance(f, dict):
            continue
        outer = f.get("outer")
        if not (isinstance(outer, list) and len(outer) >= 3):
            continue
        holes = []
        for h in (f.get("holes") or []):
            if isinstance(h, list) and len(h) >= 3:
                try:
                    holes.append([[float(p[0]), float(p[1])] for p in h])
                except (TypeError, ValueError, IndexError):
                    continue
        try:
            outer_clean = [[float(p[0]), float(p[1])] for p in outer]
        except (TypeError, ValueError, IndexError):
            continue
        faces.append({"outer": outer_clean, "holes": holes})
    return faces


def _step_faces_from_steps(steps_data):
    """Turn the raw steps array into the geom['stepFaces'] shape."""
    out = []
    if not isinstance(steps_data, list):
        return out
    for st in steps_data:
        if not isinstance(st, dict):
            continue
        outline = st.get("outline")
        if not (isinstance(outline, list) and len(outline) >= 3):
            continue
        try:
            ring = [[float(p[0]), float(p[1])] for p in outline]
        except (TypeError, ValueError, IndexError):
            continue
        out.append({"outer": ring, "holes": []})
    return out


# ---------------------------------------------------------------------------
# Fallback path — reconstruct walls from the surfaces array
# ---------------------------------------------------------------------------

def _room_centre(wall_surfaces):
    """Weighted centroid of the wall-surface midpoints — a robust
    approximation of the room's interior centre, used to determine
    which side of a wall segment is 'outward'."""
    sx = sy = sw = 0.0
    for s in wall_surfaces:
        p1, p2 = s["p1"], s["p2"]
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        w = (dx * dx + dy * dy) ** 0.5
        if w < 1e-6:
            continue
        mx = (p1[0] + p2[0]) * 0.5
        my = (p1[1] + p2[1]) * 0.5
        sx += mx * w
        sy += my * w
        sw += w
    if sw < 1e-6:
        return (0.0, 0.0)
    return (sx / sw, sy / sw)


def _extrude_wall(p1, p2, thick, cx, cy):
    """Turn an inner-face segment into its plan-view wall strip."""
    dx = p2[0] - p1[0]
    dy = p2[1] - p1[1]
    L = (dx * dx + dy * dy) ** 0.5
    if L < 1e-6:
        return None
    nx, ny = -dy / L, dx / L
    mx = (p1[0] + p2[0]) * 0.5
    my = (p1[1] + p2[1]) * 0.5
    if (cx - mx) * nx + (cy - my) * ny > 0:
        nx, ny = -nx, -ny
    return [
        [p1[0],                p1[1]],
        [p2[0],                p2[1]],
        [p2[0] + nx * thick,   p2[1] + ny * thick],
        [p1[0] + nx * thick,   p1[1] + ny * thick],
    ]


def _stub_step_from_risers(surfaces):
    """Fallback: approximate a step footprint by hulling the risers'
    endpoints.  Only used when room_walls.json predates `steps`.
    Produces a coarse convex hull per parent tag, which is enough for
    the light-blue visual even if it is not exact."""
    by_parent = {}
    for s in surfaces:
        if s.get("kind") != "step":
            continue
        tag = s.get("parent") or s.get("tag") or "step"
        by_parent.setdefault(tag, []).append(s)

    out = []
    for tag, risers in by_parent.items():
        pts = []
        for s in risers:
            pts.append((s["p1"][0], s["p1"][1]))
            pts.append((s["p2"][0], s["p2"][1]))
        if len(pts) < 3:
            continue
        pts = sorted(set(pts))

        def cross(o, a, b):
            return ((a[0] - o[0]) * (b[1] - o[1])
                    - (a[1] - o[1]) * (b[0] - o[0]))

        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        hull = lower[:-1] + upper[:-1]
        if len(hull) >= 3:
            out.append({"outer": [list(p) for p in hull], "holes": []})
    return out


# ---------------------------------------------------------------------------
# Bounds + assembly
# ---------------------------------------------------------------------------

def _finalize(faces, step_faces, data, fallback_cut_z):
    cut_z_used = float(data.get("plan_cut_z_mm", fallback_cut_z))

    if not faces:
        raise SystemExit(
            f"{WALLS_FILE} produced no wall faces at z={cut_z_used:.0f} mm.")

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for face in faces + step_faces:
        for ring in [face["outer"], *face["holes"]]:
            for x, y in ring:
                min_x = min(min_x, x); min_y = min(min_y, y)
                max_x = max(max_x, x); max_y = max(max_y, y)

    return {
        "faces":     faces,
        "stepFaces": step_faces,
        "bounds":    {"minX": min_x, "minY": min_y,
                      "maxX": max_x, "maxY": max_y},
        "cutZ":      cut_z_used,
    }


def extract_from_json(json_path=WALLS_FILE, cut_z=DEFAULT_CUT_Z):
    if not os.path.exists(json_path):
        raise SystemExit(f"{json_path} not found — run room.py first.")

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    steps_data = data.get("steps", [])
    step_faces = _step_faces_from_steps(steps_data)

    # ---- Primary path: planFaces ----------------------------------------
    plan_faces = _faces_from_plan_faces(data.get("planFaces"))
    if plan_faces:
        return _finalize(plan_faces, step_faces, data, cut_z)

    # ---- Fallback: reconstruct from surfaces ----------------------------
    print(f"  note: {json_path} has no planFaces — falling back to wall "
          f"reconstruction from surfaces (corners will not join cleanly; "
          f"re-run room.py to add planFaces)")

    surfaces = data.get("surfaces", [])
    columns  = data.get("columns", [])
    wall_thick = float(data.get("wall_thickness_mm", 150.0))
    cut_z_used = float(data.get("plan_cut_z_mm", cut_z))

    wall_surfaces = [s for s in surfaces if s.get("kind") == "wall"]
    cx, cy = _room_centre(wall_surfaces)

    faces = []
    for s in wall_surfaces:
        zr = s.get("z_range_mm", [0.0, 0.0])
        if not (zr[0] <= cut_z_used <= zr[1]):
            continue
        poly = _extrude_wall(s["p1"], s["p2"], wall_thick, cx, cy)
        if poly is not None:
            faces.append({"outer": poly, "holes": []})

    for col in columns:
        box = col.get("box")
        if not (isinstance(box, list) and len(box) == 4):
            continue
        x0, y0, x1, y1 = box
        faces.append({
            "outer": [[x0, y0], [x1, y0], [x1, y1], [x0, y1]],
            "holes": [],
        })

    if not step_faces:
        # Old sidecar — approximate from risers.
        step_faces = _stub_step_from_risers(surfaces)

    return _finalize(faces, step_faces, data, cut_z)
