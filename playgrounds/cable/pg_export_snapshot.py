"""
pg_export_snapshot.py — the per-candidate layout snapshot and the
per-leader conflict counter.

ES module.  `_buildLayout` and `_countConflictsAt` are the hot loops;
they are called thousands of times per export.  Building the layout
once per candidate and handing the paths, segments, and pill boxes to
every tier check is what makes the optimiser usable at scale.

    _buildLayout              one build, hands back paths, segments,
                              and pill boxes
    _countConflictsAt         every conflict of one leader against
                              the layout
    _leaderHasConflictAt      the same test, early-exit on first hit
    _conflictedLeaderIndices  the set of leaders that participate in
                              any conflict at all

The obstacle context
--------------------
`_countConflictsAt` takes an optional `obstacleContext` parameter
carrying `{boundaries, wireSegments, wallEdges, placed, stripH}`.
Without it, only segment-segment and segment-foreign-pill conflicts
are counted.  With it, the counter also adds boundary, wall-edge,
and wire conflicts, so a caller that can move pills sees the full
conflict picture.

Imports: `_buildLeaderPathRel`, `_bevelPath`, `_applyJogsToPath`,
`_pathSegments` from primitives, plus `_segSegDist`, `_segBoxOverlap`,
`_countSegmentCrossings`, `PILL_PROX`, `SEG_MIN_SEP` from geometry,
plus the three tier penalty functions from penalties.
"""

SNAPSHOT_JS = r"""
import {
  _buildLeaderPathRel, _bevelPath, _applyJogsToPath,
  _pathSegments,
  PILL_PROX,
} from "./pg_export_primitives.js";
import {
  _segSegDist, _segBoxOverlap,
  _countSegmentCrossings,
  PILL_PROX as PILL_PROX_G, SEG_MIN_SEP,
} from "./pg_export_geometry.js";
import {
  _boundaryOverlapPenalty,
  _wallEdgeOverlapPenalty,
  _wireOverlapPenalty,
} from "./pg_export_penalties.js";

/* ==========================================================================
   LAYOUT BUILD
   ========================================================================== */

export function _buildLayout(placed, stripH, topPad, trackOffsets) {
  const n = placed.length;
  const paths = new Array(n);
  const segs  = new Array(n);
  const boxes = new Array(n);
  for (let i = 0; i < n; i++) {
    const it = placed[i];
    let p;
    if (it.finalPath) {
      /* The overhang-hugging pass in pg_export_labels.py already
         rewrote this leader's polyline.  Use the rewritten path
         verbatim so the style-conflict graph and the drawn leaders
         both see the hugging shape, not the pre-hug shape. */
      p = it.finalPath;
    } else {
      const base = _buildLeaderPathRel(it, placed, stripH, topPad, trackOffsets);
      const bev  = _bevelPath(base, it.bevel0 || 0, it.bevel1 || 0);
      p          = _applyJogsToPath(bev, it);
    }
    paths[i] = p;
    segs[i]  = _pathSegments(p, i, 1.0);
    const qT = stripH + topPad + trackOffsets[it.track];
    boxes[i] = {
      qL: it.pillCenterX - it.w / 2,
      qR: it.pillCenterX + it.w / 2,
      qT,
      qB: qT + it.h,
    };
  }
  return { paths, segs, boxes };
}

export function _expandBox(box, prox) {
  return {
    qL: box.qL - prox,
    qR: box.qR + prox,
    qT: box.qT - prox,
    qB: box.qB + prox,
  };
}

/* ==========================================================================
   CONFLICT COUNTER
   ========================================================================== */

export function _countConflictsAt(idx, layout, obstacleContext) {
  const selfSegs = layout.segs[idx];
  let n = 0;

  for (let j = 0; j < layout.segs.length; j++) {
    if (j === idx) continue;
    const otherSegs = layout.segs[j];

    for (let a = 0; a < selfSegs.length; a++) {
      const sa = selfSegs[a];
      for (let b = 0; b < otherSegs.length; b++) {
        const sb = otherSegs[b];
        if (sa.maxX + SEG_MIN_SEP < sb.minX) continue;
        if (sb.maxX + SEG_MIN_SEP < sa.minX) continue;
        if (sa.maxY + SEG_MIN_SEP < sb.minY) continue;
        if (sb.maxY + SEG_MIN_SEP < sa.minY) continue;
        const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                              sb.ax, sb.ay, sb.bx, sb.by);
        if (d < SEG_MIN_SEP) n++;
      }
    }

    const expanded = _expandBox(layout.boxes[j], PILL_PROX_G);
    for (const s of selfSegs) {
      if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
      if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
      if (_segBoxOverlap(s, expanded) > 0) n++;
    }

    const xc = _countSegmentCrossings(selfSegs, otherSegs);
    if (xc >= 2) n += (xc - 1);
  }

  if (obstacleContext) {
    const { boundaries, wireSegments, wallEdges, placed, stripH } =
      obstacleContext;
    if (boundaries && boundaries.length) {
      n += _boundaryOverlapPenalty(selfSegs, boundaries, stripH).count;
    }
    if (wallEdges && wallEdges.length) {
      n += _wallEdgeOverlapPenalty(selfSegs, wallEdges).count;
    }
    if (wireSegments && wireSegments.length) {
      const wPen = _wireOverlapPenalty(selfSegs, placed, wireSegments);
      n += wPen.parCount * 2 + (wPen.count - wPen.parCount);
    }
  }
  return n;
}

export function _leaderHasConflictAt(idx, layout, obstacleContext) {
  const selfSegs = layout.segs[idx];
  for (let j = 0; j < layout.segs.length; j++) {
    if (j === idx) continue;
    const otherSegs = layout.segs[j];
    for (const sa of selfSegs) {
      for (const sb of otherSegs) {
        if (sa.maxX + SEG_MIN_SEP < sb.minX) continue;
        if (sb.maxX + SEG_MIN_SEP < sa.minX) continue;
        if (sa.maxY + SEG_MIN_SEP < sb.minY) continue;
        if (sb.maxY + SEG_MIN_SEP < sa.minY) continue;
        const d = _segSegDist(sa.ax, sa.ay, sa.bx, sa.by,
                              sb.ax, sb.ay, sb.bx, sb.by);
        if (d < SEG_MIN_SEP) return true;
      }
    }
    const expanded = _expandBox(layout.boxes[j], PILL_PROX_G);
    for (const s of selfSegs) {
      if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
      if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
      if (_segBoxOverlap(s, expanded) > 0) return true;
    }
    const xc = _countSegmentCrossings(selfSegs, otherSegs);
    if (xc >= 2) return true;
  }
  if (obstacleContext) {
    const { boundaries, wireSegments, wallEdges, placed, stripH } =
      obstacleContext;
    if (boundaries && boundaries.length &&
        _boundaryOverlapPenalty(selfSegs, boundaries, stripH).count > 0)
      return true;
    if (wallEdges && wallEdges.length &&
        _wallEdgeOverlapPenalty(selfSegs, wallEdges).count > 0)
      return true;
    if (wireSegments && wireSegments.length) {
      const wPen = _wireOverlapPenalty(selfSegs, placed, wireSegments);
      if (wPen.parCount > 0) return true;
    }
  }
  return false;
}

export function _conflictedLeaderIndices(layout, obstacleContext) {
  const s = new Set();
  const n = layout.segs.length;
  for (let i = 0; i < n; i++) {
    if (_leaderHasConflictAt(i, layout, obstacleContext)) s.add(i);
  }
  return s;
}
"""
