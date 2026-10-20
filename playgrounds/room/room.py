"""
room.py — parametric model of an irregular room.

Defines a room from the MEASUREMENTS block below, resolves it into a
solid, and writes the artefacts downstream tools consume:

    room.step              the built 3D solid
    room.stl               the same solid, triangulated
    room_walls.json        the build output — surfaces, planFaces,
                           columns, steps, openings
    room_dimensions.json   the declared dimension annotations — the
                           "W4 = 166" numbers, their dimension-line
                           endpoints, and the notes-block lines

2D visualization lives in the floor_plan project:

    python floor_plan.py   → floor_plan.svg (+ floor_plan.png)

Install:  pip install build123d
Run:      python room.py
"""

import json
import os
import re
from collections import defaultdict
from contextlib import contextmanager
from math import hypot, atan2, degrees


@contextmanager
def _quiet():
    """Redirect OS-level stderr (fd 2) to /dev/null for the duration
    of the block.  Used around build123d calls that trigger a noisy
    libfontconfig bundled in some wheels; restored before any
    exception unwinds past the context manager."""
    saved = os.dup(2)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, 2)
        yield
    finally:
        os.dup2(saved, 2)
        os.close(devnull)
        os.close(saved)


with _quiet():
    from build123d import *


# ============================================================================
# MEASUREMENTS
# ============================================================================
#
# Every geometric value is expressed as one of:
#
#   • a raw number in "units" — UNIT_MM converts to mm (10.0 = cm)
#   • a named constant from CONSTANTS (e.g. "STEP_DEPTH")
#   • an anchor to a feature (e.g. "W4.inner.y", "C1.E.x")
#   • an expression combining them (e.g. "W4.inner.y + 99")
#
# Bare offsets in expressions are in units.  Anchors resolve to
# absolute mm values.
#
# The numbers below describe *distances along a walk*, not walls.
# Physical surfaces are *calculated* from them — see _dump_wall_json.
# ============================================================================


DESCRIBE_ONLY = False

UNIT_MM = 10.0

CONSTANTS = {
    "STEP_DEPTH":   17.0,
    "STEP_HEIGHT":  28.0,
    "W7_EXTENSION": 13.5,
    "WALL_HEIGHT":  238.0,
    "WALL_THICK":   15.0,
    "FLOOR_THICK":  20.0,
    "PLAN_CUT_Z":   120.0,
}

GROUND_PERIMETER = [
    ("W1", 306.0, None),
    ("W7", "STEP_DEPTH", None),
    ("W8", 279.0, "L"),
    (None, "STEP_DEPTH", "L"),
    ("W3", 419.0, None),
    ("W4", 166.0, "L"),
    ("W5", 111.0, "L"),
    ("W6", 113.0, "R"),
]

WALL_DIMS_SPEC = {
    "W1": 306.0,
    "W7": {"value": "STEP_DEPTH + W7_EXTENSION",
           "extend_to": "W13.inner.y"},
    "W8": 279.0,
    "W3": 419.0,
    "W4": 166.0,
    "W5": 111.0,
    "W6": 113.0,
}

SMALL_ROOMS = [
    {
        "tag": "SR",
        "start": "D2.west",
        "heading": "N",
        "walk": [
            ("W10", 85.5, None),
            ("W11", 86.0, "R"),
            ("W12", 72.0, "R"),
            ("W13", 16.0, "R"),
            (None,  13.5, "L"),
            (None,  70.0, "R"),
        ],
        "dims": ["85.5", "86", "72", "16", None, None],
    },
]

COLUMNS = [
    {
        "tag":  "C1",
        "note": "NW corner of extension, brackets W9/W3",
        "box": {
            "west":  "W3.inner.x",
            "east":  "W3.inner.x + 14",
            "south": "W8.inner.y - 27",
            "north": "W8.inner.y",
        },
    },
    {
        "tag":  "C2",
        "note": "on W3, 99 units north of W4, 42.5 units long",
        "box": {
            "west":  "W3.inner.x",
            "east":  "W3.inner.x + 14",
            "south": "W4.inner.y + 99",
            "north": "W4.inner.y + 141.5",
        },
    },
    {
        "tag":  "C3",
        "note": "SW corner of SR, on W8/W10 junction, sits on the step",
        "box": {
            "west":  "W10.inner.x",
            "east":  "W10.inner.x + 3",
            "south": "W8.inner.y",
            "north": "W8.inner.y + 9",
        },
    },
]

STEPS = [
    {
        "tag": "EXT",
        "height": "STEP_HEIGHT",
        "bounds": {
            "south": "W3.start.y",
            "east":  "W7.inner.x",
            "north": "W8.inner.y",
            "west":  "C1.E.x",
        },
    },
    {
        "tag": "SR_STRIP",
        "height": "STEP_HEIGHT",
        "bounds": {
            "south": "W8.inner.y",
            "east":  "W7.inner.x",
            "north": "W13.inner.y",
            "west":  "W10.inner.x",
        },
    },
]

OPENINGS = [
    {"tag": "D1", "wall_tag": "W6", "offset": 15.0,
     "width": "to_end", "height": 210.0},
    {"tag": "D2", "wall_tag": "W8", "offset": 0.0,
     "width": 70.0, "height": 210.0, "sill": "STEP_HEIGHT"},
]


# ============================================================================
# BUILD TUNING — helper tolerances used during solid construction only
# ============================================================================

HOST_PARALLEL_TOL  = 0.02
HOST_COLLINEAR_TOL = 20.0
HOST_OVERLAP_MIN   = 0.05


# ============================================================================
# RESOLVER — anchors, walks, boxes, bounds
# ============================================================================

