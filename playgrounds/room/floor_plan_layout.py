"""
floor_plan_layout.py — label placement, dimension-line geometry,
notes-block placement, and leader routing for the floor-plan drawer.

Owns both the label positions and the leader geometries, because the
two are mutually constrained: a label should not sit on a leader, and
a leader should not pass through a label.  Owns the ruler geometry
too, for the same reason.

Ruler geometry — trapezoids and parallelograms
----------------------------------------------
Every ruler is drawn in one of two shapes:

    trapezoid      the classic symmetric lean.  Both extension lines
                   tilt inward along the wall's tangent; the dim line
                   is shorter than the wall by 2 × tilt.

    parallelogram  asymmetric.  The dim line keeps the wall's full
                   length and slides along the wall's tangent by
                   RULER_PARALLELOGRAM_SHIFT_UNITS; both extension
                   lines lean the same direction.

A ruler whose nominal-tilt dim line (wall_length − 2·tilt) falls
below RULER_PARALLELOGRAM_TRIGGER_UNITS gets parallelogram candidates
and a tiebreak bias toward choosing one.

Both sides for every ruler
--------------------------
Every ruler is scored on BOTH sides of its wall: the natural side
(chosen by inward_normal_via_holes — the side that faces into a
room) and its geometric opposite.  Each (side, gap, shape) triple is
scored with the same conflict count and the lowest-conflict one wins;
a small tiebreak penalty (RULER_SIDE_FLIP_PENALTY) keeps the natural
side when the two are close, so flipping only happens when it is
worth it.

Column dimension side selection
-------------------------------
Each column's width dim can be placed below or above the column, and
its height dim to the right or the left.  Both sides of each dim are
scored by pure crowding; the less crowded side wins.

The score combines two terms:

    segment grazing — how close the dim line, its extension lines,
                      and its ticks come to a foreign obstacle, up
                      to COL_DIM_CROWDING_UNITS.  An obstacle that
                      is COLLINEAR with the segment being checked
                      is a wall the segment is drawn along, not a
                      crossing; it does not count.

    label intrusion — whether the dim's value label would land on
                      top of a foreign obstacle.  This term is a
                      binary COL_DIM_LABEL_PENALTY per intersecting
                      obstacle.  An obstacle in the column's
                      `attached_walls` set (a wall the column is
                      flush against on some edge) is excluded, so a
                      narrow column's value can sit on top of the
                      wall it is attached to.  "Over the wall if you
                      must."

Attached-wall handling
----------------------
A column flush against a wall has one or more of its own edges
collinear with that wall.  Those walls are called *attached walls*.
Two consequences:

  1.  The column's own edge on the attached side is not drawn —
      the wall's boundary is already there, and drawing the column
      edge on top of it produces a doubled line.  The column outline
      is emitted as the column edge MINUS the attached-wall
      intervals, using the same axis-line + interval subtraction the
      step-edge classifier uses.

  2.  Attached walls (and the column's own edges, full and leftover)
      are added to the column dim label's `ignored_obstacles` set.
      Both the pre-relaxation scorer and the relaxation itself skip
      them for that label, so a narrow column's value can sit on top
      of the wall it is attached to without being pushed off.

Step edges — solid vs. dashed vs. merged
----------------------------------------
Every step polygon edge is one of three things in the plan:

    coincident with a wall face   the step is bounded by a wall on
                                  that side.  Drawn SOLID.

    free-standing riser           the step drops to open floor on
                                  that side.  Drawn DASHED.

    interior to a merged surface  the edge is shared with a
                                  neighbouring step of the SAME
                                  height.  Two same-height steps
                                  are, visually, one surface; no
                                  riser between them.  NOT drawn,
                                  and NOT added to fixed_obstacles.

The merging is done on the (axis, coordinate) line identity, not on
exact endpoint equality, so two collinear edges that only PARTIALLY
overlap are handled correctly.

Leader start point — visual text edge, not collision box
--------------------------------------------------------
A label carries two pairs of half-extents:

    (hw, hh)           the COLLISION BOX: generous, used for the
                       pairwise repulsion between labels and for the
                       "leader crosses another label" test.

    (hw, size × 0.35)  the VISUAL text extents: used only to place
                       the leader's tail.  A typical sans-serif has
                       cap height ≈ 0.7 em and no descender in our
                       labels, so the visible half-height is ≈ 0.35
                       × font size — the collision box is roughly
                       twice as tall.

Leader tip clearance — pushing the label off its own arrowhead
-------------------------------------------------------------
A leader runs from a label's visual edge (tail) to its anchor (tip).
For an arrow-tip label, an arrowhead of size `arrow_size` sits at the
tip, extending back along the tip→tail direction.  If the label's
center is closer to the tip than (visual tail distance + arrow_size
+ gap), the arrowhead overlaps the label text.

The layout detects this in `_leader_tip_violation`: for every arrow-
tip label whose leader segment exists, it computes how far short of
the desired clearance the current label position is.  That shortfall,
squared and weighted by `_LEADER_CLEAR_W`, is added to the label's
overlap score.  Because every relaxation pass (discrete push, pair
moves, gentle retract) minimises the overlap score, the label is
pushed outward along the label→tip direction automatically — no
separate pass, no extra bookkeeping, and the same non-overlap
discipline that governs every other label interaction applies.

Tuning:
    _LEADER_TIP_GAP     extra air, in source units, beyond the
                        arrowhead's back end.
    _LEADER_CLEAR_W     weight of the squared-shortfall penalty.

Leader routing — anchor proximity
---------------------------------
The router penalises four things when picking where a leader's dot
or arrowhead lands:

    crossings           a candidate segment that crosses a prior
                        leader.
    anchor→prior-seg    the candidate ANCHOR landing within
                        _ANCHOR_SEP of a prior leader's line.
    prior-anchor→seg    a prior leader's anchor landing within
                        _ENDPOINT_SEP of the candidate segment.
    label-box hit       the candidate segment crossing a prior
                        label's collision box.

Label targets
-------------
A label's `target` is a point, a polygon, or None.  Dimension tags
and door labels carry a point; column tags and STEP labels carry the
shape's own outline polygon.  A STEP label's preferred position is
chosen outside the polygon by _step_label_position so the label
starts in a free pocket; the polygon's edges remain normal obstacles.

Relaxation and routing
----------------------
Force relaxation, then a loop that pushes labels off leaders and
re-routes, then a verify-and-retry round.

Translation
-----------
The two user-visible strings this module writes — the notes-block
heading and the notes-placement summary — come from T() in
floor_plan_i18n.py.  The note ROW labels come from the room sidecar
in English, so they route through T_note() which maps source label
to translation key with graceful degradation (an unmapped source
label renders unchanged).
"""

import math
from collections import defaultdict

from floor_plan_i18n import T, T_note


# ============================================================================
# Presentation tunables
# ============================================================================

CHAR_ASPECT       = 0.55
LABEL_HEIGHT_FACT = 0.75

# Fraction of the font size that a typical sans-serif's ascender-to-
# baseline span occupies, i.e. the VISUAL half-height of a label whose
# text has no descenders (all of ours).  Used for the leader's tail
# placement and the leader-tip clearance check — the collision box
# keeps using LABEL_HEIGHT_FACT.
VISUAL_TEXT_HH_FACT = 0.35

LAYOUT_ITERATIONS = 300
LAYOUT_SPRING     = 0.05
LAYOUT_REPULSE    = 0.55
LAYOUT_WALL_PUSH  = 0.35

LAYOUT_MARGIN     = 60.0
OBSTACLE_PENETRATION_W = 10.0
LEADER_PENETRATION_W   = 15.0
LEADER_LABEL_MARGIN    = 10.0

DISCRETE_STEPS    = [30, 60, 100, 150, 220, 320, 450, 600]
DISCRETE_ROUNDS   = 16
DISCRETE_EPS      = 0.1

PAIR_STEPS        = [60, 120, 200, 320]
PAIR_ROUNDS       = 6
PAIR_MAX_TRIED    = 12

RETRACT_ITERATIONS = 3
RETRACT_BISECT     = 14

RELAX_RETRY_ROUNDS = 4
LEADER_ITERATIONS  = 4

LABEL_SIZE_MAIN_UNITS   = 16.0
LABEL_SIZE_SMALL_UNITS  =  9.0
LABEL_SIZE_STEP_UNITS   =  9.0
LABEL_SIZE_COLUMN_UNITS =  9.0
LABEL_SIZE_NOTES_UNITS  = 10.0
LABEL_SIZE_DIM_UNITS    = 14.0

TAG_INSET_UNITS     = 60.0
DOOR_INSET_UNITS    = 50.0
DIM_GAP_MAIN_UNITS  = 30.0
DIM_GAP_SMALL_UNITS =  8.0
COL_DIM_GAP_UNITS   = 12.0
COL_DIM_CROWDING_UNITS = 10.0     # proximity that counts as "crowded"
COL_DIM_LABEL_PENALTY  = 1.0e6    # a label box on an obstacle is unacceptable
ARROW_SIZE_UNITS    =  8.0
INWARD_PROBE_UNITS  =  5.0

EXT_GAP_UNITS    = 2.0
EXT_OVER_UNITS   = 2.0
TICK_HALF_UNITS  = 3.0

# ---- ruler geometry -----------------------------------------------
RULER_TILT_UNITS            = 5.0
RULER_MIN_DIM_LINE_UNITS    = 4.0
RULER_SHORT_DIM_LINE_UNITS        = 10.0
RULER_PARALLELOGRAM_TRIGGER_UNITS = 10.0
RULER_PARALLELOGRAM_SHIFT_UNITS   = 5.0

RULER_EXT_GAP_SHARED_UNITS    = 0.0
RULER_SHARED_VERTEX_TOL_UNITS = 1.0

RULER_GAP_STEP_UNITS       = 5.0
RULER_MAX_OFFSET_UNITS     = 15.0
RULER_COLINEAR_TOL_UNITS   = 2.0
RULER_PARALLEL_TOL_UNITS   = 4.0
RULER_WALL_CLEARANCE_UNITS = 4.0
RULER_OFFSET_PENALTY       = 0.3

RULER_SHAPE_TIEBREAK = 0.05

