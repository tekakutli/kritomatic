"""
cable_geometry.py — build the cable planner's geometry dict from
room_walls.json.

Reads the sidecar that room.py writes and returns the `geom` dict:

    {
        "faces":        [...],   # wall footprint polygons at cut height
        "stepFaces":    [...],   # walkable step surface (steps minus walls)
        "bounds":       {"minX":..., "minY":..., "maxX":..., "maxY":...},
        "cutZ":         <mm>,
        "columns":      [...],   # column boxes from room_walls.json
        "openings":     [...],   # opening metadata
        "unfoldedWall": {
            "segments":   [ {u0,u1,a,b,len,tag,z_lo,z_hi,kind,parent}, ... ],
            "footprints": [ {u0,u1,a,b,len}, ... ],
            "totalU":     <mm>,
            "height":     <mm>,
            "ccw":        true,
        },
    }

The sidecar lives at playgrounds/shared/room_walls.json, next to the
shared room.py that generates it.  WALLS_FILE below resolves to that
path via `_SHARED` computed from this module's own location, so the
reader works from any working directory.

PRIMARY PATH — planFaces
========================
room.py emits a `planFaces` field: the actual 2D section of the room
solid at PLAN_CUT_Z, taken by build123d's own `section` operator while
the solid is still in hand.  That IS the wall-plus-column footprint at
cut height; no further extraction is needed.

STEP SURFACES
=============
room_walls.json's `steps[]` array carries each step's plain outline.
The walkable surface is that outline minus any wall or column
footprint standing on it.  The STEP-based extraction this reader
replaces computed exactly that as

    section(solid, z=100)  −  section(solid, z=1200)

The subtraction is done here directly on the JSON polygons with
build123d 2D boolean ops: `make_face(Polyline(...))`, `+` to union,
`-` to subtract.  The result removes, in particular, the C3 column
that sits inside the SR_STRIP step footprint, so `pointOnStep`,
step-edge snapping, and the `stOnStep` status flag do not fire inside
a column base.

WHAT THIS GAINS
===============
No dependency on `room.step`.  room_walls.json is the single source of
truth — the same input boxes_geometry and floor_plan_geometry already
consume.

WHAT THIS REQUIRES
==================
room_walls.json must be v4 or later — i.e. produced by a room.py
whose `_dump_wall_json` emits `planFaces`.  If `planFaces` is absent,
this reader raises rather than silently degrade; the right fix is to
re-run room.py, not to reconstruct a worse approximation.

The `unfoldedWall` strip is built from `surfaces` via the spatial
ordering helpers below.
"""

import json
import os
from math import hypot

from quiet import quiet


with quiet():
    from build123d import make_face, Polyline


_HERE   = os.path.dirname(os.path.abspath(__file__))
_SHARED = os.path.normpath(os.path.join(_HERE, "..", "shared"))
WALLS_FILE  = os.path.join(_SHARED, "room_walls.json")
WALL_CUT_Z  = 1200.0
WALL_HEIGHT = 3000.0


# ---------------------------------------------------------------------------
# Polygon helpers
# ---------------------------------------------------------------------------

def _poly_to_face(points):
    """Build a build123d Face from a list of [[x, y], ...] points.

    Returns None for fewer than three points, or if `make_face` rejects
    the winding.  A step outline or a plan face that can't be built is
    skipped — the caller counts what survives."""
    if not points or len(points) < 3:
        return None
    try:
        pts = [(float(p[0]), float(p[1])) for p in points]
        return make_face(Polyline(*pts, close=True))
    except Exception as e:
        print(f"  (could not build face from {len(points)} points: {e})")
        return None


def wire_to_polygon(wire, tol=0.01):
    edges = list(wire.edges())
    if not edges:
        return []
    e0 = edges.pop(0)
    pts = [e0.position_at(0), e0.position_at(1)]
    end = pts[-1]
    while edges:
        for i, e in enumerate(edges):
            s = e.position_at(0)
            t = e.position_at(1)
            if (s - end).length < tol:
                pts.append(t); end = t; edges.pop(i); break
            if (t - end).length < tol:
                pts.append(s); end = s; edges.pop(i); break
        else:
            break
    if len(pts) > 1 and (pts[0] - pts[-1]).length < tol:
        pts.pop()
    return [[float(p.X), float(p.Y)] for p in pts]


