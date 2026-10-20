"""
pg_export_geometry.py — the pure distance and segment-pair atoms.

    SEG_MIN_SEP / SEG_MIN_LEN / PARALLEL_TOL   the three base thresholds
    PILL_PROX                                   minimum clearance to a
                                               foreign pill box
    BOUNDARY_MIN_SEP                            minimum clearance to a
                                               wall-section boundary
    WIRE_MIN_SEP                                minimum clearance to
                                               the cable wire
    WIRE_PARALLEL_TOL                           "alongside the wire"
                                               angle gate
    DOUBLE_CROSS_EXTRA_W                        the score weight of one
                                               extra crossing between a
                                               leader pair already
                                               crossing once
    _pointSegDist / _segSegDist                 distance atoms
    _pathSegments                               polyline → segments
    _angleDiff                                  acute angle
    _findSegmentConflicts                       O(n²) bbox-pre-rejected
                                               sweep
    _segBoxOverlap                              segment vs. AABB
    _segSegProperCross                          do two segments properly
                                               cross (endpoint touches
                                               excluded)?
    _countSegmentCrossings                      how many times do two
                                               segment lists properly
                                               cross?

Double-crossing
---------------
Two leaders crossing once is unavoidable: their anchors are on opposite
sides of the strip and the leader field's whole job is to route between
them.  Two leaders crossing twice means the polylines weave around each
other — which reads as noise, not as routing.

_countSegmentCrossings counts PROPER crossings: a segment from A and a
segment from B intersect at a point strictly interior to both.  Shared
endpoints and collinear overlaps do not count (they are respectively
the anchor-sharing and the parallel-overlap cases handled elsewhere).

The optimiser consumes this per pair: it charges
DOUBLE_CROSS_EXTRA_W for each crossing beyond the first between the
same two leaders, in a tier of its own between the head tier and the
base segment tier.
"""


GEO_JS = r"""
/* ---- Segment collision detection ---- */

const SEG_MIN_SEP  = 6.0;
const SEG_MIN_LEN  = 5.0;

const PILL_PROX = SEG_MIN_SEP;

const BOUNDARY_MIN_SEP = 10.0;
const WIRE_MIN_SEP = 10.0;
const WIRE_APPROACH_SEP = 26.0;
const WIRE_PARALLEL_TOL = Math.PI / 6;

const PARALLEL_TOL = Math.PI / 12;

/* Weight of one extra crossing between two leaders that already cross
   at least once.  Sits between the head tier (1e10) and the parallel
   / boundary tiers (1e11): strong enough to make the optimiser trade
   a second crossing for a first crossing elsewhere, weak enough that
   it never beats avoiding a parallel overlap or a boundary hug. */
const DOUBLE_CROSS_EXTRA_W = 1e11;

function _pointSegDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx*dx + dy*dy;
  if (len2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax)*dx + (py - ay)*dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t*dx), py - (ay + t*dy));
}

function _segSegDist(ax, ay, bx, by, cx, cy, dx, dy) {
  return Math.min(
    _pointSegDist(ax, ay, cx, cy, dx, dy),
    _pointSegDist(bx, by, cx, cy, dx, dy),
    _pointSegDist(cx, cy, ax, ay, bx, by),
    _pointSegDist(dx, dy, ax, ay, bx, by),
  );
}

function _pathSegments(path, pathIdx, minLen) {
  /* First, collapse consecutive collinear points.

     Two segments that share a vertex and lie on the same line are
     the same drawn line.  Splitting them at the shared vertex turns
     a T-junction — the endpoint of one leader's polyline touching
     the interior of another's — into two endpoint touches, both of
     which _segSegProperCross rejects.  The crossing at that touch
     is real: the vertical continues on both sides of the horizontal
     it touches.  Merging the two collinear segments back into one
     restores the interior parameter, and the crossing counts.

     This is the fix for V24 × V27: V27's descent from the channel
     down to the detour band is three consecutive collinear verticals
     at x = 811.45, and V24's channel horizontal at y = 144 touches
     them at their shared vertex (811.45, 144).  Without the merge
     the touch is a pair of endpoint contacts and V24 is classified
     as clean, so the optimiser never tries the diveMode = 2 that
     clears the pair. */
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
        /* a, b, p are collinear.  If b is between a and p (same
           direction), drop b.  If p is between a and b (reverse),
           skip p. */
        const dot = abx * bcx + aby * bcy;
        if (dot > 0) {
          pts.pop();
        } else {
          continue;
        }
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

function _angleDiff(a, b) {
  let d = Math.abs(a - b) % Math.PI;
  if (d > Math.PI / 2) d = Math.PI - d;
  return d;
}

function _findSegmentConflicts(segs, minSep) {
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

function _segBoxOverlap(s, box) {
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

/* ---- Proper segment crossing ----

   Two segments properly cross when their lines intersect at a point
   that is strictly interior to both.  Endpoint touches (shared anchors,
   T-junctions) are excluded by the 1e-6 parameter epsilon on each side,
   and parallel-or-collinear pairs are excluded by the denom check. */
function _segSegProperCross(a, b) {
  const d1x = a.bx - a.ax, d1y = a.by - a.ay;
  const d2x = b.bx - b.ax, d2y = b.by - b.ay;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return false;
  const rx = b.ax - a.ax, ry = b.ay - a.ay;
  const t = (rx * d2y - ry * d2x) / denom;
  const u = (rx * d1y - ry * d1x) / denom;
  return t > 1e-6 && t < 1 - 1e-6 && u > 1e-6 && u < 1 - 1e-6;
}

/* Count proper crossings between two segment lists.  Bbox pre-reject
   skips pairs that cannot possibly cross. */
function _countSegmentCrossings(segsA, segsB) {
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