class RoomContext:
    def __init__(self, mm):
        self.mm = mm
        self.registry = {}

    def register(self, name, value):
        self.registry[name] = value

    def _resolve_atom(self, atom):
        atom = atom.strip()
        if atom in self.registry:
            v = self.registry[atom]
            if isinstance(v, (int, float)):
                return float(v)
            raise TypeError(f"anchor {atom!r} is not a scalar")
        try:
            return float(atom) * self.mm
        except ValueError:
            raise KeyError(f"unknown anchor {atom!r}")

    def resolve(self, expr):
        if isinstance(expr, (int, float)):
            return float(expr) * self.mm
        s = str(expr).strip()
        tokens = re.split(r'\s*([+-])\s*', s)
        value = self._resolve_atom(tokens[0])
        i = 1
        while i < len(tokens):
            op = tokens[i]
            operand = self._resolve_atom(tokens[i + 1])
            value = value + operand if op == '+' else value - operand
            i += 2
        return value

    def resolve_point(self, expr):
        if isinstance(expr, (tuple, list)) and len(expr) == 2:
            x = self.resolve(expr[0]) if isinstance(expr[0], str) \
                else float(expr[0]) * self.mm
            y = self.resolve(expr[1]) if isinstance(expr[1], str) \
                else float(expr[1]) * self.mm
            return (x, y)
        v = self.registry.get(expr)
        if isinstance(v, tuple) and len(v) == 2 \
                and isinstance(v[0], (int, float)):
            return v
        raise KeyError(f"cannot resolve point anchor {expr!r}")


_DIRS = [(0, 1), (-1, 0), (0, -1), (1, 0)]
_HEADINGS = {"N": 0, "W": 1, "S": 2, "E": 3}


def walk_polygon(ctx, start, heading, entries, snap_tol_mm=30.0):
    heading_idx = _HEADINGS[heading]
    x, y = start
    pts = [(x, y)]
    for entry in entries:
        _tag, length, turn = entry
        if turn == "L":
            heading_idx = (heading_idx + 1) % 4
        elif turn == "R":
            heading_idx = (heading_idx - 1) % 4
        dx, dy = _DIRS[heading_idx]
        len_mm = ctx.resolve(length)
        x += dx * len_mm
        y += dy * len_mm
        pts.append((x, y))
    if len(pts) > 2:
        gap = hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if gap <= snap_tol_mm:
            pts.pop()
    return pts


def register_wall(ctx, name, p1, p2):
    ctx.register(f"{name}.start", p1)
    ctx.register(f"{name}.end", p2)
    ctx.register(f"{name}.inner", (p1, p2))
    ctx.register(f"{name}.start.x", p1[0])
    ctx.register(f"{name}.start.y", p1[1])
    ctx.register(f"{name}.end.x", p2[0])
    ctx.register(f"{name}.end.y", p2[1])
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    ctx.register(f"{name}.length", hypot(dx, dy))
    if abs(dx) < 1.0:
        ctx.register(f"{name}.inner.x", p1[0])
    if abs(dy) < 1.0:
        ctx.register(f"{name}.inner.y", p1[1])


def register_column(ctx, name, box):
    x0, y0, x1, y1 = box
    ctx.register(f"{name}.box", box)
    ctx.register(f"{name}.W", ((x0, y0), (x0, y1)))
    ctx.register(f"{name}.E", ((x1, y0), (x1, y1)))
    ctx.register(f"{name}.S", ((x0, y0), (x1, y0)))
    ctx.register(f"{name}.N", ((x0, y1), (x1, y1)))
    ctx.register(f"{name}.W.x", x0)
    ctx.register(f"{name}.E.x", x1)
    ctx.register(f"{name}.S.y", y0)
    ctx.register(f"{name}.N.y", y1)
    ctx.register(f"{name}.center", ((x0 + x1) / 2.0, (y0 + y1) / 2.0))


def register_opening(ctx, name, p1, p2):
    ctx.register(f"{name}.p1", p1)
    ctx.register(f"{name}.p2", p2)
    ctx.register(f"{name}.center",
                 ((p1[0] + p2[0]) / 2.0, (p1[1] + p2[1]) / 2.0))
    if abs(p2[1] - p1[1]) < 1.0:
        if p1[0] < p2[0]:
            ctx.register(f"{name}.west", p1)
            ctx.register(f"{name}.east", p2)
        else:
            ctx.register(f"{name}.west", p2)
            ctx.register(f"{name}.east", p1)
    elif abs(p2[0] - p1[0]) < 1.0:
        if p1[1] < p2[1]:
            ctx.register(f"{name}.south", p1)
            ctx.register(f"{name}.north", p2)
        else:
            ctx.register(f"{name}.south", p2)
            ctx.register(f"{name}.north", p1)


def resolve_measurements():
    ctx = RoomContext(UNIT_MM)
    for name, value in CONSTANTS.items():
        ctx.register(name, value * UNIT_MM)

    inner_pts = walk_polygon(ctx, (0.0, 0.0), "N", GROUND_PERIMETER)
    main_tags = [e[0] for e in GROUND_PERIMETER]
    for i, tag in enumerate(main_tags):
        p1 = inner_pts[i]
        p2 = inner_pts[(i + 1) % len(inner_pts)]
        name = tag if tag else f"wall[{i + 1}]"
        register_wall(ctx, name, p1, p2)

    for op in OPENINGS:
        idx = main_tags.index(op["wall_tag"])
        wall_p1 = inner_pts[idx]
        wall_p2 = inner_pts[(idx + 1) % len(inner_pts)]
        L = hypot(wall_p2[0] - wall_p1[0], wall_p2[1] - wall_p1[1])
        ux = (wall_p2[0] - wall_p1[0]) / L
        uy = (wall_p2[1] - wall_p1[1]) / L
        off_mm = ctx.resolve(op["offset"])
        w_mm = (L - off_mm) if op["width"] == "to_end" \
            else ctx.resolve(op["width"])
        p1 = (wall_p1[0] + ux * off_mm, wall_p1[1] + uy * off_mm)
        p2 = (wall_p1[0] + ux * (off_mm + w_mm),
              wall_p1[1] + uy * (off_mm + w_mm))
        op["_wall_p1"] = wall_p1
        op["_wall_p2"] = wall_p2
        op["_p1"] = p1
        op["_p2"] = p2
        op["_off_mm"] = off_mm
        op["_w_mm"] = w_mm
        op["_h_mm"] = ctx.resolve(op["height"])
        op["_sill_mm"] = ctx.resolve(op.get("sill", 0.0))
        register_opening(ctx, op["tag"], p1, p2)

    for sr in SMALL_ROOMS:
        start = ctx.resolve_point(sr["start"])
        sr["_pts"] = walk_polygon(ctx, start, sr["heading"], sr["walk"])
        sr["_wall_tags"] = [e[0] for e in sr["walk"]]
        for i, entry in enumerate(sr["walk"]):
            tag = entry[0]
            if not tag:
                continue
            p1 = sr["_pts"][i]
            p2 = sr["_pts"][(i + 1) % len(sr["_pts"])]
            register_wall(ctx, tag, p1, p2)

    for col in COLUMNS:
        x0 = ctx.resolve(col["box"]["west"])
        x1 = ctx.resolve(col["box"]["east"])
        y0 = ctx.resolve(col["box"]["south"])
        y1 = ctx.resolve(col["box"]["north"])
        col["_box"] = (x0, y0, x1, y1)
        register_column(ctx, col["tag"], col["_box"])

    for step in STEPS:
        y_s = ctx.resolve(step["bounds"]["south"])
        y_n = ctx.resolve(step["bounds"]["north"])
        x_e = ctx.resolve(step["bounds"]["east"])
        x_w = ctx.resolve(step["bounds"]["west"])
        step["_outline"] = [(x_w, y_s), (x_e, y_s), (x_e, y_n), (x_w, y_n)]
        step["_height"] = ctx.resolve(step["height"]) / UNIT_MM

    wall_dims_resolved = []
    for tag in main_tags:
        if tag is None or tag not in WALL_DIMS_SPEC:
            wall_dims_resolved.append(None)
            continue
        entry = WALL_DIMS_SPEC[tag]
        if isinstance(entry, dict):
            val = ctx.resolve(entry["value"]) / UNIT_MM
            ext = ctx.resolve(entry["extend_to"]) if "extend_to" in entry \
                else None
            wall_dims_resolved.append((val, ext))
        else:
            wall_dims_resolved.append((ctx.resolve(entry) / UNIT_MM, None))

    return ctx, inner_pts, main_tags, wall_dims_resolved


