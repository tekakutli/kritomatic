"""
pg_snap.py — snap-spec producers for both views.

A snap spec is a plain object describing where a click should land,
carrying enough metadata for the anchor registry to reuse an existing
anchor when appropriate:

    { space: "floor",     x, y, gridId?, gridAxis? }
    { space: "wall-edge", segIdx, t, v, gridId?, gridAxis? }

Four producers, two per view:

    findWallAttachSpec    — floor click → nearest wall face, with a
                            step-aware v
    findWallEdgeSnapSpec  — wall click → the boundary between a wall
                            chunk and the void, or between a step
                            chunk's top and the air above it
    snapFloorSpec         — full floor-click policy: grid line first,
                            then wall attach, else free
    snapWallSpec          — full wall-click policy: grid line, then
                            edge spec, then attach, else nearest segment

None of these mutate state; all are pure queries.

Wall grid snapping under focus mode
-----------------------------------
Focus mode remaps the focused wall and its immediate neighbours onto
a compact u-range and hides everything else.  A vertical grid line's
absolute `pos` no longer points at a visible u on the live strip, so
the snap test must translate the grid's original u into the visible
strip's current-space u via `state.focusSavedU` and
`_segAtOriginalU`.  A grid line whose original u falls on a hidden
segment is skipped.
"""

SNAP_JS = r"""
import {
  WALL, viewWall, viewFloor, state,
  segTop, segBottom,
  isStepFace, pointOnStep,
  _segAtOriginalU,
} from "./pg_state.js";
import {
  uToWallAttach, nearestSegmentToUV,
  isSegHidden, findSegmentAtU,
} from "./pg_core.js";

/* ==========================================================================
   SNAP SPEC PRODUCERS
   ========================================================================== */

/* Floor click → nearest wall face.

   The interesting case is a click that lands near a wall whose base is
   NOT at floor level — a step riser, or a wall that stands above a
   step.  Two separate v-values are plausible there ("bottom of the
   riser" vs "top of the riser", "floor under the wall" vs "base of the
   wall above the step") and picking the wrong one shifts the anchor to
   the wrong height on the strip.

   The priority ladder:

     pri=1, v=segTop(s)     a step face, cursor on the step.  Clicking
                            onto the top of a riser.
     pri=2, v=0             a step face, cursor off the step.  Clicking
                            the riser from the floor side.
     pri=2, v=segBottom(s)  a wall with a raised base (segBottom > 100),
                            cursor on a step.  Clicking the wall face
                            above the step it stands on.
     pri=0, v=segBottom(s)  any other wall.  Base at floor level.

   A distance tie within 1.0 mm goes to the higher priority. */
export function findWallAttachSpec(x, y) {
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

/* Wall click → segment edge spec.  Handles step faces specially: a
   click near the top of a step snaps to segTop(s); a click near the
   bottom snaps to 0.  Other walls only snap at their base (v = 0), so
   the "wall face" snap is a floor-level attach. */
export function findWallEdgeSnapSpec(u, v) {
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

/* Floor click policy: grid line first, then wall attach, else free.
   The ordering matters — a click that lands near both a floor grid
   and a wall snaps to the grid, so the user can place vertices along
   a grid line that runs alongside a wall. */
export function snapFloorSpec(wx, wy, snap) {
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
   the visible strip's current-space u by way of state.focusSavedU
   (the pre-remap range table the focus routine wrote), and the
   proximity test runs against that current-space value.

   Private to this module. */
function _snapWallGrids(u, v, SNAP) {
  let snappedU = u;
  let snappedV = v;

  if (!state.focusSavedU) {
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
      const saved = state.focusSavedU.get(segIdx);
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

/* Wall click policy: grid, then edge, then attach, then nearest
   segment.  The final fallback guarantees a spec whenever the cursor
   is anywhere near a wall — a wall click with no spec would abort the
   drawing step, so there is always a spec. */
export function snapWallSpec(u, v, snap) {
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
