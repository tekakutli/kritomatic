"""
pg_snap.py — snap-spec producers for both views.

A snap spec is a plain object describing where a click should land,
carrying enough metadata for the anchor registry to reuse an existing
anchor when appropriate:

    { space: "floor",     x, y, gridId?, gridAxis? }
    { space: "wall-edge", segIdx, t, v, gridId?, gridAxis? }

Four producers, two per view:

    findWallAttachSpec    — floor click → nearest wall face, with a
                            step-aware v (top-of-riser for a step, the
                            wall's z_lo for a wall, the wall's z_lo
                            for a wall above a step when the click is
                            off the step footprint)
    findWallEdgeSnapSpec  — wall click → the boundary between a wall
                            chunk and the void, or between a step
                            chunk's top and the air above it
    snapFloorSpec         — full floor-click policy: grid line first,
                            then wall attach, else free
    snapWallSpec          — full wall-click policy: grid line, then
                            edge spec, then attach, else nearest segment

None of these mutate state; all are pure queries.  isSegHidden in
findWallAttachSpec honours focus mode (which is a strip-only concept);
findWallEdgeSnapSpec's caller in snapWallSpec already skips hidden
segments via findSegmentAtU.

Wall grid snapping under focus mode
-----------------------------------
A wall grid line drawn outside focus mode stores an absolute position
along the strip: a vertical line's `pos` is an original-u value, a
horizontal line's `pos` is a height.  Outside focus, snapping is a
direct |u − pos| / |v − pos| proximity test.

Focus mode remaps the focused wall and its immediate neighbours onto
a compact u-range and hides everything else, so a vertical line's
absolute `pos` no longer points at a visible u on the live strip.
The snap test has to first translate the grid's original u into the
visible strip's current-space u, using the same focusSavedU table the
renderer uses:

    segIdx   = segment whose ORIGINAL u-range contains grid.pos
    saved    = focusSavedU.get(segIdx)      // pre-remap u-range
    uVisible = cur.u0 + (grid.pos - saved.u0)

Only then is the |u − uVisible| < SNAP test meaningful.  A vertical
grid whose original u falls on a hidden segment has no visible surface
to snap to and is skipped; a horizontal grid is height-only, so its
snap test is unchanged during focus.

The projection helpers used here are declared in pg_view_wall.py.
This module runs first in the concatenation order, but hoisted
function declarations make _segAtOriginalU callable from here — the
combined script is one flat scope.
"""


