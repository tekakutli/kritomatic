"""
pg_export_snapshot.py — the per-candidate layout snapshot and the
per-leader conflict counter.

evaluate() and countLeaderConflicts() are the hot loops; they are
called thousands of times per export.  Building the layout once per
candidate and handing the paths, segments, and pill boxes to every
tier check is what makes the optimiser usable at scale.

    _buildLayout              one build, hands back paths, segments,
                              and pill boxes
    _countConflictsAt         every conflict of one leader against
                              the layout
    _leaderHasConflictAt      the same test, early-exit on first hit
    _conflictedLeaderIndices  the set of leaders that participate in
                              any conflict at all

The obstacle context
--------------------
_countConflictsAt takes an optional `obstacleContext` parameter
carrying { boundaries, wireSegments, wallEdges, placed, stripH }.

Without it, only segment-segment and segment-foreign-pill conflicts
are counted — the behaviour the optimiser's inner evaluate() relies
on when it is scoring raw geometry.

With it, the counter ALSO adds boundary, wall-edge, and wire
conflicts, so a caller that can move pills (the pill push) sees the
full conflict picture.  Without this, the pill push had no reason to
avoid sliding a descent column onto a wall-section boundary: the
counter said zero conflicts for a leader whose only problem was a
0.6-px gap to a boundary line, and the push skipped that leader.

The context is a plain object so a caller can pass a single value
through many intermediate call sites without threading four
arguments each time.

Proximity to a pill box
-----------------------
Every foreign-pill test expands the box by PILL_PROX before asking
whether a segment overlaps it, so a descent column 2.4 px from a
pill edge counts as a conflict even when it does not intersect.
"""


SNAPSHOT_JS = r"""
/* ==========================================================================
   LAYOUT SNAPSHOT — one build per candidate
   ========================================================================== */

function _buildLayout(placed, stripH, topPad, trackOffsets) {
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
      p    = _applyJogsToPath(bev, it);
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

function _expandBox(box, prox) {
  return {
    qL: box.qL - prox,
    qR: box.qR + prox,
    qT: box.qT - prox,
    qB: box.qB + prox,
  };
}

/* Count all conflicts of one leader (index idx) against the layout.

   Basic conflicts (always counted):
     • segment-segment within SEG_MIN_SEP of a foreign leader;
     • segment-foreign-pill within PILL_PROX of the expanded box.

   Additional conflicts (counted only when obstacleContext is
   passed):
     • boundary, wall-edge, and wire penalties from
       pg_export_penalties, evaluated on this leader's segments only.

   The boundary / wall-edge / wire penalties are single-count
   quantities (they do not have a foreign-leader to double-count),
   so no division by two is needed. */
function _countConflictsAt(idx, layout, obstacleContext) {
  const selfSegs = layout.segs[idx];
  let n = 0;

  for (let j = 0; j < layout.segs.length; j++) {
    if (j === idx) continue;
    const otherSegs = layout.segs[j];

    /* Base segment-segment conflicts. */
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

    /* Foreign pill proximity. */
    const expanded = _expandBox(layout.boxes[j], PILL_PROX);
    for (const s of selfSegs) {
      if (s.maxX < expanded.qL || s.minX > expanded.qR) continue;
      if (s.maxY < expanded.qT || s.minY > expanded.qB) continue;
      if (_segBoxOverlap(s, expanded) > 0) n++;
    }

    /* Extra crossings beyond the first between this pair. */
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

function _leaderHasConflictAt(idx, layout, obstacleContext) {
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
    const expanded = _expandBox(layout.boxes[j], PILL_PROX);
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

function _conflictedLeaderIndices(layout, obstacleContext) {
  const s = new Set();
  const n = layout.segs.length;
  for (let i = 0; i < n; i++) {
    if (_leaderHasConflictAt(i, layout, obstacleContext)) s.add(i);
  }
  return s;
}
"""