CTX, INNER_PTS, MAIN_TAGS, WALL_DIMS_RESOLVED = resolve_measurements()

MM = UNIT_MM
WALL_HEIGHT = CONSTANTS["WALL_HEIGHT"]
WALL_THICK  = CONSTANTS["WALL_THICK"]
FLOOR_THICK = CONSTANTS["FLOOR_THICK"]
PLAN_CUT_Z  = CONSTANTS["PLAN_CUT_Z"]


# ============================================================================
# WALL DEFINITIONS FOR DOWNSTREAM TOOLS
# ============================================================================
#
# Schema v4.  Walls are calculated, not declared: the MEASUREMENTS
# block is a walk, and the emitted surfaces are derived from that walk
# plus the features placed on it.  A column sitting flush against a
# wall physically occupies a slice of that wall, so the wall is split
# at the column's footprint and the covered slice is dropped; the
# column contributes its own room-facing sides.  Each surface carries:
#
#     {tag, p1, p2, z_range_mm, kind, parent}
#
# Pieces of a wall that were originally one MEASUREMENTS entry share
# that entry's tag — the JSON just has more of them.  planFaces carries
# the actual 2D section at PLAN_CUT_Z, with corner joins, opening
# punches, and column merges already resolved.  That is what
# downstream tools should read when they want "the shape of the walls
# in plan"; the surfaces array is for per-piece metadata.
#
# Polarity contract: each emitted (p1, p2) is oriented so that when the
# unfolded wall strip is laid out with u increasing left→right, the
# segment's visual direction matches the plan:
#     • E–W walls run west → east
#     • N–S walls run south → north
# Enforced by _normalize_polarity() right before each surfaces.append.
# ============================================================================


def _wire_to_polygon(wire, tol=0.01):
    """Trace a build123d Wire into an ordered [[x, y], ...] polygon."""
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


