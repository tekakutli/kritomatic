"""
pg_route.py — cross-wall routing, floor→wall projection, and merge
target detection.

Four distinct queries live here, all of them "which physical feature
is where, relative to a wall the user picked":

    • Hop transition — whether two wall-edge anchors are far enough
      apart in the strip to warrant a hop arc rather than a diagonal,
      and the arc itself.  The rendering of a cable whose anchor chain
      hops between distant walls calls both.

    • Wormhole route planner — the BFS over the junction graph that
      finds the chain of walls between a first click and a second
      click, and the linear interpolation that places a route vertex
      at each corner.  Used by the drawing lifecycle when the user
      clicks a second wall while drawing on the wall strip.

    • Floor→wall projection — the true line-of-sight test the floor
      view uses to mark which walls face the cursor and at what
      distance.  Reads the same WALL.segments grid as the renderer.

    • Merge target detection — the endpoint of another cable within
      snapping distance, so Alt+click can adopt or merge.

Nothing here draws except drawHopArc, which is a small dashed arc in
the transit accent.  Everything else is a pure query.
"""


ROUTE_JS = r"""
/* ==========================================================================
   HOP TRANSITION
   ==========================================================================
   Two wall-edge anchors hop when they are on different segments and
   far apart in the strip's own u coordinate.  The threshold is in
   strip-mm, not screen px: the strip's ruler is the reference, not
   the viewport. */

function isHopTransition(a, b) {
  if (!a || !b) return false;
  if (a.space !== "wall-edge" || b.space !== "wall-edge") return false;
  if (a.segIdx === b.segIdx) return false;
  const ua = anchorWall(a), ub = anchorWall(b);
  if (!ua || !ub) return false;
  return Math.abs(ua[0] - ub[0]) > 60.0;
}

function drawHopArc(fromX, fromY, toX, toY, emphasized) {
  ctx.save();
  ctx.setLineDash([5, 3]);
  ctx.strokeStyle = emphasized ? PALETTE.transit : PALETTE.transitSoft;
  ctx.lineWidth  = emphasized ? 2.6 : 1.8;
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  const midX = (fromX + toX) / 2;
  const dipY = Math.min(fromY, toY) - 24;
  ctx.beginPath();
  ctx.moveTo(fromX, fromY);
  ctx.quadraticCurveTo(midX, dipY, toX, toY);
  ctx.stroke();
  ctx.setLineDash([]);
  const chev = (x, y, dir) => {
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + 5 * dir, y - 4);
    ctx.moveTo(x, y);
    ctx.lineTo(x + 5 * dir, y + 4);
    ctx.stroke();
  };
  chev(fromX, fromY, 1);
  chev(toX, toY, -1);
  ctx.restore();
}

/* ==========================================================================
   WORMHOLE ROUTE PLANNER
   ==========================================================================
   Given two clicks on the unfolded wall strip, find the chain of walls
   between them via the junction graph, then lay a route along it.
   A virtual straight line is drawn through the strip's total length
   between the two clicks; it is split at every corner between them,
   and the resulting vertices are filed as a single cable.

   BFS, not Dijkstra — the wall graph is a tree of rooms, so every
   path is unique and shortest by construction. */

function findSegmentChain(startSeg, endSeg) {
  if (startSeg === endSeg) return null;
  const N = WALL.segments.length;
  const prev = new Array(N).fill(-1);
  const visited = new Set([startSeg]);
  const queue = [startSeg];
  while (queue.length) {
    const cur = queue.shift();
    if (cur === endSeg) break;
    for (const j of SEGMENT_ADJ[cur]) {
      if (visited.has(j)) continue;
      visited.add(j);
      prev[j] = cur;
      queue.push(j);
    }
  }
  if (!visited.has(endSeg)) return null;

  const path = [];
  let node = endSeg;
  while (node !== startSeg) {
    path.unshift(node);
    node = prev[node];
  }
  path.unshift(startSeg);

  const chain = [];
  for (let k = 0; k < path.length; k++) {
    let entrySide = -1, exitSide = -1;
    if (k > 0) {
      const d = ADJ_DETAIL.get(path[k] + ":" + path[k-1]);
      entrySide = d ? d.side : -1;
    }
    if (k < path.length - 1) {
      const d = ADJ_DETAIL.get(path[k] + ":" + path[k+1]);
      exitSide = d ? d.side : -1;
    }
    chain.push({ segIdx: path[k], entrySide, exitSide });
  }
  return chain;
}

function planWormholeRoute(click1, click2) {
  if (click1.segIdx === click2.segIdx) return null;
  const chain = findSegmentChain(click1.segIdx, click2.segIdx);
  if (!chain || chain.length < 2) return null;

  const n = chain.length;
  const L = chain.map(e => WALL.segments[e.segIdx].len);

  const exit0 = chain[0].exitSide;
  const d0 = (exit0 === 1) ? (1 - click1.t) * L[0]
                           : click1.t * L[0];
  const entryN = chain[n - 1].entrySide;
  const dN = (entryN === 1) ? (1 - click2.t) * L[n - 1]
                            : click2.t * L[n - 1];

  let D = d0 + dN;
  for (let i = 1; i < n - 1; i++) D += L[i];
  if (D < 1e-6) return null;

  const v0 = click1.v, vN = click2.v;
  const heightAt = (u) => v0 + (vN - v0) * (u / D);

  const out = [];
  out.push({ space: "wall-edge", segIdx: click1.segIdx,
             t: click1.t, v: click1.v });

  let u = d0;
  for (let i = 0; i < n - 1; i++) {
    const h = heightAt(u);
    out.push({ space: "wall-edge", segIdx: chain[i].segIdx,
               t: chain[i].exitSide, v: h });
    out.push({ space: "wall-edge", segIdx: chain[i + 1].segIdx,
               t: chain[i + 1].entrySide, v: h });
    u += L[i + 1];
  }

  out.push({ space: "wall-edge", segIdx: click2.segIdx,
             t: click2.t, v: click2.v });
  return out;
}

/* ==========================================================================
   PROJECTING FLOOR CURSOR TO WALL
   ==========================================================================
   True line-of-sight test.  Returns true if the segment from
   (cx, cy) to (tx, ty) is crossed by any wall segment other than
   the one we are testing.  Corner-grazing is handled by an
   epsilon on both parametric coordinates: an intersection whose
   parameter along the wall is within 1e-3 of either endpoint is
   discarded, so two walls sharing a corner do not falsely block
   each other.  An intersection whose parameter along the ray is
   within 1e-3 of either end is likewise discarded — a wall the
   ray touches at its own start (the cursor) or its own end (the
   target wall) is not between the two. */
function _segmentBlockedBy(cx, cy, tx, ty, walls, selfIdx) {
  const rdx = tx - cx, rdy = ty - cy;
  if (Math.hypot(rdx, rdy) < 1e-6) return false;

  for (let i = 0; i < walls.length; i++) {
    if (i === selfIdx) continue;
    if (isSegHidden(i)) continue;
    const s = walls[i];
    const ex = s.b[0] - s.a[0], ey = s.b[1] - s.a[1];

    const denom = rdx * ey - rdy * ex;
    if (Math.abs(denom) < 1e-9) continue;   /* parallel */

    const t = ((s.a[0] - cx) * ey - (s.a[1] - cy) * ex) / denom;
    const u = ((s.a[0] - cx) * rdy - (s.a[1] - cy) * rdx) / denom;

    if (t > 1e-3 && t < 1 - 1e-3 &&
        u > 1e-3 && u < 1 - 1e-3) {
      return true;
    }
  }
  return false;
}

function projectOntoWalls(wx, wy) {
  const result = [];
  const segs = WALL.segments;

  for (let i = 0; i < segs.length; i++) {
    if (isSegHidden(i)) continue;
    const s = segs[i];
    const ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];
    const dx = bx - ax, dy = by - ay;
    const len2 = dx*dx + dy*dy;
    if (len2 < 1e-9) continue;

    /* The perpendicular foot from the cursor to this wall's line
       must land inside the wall's own span.  A cursor past the end
       of a wall does not face that wall — its nearest point is the
       endpoint, but that is a projection onto the wall's corner,
       not onto the wall.  The earlier version clamped t to [0, 1]
       and drew a dot at the endpoint; the user asked for those to
       be dropped.

       Boundary is inclusive: a foot exactly at t = 0 or t = 1 is
       still a projection onto the segment (the wall's own
       endpoint), and the wall is still facing the cursor along
       its full length.  Only t strictly outside [0, 1] — the
       cursor genuinely beyond the wall — is excluded. */
    const t = ((wx - ax)*dx + (wy - ay)*dy) / len2;
    if (t < 0 || t > 1) continue;
    const px = ax + t*dx, py = ay + t*dy;

    /* Line-of-sight test is unchanged: even for a wall whose span
       the cursor does face, a nearer wall or a column between the
       two can hide it. */
    if (_segmentBlockedBy(wx, wy, px, py, segs, i)) continue;

    result.push({
      u: s.u0 + t * s.len,
      dist: Math.hypot(wx - px, wy - py),
      segIdx: i,
    });
  }
  return result;
}

/* ==========================================================================
   MERGE TARGET DETECTION
   ==========================================================================
   Alt+click during drawing finds an existing cable's endpoint within
   fourteen screen px of the cursor.  Only the two ends of each cable
   are eligible — the interior vertices are not merge points, because
   merging into the middle of a run would require splitting it and
   that is deliberately out of scope. */

function findMergeTarget(view, sx, sy) {
  const list = (view === "floor") ? state.floorCables : state.wallCables;
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;
  const baseId = drawing ? drawing.baseCableId : null;
  let best = null, bestD = 14;
  for (const c of list) {
    if (c.id === baseId) continue;
    if (!c.anchorIds || c.anchorIds.length === 0) continue;
    for (const idx of [0, c.anchorIds.length - 1]) {
      const a = anchors.get(c.anchorIds[idx]);
      if (!a) continue;
      if (view === "wall" && a.space === "wall-edge" && isSegHidden(a.segIdx))
        continue;
      const p = project(a);
      if (!p) continue;
      const [px, py] = toScreen(p[0], p[1]);
      const d = Math.hypot(px - sx, py - sy);
      if (d < bestD) {
        bestD = d;
        best = { cable: c, endpointIdx: idx };
      }
    }
  }
  return best;
}
"""
