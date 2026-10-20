"""
pg_arrows_geometry.py — the segment geometry primitives every scoring
and layout pass sits on.

Eight functions, no state, no side effects:

    _raSegSegDist             minimum distance between two segments
    _raAngleDiff              acute angle between two angles
    _raProjOverlap            overlap of two segments' projections onto
                              the common direction they share
    _raPathSegments           a polyline → a list of segment records
                              with bounding boxes, angle, length
    _raSegmentPairScore       parallel/cross penalty for a pair of
                              segment records
    _raPathPairScore          the same, summed over two polylines
    _raTipClearanceAgainstSegs   tip-to-foreign-segment penalty
    _raSegmentsCross          boolean proper-crossing test

PathPairScore is the workhorse: every pairwise score in the solver is
a single call to it.  The two penalties it returns — parallel and
crossing — are weighted independently by the caller; _raScoreSide
sums them into the side's total, _raCountConflicts splits them into
counts, _raTryReroute evaluates them as a delta.
"""


GEO_JS = r"""
/* ==========================================================================
   SECTION 2 — SEGMENT GEOMETRY
   ========================================================================== */

function _raSegSegDist(ax, ay, bx, by, cx, cy, dx, dy) {
  return Math.min(
    pointToSegmentDist(ax, ay, cx, cy, dx, dy),
    pointToSegmentDist(bx, by, cx, cy, dx, dy),
    pointToSegmentDist(cx, cy, ax, ay, bx, by),
    pointToSegmentDist(dx, dy, ax, ay, bx, by)
  );
}

function _raAngleDiff(a, b) {
  let d = Math.abs(a - b) % Math.PI;
  if (d > Math.PI / 2) d = Math.PI - d;
  return d;
}

function _raProjOverlap(A, B) {
  const lenA = Math.hypot(A.x1 - A.x0, A.y1 - A.y0);
  const lenB = Math.hypot(B.x1 - B.x0, B.y1 - B.y0);
  if (lenA < 1e-6 || lenB < 1e-6) return 0;
  let ux = (A.x1 - A.x0) / lenA + (B.x1 - B.x0) / lenB;
  let uy = (A.y1 - A.y0) / lenA + (B.y1 - B.y0) / lenB;
  const dLen = Math.hypot(ux, uy);
  if (dLen < 1e-6) return 0;
  ux /= dLen; uy /= dLen;
  const a0 = A.x0 * ux + A.y0 * uy, a1 = A.x1 * ux + A.y1 * uy;
  const b0 = B.x0 * ux + B.y0 * uy, b1 = B.x1 * ux + B.y1 * uy;
  const aLo = Math.min(a0, a1), aHi = Math.max(a0, a1);
  const bLo = Math.min(b0, b1), bHi = Math.max(b0, b1);
  return Math.max(0, Math.min(aHi, bHi) - Math.max(aLo, bLo));
}

function _raPathSegments(pts) {
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const dx = x1 - x0, dy = y1 - y0;
    const len = Math.hypot(dx, dy);
    if (len < 2.0) continue;
    out.push({
      x0, y0, x1, y1,
      minX: Math.min(x0, x1), maxX: Math.max(x0, x1),
      minY: Math.min(y0, y1), maxY: Math.max(y0, y1),
      angle: Math.atan2(dy, dx),
      len,
    });
  }
  return out;
}

function _raSegmentPairScore(a, b) {
  if (a.maxX + ROUTE_MIN_SEP < b.minX) return { p: 0, c: 0 };
  if (b.maxX + ROUTE_MIN_SEP < a.minX) return { p: 0, c: 0 };
  if (a.maxY + ROUTE_MIN_SEP < b.minY) return { p: 0, c: 0 };
  if (b.maxY + ROUTE_MIN_SEP < a.minY) return { p: 0, c: 0 };
  const d = _raSegSegDist(a.x0, a.y0, a.x1, a.y1,
                          b.x0, b.y0, b.x1, b.y1);
  if (d >= ROUTE_MIN_SEP) return { p: 0, c: 0 };
  if (_raProjOverlap(a, b) < ROUTE_MIN_OVERLAP) return { p: 0, c: 0 };
  const ang = _raAngleDiff(a.angle, b.angle);
  if (ang <= ROUTE_PARALLEL_TOL) {
    return { p: (ROUTE_MIN_SEP - d) * ROUTE_PARALLEL_W, c: 0 };
  }
  if (ang < Math.PI / 4) {
    return { p: (ROUTE_MIN_SEP - d) * ROUTE_MILD_W, c: 0 };
  }
  return { p: 0, c: (ROUTE_MIN_SEP - d) * ROUTE_CROSS_W };
}

function _raPathPairScore(ptsA, ptsB) {
  const A = _raPathSegments(ptsA);
  const B = _raPathSegments(ptsB);
  let p = 0, c = 0;
  for (const a of A) {
    for (const b of B) {
      const r = _raSegmentPairScore(a, b);
      p += r.p; c += r.c;
    }
  }
  return { p, c, total: p + c };
}

function _raTipClearanceAgainstSegs(tipX, tipY, segs) {
  let penalty = 0;
  for (const s of segs) {
    if (tipX + ROUTE_TIP_CLEARANCE < s.minX) continue;
    if (s.maxX + ROUTE_TIP_CLEARANCE < tipX) continue;
    if (tipY + ROUTE_TIP_CLEARANCE < s.minY) continue;
    if (s.maxY + ROUTE_TIP_CLEARANCE < tipY) continue;
    const d = pointToSegmentDist(tipX, tipY, s.x0, s.y0, s.x1, s.y1);
    if (d < ROUTE_TIP_CLEARANCE) {
      const t = ROUTE_TIP_CLEARANCE - d;
      penalty += t * ROUTE_TIP_W + t * t * ROUTE_TIP_QUAD_W;
    }
  }
  return penalty;
}

function _raSegmentsCross(a, b) {
  const eps = 0.5;
  if (Math.abs(a.x0 - b.x0) < eps && Math.abs(a.y0 - b.y0) < eps) return false;
  if (Math.abs(a.x0 - b.x1) < eps && Math.abs(a.y0 - b.y1) < eps) return false;
  if (Math.abs(a.x1 - b.x0) < eps && Math.abs(a.y1 - b.y0) < eps) return false;
  if (Math.abs(a.x1 - b.x1) < eps && Math.abs(a.y1 - b.y1) < eps) return false;

  const d1x = a.x1 - a.x0, d1y = a.y1 - a.y0;
  const d2x = b.x1 - b.x0, d2y = b.y1 - b.y0;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return false;

  const t = ((b.x0 - a.x0) * d2y - (b.y0 - a.y0) * d2x) / denom;
  const u = ((b.x0 - a.x0) * d1y - (b.y0 - a.y0) * d1x) / denom;
  return t > 1e-6 && t < 1 - 1e-6 && u > 1e-6 && u < 1 - 1e-6;
}
"""
