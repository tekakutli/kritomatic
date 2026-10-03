"""
cable_geometry.py — extract wall geometry from room.step + room_walls.json.

Reads the STEP model produced by room.py and the sidecar room_walls.json
and returns a `geom` dict:

    {
        "faces":        [...],   # wall footprint polygons at cut height
        "stepFaces":    [...],   # step-level footprint polygons
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

The unfolded-wall strip is built from room_walls.json's `surfaces` via the
spatial ordering helpers below.  The STEP fallback (morphological close on
the largest hole) is used only when room_walls.json is missing.

The sidecar carries a `version` field and this reader dispatches on it,
upgrading older formats to the current shape before returning.  A sidecar
whose version is newer than any format this reader recognises is accepted
on the assumption that the shape of the `surfaces` array it reads has not
changed between then and now.
"""

import json
import os
from math import hypot

from quiet import quiet


with quiet():
    from build123d import import_step, Plane, section, offset, Kind


STEP_FILE      = "room.step"
WALLS_FILE     = "room_walls.json"
WALL_CUT_Z     = 1200.0
STEP_LOW_Z     = 100.0
WALL_HEIGHT    = 3000.0
UNFOLD_CLOSE_R = 600.0


# ---------------------------------------------------------------------------
# Wire / face helpers
# ---------------------------------------------------------------------------

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
    if not os.path.exists(STEP_FILE):
        raise SystemExit(f"{STEP_FILE} not found — run room.py first.")

    with quiet():
        room = import_step(STEP_FILE)
        wall_sketch = section(room, Plane.XY.offset(WALL_CUT_Z))
        low_sketch  = section(room, Plane.XY.offset(STEP_LOW_Z))

    wall_polys = faces_to_polys(wall_sketch.faces())

    step_polys = []
    try:
        with quiet():
            step_sketch = low_sketch - wall_sketch
        step_polys = faces_to_polys(step_sketch.faces())
    except Exception as e:
        print(f"  (could not compute step areas: {e})")
        step_polys = []

    if not wall_polys:
        raise SystemExit("Section returned no faces — is room.step valid?")

    min_x = min_y = float("inf")
    max_x = max_y = float("-inf")
    for face in wall_polys + step_polys:
        for ring in [face["outer"], *face["holes"]]:
            for x, y in ring:
                min_x = min(min_x, x); min_y = min(min_y, y)
                max_x = max(max_x, x); max_y = max(max_y, y)

    geom = {
        "faces":     wall_polys,
        "stepFaces": step_polys,
        "bounds":    {"minX": min_x, "minY": min_y,
                      "maxX": max_x, "maxY": max_y},
        "cutZ":      WALL_CUT_Z,
        "columns":   [],
        "openings":  [],
    }

    if os.path.exists(WALLS_FILE):
        try:
            with open(WALLS_FILE, "r", encoding="utf-8") as f:
                wdata = json.load(f)
            uw = _unfold_from_walls_json(wdata)
            geom["unfoldedWall"] = uw
            geom["columns"]      = wdata.get("columns", [])
            geom["openings"]     = wdata.get("openings", [])

            n_step = sum(1 for s in uw["segments"]
                         if s.get("kind") == "step")
            n_col  = sum(1 for s in uw["segments"]
                         if s.get("kind") == "column")
            print(f"  unfolded-wall source  : {WALLS_FILE} v"
                  f"{wdata.get('version', 1)} "
                  f"({len(uw['segments'])} segment(s) — "
                  f"{n_col} column, {n_step} step; "
                  f"perimeter {uw['totalU']:.0f} mm)")
            return geom
        except Exception as e:
            print(f"  (could not read {WALLS_FILE}: {e})")
            print(f"  falling back to geometric extraction from {STEP_FILE}")
    else:
        print(f"  note: {WALLS_FILE} not found — falling back to "
              f"geometric extraction from {STEP_FILE}")

    uw, method = compute_unfold(wall_sketch, geom)
    geom["unfoldedWall"] = uw
    print(f"  unfolded-wall method  : {method}")
    return geom


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


# ---------------------------------------------------------------------------
# Fallback geometric extraction (only when room_walls.json is missing).
# ---------------------------------------------------------------------------

def _ring_via_closing(wall_sketch):
    try:
        dilated = offset(wall_sketch, UNFOLD_CLOSE_R, kind=Kind.INTERSECTION)
        closed  = offset(dilated, -UNFOLD_CLOSE_R, kind=Kind.INTERSECTION)
    except Exception as e:
        print(f"  (morphological close failed: {e})")
        return None

    rings = []
    try:
        for f in closed.faces():
            try:
                for w in f.inner_wires():
                    r = wire_to_polygon(w)
                    if r and len(r) >= 3:
                        rings.append(r)
            except Exception:
                continue
    except Exception:
        try:
            for w in closed.inner_wires():
                r = wire_to_polygon(w)
                if r and len(r) >= 3:
                    rings.append(r)
        except Exception:
            pass

    if not rings:
        return None
    return max(rings, key=polygon_area)


def compute_unfold(wall_sketch, geom):
    ring = None
    method = "none"

    best_area = -1.0
    for face in geom["faces"]:
        for hole in face["holes"]:
            A = polygon_area(hole)
            if A > best_area:
                best_area = A
                ring = hole
                method = "largest hole"

    if ring is None:
        ring = _ring_via_closing(wall_sketch)
        if ring is not None:
            method = f"morphological close (r={UNFOLD_CLOSE_R:.0f} mm)"

    if ring is None:
        best_farea = -1.0
        best_face = None
        for face in geom["faces"]:
            A = polygon_area(face["outer"])
            if A > best_farea:
                best_farea = A
                best_face = face
        if best_face is not None:
            ring = best_face["outer"]
            method = "fallback (largest face outer ring)"

    if ring is None:
        return ({"segments": [], "footprints": [], "totalU": 0.0,
                 "height": WALL_HEIGHT, "ccw": True}, method)

    ccw = signed_area(ring) > 0

    segments = []
    footprints = []
    u = 0.0
    n = len(ring)
    for i in range(n):
        a = ring[i]
        b = ring[(i + 1) % n]
        dx = b[0] - a[0]
        dy = b[1] - a[1]
        L = (dx * dx + dy * dy) ** 0.5
        if L < 1e-6:
            continue
        segments.append({
            "u0": u, "u1": u + L,
            "a": [float(a[0]), float(a[1])],
            "b": [float(b[0]), float(b[1])],
            "len": float(L),
            "tag": None,
            "z_lo": 0.0, "z_hi": WALL_HEIGHT,
            "kind": "wall", "parent": None,
        })
        footprints.append({
            "u0": u, "u1": u + L,
            "a": [float(a[0]), float(a[1])],
            "b": [float(b[0]), float(b[1])],
            "len": float(L),
        })
        u += L

    return ({"segments": segments, "footprints": footprints,
             "totalU": u, "height": WALL_HEIGHT, "ccw": ccw}, method)