SNAP_JS = r"""
/* ==========================================================================
   SNAP SPEC PRODUCERS
   ========================================================================== */

function findWallAttachSpec(x, y) {
  const SNAP = 22 / viewFloor.scale;
  const onStep = pointOnStep(x, y);
  let best = null, bestDist = SNAP, bestPri = -1;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx*dx + dy*dy;
    if (len2 < 1e-9) continue;
    let t = ((x - ax)*dx + (y - ay)*dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const sx = ax + t*dx, sy = ay + t*dy;
    const d = Math.hypot(x - sx, y - sy);
    if (d > SNAP) continue;
    let pri, v;
    if (isStepFace(s)) {
      if (onStep) { pri = 1; v = segTop(s); }
      else        { pri = 2; v = 0; }
    } else if (s.kind === "wall" && segBottom(s) > 100 && onStep) {
      pri = 2; v = segBottom(s);
    } else {
      pri = 0; v = segBottom(s);
    }
    const better = (d < bestDist - 1.0) ||
                   (Math.abs(d - bestDist) <= 1.0 && pri > bestPri);
    if (better) {
      bestDist = d; bestPri = pri;
      best = { segIdx: i, t, v };
    }
  }
  if (!best) return null;
  return { space: "wall-edge", segIdx: best.segIdx, t: best.t, v: best.v };
}

function findWallEdgeSnapSpec(u, v) {
  const SNAP = 22 / Math.min(viewWall.scaleX, viewWall.scaleY);
  const sAt = findSegmentAtU(u);
  if (sAt && isStepFace(sAt.seg)) {
    const top = segTop(sAt.seg);
    const t = (u - sAt.seg.u0) / sAt.seg.len;
    if (Math.abs(v - top) < SNAP)
      return { space: "wall-edge", segIdx: sAt.segIdx, t, v: top };
    if (Math.abs(v)      < SNAP)
      return { space: "wall-edge", segIdx: sAt.segIdx, t, v: 0 };
  }
  if (Math.abs(v) < SNAP) {
    const attach = uToWallAttach(u, v);
    if (attach)
      return { space: "wall-edge", segIdx: attach.segIdx, t: attach.t, v: 0 };
  }
  return null;
}

function snapFloorSpec(wx, wy, snap) {
  if (snap) {
    const SNAP = 14 / viewFloor.scale;
    for (const g of state.floorGrids) {
      if (g.type === "ns" && Math.abs(wx - g.pos) < SNAP)
        return { space: "floor", x: g.pos, y: wy, gridId: g.id, gridAxis: "ns" };
      if (g.type === "ew" && Math.abs(wy - g.pos) < SNAP)
        return { space: "floor", x: wx, y: g.pos, gridId: g.id, gridAxis: "ew" };
    }
    const wall = findWallAttachSpec(wx, wy);
    if (wall) return wall;
  }
  return { space: "floor", x: wx, y: wy };
}

/* Snap the cursor to the nearest wall grid line.

   Outside focus: a vertical grid line's `pos` is an absolute u along
   the strip, and a horizontal grid line's `pos` is a height.  Both
   are direct proximity tests.

   Inside focus: a horizontal grid line's `pos` is still a height, so
   its test is unchanged.  A vertical grid line's `pos` is an ORIGINAL
   u — the strip has been remapped and re-ordered, so the live u-range
   no longer contains it.  The grid's original u is translated into
   the visible strip's current-space u by way of focusSavedU (the
   pre-remap range table the focus routine wrote), and the proximity
   test runs against that current-space value:

       segIdx    = _segAtOriginalU(g.pos)
       if (segIdx < 0) skip       // grid sits on a hidden segment
       saved     = focusSavedU.get(segIdx)
       uVisible  = cur.u0 + (g.pos - saved.u0)

   If the grid's original u falls on a hidden segment, there is no
   visible wall to snap to and the entry is skipped — the alternative
   would be silently snapping the cursor to a u that points at empty
   space. */
function _snapWallGrids(u, v, SNAP) {
  let snappedU = u;
  let snappedV = v;

  if (!focusSavedU) {
    for (const g of state.wallGrids) {
      if (g.type === "v" && Math.abs(u - g.pos) < SNAP) snappedU = g.pos;
      if (g.type === "h" && Math.abs(v - g.pos) < SNAP) snappedV = g.pos;
    }
    return { u: snappedU, v: snappedV };
  }

  for (const g of state.wallGrids) {
    if (g.type === "v") {
      const segIdx = _segAtOriginalU(g.pos);
      if (segIdx < 0) continue;
      const saved = focusSavedU.get(segIdx);
      const cur   = WALL.segments[segIdx];
      if (!saved || !cur) continue;
      const uVisible = cur.u0 + (g.pos - saved.u0);
      if (Math.abs(u - uVisible) < SNAP) snappedU = uVisible;
    } else {
      if (Math.abs(v - g.pos) < SNAP) snappedV = g.pos;
    }
  }
  return { u: snappedU, v: snappedV };
}

function snapWallSpec(u, v, snap) {
  if (snap) {
    const SNAP = 14 / Math.min(viewWall.scaleX, viewWall.scaleY);
    const snapped = _snapWallGrids(u, v, SNAP);
    u = snapped.u;
    v = snapped.v;
  }
  if (snap) {
    const edge = findWallEdgeSnapSpec(u, v);
    if (edge) return edge;
  }
  const attach = uToWallAttach(u, v);
  if (attach) {
    const s = WALL.segments[attach.segIdx];
    const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
    return { space: "wall-edge", segIdx: attach.segIdx, t: attach.t, v: vv };
  }
  const i = nearestSegmentToUV(u, v);
  if (i < 0) return null;
  const s = WALL.segments[i];
  const t = Math.max(0, Math.min(1, (u - s.u0) / s.len));
  const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
  return { space: "wall-edge", segIdx: i, t, v: vv };
}
"""
