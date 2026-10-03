"""
pg_export_penalties.py — the four tier-list penalty functions.

Each one is a pure query: it takes the leader segments (or the placed
list, for the corner feature) plus its own obstacle set, and returns
a { count, depth } pair or the parallel/crossing split.

    _boundaryOverlapPenalty   a leader leg running alongside a
                              wall-section boundary.  Angle gate is
                              ±45° from vertical; threshold
                              BOUNDARY_MIN_SEP = 10 px.

    _wireOverlapPenalty       a leader leg running alongside or
                              crossing the cable wire.  Two hard
                              sub-tiers and one soft sub-tier:

                                hard parallel   within WIRE_MIN_SEP,
                                                angle within
                                                WIRE_PARALLEL_TOL
                                hard crossing   within SEG_MIN_SEP
                                                (any angle)
                                soft parallel   within
                                                WIRE_APPROACH_SEP,
                                                angle within
                                                WIRE_PARALLEL_TOL.
                                                Tiebreaker weight.

    _wallEdgeOverlapPenalty   a near-horizontal leader leg running
                              alongside a wall chunk's top or bottom
                              z-edge.  Max-distance criterion.

    _cornerFeature / _cornerDist
                              the bevel-cut region of each leader.

Anchor-proximity exemption — the guard is on segment LENGTH
-----------------------------------------------------------
A leader's anchor sits on the wire by construction, so a plain
seg-seg distance from a leader segment to the wire always reports
zero when the leader touches its own anchor.

The exemption is: if the leader segment has an endpoint at the
anchor (within WIRE_ANCHOR_TRIM), use the OTHER endpoint's distance
to the wire instead of the seg-seg distance.  This distinguishes
"touches the wire at the anchor, then diverges" (large far-end
distance, no conflict) from "runs alongside the wire away from
the anchor" (small far-end distance, conflict).

An earlier revision added a second guard — skip if the far endpoint
is also within WIRE_ANCHOR_TRIM.  That was wrong: it re-exempted the
exact case the far-endpoint rule exists to detect (a long segment
from the anchor to a point 6 px off the wire is a genuine hug, not a
degenerate stub).  The correct stub guard is on the segment's own
LENGTH: a segment shorter than WIRE_ANCHOR_TRIM that also touches
the anchor is a degenerate stub and is skipped; anything longer is
evaluated by its far endpoint.

The tier weights themselves live in evaluate() in
pg_export_optimizer.py, not here.  This module computes the raw
counts and depths.
"""