# Penalty, in "conflict count" units, for using the unnatural side of
# a wall ruler.  Small enough that one saved conflict is worth a
# flip, large enough that a tie goes to the natural side.
RULER_SIDE_FLIP_PENALTY = 1.0

# ---- leader-tip clearance ----------------------------------------
#
# Extra air beyond the arrowhead's back end, in source units.  The
# desired distance from a label's center to its own leader's tip is
# (visual tail distance along the tip→label direction)
# + arrow_size + _LEADER_TIP_GAP.  A shortfall of m costs m² ×
# _LEADER_CLEAR_W in the overlap score, which every relaxation pass
# already minimises.
_LEADER_TIP_GAP = 40.0
_LEADER_CLEAR_W = 40.0

# Tolerance, in source units, at which a step edge and a wall face
# count as "collinear".  Used by the step-edge classifier.
STEP_WALL_TOL = 1.0

# Tolerance, in source units, at which two same-height step edges
# count as "the same edge for the merging rule", and at which a
# column edge and a wall face count as "collinear" for the column
# edge-erasure rule.
STEP_MERGE_TOL = 0.5

NOTES_LINE_SPACING = 1.5
NOTES_GAP_FACTOR   = 5.0
NOTES_LINE_MARGIN  = 20.0
NOTES_MAX_CELLS    = 700

TAG_OVERRIDES_UNITS = {
    "W4": (-240.0, -50.0),
    "W5": (-160.0, -80.0),
    "W6": ( -90.0,  80.0),
    "D1": ( -20.0,  40.0),
}

_DISCRETE_DIRS = [
    ( 1.0,      0.0),
    (-1.0,      0.0),
    ( 0.0,      1.0),
    ( 0.0,     -1.0),
    ( 0.7071,   0.7071),
    ( 0.7071,  -0.7071),
    (-0.7071,   0.7071),
    (-0.7071,  -0.7071),
]

_PAIR_DIRS = [(1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0)]

_CROSS_W       = 1_000_000.0
_LABEL_HIT_W   = 500.0
_ENDPOINT_SEP  = 80.0
_ENDPOINT_W    = 2.0
_CENTER_W      = 1.0
_LENGTH_W      = 0.05

# ---- anchor-to-prior-leader-segment proximity ----
_ANCHOR_SEP    = 30.0
_ANCHOR_W      = 100.0

_POLY_CANDIDATE_GRID = 7

# Tolerance, in source units, at which a segment counts as collinear
# with a wall.  Used both for the "column attached to a wall" test and
# for the "ext line runs along a wall, not across it" exemption in
# the column-dim scorer.
_ATTACH_COLLINEAR_TOL = 1.0


# ============================================================================
# Geometric primitives
# ============================================================================

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


def _signed_area(ring):
    n = len(ring)
    s = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return s * 0.5


def inward_normal_via_holes(p1, p2, holes, probe):
    x1, y1 = p1
    x2, y2 = p2
    dx = x2 - x1
    dy = y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-9:
        return (0.0, 0.0)
    nx, ny = dy / L, -dx / L
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0

    best_sign = 1
    best_area = float("inf")
    for sign in (1, -1):
        px = mx + sign * nx * probe
        py = my + sign * ny * probe
        for hole in holes:
            if point_in_polygon((px, py), hole):
                a = abs(_signed_area(hole))
                if a < best_area:
                    best_area = a
                    best_sign = sign
    return (best_sign * nx, best_sign * ny)


# ============================================================================
# Axis-aligned segment helpers
# ============================================================================

def _seg_axis_info(p1, p2, tol=0.5):
    """Return ((kind, coord), lo, hi) for an axis-aligned segment, or
    (None, 0, 0) for a diagonal."""
    dx = abs(p2[0] - p1[0])
    dy = abs(p2[1] - p1[1])
    if dy < tol and dx > tol:
        y = round((p1[1] + p2[1]) / 2.0, 3)
        return (("H", y), min(p1[0], p2[0]), max(p1[0], p2[0]))
    if dx < tol and dy > tol:
        x = round((p1[0] + p2[0]) / 2.0, 3)
        return (("V", x), min(p1[1], p2[1]), max(p1[1], p2[1]))
    return (None, 0.0, 0.0)


def _points_from_axis(line_key, lo, hi):
    """Inverse of _seg_axis_info."""
    kind, coord = line_key
    if kind == "H":
        return (lo, coord), (hi, coord)
    return (coord, lo), (coord, hi)


def _subtract_intervals(lo, hi, subtractions, tol=0.5):
    """Return the pieces of [lo, hi] outside every interval in
    `subtractions`, sorted and merged."""
    if hi - lo < tol:
        return []
    out = []
    cur = lo
    for (slo, shi) in sorted(subtractions):
        if shi <= cur + tol:
            continue
        if slo >= hi - tol:
            break
        if slo > cur + tol:
            out.append((cur, min(slo, hi)))
        cur = max(cur, shi)
        if cur >= hi - tol:
            return out
    if cur < hi - tol:
        out.append((cur, hi))
    return out


# ============================================================================
# Label object
# ============================================================================

class Label:
    """One label.  `target` is a point, a polygon, or None.  `tip`
    selects the marker at the anchor end: "arrow" for point targets,
    "dot" for polygon targets.

    `ignored_obstacles` is an optional set of (p1, p2) segment tuples
    (in either orientation) that both the pre-relaxation scorer and
    the relaxation itself will treat as if they did not exist for
    this label."""

    def __init__(self, text, preferred, target, size, tip="arrow"):
        self.text      = text
        self.preferred = (float(preferred[0]), float(preferred[1]))
        self.target    = target
        self.size      = float(size)
        self.tip       = tip
        self.hw        = CHAR_ASPECT * size * len(text) / 2.0
        self.hh        = LABEL_HEIGHT_FACT * size
        self.pos       = [self.preferred[0], self.preferred[1]]
        self.ignored_obstacles = set()


def _dim_label(text, target, size, direction, arrow_size):
    hw = CHAR_ASPECT * size * len(text) / 2.0
    hh = LABEL_HEIGHT_FACT * size
    dx, dy = direction
    box_extent = abs(dx) * hw + abs(dy) * hh
    offset = box_extent + arrow_size
    preferred = (target[0] + dx * offset, target[1] + dy * offset)
    return Label(text, preferred, target, size, tip="arrow")


def _step_label_position(poly, hw, hh, margin, obstacles):
    """Preferred position for a STEP label: least crowded position
    outside the polygon at three distances in each of four
    directions."""
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    cx = (x0 + x1) / 2.0
    cy = (y0 + y1) / 2.0

    gaps = [margin * 0.3, margin, margin * 1.7]
    candidates = []
    for g in gaps:
        candidates.append((cx, y1 + g + hh))
        candidates.append((cx, y0 - g - hh))
        candidates.append((x1 + g + hw, cy))
        candidates.append((x0 - g - hw, cy))

    class _Tmp:
        pass

    best = (cx, y1 + hh + margin)
    best_score = float("inf")
    for c in candidates:
        t = _Tmp()
        t.pos = [c[0], c[1]]
        t.hw = hw
        t.hh = hh
        score = 0.0
        for (p1, p2) in obstacles:
            score += _aabb_seg_penetration(t, p1, p2, 0.0)
        if score < best_score - 1e-9:
            best_score = score
            best = c
    return best


# ============================================================================
# Ruler object and resolution
# ============================================================================

class _Ruler:
    __slots__ = ("tag", "kind", "p1", "p2", "inward",
                 "base_gap", "gap", "tilt", "effective_tilt", "value",
                 "length", "shared_p1", "shared_p2",
                 "shape", "parallelogram_shift", "short_wall",
                 "parallelogram_preferred", "side_flipped",
                 "natural_inward")

    def __init__(self, tag, kind, p1, p2, inward, base_gap, tilt, value):
        self.tag      = tag
        self.kind     = kind
        self.p1       = p1
        self.p2       = p2
        self.inward   = inward
        self.natural_inward = inward
        self.base_gap = base_gap
        self.gap      = base_gap
        self.tilt     = tilt
        self.effective_tilt = tilt
        self.value    = value
        self.length   = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
        self.shared_p1 = False
        self.shared_p2 = False
        self.shape     = "trapezoid"
        self.parallelogram_shift = 0.0
        self.short_wall = False
        self.parallelogram_preferred = False
        self.side_flipped = False


def _points_close(a, b, tol):
    return (abs(a[0] - b[0]) <= tol) and (abs(a[1] - b[1]) <= tol)


def _detect_shared_vertices(rulers, tol):
    n = len(rulers)
    for i in range(n):
        r = rulers[i]
        for j in range(n):
            if i == j:
                continue
            s = rulers[j]
            if _points_close(r.p1, s.p1, tol) or \
               _points_close(r.p1, s.p2, tol):
                r.shared_p1 = True
            if _points_close(r.p2, s.p1, tol) or \
               _points_close(r.p2, s.p2, tol):
                r.shared_p2 = True


def _segments_collinear(a, b, tol):
    ax0, ay0 = a[0]; ax1, ay1 = a[1]
    bx0, by0 = b[0]; bx1, by1 = b[1]
    a_h = abs(ay0 - ay1) < tol
    b_h = abs(by0 - by1) < tol
    if a_h and b_h:
        if abs(ay0 - by0) > tol:
            return False
        a_lo, a_hi = sorted([ax0, ax1])
        b_lo, b_hi = sorted([bx0, bx1])
        return a_hi + tol > b_lo and b_hi + tol > a_lo
    a_v = abs(ax0 - ax1) < tol
    b_v = abs(bx0 - bx1) < tol
    if a_v and b_v:
        if abs(ax0 - bx0) > tol:
            return False
        a_lo, a_hi = sorted([ay0, ay1])
        b_lo, b_hi = sorted([by0, by1])
        return a_hi + tol > b_lo and b_hi + tol > a_lo
    return False


def _point_seg_dist(px, py, ax, ay, bx, by):
    dx = bx - ax
    dy = by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _seg_point_dist(p1, p2, q):
    px, py = q
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 < 1e-9:
        return math.hypot(px - x1, py - y1), (x1, y1)
    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    cx, cy = x1 + t * dx, y1 + t * dy
    return math.hypot(px - cx, py - cy), (cx, cy)