def _dump_wall_json(room_solid, path="room_walls.json"):

    # ---------- inline helpers ------------------------------------------
    def _pip(px, py, poly):
        inside = False
        j = len(poly) - 1
        for i in range(len(poly)):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > py) != (yj > py)) and \
               (px < (xj - xi) * (py - yi) / (yj - yi) + xi):
                inside = not inside
            j = i
        return inside

    def _orient(p1, p2, tol=0.5):
        x1, y1 = p1; x2, y2 = p2
        if abs(y2 - y1) < tol:
            return ("H", y1, min(x1, x2), max(x1, x2))
        if abs(x2 - x1) < tol:
            return ("V", x1, min(y1, y2), max(y1, y2))
        return None

    def _overlaps(s1, s2, tol=0.5):
        a = _orient(*s1, tol=tol)
        b = _orient(*s2, tol=tol)
        if a is None or b is None:
            return False
        if a[0] != b[0] or abs(a[1] - b[1]) > tol:
            return False
        return a[2] < b[3] - tol and b[2] < a[3] - tol

    def _ensure_ccw(poly):
        n = len(poly)
        a2 = sum(poly[i][0] * poly[(i + 1) % n][1]
                 - poly[(i + 1) % n][0] * poly[i][1]
                 for i in range(n))
        return list(poly) if a2 >= 0 else list(reversed(poly))

    def _normalize_polarity(p1, p2):
        dx = abs(p2[0] - p1[0])
        dy = abs(p2[1] - p1[1])
        if dx >= dy:
            if p1[0] > p2[0]:
                return p2, p1
        else:
            if p1[1] > p2[1]:
                return p2, p1
        return p1, p2

    def _split_by_covers(seg, covers, tol=0.5):
        info = _orient(*seg, tol=tol)
        if info is None:
            return [seg]
        skind, scoord, s0, s1 = info

        intervals = []
        for cover in covers:
            cinfo = _orient(*cover, tol=tol)
            if cinfo is None:
                continue
            ckind, ccoord, c0, c1 = cinfo
            if ckind != skind or abs(ccoord - scoord) > tol:
                continue
            lo = max(s0, c0); hi = min(s1, c1)
            if hi > lo + tol:
                intervals.append((lo, hi))

        if not intervals:
            return [seg]

        intervals.sort()
        merged = [list(intervals[0])]
        for lo, hi in intervals[1:]:
            if lo <= merged[-1][1] + tol:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])

        pieces = []
        cur = s0
        for lo, hi in merged:
            if lo > cur + tol:
                pieces.append((cur, lo))
            cur = max(cur, hi)
        if cur < s1 - tol:
            pieces.append((cur, s1))

        p1, p2 = seg
        forward = (p2[0] >= p1[0]) if skind == "H" else (p2[1] >= p1[1])
        out = []
        for lo, hi in pieces:
            if skind == "H":
                out.append(((lo, scoord), (hi, scoord)) if forward
                           else ((hi, scoord), (lo, scoord)))
            else:
                out.append(((scoord, lo), (scoord, hi)) if forward
                           else ((scoord, hi), (scoord, lo)))
        return out

    def _column_overlap_on_wall(wp1, wp2, col_box, tol=0.5, probe=5.0):
        x0, y0, x1, y1 = col_box
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            return None
        wux, wuy = wdx / wL, wdy / wL
        wnx, wny = -wuy, wux
        rnx, rny = wnx, wny

        edges = [((x0, y0), (x1, y0)),
                 ((x1, y0), (x1, y1)),
                 ((x1, y1), (x0, y1)),
                 ((x0, y1), (x0, y0))]
        for cp1, cp2 in edges:
            cdx = cp2[0] - cp1[0]; cdy = cp2[1] - cp1[1]
            cL = hypot(cdx, cdy)
            if cL < 1e-6:
                continue
            cux, cuy = cdx / cL, cdy / cL
            if abs(cux * wuy - cuy * wux) > 0.02:
                continue
            perp = (cp1[0] - wp1[0]) * wnx + (cp1[1] - wp1[1]) * wny
            if abs(perp) > tol:
                continue
            t1 = (cp1[0] - wp1[0]) * wux + (cp1[1] - wp1[1]) * wuy
            t2 = (cp2[0] - wp1[0]) * wux + (cp2[1] - wp1[1]) * wuy
            tlo = max(0.0, min(t1, t2))
            thi = min(wL, max(t1, t2))
            if thi - tlo < tol:
                continue
            tmid = (tlo + thi) * 0.5
            px = wp1[0] + wux * tmid + rnx * probe
            py = wp1[1] + wuy * tmid + rny * probe
            if not (x0 <= px <= x1 and y0 <= py <= y1):
                continue
            return (tlo, thi)
        return None

    WALL_H_MM = CONSTANTS["WALL_HEIGHT"] * UNIT_MM

    # ---------- main perimeter walls (CCW: room on left) ----------------
    n = len(INNER_PTS)
    main_poly = _ensure_ccw(INNER_PTS)
    rev = (main_poly[0] != INNER_PTS[0] or main_poly[1] != INNER_PTS[1])

    if not rev:
        main_tags_ccw = [
            (MAIN_TAGS[i] if i < len(MAIN_TAGS) and MAIN_TAGS[i]
                          else f"wall[{i+1}]") for i in range(n)
        ]
    else:
        main_tags_ccw = [
            (MAIN_TAGS[n - 1 - j] if (n - 1 - j) < len(MAIN_TAGS)
                                   and MAIN_TAGS[n - 1 - j]
                                 else f"wall[{n - j}]")
            for j in range(n)
        ]

    main_edges = [(main_tags_ccw[i], main_poly[i], main_poly[(i + 1) % n])
                  for i in range(n)]
    main_segs_all = [(p1, p2) for (_t, p1, p2) in main_edges]

    # ---------- small rooms (CCW; drop edges that overlap main) --------
    sr_polys_ccw = []
    for sr in SMALL_ROOMS:
        sr_polys_ccw.append((sr, _ensure_ccw(sr["_pts"])))

    sr_edges = []
    for sr, _poly in sr_polys_ccw:
        orig = sr["_pts"]
        n_orig = len(orig)
        a2_orig = sum(
            orig[i][0] * orig[(i + 1) % n_orig][1]
            - orig[(i + 1) % n_orig][0] * orig[i][1]
            for i in range(n_orig))
        reverse = a2_orig < 0
        tags_orig = sr["_wall_tags"]
        for i in range(n_orig):
            tag = tags_orig[i] if i < len(tags_orig) else None
            p1 = orig[i]; p2 = orig[(i + 1) % n_orig]
            if reverse:
                p1, p2 = p2, p1
            if any(_overlaps((p1, p2), ms) for ms in main_segs_all):
                continue
            if not tag:
                tag = f"{sr['tag']}.edge[{i + 1}]"
            sr_edges.append((tag, p1, p2))

    # ---------- column faces facing the room ---------------------------
    PROBE = 5.0
    col_edges = []
    for col in COLUMNS:
        x0, y0, x1, y1 = col["_box"]
        candidates = [
            ("W", (x0, y0), (x0, y1), (-1.0,  0.0)),
            ("N", (x0, y1), (x1, y1), ( 0.0,  1.0)),
            ("E", (x1, y1), (x1, y0), ( 1.0,  0.0)),
            ("S", (x1, y0), (x0, y0), ( 0.0, -1.0)),
        ]
        for name, p1, p2, (nx, ny) in candidates:
            mx = (p1[0] + p2[0]) * 0.5 + nx * PROBE
            my = (p1[1] + p2[1]) * 0.5 + ny * PROBE
            faces_room = _pip(mx, my, main_poly)
            if not faces_room:
                for _sr, poly in sr_polys_ccw:
                    if _pip(mx, my, poly):
                        faces_room = True
                        break
            if not faces_room:
                continue
            if any(_overlaps((p1, p2), ms) for ms in main_segs_all):
                continue
            col_edges.append((f"{col['tag']}.{name}", p1, p2, col["tag"]))

    # ---------- master list of wall-like edges (all CCW) ---------------
    all_wall_edges = []
    for (tag, p1, p2) in main_edges:
        all_wall_edges.append((tag, p1, p2, "wall", None))
    for (tag, p1, p2) in sr_edges:
        all_wall_edges.append((tag, p1, p2, "wall", None))
    for (tag, p1, p2, parent) in col_edges:
        all_wall_edges.append((tag, p1, p2, "column", parent))

    # ---------- step boundary edges (from merged step regions) ---------
    regions = None
    for s in STEPS:
        face = make_face(Polyline(*s["_outline"], close=True))
        regions = face if regions is None else regions + face

    raw_step_edges = []
    if regions is not None:
        for face in regions.faces():
            outer = face.outer_wire()
            outer_pts = _ensure_ccw([(v.X, v.Y) for v in outer.vertices()])

            edges = []
            for e in outer.edges():
                a = e.position_at(0); b = e.position_at(1)
                edges.append(((a.X, a.Y), (b.X, b.Y)))
            try:
                for iw in face.inner_wires():
                    for e in iw.edges():
                        a = e.position_at(0); b = e.position_at(1)
                        edges.append(((a.X, a.Y), (b.X, b.Y)))
            except Exception:
                pass

            for (p1, p2) in edges:
                dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
                L = hypot(dx, dy)
                if L < 1e-6:
                    continue
                mx = (p1[0] + p2[0]) * 0.5
                my = (p1[1] + p2[1]) * 0.5
                nx = -dy / L; ny = dx / L
                if _pip(mx + nx * 5.0, my + ny * 5.0, outer_pts):
                    p1, p2 = p2, p1
                owner = None
                for step in STEPS:
                    ol = step["_outline"]
                    for k in range(len(ol)):
                        a = ol[k]; b = ol[(k + 1) % len(ol)]
                        d2x = b[0] - a[0]; d2y = b[1] - a[1]
                        L2 = d2x * d2x + d2y * d2y
                        if L2 < 1e-9:
                            continue
                        t = max(0.0, min(1.0,
                            ((mx - a[0]) * d2x + (my - a[1]) * d2y) / L2))
                        cx = a[0] + t * d2x; cy = a[1] + t * d2y
                        if hypot(mx - cx, my - cy) < 0.75:
                            owner = step
                            break
                    if owner:
                        break
                if owner is None:
                    owner = STEPS[0]
                raw_step_edges.append(
                    (owner["tag"], owner["_height"] * UNIT_MM, p1, p2))

    # ---------- wall ↔ step overlays ------------------------------------
    def _step_on_wall(wp1, wp2, sp1, sp2, step):
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            return None
        wux, wuy = wdx / wL, wdy / wL
        wnx, wny = -wuy, wux
        sdx = sp2[0] - sp1[0]; sdy = sp2[1] - sp1[1]
        sL = hypot(sdx, sdy)
        if sL < 1e-6:
            return None
        sux, suy = sdx / sL, sdy / sL
        if abs(sux * wuy - suy * wux) > 0.02:
            return None
        perp = (sp1[0] - wp1[0]) * wnx + (sp1[1] - wp1[1]) * wny
        if abs(perp) > 0.5:
            return None
        t1 = (sp1[0] - wp1[0]) * wux + (sp1[1] - wp1[1]) * wuy
        t2 = (sp2[0] - wp1[0]) * wux + (sp2[1] - wp1[1]) * wuy
        tlo = max(0.0, min(t1, t2))
        thi = min(wL, max(t1, t2))
        if thi <= tlo + 0.5:
            return None
        tmid = (tlo + thi) * 0.5
        px = wp1[0] + wux * tmid + wnx * 1.0
        py = wp1[1] + wuy * tmid + wny * 1.0
        if not _pip(px, py, step["_outline"]):
            return None
        return (tlo, thi)

    wall_step_feats = defaultdict(list)
    for idx, (_tag, wp1, wp2, _k, _p) in enumerate(all_wall_edges):
        for step in STEPS:
            sh = step["_height"] * UNIT_MM
            ol = step["_outline"]
            for k in range(len(ol)):
                sp1 = ol[k]; sp2 = ol[(k + 1) % len(ol)]
                r = _step_on_wall(wp1, wp2, sp1, sp2, step)
                if r is not None:
                    wall_step_feats[idx].append((r[0], r[1], float(sh)))

    # ---------- wall ↔ opening overlays ---------------------------------
    wall_opening_feats = defaultdict(list)
    for op in OPENINGS:
        wall_tag = op["wall_tag"]
        wall_idx = None
        for idx, (wtag, _p1, _p2, _k, _p) in enumerate(all_wall_edges):
            if wtag == wall_tag:
                wall_idx = idx
                break
        if wall_idx is None:
            continue
        wp1 = all_wall_edges[wall_idx][1]
        wp2 = all_wall_edges[wall_idx][2]
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            continue
        wux, wuy = wdx / wL, wdy / wL
        t1 = (op["_p1"][0] - wp1[0]) * wux + (op["_p1"][1] - wp1[1]) * wuy
        t2 = (op["_p2"][0] - wp1[0]) * wux + (op["_p2"][1] - wp1[1]) * wuy
        tlo = max(0.0, min(t1, t2))
        thi = min(wL, max(t1, t2))
        if thi <= tlo + 0.5:
            continue
        sill = float(op.get("_sill_mm", 0.0))
        top = float(sill + op.get("_h_mm", 0.0))
        wall_opening_feats[wall_idx].append((tlo, thi, sill, top))

    # ---------- wall ↔ column overlays ----------------------------------
    n_col_overlaps = 0
    for idx, (_tag, wp1, wp2, _k, _p) in enumerate(all_wall_edges):
        if _k != "wall":
            continue
        for col in COLUMNS:
            r = _column_overlap_on_wall(wp1, wp2, col["_box"])
            if r is None:
                continue
            wall_opening_feats[idx].append((r[0], r[1], 0.0, WALL_H_MM))
            n_col_overlaps += 1

    # ---------- z-profile along a wall ----------------------------------
    def _wall_z_pieces(wp1, wp2, step_feats, opening_feats):
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            return []
        ev = {0.0, wL}
        for (a, b, _h) in step_feats:
            ev.add(max(0.0, min(wL, a)))
            ev.add(max(0.0, min(wL, b)))
        for (a, b, _s, _t) in opening_feats:
            ev.add(max(0.0, min(wL, a)))
            ev.add(max(0.0, min(wL, b)))
        ev = sorted(ev)
        pieces = []
        for i in range(len(ev) - 1):
            a, b = ev[i], ev[i + 1]
            if b - a < 0.5:
                continue
            tmid = (a + b) * 0.5
            zr = [(0.0, WALL_H_MM)]
            for (s0, s1, sh) in step_feats:
                if s0 <= tmid <= s1:
                    zr = [(max(lo, sh), hi)
                          for (lo, hi) in zr if hi > sh + 0.5]
            for (o0, o1, sill, top) in opening_feats:
                if o0 <= tmid <= o1:
                    new = []
                    for (lo, hi) in zr:
                        if hi <= sill + 0.5 or lo >= top - 0.5:
                            new.append((lo, hi))
                        else:
                            if lo < sill - 0.5:
                                new.append((lo, sill))
                            if hi > top + 0.5:
                                new.append((top, hi))
                    zr = new
            if zr:
                pieces.append((a, b, zr))
        return pieces

    # ---------- emit wall/column surfaces -------------------------------
    surfaces = []
    for idx, (wtag, wp1, wp2, kind, parent) in enumerate(all_wall_edges):
        wdx = wp2[0] - wp1[0]; wdy = wp2[1] - wp1[1]
        wL = hypot(wdx, wdy)
        if wL < 1e-6:
            continue
        wux, wuy = wdx / wL, wdy / wL
        pieces = _wall_z_pieces(
            wp1, wp2,
            wall_step_feats.get(idx, []),
            wall_opening_feats.get(idx, []))
        for (a, b, zr) in pieces:
            p1 = (wp1[0] + wux * a, wp1[1] + wuy * a)
            p2 = (wp1[0] + wux * b, wp1[1] + wuy * b)
            p1, p2 = _normalize_polarity(p1, p2)
            for (lo, hi) in zr:
                if hi - lo < 0.5:
                    continue
                surfaces.append({
                    "tag":        wtag,
                    "p1":         [float(p1[0]), float(p1[1])],
                    "p2":         [float(p2[0]), float(p2[1])],
                    "z_range_mm": [float(lo), float(hi)],
                    "kind":       kind,
                    "parent":     parent,
                })

    # ---------- emit free-standing step risers --------------------------
    wall_segs = [(p1, p2) for (_t, p1, p2, _k, _p) in all_wall_edges]
    for (stag, sh, sp1, sp2) in raw_step_edges:
        for (fp1, fp2) in _split_by_covers((sp1, sp2), wall_segs):
            if hypot(fp2[0] - fp1[0], fp2[1] - fp1[1]) < 1.0:
                continue
            fp1, fp2 = _normalize_polarity(fp1, fp2)
            surfaces.append({
                "tag":        f"{stag}.step",
                "p1":         [float(fp1[0]), float(fp1[1])],
                "p2":         [float(fp2[0]), float(fp2[1])],
                "z_range_mm": [0.0, float(sh)],
                "kind":       "step",
                "parent":     stag,
            })

    # ---------- openings + columns metadata -----------------------------
    openings_data = [{
        "tag":      op["tag"],
        "wall_tag": op["wall_tag"],
        "p1":       [float(op["_p1"][0]), float(op["_p1"][1])],
        "p2":       [float(op["_p2"][0]), float(op["_p2"][1])],
        "sill_mm":  float(op.get("_sill_mm", 0.0)),
        "top_mm":   float(op.get("_sill_mm", 0.0) + op.get("_h_mm", 0.0)),
    } for op in OPENINGS]

    columns_data = [{
        "tag":  col["tag"],
        "note": col.get("note", ""),
        "box":  [float(col["_box"][0]), float(col["_box"][1]),
                 float(col["_box"][2]), float(col["_box"][3])],
    } for col in COLUMNS]

    # ---------- step footprints -----------------------------------------
    steps_data = [{
        "tag":       s["tag"],
        "outline":   [[float(p[0]), float(p[1])] for p in s["_outline"]],
        "height_mm": float(s["_height"] * UNIT_MM),
    } for s in STEPS]

    # ---------- plan section at cut height ------------------------------
    plan_cut_z_mm = CONSTANTS["PLAN_CUT_Z"] * UNIT_MM
    plan_faces = []
    try:
        with _quiet():
            plan_sketch = section(room_solid, Plane.XY.offset(plan_cut_z_mm))
        for face in plan_sketch.faces():
            outer = _wire_to_polygon(face.outer_wire())
            if len(outer) < 3:
                continue
            holes = []
            for w in face.inner_wires():
                h = _wire_to_polygon(w)
                if len(h) >= 3:
                    holes.append(h)
            plan_faces.append({"outer": outer, "holes": holes})
    except Exception as e:
        print(f"  (could not compute plan section: {e})")
        plan_faces = []

    data = {
        "version":           4,
        "unit_mm":           UNIT_MM,
        "wall_height_mm":    WALL_H_MM,
        "wall_thickness_mm": CONSTANTS["WALL_THICK"] * UNIT_MM,
        "plan_cut_z_mm":     CONSTANTS["PLAN_CUT_Z"] * UNIT_MM,
        "surfaces":          surfaces,
        "openings":          openings_data,
        "columns":           columns_data,
        "steps":             steps_data,
        "planFaces":         plan_faces,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    n_w  = sum(1 for s in surfaces if s["kind"] == "wall")
    n_c  = sum(1 for s in surfaces if s["kind"] == "column")
    n_st = sum(1 for s in surfaces if s["kind"] == "step")
    print(f"Wrote {path}  (v4: {len(surfaces)} surface(s) — "
          f"{n_w} wall, {n_c} column, {n_st} step; "
          f"{len(openings_data)} opening(s), "
          f"{len(columns_data)} column(s); "
          f"{n_col_overlaps} wall/column split(s); "
          f"{len(plan_faces)} plan face(s))")


def _dump_dimensions_json(path="room_dimensions.json"):
    """Emit the declared dimension annotations.

    A flat bag of entries — one per declared measurement — carrying
    the tag, the two points its dimension line runs between (with any
    extend_to override already applied), the value in source units,
    and a `kind` presentation hint ("main" vs "small").  No
    vocabulary from the MEASUREMENTS block leaks through: this is
    just a list of annotated segments."""
    dims = []

    for i in range(len(INNER_PTS)):
        tag = MAIN_TAGS[i]
        entry = WALL_DIMS_RESOLVED[i]
        if tag is None or entry is None:
            continue
        value, extend_to = entry
        p1 = INNER_PTS[i]
        p2 = INNER_PTS[(i + 1) % len(INNER_PTS)]
        if extend_to is not None:
            dx = p2[0] - p1[0]
            dy = p2[1] - p1[1]
            if abs(dy) > abs(dx):
                p2 = (p2[0], extend_to)
            else:
                p2 = (extend_to, p2[1])
        dims.append({
            "tag":   tag,
            "p1":    [float(p1[0]), float(p1[1])],
            "p2":    [float(p2[0]), float(p2[1])],
            "value": float(value),
            "kind":  "main",
        })

    for sr in SMALL_ROOMS:
        pts  = sr["_pts"]
        tags = sr["_wall_tags"]
        vals = sr["dims"]
        for i, tag in enumerate(tags):
            if not tag:
                continue
            if i >= len(vals) or vals[i] is None:
                continue
            p1 = pts[i]
            p2 = pts[(i + 1) % len(pts)]
            try:
                value = float(vals[i])
            except (TypeError, ValueError):
                value = str(vals[i])
            dims.append({
                "tag":   tag,
                "p1":    [float(p1[0]), float(p1[1])],
                "p2":    [float(p2[0]), float(p2[1])],
                "value": value,
                "kind":  "small",
            })

    notes = [
        {"label": "Room height",    "value": f"{CONSTANTS['WALL_HEIGHT']:g}"},
        {"label": "Step rise",
         "value": f"{max((s['_height'] for s in STEPS), default=0.0):g}"},
        {"label": "Wall thickness", "value": f"{CONSTANTS['WALL_THICK']:g}"},
        {"label": "Floor slab",     "value": f"{CONSTANTS['FLOOR_THICK']:g}"},
    ]

    with open(path, "w", encoding="utf-8") as f:
        json.dump({
            "version":    1,
            "unit_mm":    UNIT_MM,
            "dimensions": dims,
            "notes":      notes,
        }, f, indent=2)

    print(f"Wrote {path}  ({len(dims)} dimension(s), "
          f"{len(notes)} note(s))")


# ============================================================================
# BUILD HELPERS
# ============================================================================

ALIGN_MIN = (Align.MIN, Align.MIN, Align.MIN)


def point_in_polygon(pt, poly):
    x, y = pt
    n = len(poly)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and \
           (x < (xj - xi) * (y - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def inward_normal(p1, p2, poly, probe):
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    L = hypot(dx, dy)
    nx, ny = dy / L, -dx / L
    mx, my = (x1 + x2) / 2, (y1 + y2) / 2
    for sign in (1, -1):
        if point_in_polygon((mx + sign * nx * probe,
                             my + sign * ny * probe), poly):
            return sign * nx, sign * ny
    return nx, ny


def to_ccw(poly):
    n = len(poly)
    area = sum(poly[i][0] * poly[(i + 1) % n][1]
               - poly[(i + 1) % n][0] * poly[i][1]
               for i in range(n))
    return list(reversed(poly)) if area < 0 else list(poly)


def build_wall_ring(inner_pts_mm, wall_t_mm, height_mm, skip_edge=None):
    pts = to_ccw(inner_pts_mm)
    n = len(pts)
    edges = []
    for i in range(n):
        p1 = pts[i]
        p2 = pts[(i + 1) % n]
        if skip_edge is not None and skip_edge(p1, p2):
            edges.append(None)
        else:
            edges.append((p1, p2))

    solids = []
    for i in range(n):
        edge = edges[i]
        if edge is None:
            continue
        p1, p2 = edge
        dx = p2[0] - p1[0]
        dy = p2[1] - p1[1]
        L = hypot(dx, dy)
        if L < 1e-9:
            continue
        ux, uy = dx / L, dy / L

        prev = edges[(i - 1) % n]
        start_ext = 0.0
        if prev is not None:
            pp1, pp2 = prev
            pdx = pp2[0] - pp1[0]
            pdy = pp2[1] - pp1[1]
            if pdx * dy - pdy * dx > 0:
                start_ext = wall_t_mm

        nxt = edges[(i + 1) % n]
        end_ext = 0.0
        if nxt is not None:
            np1, np2 = nxt
            ndx = np2[0] - np1[0]
            ndy = np2[1] - np1[1]
            if dx * ndy - dy * ndx > 0:
                end_ext = wall_t_mm

        ext_p1 = (p1[0] - ux * start_ext, p1[1] - uy * start_ext)
        ext_p2 = (p2[0] + ux * end_ext, p2[1] + uy * end_ext)
        L_ext = L + start_ext + end_ext

        nx, ny = uy, -ux
        mx = (ext_p1[0] + ext_p2[0]) / 2 + nx * wall_t_mm / 2
        my = (ext_p1[1] + ext_p2[1]) / 2 + ny * wall_t_mm / 2

        angle = degrees(atan2(dy, dx))
        box = Box(L_ext, wall_t_mm, height_mm)
        box = box.rotate(Axis.Z, angle)
        solids.append(Pos(mx, my, height_mm / 2) * box)

    if not solids:
        return None
    result = solids[0]
    for s in solids[1:]:
        result = result + s
    return result


def find_host(edge, features,
              parallel_tol=HOST_PARALLEL_TOL,
              collinear_tol=HOST_COLLINEAR_TOL,
              overlap_min=HOST_OVERLAP_MIN):
    ex1, ey1 = edge[0]
    ex2, ey2 = edge[1]
    edx, edy = ex2 - ex1, ey2 - ey1
    elen = hypot(edx, edy)
    if elen < 1e-6:
        return None
    ux, uy = edx / elen, edy / elen
    best_name = None
    best_score = None
    for name, fp1, fp2 in features:
        fx1, fy1 = fp1
        fx2, fy2 = fp2
        fdx, fdy = fx2 - fx1, fy2 - fy1
        flen = hypot(fdx, fdy)
        if flen < 1e-6:
            continue
        fux, fuy = fdx / flen, fdy / flen
        if abs(ux * fuy - uy * fux) > parallel_tol:
            continue
        dist = abs((ex1 - fx1) * fuy - (ey1 - fy1) * fux)
        if dist > collinear_tol:
            continue
        t1 = ((ex1 - fx1) * fux + (ey1 - fy1) * fuy) / flen
        t2 = ((ex2 - fx1) * fux + (ey2 - fy1) * fuy) / flen
        tmin, tmax = min(t1, t2), max(t1, t2)
        if tmax < -overlap_min or tmin > 1.0 + overlap_min:
            continue
        overlap = min(tmax, 1.0) - max(tmin, 0.0)
        if overlap < overlap_min:
            continue
        score = (-overlap, -flen, dist)
        if best_score is None or score < best_score:
            best_score = score
            best_name = name
    return best_name


def make_wall_opening(p1, p2, poly, offset_along, width, z0, height,
                      wall_t, pad=30.0):
    dx, dy = p2[0] - p1[0], p2[1] - p1[1]
    L = hypot(dx, dy)
    ux, uy = dx / L, dy / L
    ix, iy = inward_normal(p1, p2, poly, wall_t)
    ox, oy = -ix, -iy
    cx = p1[0] + ux * (offset_along + width / 2) + ox * (wall_t / 2)
    cy = p1[1] + uy * (offset_along + width / 2) + oy * (wall_t / 2)
    cz = z0 + height / 2
    angle = degrees(atan2(dy, dx))
    box = Box(width, wall_t + 2 * pad, height)
    box = box.rotate(Axis.Z, angle)
    return Pos(cx, cy, cz) * box


# ============================================================================
# DESCRIPTION
# ============================================================================

def describe_room():
    W = 68
    eq = "=" * W
    dash = "-" * W
    print(eq)
    print("ROOM DESCRIPTION — lego-style, AI-parseable")
    print(eq)
    print("A room is a set of composable pieces.  To recreate this")
    print("room, populate the MEASUREMENTS block of room.py.")
    print()
    print(dash); print("GLOBAL"); print(dash)
    print(f"  unit             : {UNIT_MM} mm per unit")
    for k, v in CONSTANTS.items():
        print(f"  {k:<17}: {v:g} units")
    print()
    print(dash); print("PIECE 1 — main perimeter"); print(dash)
    n = len(INNER_PTS)
    for i in range(n):
        p1 = INNER_PTS[i]
        p2 = INNER_PTS[(i + 1) % n]
        dx = p2[0] - p1[0]; dy = p2[1] - p1[1]
        L = hypot(dx, dy) / MM
        tag = MAIN_TAGS[i] if i < len(MAIN_TAGS) else None
        tag_s = tag if tag else "--"
        print(f"  {i+1}   {tag_s:<4}  L={L:>7.1f}")
    print()
    print(dash); print("TOTALS"); print(dash)
    main_tagged = [t for t in MAIN_TAGS if t]
    print(f"  walls (main)  : {len(main_tagged)}  ({', '.join(main_tagged)})")
    print()


if DESCRIBE_ONLY:
    describe_room()
    raise SystemExit(0)


# ============================================================================
# BUILD 3D
# ============================================================================

WALL_H_MM  = WALL_HEIGHT * MM
WALL_T_MM  = WALL_THICK  * MM
FLOOR_T_MM = FLOOR_THICK * MM
CUT_Z_MM   = PLAN_CUT_Z  * MM

inner_face = make_face(Polyline(*INNER_PTS, close=True))
outer_face = offset(inner_face, WALL_T_MM, kind=Kind.INTERSECTION)

walls = extrude(outer_face, WALL_H_MM) - extrude(inner_face, WALL_H_MM)
floor = Pos(0, 0, -FLOOR_T_MM) * extrude(outer_face, FLOOR_T_MM)

platform = None
for s in STEPS:
    face = make_face(Polyline(*s["_outline"], close=True))
    piece = extrude(face, s["_height"] * MM)
    platform = piece if platform is None else platform + piece

room = walls + floor
if platform is not None:
    room = room + platform
for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    room = room + Pos(x0, y0, 0) * Box(x1 - x0, y1 - y0, WALL_H_MM,
                                       align=ALIGN_MIN)

main_wall_features = [
    (MAIN_TAGS[i] if MAIN_TAGS[i] else f"wall[{i + 1}]",
     INNER_PTS[i], INNER_PTS[(i + 1) % len(INNER_PTS)])
    for i in range(len(INNER_PTS))
]
for sr in SMALL_ROOMS:
    sr_ring = build_wall_ring(
        sr["_pts"], WALL_T_MM, WALL_H_MM,
        skip_edge=lambda p1, p2: find_host(
            (p1, p2), main_wall_features) is not None,
    )
    if sr_ring is not None:
        room = room + sr_ring

for d in OPENINGS:
    room -= make_wall_opening(d["_wall_p1"], d["_wall_p2"], INNER_PTS,
                              d["_off_mm"], d["_w_mm"],
                              d["_sill_mm"], d["_h_mm"], WALL_T_MM)

# Emit the two sidecars.  room_walls.json needs the built solid (its
# planFaces field comes from a section of it); room_dimensions.json
# does not, but is written here so both files appear together.
_dump_wall_json(room)
_dump_dimensions_json()


# ============================================================================
# EXPORT 3D
# ============================================================================

export_step(room, "room.step")
export_stl(room, "room.stl")


# ============================================================================
# REPORT
# ============================================================================

n = len(INNER_PTS)

bb = room.bounding_box()
area_mm2 = abs(sum(
    INNER_PTS[i][0] * INNER_PTS[(i + 1) % n][1]
    - INNER_PTS[(i + 1) % n][0] * INNER_PTS[i][1]
    for i in range(n)
)) / 2

main_tagged = [t for t in MAIN_TAGS if t is not None]
sr_tagged = []
for sr in SMALL_ROOMS:
    sr_tagged += [t for t in sr["_wall_tags"] if t]
print(f"Walls (main)   : {len(main_tagged)}  ({', '.join(main_tagged)})")
print(f"Walls (SR)     : {', '.join(sr_tagged)}")
print(f"Steps          : {len(STEPS)} primitives")
print(f"Wall height    : {WALL_HEIGHT:g} units ({WALL_H_MM:.0f} mm)")
print(f"Wall thickness : {WALL_THICK:.0f} units ({WALL_T_MM:.0f} mm)")
for c in COLUMNS:
    x0, y0, x1, y1 = c["_box"]
    print(f"Column {c['tag']:<3}     : {(x1-x0)/MM:g} × {(y1-y0)/MM:g} "
          f"units  ({c['note']})")
for d in OPENINGS:
    print(f"Open {d['tag']:<5}     : on {d['wall_tag']}, "
          f"starts {d['offset']:g} units from its start, "
          f"width {d['_w_mm']/MM:g} units, height {d['height']:g} units, "
          f"sill {d.get('sill', 0)}")
print(f"Interior area  : {area_mm2 / 1e6:.2f} m² (main room only)")
print(f"Overall bbox   : {bb.size.X:.0f} × {bb.size.Y:.0f} × {bb.size.Z:.0f} mm")
print("Wrote room.step, room.stl, room_walls.json, room_dimensions.json")
print("  (run floor_plan.py to render the 2D plan)")
