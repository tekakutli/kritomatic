"""
pg_export_geometry.py — pure geometry atoms for the export pipeline.

ES module.  Every function is a pure computation over numbers and
plain point/segment records.  No state, no drawing, no imports beyond
`pg_state.js` for `WALL` / `WALL_HEIGHT`.

Contents:

    pointToSegmentDist / segSegDist / angleDiff
        distance and angle primitives.

    _pointSegDist / _segSegDist / _angleDiff
        underscore-prefixed wrappers used internally and re-exported
        through `pg_export_primitives.js`.

    _pathSegments(path, pathIdx, minLen)
        polyline → flat list of segment records, with collinear
        points merged so that a T-junction touching the interior of a
        merged segment still counts as a crossing.

    _findSegmentConflicts(segs, minSep)
        O(n²) sweep over a segment list, returns pairs closer than
        minSep.

    _segBoxOverlap(segment, {qL, qR, qT, qB})
        Liang–Barsky clipped-overlap length.  Zero when the segment
        does not touch the box.

    _segSegProperCross(a, b) / _countSegmentCrossings(segsA, segsB)
        proper (interior-interior) crossing test and count.  Touches
        and collinear overlaps are *not* proper crossings.

BOUNDS: all distances and lengths are in the same units as the room
model — millimetres for real geometry, strip-pixel units for the
unfolded wall strip.  The atoms never mix the two.
"""