def _seg_intersect(a1, a2, b1, b2):
    def cross(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1])
                - (a[1] - o[1]) * (b[0] - o[0]))
    d1 = cross(b1, b2, a1)
    d2 = cross(b1, b2, a2)
    d3 = cross(a1, a2, b1)
    d4 = cross(a1, a2, b2)
    return (((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and
            ((d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)))


def _seg_seg_dist(a1, a2, b1, b2):
    if _seg_intersect(a1, a2, b1, b2):
        return 0.0
    return min(
        _point_seg_dist(a1[0], a1[1], b1[0], b1[1], b2[0], b2[1]),
        _point_seg_dist(a2[0], a2[1], b1[0], b1[1], b2[0], b2[1]),
        _point_seg_dist(b1[0], b1[1], a1[0], a1[1], a2[0], a2[1]),
        _point_seg_dist(b2[0], b2[1], a1[0], a1[1], a2[0], a2[1]),
    )


def _segment_close_to_segment(a, b, tol):
    return _seg_seg_dist(a[0], a[1], b[0], b[1]) < tol


def _is_own_wall(obs_seg, ruler, tol):
    return _segments_collinear(obs_seg, (ruler.p1, ruler.p2), tol)


def _ruler_conflict_count(ruler, placed_rulers, fixed_obstacles,
                          ext_gap, ext_over, tick_half,
                          ext_gap_shared, MM):
    paral      = RULER_PARALLEL_TOL_UNITS * MM
    colin      = RULER_COLINEAR_TOL_UNITS * MM
    min_dim    = RULER_MIN_DIM_LINE_UNITS * MM

    if ruler.shape == "parallelogram" and ruler.parallelogram_preferred:
        wall_clear = 0.0
    else:
        wall_clear = RULER_WALL_CLEARANCE_UNITS * MM
    own_tol = max(wall_clear, colin)

    eg_p1 = ext_gap_shared if ruler.shared_p1 else ext_gap
    eg_p2 = ext_gap_shared if ruler.shared_p2 else ext_gap

    segs, ticks, _ = _dimension_lines(
        ruler.p1, ruler.p2, ruler.inward, ruler.gap,
        eg_p1, eg_p2, ext_over, tick_half,
        tilt=ruler.effective_tilt, min_dim_len=min_dim,
        parallelogram_shift=ruler.parallelogram_shift)
    dim_line  = segs[2]

    count = 0

    for obs in fixed_obstacles:
        if _is_own_wall(obs, ruler, own_tol):
            continue
        if _segment_close_to_segment(dim_line, obs, wall_clear):
            count += 1

    for (_prev, prev_segs, prev_ticks) in placed_rulers:
        prev_dim = prev_segs[2]
        if _segments_collinear(dim_line, prev_dim, colin):
            count += 5
            continue
        if _segment_close_to_segment(dim_line, prev_dim, paral):
            count += 1
        for s in (segs + ticks):
            if _segment_close_to_segment(s, prev_dim, paral):
                count += 1
        for s in (prev_segs + prev_ticks):
            if _segment_close_to_segment(dim_line, s, paral):
                count += 1

    return count


def _resolve_ruler_gaps(rulers, fixed_obstacles,
                        ext_gap, ext_over, tick_half,
                        ext_gap_shared, MM):
    ordered = sorted(rulers, key=lambda r: -r.length)
    placed  = []

    step = RULER_GAP_STEP_UNITS * MM
    max_offset = RULER_MAX_OFFSET_UNITS * MM
    min_gap = ext_gap + step
    short_threshold = RULER_SHORT_DIM_LINE_UNITS * MM
    para_shift = RULER_PARALLELOGRAM_SHIFT_UNITS * MM
    para_trigger = RULER_PARALLELOGRAM_TRIGGER_UNITS * MM

    for ruler in ordered:
        if ruler.length - 2 * ruler.tilt >= short_threshold:
            ruler.effective_tilt = ruler.tilt
            ruler.short_wall = False
        else:
            ruler.effective_tilt = max(0.0,
                (ruler.length - short_threshold) / 2.0)
            ruler.short_wall = True

        ruler.parallelogram_preferred = \
            (ruler.length - 2 * ruler.tilt < para_trigger)

        base = ruler.base_gap
        natural_inward = ruler.natural_inward

        gap_candidates = [base]
        k = 1
        while k * step <= max_offset + 1e-6:
            hi = base + k * step
            lo = base - k * step
            gap_candidates.append(hi)
            if lo >= min_gap:
                gap_candidates.append(lo)
            k += 1

        shape_candidates = [("trapezoid", 0.0)]
        if ruler.parallelogram_preferred:
            shape_candidates.append(("parallelogram", +para_shift))
            shape_candidates.append(("parallelogram", -para_shift))

        flipped_inward = (-natural_inward[0], -natural_inward[1])
        side_candidates = [
            (natural_inward, False),
            (flipped_inward, True),
        ]

        best_gap = base
        best_shape = "trapezoid"
        best_shift = 0.0
        best_inward = natural_inward
        best_flipped = False
        best_score = None

        for side, is_flipped in side_candidates:
            ruler.inward = side
            for gap in gap_candidates:
                for shape, shift in shape_candidates:
                    ruler.gap = gap
                    ruler.shape = shape
                    ruler.parallelogram_shift = shift

                    score = _ruler_conflict_count(
                        ruler, placed, fixed_obstacles,
                        ext_gap, ext_over, tick_half,
                        ext_gap_shared, MM)

                    effective = float(score)
                    effective += (abs(gap - base) / step) * RULER_OFFSET_PENALTY

                    if is_flipped:
                        effective += RULER_SIDE_FLIP_PENALTY

                    natural_is_para = ruler.parallelogram_preferred
                    using_para = (shape == "parallelogram")
                    if using_para != natural_is_para:
                        effective += RULER_SHAPE_TIEBREAK

                    if best_score is None or effective < best_score - 1e-9:
                        best_score = effective
                        best_gap = gap
                        best_shape = shape
                        best_shift = shift
                        best_inward = side
                        best_flipped = is_flipped

        ruler.inward = best_inward
        ruler.gap = best_gap
        ruler.shape = best_shape
        ruler.parallelogram_shift = best_shift
        ruler.side_flipped = best_flipped

        eg_p1 = ext_gap_shared if ruler.shared_p1 else ext_gap
        eg_p2 = ext_gap_shared if ruler.shared_p2 else ext_gap

        segs, ticks, _ = _dimension_lines(
            ruler.p1, ruler.p2, ruler.inward, ruler.gap,
            eg_p1, eg_p2, ext_over, tick_half,
            tilt=ruler.effective_tilt,
            min_dim_len=RULER_MIN_DIM_LINE_UNITS * MM,
            parallelogram_shift=ruler.parallelogram_shift)
        placed.append((ruler, segs, ticks))

    return placed


# ============================================================================
# Leader geometry — routing primitives
# ============================================================================

def _leader_segment(label_pos, hw, hh, anchor_pt, pad=0.0):
    """Return (tail, tip) of the leader segment that runs from the
    label's visual edge to its target."""
    tx, ty = label_pos
    wx, wy = anchor_pt
    dx = wx - tx
    dy = wy - ty
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return None
    ux, uy = dx / L, dy / L
    t_x = hw / abs(ux) if abs(ux) > 1e-9 else float("inf")
    t_y = hh / abs(uy) if abs(uy) > 1e-9 else float("inf")
    t = min(t_x, t_y) + pad
    if t >= L:
        return None
    sx = tx + ux * t
    sy = ty + uy * t
    return ((sx, sy), (wx, wy))


def _polygon_interior_candidates(poly, n_per_axis=_POLY_CANDIDATE_GRID):
    if not poly:
        return []
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    cx = sum(xs) / len(xs)
    cy = sum(ys) / len(ys)
    out = [(cx, cy)]
    for ix in range(n_per_axis):
        for iy in range(n_per_axis):
            x = x0 + (x1 - x0) * (ix + 0.5) / n_per_axis
            y = y0 + (y1 - y0) * (iy + 0.5) / n_per_axis
            if point_in_polygon((x, y), poly):
                out.append((x, y))
    return out


def _count_crossings(seg, prior_segs):
    n = 0
    a1, a2 = seg
    for b1, b2 in prior_segs:
        if _seg_intersect(a1, a2, b1, b2):
            n += 1
    return n


def _endpoint_proximity(seg, prior_endpoints):
    pen = 0.0
    if not prior_endpoints:
        return pen
    a, b = seg
    for (ex, ey) in prior_endpoints:
        d = _point_seg_dist(ex, ey, a[0], a[1], b[0], b[1])
        if d < _ENDPOINT_SEP:
            short = _ENDPOINT_SEP - d
            pen += short * short
    return pen


def _anchor_segment_proximity(anchor, prior_segs, sep):
    pen = 0.0
    if not prior_segs:
        return pen
    px, py = anchor
    for b1, b2 in prior_segs:
        d = _point_seg_dist(px, py, b1[0], b1[1], b2[0], b2[1])
        if d < sep:
            short = sep - d
            pen += short * short
    return pen


def _label_box_penetration_length(seg, labels, exclude_idx, margin):
    if not labels:
        return 0.0
    a1, a2 = seg
    total = 0.0
    for j, lb in enumerate(labels):
        if j == exclude_idx:
            continue
        total += _aabb_seg_penetration(lb, a1, a2, margin)
    return total


def _is_polygon_target(t):
    return (isinstance(t, (list, tuple)) and len(t) > 0 and
            isinstance(t[0], (list, tuple)))


def _polygon_centroid(poly):
    xs = [p[0] for p in poly]
    ys = [p[1] for p in poly]
    return (sum(xs) / len(xs), sum(ys) / len(ys))


def _route_leaders(labels):
    order = sorted(
        range(len(labels)),
        key=lambda i: 1 if _is_polygon_target(labels[i].target) else 0,
    )

    prior_segs = []
    prior_endpoints = []
    routed = {}

    for i in order:
        lb = labels[i]

        if lb.target is None:
            candidates = [lb.preferred]
            center = lb.preferred
        elif _is_polygon_target(lb.target):
            candidates = _polygon_interior_candidates(lb.target)
            if not candidates:
                continue
            center = _polygon_centroid(lb.target)
        else:
            candidates = [tuple(lb.target)]
            center = tuple(lb.target)

        # The leader's tail lands on the VISUAL edge of the text,
        # not on the larger collision box, so the tail meets the
        # glyphs with no gap.
        vis_hw = lb.hw
        vis_hh = lb.size * VISUAL_TEXT_HH_FACT

        best_anchor = None
        best_seg = None
        best_score = float("inf")

        for anchor_pt in candidates:
            seg = _leader_segment(lb.pos, vis_hw, vis_hh, anchor_pt)
            if seg is None:
                cost = _CENTER_W * math.hypot(anchor_pt[0] - center[0],
                                              anchor_pt[1] - center[1])
                if cost < best_score:
                    best_score = cost
                    best_anchor = anchor_pt
                    best_seg = None
                continue

            cross = _count_crossings(seg, prior_segs)
            ep_pen = _endpoint_proximity(seg, prior_endpoints)
            lb_pen = _label_box_penetration_length(
                seg, labels, i, LEADER_LABEL_MARGIN)
            anc_pen = _anchor_segment_proximity(
                anchor_pt, prior_segs, _ANCHOR_SEP)
            c_dist = math.hypot(anchor_pt[0] - center[0],
                                anchor_pt[1] - center[1])
            l_len = math.hypot(anchor_pt[0] - lb.pos[0],
                               anchor_pt[1] - lb.pos[1])

            score = (cross    * _CROSS_W
                     + lb_pen * _LABEL_HIT_W
                     + ep_pen * _ENDPOINT_W
                     + anc_pen * _ANCHOR_W
                     + c_dist * _CENTER_W
                     + l_len  * _LENGTH_W)
            if score < best_score:
                best_score = score
                best_anchor = anchor_pt
                best_seg = seg

        if best_anchor is None:
            continue
        routed[i] = (best_anchor, best_seg)
        if best_seg is not None:
            prior_segs.append(best_seg)
            prior_endpoints.append(best_anchor)

    return routed


def _leaders_settled(a, b):
    if set(a.keys()) != set(b.keys()):
        return False
    for k in a:
        pa, _ = a[k]
        pb, _ = b[k]
        if pa is None and pb is None:
            continue
        if pa is None or pb is None:
            return False
        if abs(pa[0] - pb[0]) > 1.0 or abs(pa[1] - pb[1]) > 1.0:
            return False
    return True


def _leader_tip_violation(li, labels, leaders, arrow_size, gap):
    """Return (mag, ux, uy) where (ux, uy) is the unit vector from the
    leader's tip to the label's center, and `mag` is how far short of
    the desired clearance the label currently is.  Zero when there
    is no violation — the label has no leader, its tip is a dot (a
    dot is anchored inside the shape it labels and is small), or the
    clearance is already satisfied.

    Desired distance: the label's visual tail distance along the
    tip→label direction, PLUS the arrowhead's length, PLUS `gap`.
    The visual tail distance is the ray-AABB boundary distance with
    the visual half-extents (hw, size × VISUAL_TEXT_HH_FACT) — the
    same formula `_leader_segment` uses to place the tail.  So the
    arrowhead's back end (at `tip + arrow_size` along tip→tail)
    clears the visual text edge by `gap`."""
    lb = labels[li]
    if lb.tip != "arrow" or not leaders:
        return (0.0, 0.0, 0.0)
    entry = leaders.get(li)
    if entry is None:
        return (0.0, 0.0, 0.0)
    tip, seg = entry
    if tip is None or seg is None:
        return (0.0, 0.0, 0.0)
    dx = lb.pos[0] - tip[0]
    dy = lb.pos[1] - tip[1]
    d = math.hypot(dx, dy)
    if d < 1e-6:
        return (0.0, 1.0, 1.0)
    ux = dx / d
    uy = dy / d
    vis_hw = lb.hw
    vis_hh = lb.size * VISUAL_TEXT_HH_FACT
    t_x = vis_hw / abs(ux) if abs(ux) > 1e-9 else float("inf")
    t_y = vis_hh / abs(uy) if abs(uy) > 1e-9 else float("inf")
    d_tail = min(t_x, t_y)
    need = d_tail + arrow_size + gap
    if d >= need:
        return (0.0, 0.0, 0.0)
    return (need - d, ux, uy)


# ============================================================================
# Scoring primitives
# ============================================================================

def _pair_overlap_area(a, b, margin):
    dx = abs(b.pos[0] - a.pos[0])
    dy = abs(b.pos[1] - a.pos[1])
    ox = (a.hw + b.hw + margin) - dx
    oy = (a.hh + b.hh + margin) - dy
    if ox > 0.0 and oy > 0.0:
        return ox * oy
    return 0.0


def _aabb_seg_penetration(a, p1, p2, margin):
    x0 = a.pos[0] - a.hw - margin
    x1 = a.pos[0] + a.hw + margin
    y0 = a.pos[1] - a.hh - margin
    y1 = a.pos[1] + a.hh + margin

    sx, sy = p1
    ex, ey = p2
    dx = ex - sx
    dy = ey - sy

    t0, t1 = 0.0, 1.0
    for p, q in [(-dx, sx - x0), (dx, x1 - sx),
                 (-dy, sy - y0), (dy, y1 - sy)]:
        if abs(p) < 1e-12:
            if q < 0.0:
                return 0.0
            continue
        t = q / p
        if p < 0.0:
            if t > t1:
                return 0.0
            if t > t0:
                t0 = t
        else:
            if t < t0:
                return 0.0
            if t < t1:
                t1 = t
    if t1 <= t0:
        return 0.0
    return math.hypot(dx * (t1 - t0), dy * (t1 - t0))


def _label_overlap_score(li, labels, obstacles, margin, leaders=None,
                         arrow_size=80.0):
    """Total cost of a label's current position.

    Obstacles in the label's `ignored_obstacles` set (both orientations)
    are skipped.  A label whose own arrow tip sits too close to its own
    center pays a squared-shortfall penalty, so every relaxation pass
    pushes the label outward along the tip→label direction until the
    arrowhead clears the text."""
    a = labels[li]
    score = 0.0
    ignore = getattr(a, "ignored_obstacles", None) or set()

    for j, b in enumerate(labels):
        if j == li:
            continue
        score += _pair_overlap_area(a, b, margin)

    for obs in obstacles:
        if obs in ignore or (obs[1], obs[0]) in ignore:
            continue
        score += _aabb_seg_penetration(a, obs[0], obs[1], margin) \
                 * OBSTACLE_PENETRATION_W

    if leaders:
        for lidx, (anchor, seg) in leaders.items():
            if lidx == li or seg is None:
                continue
            pen = _aabb_seg_penetration(a, seg[0], seg[1],
                                        LEADER_LABEL_MARGIN)
            score += pen * LEADER_PENETRATION_W

        # Own-leader tip clearance.
        mag, _ux, _uy = _leader_tip_violation(
            li, labels, leaders, arrow_size, _LEADER_TIP_GAP)
        score += (mag * mag) * _LEADER_CLEAR_W

    return score


def _has_any_overlap(labels, margin):
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            if _pair_overlap_area(labels[i], labels[j], margin) > 0.5:
                return True
    return False


# ============================================================================
# Relaxation — pass 1: force
# ============================================================================

def _force_relax(labels, obstacles, margin):
    n = len(labels)
    if not n:
        return
    for _ in range(LAYOUT_ITERATIONS):
        fx = [0.0] * n
        fy = [0.0] * n
        for i, l in enumerate(labels):
            fx[i] += (l.preferred[0] - l.pos[0]) * LAYOUT_SPRING
            fy[i] += (l.preferred[1] - l.pos[1]) * LAYOUT_SPRING
        for i in range(n):
            a = labels[i]
            for j in range(i + 1, n):
                b = labels[j]
                dx = b.pos[0] - a.pos[0]
                dy = b.pos[1] - a.pos[1]
                ox = (a.hw + b.hw + margin) - abs(dx)
                oy = (a.hh + b.hh + margin) - abs(dy)
                if ox > 0 and oy > 0:
                    if ox < oy:
                        s = 1.0 if dx >= 0 else -1.0
                        push = ox * LAYOUT_REPULSE
                        fx[i] -= s * push
                        fx[j] += s * push
                    else:
                        s = 1.0 if dy >= 0 else -1.0
                        push = oy * LAYOUT_REPULSE
                        fy[i] -= s * push
                        fy[j] += s * push
        for i, l in enumerate(labels):
            r = max(l.hw, l.hh)
            limit = r + margin
            ignore = getattr(l, "ignored_obstacles", None) or set()
            for obs in obstacles:
                if obs in ignore or (obs[1], obs[0]) in ignore:
                    continue
                p1, p2 = obs
                d, closest = _seg_point_dist(p1, p2, l.pos)
                if 1e-6 < d < limit:
                    ux = (l.pos[0] - closest[0]) / d
                    uy = (l.pos[1] - closest[1]) / d
                    push = (limit - d) * LAYOUT_WALL_PUSH
                    fx[i] += ux * push
                    fy[i] += uy * push
        max_move = 0.0
        for i, l in enumerate(labels):
            l.pos[0] += fx[i]
            l.pos[1] += fy[i]
            m = abs(fx[i]) + abs(fy[i])
            if m > max_move:
                max_move = m
        if max_move < 0.01:
            break


# ============================================================================
# Relaxation — pass 2: discrete push
# ============================================================================

def _discrete_push(labels, obstacles, margin, leaders=None,
                   arrow_size=80.0):
    candidates = []
    for s in DISCRETE_STEPS:
        for dx, dy in _DISCRETE_DIRS:
            candidates.append((s * dx, s * dy))

    for _ in range(DISCRETE_ROUNDS):
        moved = False
        for li, a in enumerate(labels):
            before = _label_overlap_score(
                li, labels, obstacles, margin, leaders=leaders,
                arrow_size=arrow_size)
            if before <= 0.0:
                continue
            ox, oy = a.pos[0], a.pos[1]
            best_x, best_y = ox, oy
            best_n = before
            for dx, dy in candidates:
                a.pos[0] = ox + dx
                a.pos[1] = oy + dy
                n = _label_overlap_score(
                    li, labels, obstacles, margin, leaders=leaders,
                    arrow_size=arrow_size)
                if n < best_n - DISCRETE_EPS:
                    best_n = n
                    best_x = a.pos[0]
                    best_y = a.pos[1]
            a.pos[0] = best_x
            a.pos[1] = best_y
            if best_x != ox or best_y != oy:
                moved = True
        if not moved:
            break


# ============================================================================
# Relaxation — pass 3: coordinated pair moves
# ============================================================================

def _coordinated_pair_moves(labels, obstacles, margin, leaders=None,
                            arrow_size=80.0):
    for _ in range(PAIR_ROUNDS):
        pairs = []
        n = len(labels)
        for i in range(n):
            for j in range(i + 1, n):
                area = _pair_overlap_area(labels[i], labels[j], margin)
                if area > 0.5:
                    pairs.append((area, i, j))
        if not pairs:
            break
        pairs.sort(reverse=True)

        improved = False
        for _, i, j in pairs[:PAIR_MAX_TRIED]:
            a, b = labels[i], labels[j]
            ax0, ay0 = a.pos[0], a.pos[1]
            bx0, by0 = b.pos[0], b.pos[1]

            best_score = (
                _label_overlap_score(i, labels, obstacles, margin,
                                     leaders=leaders,
                                     arrow_size=arrow_size) +
                _label_overlap_score(j, labels, obstacles, margin,
                                     leaders=leaders,
                                     arrow_size=arrow_size)
            )
            best = (ax0, ay0, bx0, by0)

            for s in PAIR_STEPS:
                for dx, dy in _PAIR_DIRS:
                    a.pos[0] = ax0 + dx * s
                    a.pos[1] = ay0 + dy * s
                    b.pos[0] = bx0 - dx * s
                    b.pos[1] = by0 - dy * s
                    score = (
                        _label_overlap_score(i, labels, obstacles, margin,
                                             leaders=leaders,
                                             arrow_size=arrow_size) +
                        _label_overlap_score(j, labels, obstacles, margin,
                                             leaders=leaders,
                                             arrow_size=arrow_size)
                    )
                    if score < best_score - DISCRETE_EPS:
                        best_score = score
                        best = (a.pos[0], a.pos[1],
                                b.pos[0], b.pos[1])

            a.pos[0], a.pos[1], b.pos[0], b.pos[1] = best
            if best != (ax0, ay0, bx0, by0):
                improved = True

        if not improved:
            break


# ============================================================================
# Relaxation — pass 4: gentle retract
# ============================================================================

def _gentle_retract(labels, obstacles, margin, leaders=None,
                    arrow_size=80.0):
    for _ in range(RETRACT_ITERATIONS):
        for li, a in enumerate(labels):
            px, py = a.preferred
            cx, cy = a.pos[0], a.pos[1]
            if abs(px - cx) < 1.0 and abs(py - cy) < 1.0:
                continue

            current_score = _label_overlap_score(
                li, labels, obstacles, margin, leaders=leaders,
                arrow_size=arrow_size)

            lo, hi = 0.0, 1.0
            for _ in range(RETRACT_BISECT):
                mid = (lo + hi) / 2.0
                a.pos[0] = cx + (px - cx) * mid
                a.pos[1] = cy + (py - cy) * mid
                score = _label_overlap_score(
                    li, labels, obstacles, margin, leaders=leaders,
                    arrow_size=arrow_size)
                if score > current_score + 0.01:
                    hi = mid
                else:
                    lo = mid

            a.pos[0] = cx + (px - cx) * lo
            a.pos[1] = cy + (py - cy) * lo


# ============================================================================
# Relaxation — orchestrator
# ============================================================================

def relax_labels(labels, obstacles, arrow_size=80.0):
    if not labels:
        return {}

    _force_relax(labels, obstacles, LAYOUT_MARGIN)
    _discrete_push(labels, obstacles, LAYOUT_MARGIN)
    _coordinated_pair_moves(labels, obstacles, LAYOUT_MARGIN)
    _gentle_retract(labels, obstacles, LAYOUT_MARGIN)

    leaders = _route_leaders(labels)
    for _ in range(LEADER_ITERATIONS):
        _discrete_push(labels, obstacles, LAYOUT_MARGIN,
                       leaders=leaders, arrow_size=arrow_size)
        _coordinated_pair_moves(labels, obstacles, LAYOUT_MARGIN,
                                leaders=leaders, arrow_size=arrow_size)
        _gentle_retract(labels, obstacles, LAYOUT_MARGIN,
                        leaders=leaders, arrow_size=arrow_size)

        new_leaders = _route_leaders(labels)
        if _leaders_settled(leaders, new_leaders):
            leaders = new_leaders
            break
        leaders = new_leaders

    for _ in range(RELAX_RETRY_ROUNDS):
        if not _has_any_overlap(labels, LAYOUT_MARGIN):
            break
        _discrete_push(labels, obstacles, LAYOUT_MARGIN,
                       leaders=leaders, arrow_size=arrow_size)
        _coordinated_pair_moves(labels, obstacles, LAYOUT_MARGIN,
                                leaders=leaders, arrow_size=arrow_size)

    leaders = _route_leaders(labels)
    return leaders


# ============================================================================
# Notes block
# ============================================================================

def build_notes_lines(notes_data):
    if not notes_data:
        return []
    pairs = [(T_note(n["label"]), n["value"]) for n in notes_data]
    key_w = max(len(k) for k, _ in pairs)
    body = [f"{k.ljust(key_w)}  {v}" for k, v in pairs]
    title = T("notesTitle")
    width = max([len(title)] + [len(b) for b in body])
    rule = ("=" * len(title)).ljust(width)
    title = title.ljust(width)
    body = [b.ljust(width) for b in body]
    return [title, rule] + body


def place_notes_block(lines, font_size, obstacles, labels):
    if not lines:
        return [], None
    line_h  = font_size * NOTES_LINE_SPACING
    block_w = CHAR_ASPECT * font_size * max(len(l) for l in lines)
    block_h = line_h * len(lines)
    margin  = font_size * NOTES_GAP_FACTOR

    xs, ys = [], []
    for (p1, p2) in obstacles:
        xs.append(p1[0]); xs.append(p2[0])
        ys.append(p1[1]); ys.append(p2[1])
    for lb in labels:
        xs.append(lb.pos[0] - lb.hw); xs.append(lb.pos[0] + lb.hw)
        ys.append(lb.pos[1] - lb.hh); ys.append(lb.pos[1] + lb.hh)
    if not xs:
        return [], None
    bx0, by0, bx1, by1 = min(xs), min(ys), max(xs), max(ys)

    gx0 = bx0 - 2 * (block_w + margin)
    gy0 = by0 - 2 * (block_h + margin)
    gx1 = bx1 + 2 * (block_w + margin)
    gy1 = by1 + 2 * (block_h + margin)

    min_cell = max(2.0, min(block_w, block_h) / 20.0)
    max_dim  = max(gx1 - gx0, gy1 - gy0, 1.0)
    cell     = max(min_cell, max_dim / NOTES_MAX_CELLS)

    nx = int((gx1 - gx0) / cell) + 1
    ny = int((gy1 - gy0) / cell) + 1
    grid = bytearray(nx * ny)

    def mark(x0, y0, x1, y1):
        ix0 = max(0, int((x0 - gx0) / cell))
        iy0 = max(0, int((y0 - gy0) / cell))
        ix1 = min(nx - 1, int((x1 - gx0) / cell))
        iy1 = min(ny - 1, int((y1 - gy0) / cell))
        for iy in range(iy0, iy1 + 1):
            base = iy * nx
            for ix in range(ix0, ix1 + 1):
                grid[base + ix] = 1

    r = NOTES_LINE_MARGIN
    for (p1, p2) in obstacles:
        mark(min(p1[0], p2[0]) - r, min(p1[1], p2[1]) - r,
             max(p1[0], p2[0]) + r, max(p1[1], p2[1]) + r)
    for lb in labels:
        mark(lb.pos[0] - lb.hw, lb.pos[1] - lb.hh,
             lb.pos[0] + lb.hw, lb.pos[1] + lb.hh)

    stride    = nx + 1
    integral  = [0] * (stride * (ny + 1))
    for iy in range(ny):
        base  = iy * nx
        ibase = (iy + 1) * stride
        pbase = iy * stride
        s = 0
        for ix in range(nx):
            if grid[base + ix]:
                s += 1
            integral[ibase + ix + 1] = integral[pbase + ix + 1] + s

    def rect_sum(ix0, iy0, ix1, iy1):
        return (integral[iy1 * stride + ix1]
                - integral[iy0 * stride + ix1]
                - integral[iy1 * stride + ix0]
                + integral[iy0 * stride + ix0])

    bw_cells = int(block_w / cell) + 1
    bh_cells = int(block_h / cell) + 1
    bcx = (bx0 + bx1) / 2.0
    bcy = (by0 + by1) / 2.0
    bhw = max(1.0, (bx1 - bx0) / 2.0)
    bhh = max(1.0, (by1 - by0) / 2.0)

    best_inside  = None
    best_outside = None
    for iy in range(0, ny - bh_cells + 1):
        for ix in range(0, nx - bw_cells + 1):
            if rect_sum(ix, iy, ix + bw_cells, iy + bh_cells) > 0:
                continue
            cx = gx0 + (ix + bw_cells / 2.0) * cell
            cy = gy0 + (iy + bh_cells / 2.0) * cell
            x0 = cx - block_w / 2.0; x1 = cx + block_w / 2.0
            y0 = cy - block_h / 2.0; y1 = cy + block_h / 2.0
            dx_out = max(0.0, bx0 - x0) + max(0.0, x1 - bx1)
            dy_out = max(0.0, by0 - y0) + max(0.0, y1 - by1)
            expansion = dx_out + dy_out
            nx_off = (cx - bcx) / bhw
            ny_off = (cy - bcy) / bhh
            corner = (nx_off * nx_off + ny_off * ny_off) ** 0.5
            if expansion == 0:
                if best_inside is None or corner > best_inside[0]:
                    best_inside = (corner, cx, cy)
            else:
                key = (expansion, corner)
                if best_outside is None or key < (best_outside[0], best_outside[1]):
                    best_outside = (expansion, corner, cx, cy)

    if best_inside is not None:
        _, cx, cy = best_inside
        vert  = T("notesPlacementTop")   if cy > bcy \
                else T("notesPlacementBottom")
        horiz = T("notesPlacementRight") if cx > bcx \
                else T("notesPlacementLeft")
        name  = T("notesPlacementInside")(vert, horiz)
    elif best_outside is not None:
        _, _, cx, cy = best_outside
        vert  = T("notesPlacementTop")   if cy > bcy \
                else T("notesPlacementBottom")
        horiz = T("notesPlacementRight") if cx > bcx \
                else T("notesPlacementLeft")
        name  = T("notesPlacementOutside")(vert, horiz)
    else:
        cx = bx1 + margin + block_w / 2.0
        cy = by0 - margin - block_h / 2.0
        name = T("notesPlacementFallback")

    out = []
    top_y = cy + block_h / 2.0
    for i, line in enumerate(lines):
        ly = top_y - line_h * (i + 0.5)
        lw = CHAR_ASPECT * font_size * len(line)
        lx = (cx - block_w / 2.0) + lw / 2.0
        out.append(Label(line, (lx, ly), None, font_size))
    return out, name


# ============================================================================
# Layout meta
# ============================================================================

def _serialize_target(t):
    if t is None:
        return None
    if isinstance(t, (list, tuple)) and len(t) > 0 and \
       isinstance(t[0], (list, tuple)):
        return [[round(p[0], 2), round(p[1], 2)] for p in t]
    return [round(t[0], 2), round(t[1], 2)]


def compute_layout_meta(labels, obstacles, leaders, margin, unit_mm,
                        arrow_size=80.0):
    overlaps = []
    n = len(labels)
    for i in range(n):
        for j in range(i + 1, n):
            area = _pair_overlap_area(labels[i], labels[j], margin)
            if area > 0.5:
                overlaps.append({
                    "a":       i,
                    "b":       j,
                    "a_text":  labels[i].text,
                    "b_text":  labels[j].text,
                    "area":    round(area, 3),
                })

    leader_penetrations = []
    for i, lb in enumerate(labels):
        for lidx, (anchor, seg) in (leaders or {}).items():
            if lidx == i or seg is None:
                continue
            pen = _aabb_seg_penetration(lb, seg[0], seg[1],
                                        LEADER_LABEL_MARGIN)
            if pen > 0.5:
                leader_penetrations.append({
                    "label":  i,
                    "text":   lb.text,
                    "leader": lidx,
                    "length": round(pen, 2),
                })

    label_info = []
    for i, lb in enumerate(labels):
        ignore = getattr(lb, "ignored_obstacles", None) or set()

        pair_overlap = 0.0
        per_pair = []
        for j, other in enumerate(labels):
            if i == j:
                continue
            area = _pair_overlap_area(lb, other, margin)
            pair_overlap += area
            if area > 0.5:
                per_pair.append({"with": j, "area": round(area, 3)})

        pen = 0.0
        for obs in obstacles:
            if obs in ignore or (obs[1], obs[0]) in ignore:
                continue
            pen += _aabb_seg_penetration(lb, obs[0], obs[1], margin)

        leader_pen = 0.0
        for lidx, (anchor, seg) in (leaders or {}).items():
            if lidx == i or seg is None:
                continue
            leader_pen += _aabb_seg_penetration(lb, seg[0], seg[1],
                                                LEADER_LABEL_MARGIN)

        dx = lb.pos[0] - lb.preferred[0]
        dy = lb.pos[1] - lb.preferred[1]

        nearest_idx = -1
        nearest_d = float("inf")
        for j, other in enumerate(labels):
            if j == i:
                continue
            d = math.hypot(lb.pos[0] - other.pos[0],
                           lb.pos[1] - other.pos[1])
            if d < nearest_d:
                nearest_d = d
                nearest_idx = j

        leader_info = None
        if leaders and i in leaders:
            anchor, seg = leaders[i]
            if anchor is not None:
                leader_info = {
                    "anchor": [round(anchor[0], 2),
                               round(anchor[1], 2)],
                    "visible": seg is not None,
                }

        # The leader-tip clearance shortfall for this label, if any.
        tip_mag, _ux, _uy = _leader_tip_violation(
            i, labels, leaders, arrow_size, _LEADER_TIP_GAP)

        label_info.append({
            "index":   i,
            "text":    lb.text,
            "size":    round(lb.size, 2),
            "hw":      round(lb.hw, 2),
            "hh":      round(lb.hh, 2),
            "tip":     lb.tip,
            "preferred":  [round(lb.preferred[0], 2),
                           round(lb.preferred[1], 2)],
            "target":     _serialize_target(lb.target),
            "current":    [round(lb.pos[0], 2), round(lb.pos[1], 2)],
            "displacement":     [round(dx, 2), round(dy, 2)],
            "displacement_mag": round(math.hypot(dx, dy), 2),
            "pair_overlap_area":      round(pair_overlap, 3),
            "obstacle_penetration":   round(pen, 3),
            "leader_penetration":     round(leader_pen, 3),
            "leader_tip_clearance":   round(tip_mag, 3),
            "overlaps_with":          per_pair,
            "nearest_label":          nearest_idx,
            "nearest_distance":       round(nearest_d, 2),
            "leader":                 leader_info,
            "ignored_obstacles":      len(ignore),
        })

    total_pair = sum(o["area"] for o in overlaps)
    total_pen  = sum(li["obstacle_penetration"] for li in label_info)
    total_lpen = sum(li["leader_penetration"]  for li in label_info)
    total_clea = sum(li["leader_tip_clearance"] for li in label_info)
    labels_with_overlap = sorted(set(
        [o["a"] for o in overlaps] + [o["b"] for o in overlaps]
    ))

    return {
        "version": 15,
        "unit_mm": unit_mm,
        "config": {
            "layout_margin":           margin,
            "obstacle_penetration_w":  OBSTACLE_PENETRATION_W,
            "leader_penetration_w":    LEADER_PENETRATION_W,
            "leader_label_margin":     LEADER_LABEL_MARGIN,
            "leader_iterations":       LEADER_ITERATIONS,
            "visual_text_hh_fact":     VISUAL_TEXT_HH_FACT,
            "leader_tip_gap":          _LEADER_TIP_GAP,
            "leader_clear_w":          _LEADER_CLEAR_W,
            "anchor_sep":              _ANCHOR_SEP,
            "anchor_w":                _ANCHOR_W,
            "ruler_tilt":              RULER_TILT_UNITS,
            "ruler_min_dim_line":      RULER_MIN_DIM_LINE_UNITS,
            "ruler_short_dim_line":    RULER_SHORT_DIM_LINE_UNITS,
            "ruler_para_trigger":      RULER_PARALLELOGRAM_TRIGGER_UNITS,
            "ruler_para_shift":        RULER_PARALLELOGRAM_SHIFT_UNITS,
            "ruler_ext_gap_shared":    RULER_EXT_GAP_SHARED_UNITS,
            "ruler_shared_vertex_tol": RULER_SHARED_VERTEX_TOL_UNITS,
            "ruler_gap_step":          RULER_GAP_STEP_UNITS,
            "ruler_max_offset":        RULER_MAX_OFFSET_UNITS,
            "ruler_colinear_tol":      RULER_COLINEAR_TOL_UNITS,
            "ruler_parallel_tol":      RULER_PARALLEL_TOL_UNITS,
            "ruler_wall_clearance":    RULER_WALL_CLEARANCE_UNITS,
            "ruler_offset_penalty":    RULER_OFFSET_PENALTY,
            "ruler_shape_tiebreak":    RULER_SHAPE_TIEBREAK,
            "ruler_side_flip_penalty": RULER_SIDE_FLIP_PENALTY,
            "col_dim_crowding":        COL_DIM_CROWDING_UNITS,
            "col_dim_label_penalty":   COL_DIM_LABEL_PENALTY,
            "attach_collinear_tol":    _ATTACH_COLLINEAR_TOL,
            "step_wall_tol":           STEP_WALL_TOL,
            "step_merge_tol":          STEP_MERGE_TOL,
            "discrete_steps":          list(DISCRETE_STEPS),
            "discrete_rounds":         DISCRETE_ROUNDS,
            "pair_steps":              list(PAIR_STEPS),
            "pair_rounds":             PAIR_ROUNDS,
            "retract_iterations":      RETRACT_ITERATIONS,
            "relax_retry_rounds":      RELAX_RETRY_ROUNDS,
            "layout_iterations":       LAYOUT_ITERATIONS,
        },
        "summary": {
            "n_labels":                    len(labels),
            "n_obstacles":                 len(obstacles),
            "n_overlap_pairs":             len(overlaps),
            "n_leader_penetrations":       len(leader_penetrations),
            "total_pair_overlap":          round(total_pair, 3),
            "total_obstacle_penetration":  round(total_pen, 3),
            "total_leader_penetration":    round(total_lpen, 3),
            "total_leader_tip_clearance":  round(total_clea, 3),
            "labels_with_overlap":         labels_with_overlap,
        },
        "labels":              label_info,
        "overlaps":            overlaps,
        "leader_penetrations": leader_penetrations,
    }


# ============================================================================
# Dimension-line geometry
# ============================================================================

def _ticks_at_endpoints(dx1, dy1, dx2, dy2, nx, ny, tick_half):
    ddx = dx2 - dx1
    ddy = dy2 - dy1
    L = math.hypot(ddx, ddy)
    if L < 1e-6:
        return []
    ux, uy = ddx / L, ddy / L
    sx = (ux + nx) / math.sqrt(2)
    sy = (uy + ny) / math.sqrt(2)
    return [
        ((dx1 - sx * tick_half, dy1 - sy * tick_half),
         (dx1 + sx * tick_half, dy1 + sy * tick_half)),
        ((dx2 - sx * tick_half, dy2 - sy * tick_half),
         (dx2 + sx * tick_half, dy2 + sy * tick_half)),
    ]


def _dimension_lines(p1, p2, inward_dir, gap,
                     ext_gap_p1, ext_gap_p2, ext_over, tick_half,
                     tilt=0.0, min_dim_len=0.0,
                     parallelogram_shift=0.0):
    (x1, y1), (x2, y2) = p1, p2
    nx, ny = inward_dir

    dx_w = x2 - x1
    dy_w = y2 - y1
    L = math.hypot(dx_w, dy_w)
    if L < 1e-9:
        return [], [], (x1, y1)
    tx, ty = dx_w / L, dy_w / L

    if parallelogram_shift != 0.0:
        dx1 = x1 + nx * gap + tx * parallelogram_shift
        dy1 = y1 + ny * gap + ty * parallelogram_shift
        dx2 = x2 + nx * gap + tx * parallelogram_shift
        dy2 = y2 + ny * gap + ty * parallelogram_shift
    else:
        if L - 2 * tilt < min_dim_len:
            t_use = max(0.0, (L - min_dim_len) / 2.0)
        else:
            t_use = max(0.0, tilt)
        dx1 = x1 + nx * gap + tx * t_use
        dy1 = y1 + ny * gap + ty * t_use
        dx2 = x2 + nx * gap - tx * t_use
        dy2 = y2 + ny * gap - ty * t_use

    def _ext_dir(px, py, qx, qy):
        ex, ey = qx - px, qy - py
        el = math.hypot(ex, ey)
        if el < 1e-9:
            return nx, ny
        return ex / el, ey / el

    eux1, euy1 = _ext_dir(x1, y1, dx1, dy1)
    eux2, euy2 = _ext_dir(x2, y2, dx2, dy2)

    sx1 = x1 + eux1 * ext_gap_p1
    sy1 = y1 + euy1 * ext_gap_p1
    ex1 = dx1 + eux1 * ext_over
    ey1 = dy1 + euy1 * ext_over

    sx2 = x2 + eux2 * ext_gap_p2
    sy2 = y2 + euy2 * ext_gap_p2
    ex2 = dx2 + eux2 * ext_over
    ey2 = dy2 + euy2 * ext_over

    segs = [
        ((sx1, sy1), (ex1, ey1)),
        ((sx2, sy2), (ex2, ey2)),
        ((dx1, dy1), (dx2, dy2)),
    ]
    ticks = _ticks_at_endpoints(dx1, dy1, dx2, dy2, nx, ny, tick_half)
    mid   = ((dx1 + dx2) / 2.0, (dy1 + dy2) / 2.0)
    return segs, ticks, mid


def _simple_dimension(p1, p2, offset_dir, gap,
                      ext_gap, ext_over, tick_half,
                      tilt=0.0, min_dim_len=0.0):
    return _dimension_lines(p1, p2, offset_dir, gap,
                            ext_gap, ext_gap, ext_over, tick_half,
                            tilt=tilt, min_dim_len=min_dim_len,
                            parallelogram_shift=0.0)


class _Box:
    __slots__ = ("pos", "hw", "hh")
    def __init__(self, pos, hw, hh):
        self.pos = list(pos)
        self.hw  = hw
        self.hh  = hh


def _col_dim_candidate_penalty(segs, ticks, mid, direction,
                               label_hw, label_hh, arrow_size,
                               obstacles, own_edges_set, attached_walls,
                               threshold):
    pen = 0.0
    for s in segs + ticks:
        for obs in obstacles:
            if obs in own_edges_set:
                continue
            if _segments_collinear(s, obs, tol=_ATTACH_COLLINEAR_TOL):
                continue
            d = _seg_seg_dist(s[0], s[1], obs[0], obs[1])
            if d < threshold:
                pen += threshold - d

    offset = (label_hw * abs(direction[0])
              + label_hh * abs(direction[1])
              + arrow_size)
    lx = mid[0] + direction[0] * offset
    ly = mid[1] + direction[1] * offset
    box = _Box((lx, ly), label_hw, label_hh)
    for obs in obstacles:
        if obs in own_edges_set:
            continue
        if obs in attached_walls:
            continue
        if _aabb_seg_penetration(box, obs[0], obs[1], 0.0) > 0.0:
            pen += COL_DIM_LABEL_PENALTY
    return pen


# ============================================================================
# Top-level pipeline
# ============================================================================

def build_layout(geom):
    MM = geom["unit_mm"]

    LABEL_SIZE_MAIN   = LABEL_SIZE_MAIN_UNITS   * MM
    LABEL_SIZE_SMALL  = LABEL_SIZE_SMALL_UNITS  * MM
    LABEL_SIZE_STEP   = LABEL_SIZE_STEP_UNITS   * MM
    LABEL_SIZE_COLUMN = LABEL_SIZE_COLUMN_UNITS * MM
    LABEL_SIZE_NOTES  = LABEL_SIZE_NOTES_UNITS  * MM
    LABEL_SIZE_DIM    = LABEL_SIZE_DIM_UNITS    * MM

    TAG_INSET    = TAG_INSET_UNITS    * MM
    DOOR_INSET   = DOOR_INSET_UNITS   * MM
    DIM_GAP_MAIN  = DIM_GAP_MAIN_UNITS  * MM
    DIM_GAP_SMALL = DIM_GAP_SMALL_UNITS * MM
    COL_DIM_GAP  = COL_DIM_GAP_UNITS  * MM
    ARROW_SIZE   = ARROW_SIZE_UNITS   * MM
    INWARD_PROBE = INWARD_PROBE_UNITS * MM

    EXT_GAP   = EXT_GAP_UNITS   * MM
    EXT_OVER  = EXT_OVER_UNITS  * MM
    TICK_HALF = TICK_HALF_UNITS * MM

    RULER_TILT = RULER_TILT_UNITS * MM

    TAG_OVERRIDES = {k: (v[0] * MM, v[1] * MM)
                     for k, v in TAG_OVERRIDES_UNITS.items()}

    holes = geom["holes"]

    # ---- 1. fixed obstacles: walls --------------------------------
    fixed_obstacles = []
    wall_line_intervals = defaultdict(list)

    for face in geom["planFaces"]:
        outer = face.get("outer", [])
        for i in range(len(outer)):
            p1 = tuple(outer[i])
            p2 = tuple(outer[(i + 1) % len(outer)])
            fixed_obstacles.append((p1, p2))
            lk, lo, hi = _seg_axis_info(p1, p2)
            if lk is not None:
                wall_line_intervals[lk].append((lo, hi))
        for hole in face.get("holes", []):
            for i in range(len(hole)):
                p1 = tuple(hole[i])
                p2 = tuple(hole[(i + 1) % len(hole)])
                fixed_obstacles.append((p1, p2))
                lk, lo, hi = _seg_axis_info(p1, p2)
                if lk is not None:
                    wall_line_intervals[lk].append((lo, hi))

    # Snapshot the wall-only obstacles.  Column-edge erasure scans
    # THIS list (not the growing fixed_obstacles) so a column edge is
    # only erased against actual wall faces, not against a previous
    # column's leftover.
    wall_only_obstacles = list(fixed_obstacles)

    # ---- 2. steps --------------------------------------------------
    col_outline_lines = []
    step_lines_solid  = []
    step_lines_dashed = []
    step_polys     = []
    step_centroids = []
    step_heights   = []

    for st in geom["steps"]:
        outline = [tuple(p) for p in st["outline"]]
        step_polys.append(outline)
        step_heights.append(float(st.get("height_mm", 0.0)))
        cx = sum(p[0] for p in outline) / len(outline)
        cy = sum(p[1] for p in outline) / len(outline)
        step_centroids.append((cx, cy))

    step_edges = []
    for si, st in enumerate(geom["steps"]):
        outline = [tuple(p) for p in st["outline"]]
        h = step_heights[si]
        for i in range(len(outline)):
            p1 = outline[i]
            p2 = outline[(i + 1) % len(outline)]
            lk, lo, hi = _seg_axis_info(p1, p2)
            if lk is None:
                step_lines_dashed.append((p1, p2))
                fixed_obstacles.append((p1, p2))
                continue
            step_edges.append({
                "step_idx": si,
                "line_key": lk,
                "lo": lo, "hi": hi,
                "height": h,
            })

    shared_intervals = defaultdict(list)
    n_e = len(step_edges)
    for i in range(n_e):
        ei = step_edges[i]
        for j in range(i + 1, n_e):
            ej = step_edges[j]
            if ei["step_idx"] == ej["step_idx"]:
                continue
            if ei["line_key"] != ej["line_key"]:
                continue
            if abs(ei["height"] - ej["height"]) > 0.1:
                continue
            lo = max(ei["lo"], ej["lo"])
            hi = min(ei["hi"], ej["hi"])
            if hi > lo + STEP_MERGE_TOL:
                shared_intervals[ei["line_key"]].append((lo, hi))

    for lk in list(shared_intervals.keys()):
        ivs = sorted(shared_intervals[lk])
        merged = [list(ivs[0])]
        for lo, hi in ivs[1:]:
            if lo <= merged[-1][1] + STEP_MERGE_TOL:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        shared_intervals[lk] = [tuple(m) for m in merged]

    for e in step_edges:
        pieces = _subtract_intervals(
            e["lo"], e["hi"],
            shared_intervals.get(e["line_key"], []),
            tol=STEP_MERGE_TOL)
        for (lo, hi) in pieces:
            if hi - lo < STEP_MERGE_TOL:
                continue
            p1, p2 = _points_from_axis(e["line_key"], lo, hi)

            is_wall = False
            for (wlo, whi) in wall_line_intervals.get(e["line_key"], []):
                if min(whi, hi) > max(wlo, lo) + STEP_WALL_TOL:
                    is_wall = True
                    break

            if is_wall:
                step_lines_solid.append((p1, p2))
            else:
                step_lines_dashed.append((p1, p2))
            fixed_obstacles.append((p1, p2))

    # ---- 3. column dims and column outlines ------------------------
    col_dim_lines      = []
    col_dim_ext_lines  = []
    col_dim_labels     = []
    col_dim_ticks      = []
    crowding_threshold = COL_DIM_CROWDING_UNITS * MM

    for col in geom["columns"]:
        x0, y0, x1, y1 = col["box"]

        own_edges = [
            ((x0, y0), (x1, y0)),
            ((x1, y0), (x1, y1)),
            ((x1, y1), (x0, y1)),
            ((x0, y1), (x0, y0)),
        ]
        own_set = set()
        for e in own_edges:
            own_set.add(e)
            own_set.add((e[1], e[0]))

        attached_walls = set()
        for obs in wall_only_obstacles:
            for e in own_edges:
                if _segments_collinear(obs, e, tol=_ATTACH_COLLINEAR_TOL):
                    attached_walls.add(obs)
                    attached_walls.add((obs[1], obs[0]))
                    break

        for e in own_edges:
            lk, lo, hi = _seg_axis_info(e[0], e[1])
            if lk is None:
                col_outline_lines.append(e)
                fixed_obstacles.append(e)
                own_set.add(e)
                own_set.add((e[1], e[0]))
                continue

            subtractions = wall_line_intervals.get(lk, [])
            leftovers = _subtract_intervals(lo, hi, subtractions,
                                            tol=_ATTACH_COLLINEAR_TOL)

            for (plo, phi) in leftovers:
                if phi - plo < _ATTACH_COLLINEAR_TOL:
                    continue
                p1, p2 = _points_from_axis(lk, plo, phi)
                col_outline_lines.append((p1, p2))
                fixed_obstacles.append((p1, p2))
                own_set.add((p1, p2))
                own_set.add((p2, p1))

        w_text = f"{(x1 - x0) / MM:g}"
        w_hw = CHAR_ASPECT * LABEL_SIZE_DIM * len(w_text) / 2.0
        w_hh = LABEL_HEIGHT_FACT * LABEL_SIZE_DIM

        h_text = f"{(y1 - y0) / MM:g}"
        h_hw = CHAR_ASPECT * LABEL_SIZE_DIM * len(h_text) / 2.0
        h_hh = LABEL_HEIGHT_FACT * LABEL_SIZE_DIM

        w_candidates = [
            ((x0, y0), (x1, y0), (0, -1)),
            ((x0, y1), (x1, y1), (0, +1)),
        ]
        h_candidates = [
            ((x1, y0), (x1, y1), (1, 0)),
            ((x0, y0), (x0, y1), (-1, 0)),
        ]

        def _pick(candidates, lhw, lhh):
            best = None
            best_pen = None
            for (p1, p2, direction) in candidates:
                segs, ticks, mid = _simple_dimension(
                    p1, p2, direction, COL_DIM_GAP,
                    EXT_GAP, EXT_OVER, TICK_HALF,
                    tilt=0.0, min_dim_len=0.0)
                pen = _col_dim_candidate_penalty(
                    segs, ticks, mid, direction,
                    lhw, lhh, ARROW_SIZE,
                    fixed_obstacles, own_set, attached_walls,
                    crowding_threshold)
                if best_pen is None or pen < best_pen - 1e-9:
                    best_pen = pen
                    best = (segs, ticks, mid, direction)
            return best

        w_segs, w_ticks, w_mid, w_dir = _pick(w_candidates, w_hw, w_hh)
        col_dim_lines.append(w_segs[2])
        col_dim_ext_lines.append(w_segs[0])
        col_dim_ext_lines.append(w_segs[1])
        col_dim_ticks += w_ticks
        for s in w_segs + w_ticks:
            fixed_obstacles.append(s)
        w_label = _dim_label(w_text, w_mid, LABEL_SIZE_DIM,
                             w_dir, ARROW_SIZE)
        w_label.ignored_obstacles = set(attached_walls) | set(own_set)
        col_dim_labels.append(w_label)

        h_segs, h_ticks, h_mid, h_dir = _pick(h_candidates, h_hw, h_hh)
        col_dim_lines.append(h_segs[2])
        col_dim_ext_lines.append(h_segs[0])
        col_dim_ext_lines.append(h_segs[1])
        col_dim_ticks += h_ticks
        for s in h_segs + h_ticks:
            fixed_obstacles.append(s)
        h_label = _dim_label(h_text, h_mid, LABEL_SIZE_DIM,
                             h_dir, ARROW_SIZE)
        h_label.ignored_obstacles = set(attached_walls) | set(own_set)
        col_dim_labels.append(h_label)

    # ---- 4. build and resolve rulers -------------------------------
    rulers = []
    for d in geom["dimensions"]:
        p1 = tuple(d["p1"])
        p2 = tuple(d["p2"])
        gap = DIM_GAP_MAIN if d.get("kind") == "main" else DIM_GAP_SMALL
        normal = inward_normal_via_holes(p1, p2, holes, INWARD_PROBE)
        rulers.append(_Ruler(d["tag"], d.get("kind", "main"),
                             p1, p2, normal, gap, RULER_TILT,
                             d["value"]))

    shared_tol = RULER_SHARED_VERTEX_TOL_UNITS * MM
    _detect_shared_vertices(rulers, shared_tol)

    ext_gap_shared = RULER_EXT_GAP_SHARED_UNITS * MM

    placed = _resolve_ruler_gaps(rulers, fixed_obstacles,
                                 EXT_GAP, EXT_OVER, TICK_HALF,
                                 ext_gap_shared, MM)

    dim_lines_main      = []
    dim_lines_small     = []
    dim_ext_lines_main  = []
    dim_ext_lines_small = []
    dim_ticks           = list(col_dim_ticks)
    obstacles           = list(fixed_obstacles)

    wall_label_positions = {}
    ruler_meta = []
    for ruler, segs, ticks in placed:
        if ruler.kind == "main":
            dim_lines_main.append(segs[2])
            dim_ext_lines_main.append(segs[0])
            dim_ext_lines_main.append(segs[1])
        else:
            dim_lines_small.append(segs[2])
            dim_ext_lines_small.append(segs[0])
            dim_ext_lines_small.append(segs[1])
        dim_ticks += ticks
        for s in segs:
            obstacles.append(s)
        for s in ticks:
            obstacles.append(s)
        mid = ((segs[2][0][0] + segs[2][1][0]) / 2.0,
               (segs[2][0][1] + segs[2][1][1]) / 2.0)
        wall_label_positions[ruler.tag] = {
            "p1": ruler.p1, "p2": ruler.p2,
            "mid": mid,
            "normal": ruler.inward,
            "value": ruler.value,
            "kind":  ruler.kind,
        }
        ruler_meta.append({
            "tag":                     ruler.tag,
            "kind":                    ruler.kind,
            "base_gap":                round(ruler.base_gap, 2),
            "gap":                     round(ruler.gap, 2),
            "tilt":                    round(ruler.tilt, 2),
            "effective_tilt":          round(ruler.effective_tilt, 2),
            "shape":                   ruler.shape,
            "para_shift":              round(ruler.parallelogram_shift, 2),
            "short_wall":              ruler.short_wall,
            "parallelogram_preferred": ruler.parallelogram_preferred,
            "shared_p1":               ruler.shared_p1,
            "shared_p2":               ruler.shared_p2,
            "side_flipped":            ruler.side_flipped,
            "value":                   ruler.value,
        })

    # ---- 5. labels -------------------------------------------------
    labels = []

    for tag, info in wall_label_positions.items():
        p1  = info["p1"]
        p2  = info["p2"]
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        is_small = (info["kind"] == "small")
        size = LABEL_SIZE_SMALL if is_small else LABEL_SIZE_MAIN

        if tag in TAG_OVERRIDES:
            preferred = TAG_OVERRIDES[tag]
        else:
            nx, ny = info["normal"]
            preferred = (mx + nx * TAG_INSET, my + ny * TAG_INSET)

        v = info["value"]
        text = f"{tag} = {v:g}" if isinstance(v, float) else f"{tag} = {v}"
        labels.append(Label(text, preferred, info["mid"], size, tip="arrow"))

    for op in geom["openings"]:
        tag = op["tag"]
        p1  = tuple(op["p1"])
        p2  = tuple(op["p2"])
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        if tag in TAG_OVERRIDES:
            preferred = TAG_OVERRIDES[tag]
        else:
            nx, ny = inward_normal_via_holes(p1, p2, holes, INWARD_PROBE)
            preferred = (mx + nx * DOOR_INSET, my + ny * DOOR_INSET)
        labels.append(Label(tag, preferred, (mx, my), LABEL_SIZE_MAIN,
                            tip="arrow"))

    for col in geom["columns"]:
        x0, y0, x1, y1 = col["box"]
        col_poly = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
        labels.append(Label(col["tag"],
                            ((x0 + x1) / 2, (y0 + y1) / 2),
                            col_poly, LABEL_SIZE_COLUMN, tip="dot"))

    labels += col_dim_labels

    for i, (cx, cy) in enumerate(step_centroids):
        poly = step_polys[i]
        lb = Label("STEP", (cx, cy), poly, LABEL_SIZE_STEP, tip="dot")
        outside = _step_label_position(
            poly, lb.hw, lb.hh, LAYOUT_MARGIN, obstacles)
        lb.preferred = outside
        lb.pos = [outside[0], outside[1]]
        labels.append(lb)

    # ---- 6. relaxation + leader routing ----------------------------
    leaders = relax_labels(labels, obstacles, arrow_size=ARROW_SIZE)

    # ---- 7. notes block --------------------------------------------
    notes_lines = build_notes_lines(geom.get("notes", []))
    notes_labels, notes_placement = place_notes_block(
        notes_lines, LABEL_SIZE_NOTES, obstacles, labels)

    # ---- 8. meta ---------------------------------------------------
    meta = compute_layout_meta(labels, obstacles, leaders,
                               LAYOUT_MARGIN, MM, arrow_size=ARROW_SIZE)
    meta["rulers"] = ruler_meta

    return {
        "dim_lines_main":      dim_lines_main,
        "dim_lines_small":     dim_lines_small,
        "dim_ext_lines_main":  dim_ext_lines_main,
        "dim_ext_lines_small": dim_ext_lines_small,
        "col_dim_lines":       col_dim_lines,
        "col_dim_ext_lines":   col_dim_ext_lines,
        "dim_ticks":           dim_ticks,
        "col_outline_lines":   col_outline_lines,
        "step_lines_solid":    step_lines_solid,
        "step_lines_dashed":   step_lines_dashed,
        "labels":              labels,
        "notes_labels":        notes_labels,
        "notes_placement":     notes_placement,
        "leaders":             leaders,
        "arrow_size":          ARROW_SIZE,
        "meta":                meta,
    }