PENALTIES_JS = r"""
/* ---- Wall-section boundary collision ----

   The boundary is a vertical line — the left or right edge of a wall
   chunk.  A leader running alongside it must keep BOUNDARY_MIN_SEP
   of air, and the angle gate admits segments up to ±45° from
   vertical.  The average-distance criterion (not max) is what makes
   a slanted leader count when it stays close over a stretch: an
   exactly-vertical leader has a single constant distance, and a
   30°-from-vertical leader varies over its length but its average
   is still small when the whole segment hugs the boundary. */
function _boundaryOverlapPenalty(leaderSegs, boundaries, stripH) {
  let count = 0, depth = 0;
  if (!boundaries || !boundaries.length) return { count, depth };

  /* Slope gate: catch any leader segment within 45° of vertical.
     A vertical segment has slope |dx/dy| = 0; a 45° segment has
     slope 1.0.  Beyond that the segment reads as diagonal, not as
     running alongside a vertical line. */
  const SLOPE_MAX = 1.0;

  for (const s of leaderSegs) {
    const dy = s.by - s.ay;
    if (Math.abs(dy) < 1e-6) continue;
    const slope = Math.abs((s.bx - s.ax) / dy);
    if (slope > SLOPE_MAX) continue;

    const yLo = Math.max(s.minY, 0);
    const yHi = Math.min(s.maxY, stripH);
    if (yHi - yLo < SEG_MIN_LEN) continue;

    const dx = s.bx - s.ax;
    for (const bx of boundaries) {
      if (s.maxX + BOUNDARY_MIN_SEP < bx) continue;
      if (s.minX - BOUNDARY_MIN_SEP > bx) continue;

      let sumDist = 0;
      const N = 8;
      for (let k = 0; k <= N; k++) {
        const y = yLo + (yHi - yLo) * k / N;
        const t = (y - s.ay) / dy;
        const x = s.ax + dx * t;
        sumDist += Math.abs(x - bx);
      }
      const avgDist = sumDist / (N + 1);
      if (avgDist < BOUNDARY_MIN_SEP) {
        count++;
        depth += (BOUNDARY_MIN_SEP - avgDist);
      }
    }
  }
  return { count, depth };
}

/* ---- Cable-wire collision ----

   See the module docstring for the anchor-proximity rule.  The
   stub guard is on segment LENGTH, not on the far endpoint's
   distance to the wire: a segment shorter than WIRE_ANCHOR_TRIM
   that touches the anchor is a genuine stub and is skipped; a
   longer segment from the anchor to a point 6 px off the wire is
   exactly the "leader hugs the wire" case and MUST fire. */
function _wireOverlapPenalty(leaderSegs, placed, wireSegments) {
  let count = 0, depth = 0;
  let parCount = 0, parDepth = 0;
  let approachCount = 0, approachDepth = 0;
  if (!wireSegments || !wireSegments.length)
    return { count, depth, parCount, parDepth,
             approachCount, approachDepth };

  for (const s of leaderSegs) {
    const it = placed[s.pathIdx];
    if (!it) continue;
    const ax = it.anchorCx;
    const ay = it.anchorCyRel;
    const sAngle = s.angle;

    /* Distance from the leader segment's endpoints to the anchor. */
    const dA = Math.hypot(s.ax - ax, s.ay - ay);
    const dB = Math.hypot(s.bx - ax, s.by - ay);
    const nearAnchor = Math.min(dA, dB) < WIRE_ANCHOR_TRIM;
    const farPt = dA > dB ? [s.ax, s.ay] : [s.bx, s.by];

    /* Stub guard: a leader segment that touches the anchor AND is
       shorter than WIRE_ANCHOR_TRIM is degenerate.  Skip it.  Any
       longer segment is evaluated normally, even if its far
       endpoint happens to be close to the wire — that IS the case
       this penalty exists to detect. */
    const segLen = Math.hypot(s.bx - s.ax, s.by - s.ay);
    if (nearAnchor && segLen < WIRE_ANCHOR_TRIM) continue;

    for (const w of wireSegments) {
      const wminX = Math.min(w.ax, w.bx);
      const wmaxX = Math.max(w.ax, w.bx);
      const wminY = Math.min(w.ay, w.by);
      const wmaxY = Math.max(w.ay, w.by);

      if (s.maxX + WIRE_APPROACH_SEP < wminX) continue;
      if (wmaxX + WIRE_APPROACH_SEP < s.minX) continue;
      if (s.maxY + WIRE_APPROACH_SEP < wminY) continue;
      if (wmaxY + WIRE_APPROACH_SEP < s.minY) continue;

      let d;
      if (nearAnchor) {
        d = _pointSegDist(farPt[0], farPt[1], w.ax, w.ay, w.bx, w.by);
      } else {
        d = _segSegDist(s.ax, s.ay, s.bx, s.by,
                        w.ax, w.ay, w.bx, w.by);
      }

      const wAngle = Math.atan2(w.by - w.ay, w.bx - w.ax);
      const angDiff = _angleDiff(sAngle, wAngle);

      if (angDiff < WIRE_PARALLEL_TOL && d < WIRE_MIN_SEP) {
        /* hard parallel tier */
        parCount++;
        parDepth += (WIRE_MIN_SEP - d);
        count++;
        depth += (WIRE_MIN_SEP - d);
      } else if (angDiff < WIRE_PARALLEL_TOL && d < WIRE_APPROACH_SEP) {
        /* soft parallel tier */
        approachCount++;
        approachDepth += (WIRE_APPROACH_SEP - d);
      } else if (d < SEG_MIN_SEP) {
        /* crossing tier */
        count++;
        depth += (SEG_MIN_SEP - d);
      }
    }
  }
  return { count, depth, parCount, parDepth,
           approachCount, approachDepth };
}

const WIRE_ANCHOR_TRIM = 8.0;

/* ---- Wall-edge collision ---- */

function _wallEdgeOverlapPenalty(leaderSegs, wallEdges) {
  let count = 0, depth = 0;
  if (!wallEdges || !wallEdges.length) return { count, depth };

  const SLOPE_MAX = 0.4;

  for (const s of leaderSegs) {
    const dx = s.bx - s.ax;
    const dy = s.by - s.ay;
    if (Math.abs(dx) < 1e-6) continue;
    const slope = Math.abs(dy / dx);
    if (slope > SLOPE_MAX) continue;

    for (const we of wallEdges) {
      const xLo = Math.max(s.minX, we.x0);
      const xHi = Math.min(s.maxX, we.x1);
      if (xHi - xLo < 2 * SEG_MIN_LEN) continue;

      if (s.minY - SEG_MIN_SEP > we.y) continue;
      if (s.maxY + SEG_MIN_SEP < we.y) continue;

      let maxDist = 0;
      let sumDist = 0;
      const N = 5;
      for (let k = 0; k <= N; k++) {
        const x = xLo + (xHi - xLo) * k / N;
        const t = (x - s.ax) / dx;
        const y = s.ay + dy * t;
        const d = Math.abs(y - we.y);
        if (d > maxDist) maxDist = d;
        sumDist += d;
      }
      if (maxDist < SEG_MIN_SEP) {
        count++;
        depth += (SEG_MIN_SEP - sumDist / (N + 1));
      }
    }
  }
  return { count, depth };
}

/* ---- Corner features ---- */

const CORNER_MIN_DIST = 24.0;

function _cornerFeature(it, placed, stripH, topPad, trackOffsets, which) {
  const anchorY  = it.anchorCyRel;
  const chanY    = stripH + (it.channelYRel || 0);
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  const basePath = computeLeaderPath(
    it.anchorCx, anchorY, it.offsetA || 0, it.offsetP || 0,
    chanY, it.pillCenterX, pillTopY,
    it, placed, stripH, topPad, trackOffsets, it.diveMode || 0);

  if (basePath.length < 3) {
    const p = basePath[0] || [0, 0];
    return { x0: p[0], y0: p[1], x1: p[0], y1: p[1] };
  }

  let A, C, N, b;
  if (which === 0) {
    A = basePath[0];
    C = basePath[1];
    N = basePath[2];
    b = it.bevel0 || 0;
  } else {
    A = basePath[basePath.length - 3];
    C = basePath[basePath.length - 2];
    N = basePath[basePath.length - 1];
    b = it.bevel1 || 0;
  }

  if (b < BEVEL_MIN_APPLY) {
    return { x0: C[0], y0: C[1], x1: C[0], y1: C[1] };
  }
  const lenA = Math.hypot(C[0] - A[0], C[1] - A[1]);
  const lenB = Math.hypot(N[0] - C[0], N[1] - C[1]);
  const bU = Math.min(b, lenA * 0.85, lenB * 0.85);
  if (bU < BEVEL_MIN_APPLY || lenA < 1e-6 || lenB < 1e-6) {
    return { x0: C[0], y0: C[1], x1: C[0], y1: C[1] };
  }
  const t1 = (lenA - bU) / lenA;
  const t2 = bU / lenB;
  const p1 = [A[0] + (C[0] - A[0]) * t1, A[1] + (C[1] - A[1]) * t1];
  const p2 = [C[0] + (N[0] - C[0]) * t2, C[1] + (N[1] - C[1]) * t2];
  return { x0: p1[0], y0: p1[1], x1: p2[0], y1: p2[1] };
}

function _cornerDist(fA, fB) {
  const aIsPt = (Math.abs(fA.x0 - fA.x1) < 0.5 && Math.abs(fA.y0 - fA.y1) < 0.5);
  const bIsPt = (Math.abs(fB.x0 - fB.x1) < 0.5 && Math.abs(fB.y0 - fB.y1) < 0.5);
  if (aIsPt && bIsPt) {
    return Math.hypot(fA.x0 - fB.x0, fA.y0 - fB.y0);
  }
  if (aIsPt) {
    return _pointSegDist(fA.x0, fA.y0, fB.x0, fB.y0, fB.x1, fB.y1);
  }
  if (bIsPt) {
    return _pointSegDist(fB.x0, fB.y0, fA.x0, fA.y0, fA.x1, fA.y1);
  }
  return _segSegDist(fA.x0, fA.y0, fA.x1, fA.y1,
                     fB.x0, fB.y0, fB.x1, fB.y1);
}
"""