def faces_to_polys(faces):
    out = []
    for f in faces:
        outer = wire_to_polygon(f.outer_wire())
        if not outer:
            continue
        holes = []
        for w in f.inner_wires():
            h = wire_to_polygon(w)
            if h:
                holes.append(h)
        out.append({"outer": outer, "holes": holes})
    return out


def signed_area(ring):
    n = len(ring)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def polygon_area(ring):
    return abs(signed_area(ring))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def extract_geometry():
    if not os.path.exists(WALLS_FILE):
        raise SystemExit(f"{WALLS_FILE} not found — run room.py first.")

    with open(WALLS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    plan_faces = data.get("planFaces") or []
    if not plan_faces:
        raise SystemExit(
            f"{WALLS_FILE} has no 'planFaces' — re-run room.py "
            f"(v4 or later emits the plan section)."
        )

    # ---- wall faces ----
    wall_polys = []
    for pf in plan_faces:
        outer = pf.get("outer") or []
        if len(outer) < 3:
            continue
        try:
            outer_clean = [[float(p[0]), float(p[1])] for p in outer]
        except (TypeError, ValueError, IndexError):
            continue
        holes = []
        for h in (pf.get("holes") or []):
            if not (isinstance(h, list) and len(h) >= 3):
                continue
            try:
                holes.append([[float(p[0]), float(p[1])] for p in h])
            except (TypeError, ValueError, IndexError):
                continue
        wall_polys.append({"outer": outer_clean, "holes": holes})

    if not wall_polys:
        raise SystemExit(f"{WALLS_FILE} planFaces produced no wall faces.")

    # ---- step faces: step_outline − plan_faces ----
    step_polys = _compute_step_faces(data, plan_faces)

    # ---- bounds ----
    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for face in wall_polys + step_polys:
        for ring in [face["outer"], *face["holes"]]:
            for x, y in ring:
                if x < min_x: min_x = x
                if y < min_y: min_y = y
                if x > max_x: max_x = x
                if y > max_y: max_y = y

    geom = {
        "faces":     wall_polys,
        "stepFaces": step_polys,
        "bounds":    {"minX": min_x, "minY": min_y,
                      "maxX": max_x, "maxY": max_y},
        "cutZ":      float(data.get("plan_cut_z_mm", WALL_CUT_Z)),
        "columns":   data.get("columns", []),
        "openings":  data.get("openings", []),
    }

    uw = _unfold_from_walls_json(data)
    geom["unfoldedWall"] = uw

    n_step = sum(1 for s in uw["segments"] if s.get("kind") == "step")
    n_col  = sum(1 for s in uw["segments"] if s.get("kind") == "column")
    print(f"  unfolded-wall source  : {WALLS_FILE} v"
          f"{data.get('version', 1)} "
          f"({len(uw['segments'])} segment(s) — "
          f"{n_col} column, {n_step} step; "
          f"perimeter {uw['totalU']:.0f} mm)")
    return geom


def _compute_step_faces(data, plan_faces):
    """Walkable step surfaces.

    Each step in `data["steps"]` is built into a 2D face from its
    `outline`.  The faces are unioned, and the union of every
    `plan_faces` polygon (with holes) is subtracted — reproducing the
    old `section(solid, z=100) - section(solid, z=1200)`.

    A step whose outline doesn't overlap any wall footprint passes
    through unchanged (the union carries it and the subtraction is a
    no-op over its area), so the common case costs one `make_face`
    and no boolean ops.

    The boolean may produce multiple disjoint faces (e.g. a step with
    a column in its middle becomes an outer face with a hole), which
    `faces_to_polys` walks as `.faces()` of the result.
    """
    step_outlines = []
    for step in data.get("steps", []):
        outline = step.get("outline") or []
        if len(outline) < 3:
            continue
        try:
            ring = [[float(p[0]), float(p[1])] for p in outline]
        except (TypeError, ValueError, IndexError):
            continue
        step_outlines.append(ring)

    if not step_outlines:
        return []

    with quiet():
        step_faces = []
        for ring in step_outlines:
            face = _poly_to_face(ring)
            if face is not None:
                step_faces.append(face)

        if not step_faces:
            return []

        try:
            step_union = step_faces[0]
            for f in step_faces[1:]:
                step_union = step_union + f

            wall_faces = []
            for pf in plan_faces:
                outer = pf.get("outer") or []
                face = _poly_to_face(outer)
                if face is None:
                    continue
                for hole in (pf.get("holes") or []):
                    hole_face = _poly_to_face(hole)
                    if hole_face is not None:
                        try:
                            face = face - hole_face
                        except Exception:
                            pass
                wall_faces.append(face)

            if wall_faces:
                wall_union = wall_faces[0]
                for f in wall_faces[1:]:
                    wall_union = wall_union + f
                step_union = step_union - wall_union

            result = faces_to_polys(step_union.faces())
            if result:
                return result
        except Exception as e:
            print(f"  (step/wall subtraction failed: {e})")

    # Fallback: plain step outlines.  Same shape the pre-refactor
    # reader produced when the boolean step failed; the visual and
    # most of the snap behavior are preserved, only the "not walkable
    # under a column" refinement is lost.
    return [{"outer": ring, "holes": []} for ring in step_outlines]


# ---------------------------------------------------------------------------
# Spatial ordering helpers for the unfolded wall strip
# ---------------------------------------------------------------------------

def _pt_seg_dist(px, py, x1, y1, x2, y2):
    dx = x2 - x1; dy = y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-9:
        return hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    t = max(0.0, min(1.0, t))
    return hypot(px - (x1 + t * dx), py - (y1 + t * dy))


def _footprints_adjacent(g1, g2, tol=20.0):
    a1, b1 = g1["a"], g1["b"]
    a2, b2 = g2["a"], g2["b"]
    for p in (a1, b1):
        for q in (a2, b2):
            if hypot(p[0] - q[0], p[1] - q[1]) < tol:
                return True
    if _pt_seg_dist(a1[0], a1[1], a2[0], a2[1], b2[0], b2[1]) < tol: return True
    if _pt_seg_dist(b1[0], b1[1], a2[0], a2[1], b2[0], b2[1]) < tol: return True
    if _pt_seg_dist(a2[0], a2[1], a1[0], a1[1], b1[0], b1[1]) < tol: return True
    if _pt_seg_dist(b2[0], b2[1], a1[0], a1[1], b1[0], b1[1]) < tol: return True
    return False


def _order_footprints_spatially(footprints_geom, original_order):
    keys = list(original_order)
    n = len(keys)
    if n <= 1:
        return keys

    pts = {k: (footprints_geom[k]["a"], footprints_geom[k]["b"]) for k in keys}

    def endpoint_dist(k, pt):
        a, b = pts[k]
        return min(hypot(a[0] - pt[0], a[1] - pt[1]),
                   hypot(b[0] - pt[0], b[1] - pt[1]))

    def on_seg_dist(k, pt):
        a, b = pts[k]
        return _pt_seg_dist(pt[0], pt[1], a[0], a[1], b[0], b[1])

    def proximity(k, pt):
        return min(endpoint_dist(k, pt), on_seg_dist(k, pt))

    def far_end(k, pt):
        a, b = pts[k]
        da = hypot(a[0] - pt[0], a[1] - pt[1])
        db = hypot(b[0] - pt[0], b[1] - pt[1])
        return b if da < db else a

    adj = {k: set() for k in keys}
    for i in range(n):
        ki = keys[i]
        for j in range(i + 1, n):
            kj = keys[j]
            if _footprints_adjacent(footprints_geom[ki], footprints_geom[kj]):
                adj[ki].add(kj)
                adj[kj].add(ki)

    start = keys[0]
    for k in keys:
        if len(adj[k]) == 1:
            start = k
            break

    visited = {start}
    order = [start]
    cur = start
    cur_end = pts[cur][1]

    while len(order) < n:
        best = None
        best_d = float("inf")
        for nb in adj[cur]:
            if nb in visited:
                continue
            d = proximity(nb, cur_end)
            if d < 20.0 and d < best_d:
                best = nb; best_d = d
        if best is None:
            for nb in adj[cur]:
                if nb in visited:
                    continue
                d = endpoint_dist(nb, cur_end)
                if d < best_d:
                    best = nb; best_d = d
        if best is None:
            for k in keys:
                if k in visited:
                    continue
                d = endpoint_dist(k, cur_end)
                if d < best_d:
                    best = k; best_d = d
        if best is None:
            break
        cur_end = far_end(best, cur_end)
        visited.add(best)
        order.append(best)
        cur = best

    return order


def _unfold_from_walls_json(wdata):
    version = int(wdata.get("version", 1))
    wh_default = float(wdata.get("wall_height_mm", WALL_HEIGHT))

    raw = []
    if version >= 3:
        raw = list(wdata.get("surfaces", []))
    elif version == 2:
        for s in wdata.get("surfaces", []):
            raw.append({
                "tag":    s.get("tag"),
                "p1":     s["p1"],
                "p2":     s["p2"],
                "z_range_mm": [0.0, float(s.get("height_mm", wh_default))],
                "kind":   s.get("kind", "wall"),
                "parent": s.get("parent"),
            })
    else:
        for w in wdata.get("walls", []):
            raw.append({
                "tag":    w.get("tag"),
                "p1":     w["p1"],
                "p2":     w["p2"],
                "z_range_mm": [0.0, wh_default],
                "kind":   "wall",
                "parent": None,
            })
        for sf in wdata.get("stepFaces", []):
            raw.append({
                "tag":    sf.get("tag"),
                "p1":     sf["p1"],
                "p2":     sf["p2"],
                "z_range_mm": [0.0, float(sf.get("top_mm", wh_default))],
                "kind":   "step",
                "parent": None,
            })

    def _key(p1, p2):
        a = (round(p1[0], 3), round(p1[1], 3))
        b = (round(p2[0], 3), round(p2[1], 3))
        if a > b:
            a, b = b, a
        return (a, b)

    footprints_geom = {}
    original_order = []
    for s in raw:
        p1 = s["p1"]; p2 = s["p2"]
        k = _key(p1, p2)
        if k in footprints_geom:
            continue
        dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-6:
            continue
        footprints_geom[k] = {
            "a":   [float(p1[0]), float(p1[1])],
            "b":   [float(p2[0]), float(p2[1])],
            "len": float(L),
        }
        original_order.append(k)

    ordered_keys = _order_footprints_spatially(footprints_geom, original_order)

    footprints = {}
    u = 0.0
    for k in ordered_keys:
        g = footprints_geom[k]
        footprints[k] = {
            "u0":  u,
            "u1":  u + g["len"],
            "a":   g["a"],
            "b":   g["b"],
            "len": g["len"],
        }
        u += g["len"]

    segments = []
    for s in raw:
        p1 = s["p1"]; p2 = s["p2"]
        k = _key(p1, p2)
        if k not in footprints:
            continue
        fp = footprints[k]
        zr = s.get("z_range_mm") or [0.0, float(s.get("height_mm", wh_default))]
        segments.append({
            "u0":     fp["u0"],
            "u1":     fp["u1"],
            "a":      fp["a"],
            "b":      fp["b"],
            "len":    fp["len"],
            "tag":    s.get("tag"),
            "z_lo":   float(zr[0]),
            "z_hi":   float(zr[1]),
            "kind":   s.get("kind", "wall"),
            "parent": s.get("parent"),
        })

    footprints_list = []
    for k in ordered_keys:
        fp = footprints[k]
        footprints_list.append({
            "u0":  fp["u0"], "u1":  fp["u1"],
            "a":   fp["a"],  "b":   fp["b"],
            "len": fp["len"],
        })

    return {
        "segments":   segments,
        "footprints": footprints_list,
        "totalU":     u,
        "height":     wh_default,
        "ccw":        True,
    }