GEO_JS = r"""
import { WALL, WALL_HEIGHT } from "./pg_state.js";

/* ==========================================================================
   BASELINE CONSTANTS (re-exported by `pg_export_primitives.js`)
   ========================================================================== */

/* Horizontal clearance between an anchor-side leader and its two
   neighbours — used by `refineLeaderOffsets` and by the style graph
   to decide whether a leader is "close enough" to conflict. */
export const SEG_MIN_SEP  = 6.0;

/* Minimum segment length used by `_pathSegments`.  Segments shorter
   than this are dropped — they don't contribute meaningfully to the
   conflict or presence counts, and they can cause numerical jitter
   in the leader optimiser. */
export const SEG_MIN_LEN  = 5.0;

/* Pill-vs-segment proximity used by the style graph.  Same value as
   SEG_MIN_SEP by construction; kept as its own name so that tuning
   one does not silently drag the other. */
export const PILL_PROX    = SEG_MIN_SEP;

/* Extra length added to the "double crossing" weight in the optimiser
   so that a path crossing the same obstacle twice is worse than two
   paths crossing it once apiece.  Enormous by design — it dominates
   any other geometric term. */
export const DOUBLE_CROSS_EXTRA_W = 1e11;

/* Wire-collision family — consumed by `_wireOverlapPenalty` in
   `pg_export_penalties.js`.

     WIRE_MIN_SEP       minimum clearance between a leader leg and
                        the cable wire, when they run parallel.
     WIRE_APPROACH_SEP  outer threshold: within this band but past
                        WIRE_MIN_SEP is a soft "approaching" tier
                        (a tiebreaker, not a hard conflict).
     WIRE_PARALLEL_TOL  angle gate: beyond this, the leg counts as
                        crossing rather than running alongside.

   These values are from the original split (pre-refactor).  The
   recovered file lost them entirely; the reconstruct that used low
   estimates (4.0 / 12.0 / π/10) made the optimiser accept layouts
   with leaders hugging the wire too closely. */
export const WIRE_MIN_SEP      = 10.0;
export const WIRE_APPROACH_SEP = 26.0;
export const WIRE_PARALLEL_TOL = Math.PI / 6;

/* Wall-section boundary clearance — consumed by
   `_boundaryOverlapPenalty` in `pg_export_penalties.js`.

   A leader leg running nearly vertically alongside a wall-section
   boundary is penalised when its averaged distance to that boundary
   is below this value.  Value is from the original split; the earlier
   reconstructed 6.0 was half the real one and made the polish loop
   terminate early on layouts the real code would have kept refining. */
export const BOUNDARY_MIN_SEP  = 10.0;

/* Base parallel gate used by the (currently unused) legacy styling
   helpers.  Present in the original split; nothing in the recovered
   optimiser imports it, kept for docstring fidelity. */
export const PARALLEL_TOL     = Math.PI / 12;

/* ==========================================================================
   DISTANCE ATOMS
   ========================================================================== */

export function pointToSegmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const L2 = dx * dx + dy * dy;
  if (L2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / L2;
  t = Math.max(0, Math.min(1, t));
  const qx = ax + t * dx;
  const qy = ay + t * dy;
  return Math.hypot(px - qx, py - qy);
}

export function _pointSegDist(px, py, ax, ay, bx, by) {
  return pointToSegmentDist(px, py, ax, ay, bx, by);
}

export function _segSegDist(ax, ay, bx, by, cx, cy, dx, dy) {
  return Math.min(
    pointToSegmentDist(ax, ay, cx, cy, dx, dy),
    pointToSegmentDist(bx, by, cx, cy, dx, dy),
    pointToSegmentDist(cx, cy, ax, ay, bx, by),
    pointToSegmentDist(dx, dy, ax, ay, bx, by),
  );
}

export function _angleDiff(a, b) {
  let d = Math.abs(a - b) % Math.PI;
  if (d > Math.PI / 2) d = Math.PI - d;
  return d;
}

/* ==========================================================================
   POLYLINE → SEGMENTS
   ========================================================================== */

export function _pathSegments(path, pathIdx, minLen) {
  /* First, collapse consecutive collinear points.

     Two segments that share a vertex and lie on the same line are
     the same drawn line.  Splitting them at the shared vertex turns
     a T-junction — the endpoint of one leader's polyline touching
     the interior of another's — into two endpoint touches, both of
     which `_segSegProperCross` rejects.  The crossing at that touch
     is real: the vertical continues on both sides of the horizontal
     it touches.  Merging the two collinear segments back into one
     restores the interior parameter, and the crossing counts. */
  const pts = [];
  for (let i = 0; i < path.length; i++) {
    const p = path[i];
    if (pts.length >= 2) {
      const a = pts[pts.length - 2];
      const b = pts[pts.length - 1];
      const abx = b[0] - a[0], aby = b[1] - a[1];
      const bcx = p[0] - b[0], bcy = p[1] - b[1];
      const cross = abx * bcy - aby * bcx;
      if (Math.abs(cross) < 1e-3) {
        const dot = abx * bcx + aby * bcy;
        if (dot > 0) { pts.pop(); }
        else         { continue; }
      }
    }
    pts.push(p);
  }

  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const a = pts[i], b = pts[i + 1];
    const dx = b[0] - a[0], dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    if (len < minLen) continue;
    let kind;
    if (Math.abs(dx) < 0.5)      kind = "vert";
    else if (Math.abs(dy) < 0.5) kind = "horiz";
    else                          kind = "diag";
    out.push({
      pathIdx, segIdx: i, kind, len,
      ax: a[0], ay: a[1], bx: b[0], by: b[1],
      minX: Math.min(a[0], b[0]), maxX: Math.max(a[0], b[0]),
      minY: Math.min(a[1], b[1]), maxY: Math.max(a[1], b[1]),
      angle: Math.atan2(dy, dx),
    });
  }
  return out;
}

/* ==========================================================================
   SEGMENT SWEEP
   ========================================================================== */

export function _findSegmentConflicts(segs, minSep) {
  const out = [];
  for (let i = 0; i < segs.length; i++) {
    const A = segs[i];
    for (let j = i + 1; j < segs.length; j++) {
      const B = segs[j];
      if (A.pathIdx === B.pathIdx) continue;
      if (A.maxX + minSep < B.minX) continue;
      if (B.maxX + minSep < A.minX) continue;
      if (A.maxY + minSep < B.minY) continue;
      if (B.maxY + minSep < A.minY) continue;
      const d = _segSegDist(A.ax, A.ay, A.bx, A.by,
                            B.ax, B.ay, B.bx, B.by);
      if (d < minSep) out.push({ a: A, b: B, dist: d });
    }
  }
  return out;
}

/* ==========================================================================
   SEGMENT vs. BOX
   ========================================================================== */

export function _segBoxOverlap(s, box) {
  const x0 = s.ax, y0 = s.ay;
  const dx = s.bx - x0, dy = s.by - y0;
  let t0 = 0, t1 = 1;
  const clip = (p, q) => {
    if (Math.abs(p) < 1e-9) return q >= 0;
    const r = q / p;
    if (p < 0) { if (r > t1) return false; if (r > t0) t0 = r; }
    else       { if (r < t0) return false; if (r < t1) t1 = r; }
    return true;
  };
  if (!clip(-dx, x0 - box.qL)) return 0;
  if (!clip( dx, box.qR - x0)) return 0;
  if (!clip(-dy, y0 - box.qT)) return 0;
  if (!clip( dy, box.qB - y0)) return 0;
  return Math.max(0, t1 - t0);
}

/* ==========================================================================
   PROPER CROSSING
   ==========================================================================
   A "proper" crossing is one where the two segments each contribute
   an interior point — t strictly between 0 and 1 for the first, u
   strictly between 0 and 1 for the second.  Endpoint touches are
   excluded (they are trivially unavoidable when two leaders share an
   anchor), collinear overlaps are excluded (denominator goes to 0),
   and the two 1e-6 epsilons are large enough to survive the DPR-3
   floating-point round-trip that produces the strip-pixel
   coordinates. */

export function _segSegProperCross(a, b) {
  const d1x = a.bx - a.ax, d1y = a.by - a.ay;
  const d2x = b.bx - b.ax, d2y = b.by - b.ay;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return false;
  const rx = b.ax - a.ax, ry = b.ay - a.ay;
  const t = (rx * d2y - ry * d2x) / denom;
  const u = (rx * d1y - ry * d1x) / denom;
  return t > 1e-6 && t < 1 - 1e-6 && u > 1e-6 && u < 1 - 1e-6;
}

export function _countSegmentCrossings(segsA, segsB) {
  let n = 0;
  for (let i = 0; i < segsA.length; i++) {
    const a = segsA[i];
    for (let j = 0; j < segsB.length; j++) {
      const b = segsB[j];
      if (a.maxX + 0.5 < b.minX || b.maxX + 0.5 < a.minX) continue;
      if (a.maxY + 0.5 < b.minY || b.maxY + 0.5 < a.minY) continue;
      if (_segSegProperCross(a, b)) n++;
    }
  }
  return n;
}
"""
