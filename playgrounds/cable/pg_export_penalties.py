"""
pg_export_penalties.py — the four tier-list penalty functions.

ES module.  Each is a pure query: it takes the leader segments (or the
placed list, for the corner feature) plus its own obstacle set and
returns a `{count, depth}` pair, or — for `_wireOverlapPenalty` — the
parallel/crossing split as well.

    _boundaryOverlapPenalty   a leader leg running alongside a
                              wall-section boundary.  Angle gate is
                              ±45° from vertical; threshold
                              BOUNDARY_MIN_SEP.

    _wireOverlapPenalty       a leader leg running alongside or
                              crossing the cable wire.

    _wallEdgeOverlapPenalty   a near-horizontal leader leg running
                              alongside a wall chunk's top or bottom
                              z-edge.

    _cornerFeature / _cornerDist
                              the bevel-cut region of each leader.

IMPORT SOURCE
=============
`computeLeaderPath` and `BEVEL_MIN_APPLY` are defined in
`pg_export_primitives.js` — they are the shared leader-path builder
and its bevel-cut threshold.  `_bevelPath` is also in primitives.

An earlier revision of this module imported `computeLeaderPath`,
`_bevelPath`, and `BEVEL_MIN_APPLY` from `pg_export_geometry.js`
(wrong) while ALSO importing them under aliased names from
primitives (right).  ES modules reject a name that isn't in a
module's exports, so the module graph aborted on load.  The
geometry import now names only the distance atoms and constants that
actually live there.

The threshold and atom constants (`BOUNDARY_MIN_SEP`, `WIRE_MIN_SEP`,
`WIRE_APPROACH_SEP`, `WIRE_PARALLEL_TOL`, `SEG_MIN_SEP`,
`SEG_MIN_LEN`) are in geometry and stay there.
"""

PENALTIES_JS = r"""
import {
  _segSegDist, _pointSegDist, _angleDiff,
} from "./pg_export_geometry.js";
import {
  BOUNDARY_MIN_SEP, WIRE_MIN_SEP, WIRE_APPROACH_SEP,
  WIRE_PARALLEL_TOL, SEG_MIN_SEP, SEG_MIN_LEN,
} from "./pg_export_geometry.js";
import {
  computeLeaderPath as _computeLeaderPathP,
  BEVEL_MIN_APPLY as _BEVEL_MIN_APPLY_P,
} from "./pg_export_primitives.js";

/* ==========================================================================
   BOUNDARY COLLISION
   ========================================================================== */

export function _boundaryOverlapPenalty(leaderSegs, boundaries, stripH) {
  let count = 0, depth = 0;
  if (!boundaries || !boundaries.length) return { count, depth };

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

/* ==========================================================================
   WIRE COLLISION
   ========================================================================== */

const WIRE_ANCHOR_TRIM = 8.0;

export function _wireOverlapPenalty(leaderSegs, placed, wireSegments) {
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

    const dA = Math.hypot(s.ax - ax, s.ay - ay);
    const dB = Math.hypot(s.bx - ax, s.by - ay);
    const nearAnchor = Math.min(dA, dB) < WIRE_ANCHOR_TRIM;
    const farPt = dA > dB ? [s.ax, s.ay] : [s.bx, s.by];

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
        parCount++;
        parDepth += (WIRE_MIN_SEP - d);
        count++;
        depth += (WIRE_MIN_SEP - d);
      } else if (angDiff < WIRE_PARALLEL_TOL && d < WIRE_APPROACH_SEP) {
        approachCount++;
        approachDepth += (WIRE_APPROACH_SEP - d);
      } else if (d < SEG_MIN_SEP) {
        count++;
        depth += (SEG_MIN_SEP - d);
      }
    }
  }
  return { count, depth, parCount, parDepth,
           approachCount, approachDepth };
}

/* ==========================================================================
   WALL EDGE COLLISION
   ========================================================================== */

export function _wallEdgeOverlapPenalty(leaderSegs, wallEdges) {
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

/* ==========================================================================
   CORNER FEATURES
   ========================================================================== */

export const CORNER_MIN_DIST = 24.0;

export function _cornerFeature(it, placed, stripH, topPad, trackOffsets, which) {
  const anchorY  = it.anchorCyRel;
  const chanY    = stripH + (it.channelYRel || 0);
  const pillTopY = stripH + topPad + trackOffsets[it.track];
  const basePath = _computeLeaderPathP(
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

  if (b < _BEVEL_MIN_APPLY_P) {
    return { x0: C[0], y0: C[1], x1: C[0], y1: C[1] };
  }
  const lenA = Math.hypot(C[0] - A[0], C[1] - A[1]);
  const lenB = Math.hypot(N[0] - C[0], N[1] - C[1]);
  const bU = Math.min(b, lenA * 0.85, lenB * 0.85);
  if (bU < _BEVEL_MIN_APPLY_P || lenA < 1e-6 || lenB < 1e-6) {
    return { x0: C[0], y0: C[1], x1: C[0], y1: C[1] };
  }
  const t1 = (lenA - bU) / lenA;
  const t2 = bU / lenB;
  const p1 = [A[0] + (C[0] - A[0]) * t1, A[1] + (C[1] - A[1]) * t1];
  const p2 = [C[0] + (N[0] - C[0]) * t2, C[1] + (N[1] - C[1]) * t2];
  return { x0: p1[0], y0: p1[1], x1: p2[0], y1: p2[1] };
}

export function _cornerDist(fA, fB) {
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
