"""
pg_view_cables.py — cable drawing on both views.

Every cable in the playground is drawn by the same function, drawCable,
with a view parameter that selects the plan projection (anchorPlan) or
the strip projection (anchorWall).  The other functions here are the
pieces drawCable uses plus the two cross-view marker passes and the
step-bridge bar pass.

Cable drawing details
---------------------
A cable is stored as an ordered list of anchor ids.  drawCable walks
that list once, emitting a single beginPath()/moveTo()/lineTo() walk —
hops between far-apart walls are handled by a moveTo() so the polyline
becomes several sub-paths in one path object, and every sub-path is
then stroked with the same recipe in one call.  This is the fix for a
bug where the hop branch used to stroke and re-beginPath() inline,
which left the pre-hop sub-path un-haloed.

Cross-view markers
------------------
An anchor that sits on a wall's bottom edge but belongs to a cable in
the floor view is drawn as a boundary marker on the plan.  The
transpose — an anchor on the plan that belongs to a wall cable — is
drawn on the strip.  These two passes make cross-view connections
visible in both directions.

Step-bridge bars
----------------
When an anchor sits on a step's top edge and is used only by a floor
cable (not by a wall cable), a dashed magenta bar connects the step's
top in the strip to its footprint in the plan, so the user can see
that the floor anchor is on a raised surface.
"""


CABLES_VIEW_JS = r"""
/* ==========================================================================
   CABLE RENDERING
   ========================================================================== */

function _strokeCablePath(sel, selGroup) {
  ctx.lineJoin = "round"; ctx.lineCap = "round";
  if (sel) {
    const r = CABLE_STROKE.selected;
    ctx.strokeStyle = PALETTE.paper; ctx.lineWidth = r.outerHalo;  ctx.stroke();
    ctx.strokeStyle = PALETTE.ink;   ctx.lineWidth = r.outerInk;   ctx.stroke();
    ctx.strokeStyle = PALETTE.paper; ctx.lineWidth = r.innerPaper; ctx.stroke();
    ctx.strokeStyle = PALETTE.ink;   ctx.lineWidth = r.innerInk;   ctx.stroke();
  } else {
    const r = selGroup ? CABLE_STROKE.sibling : CABLE_STROKE.plain;
    ctx.strokeStyle = PALETTE.paper; ctx.lineWidth = r.halo; ctx.stroke();
    ctx.strokeStyle = PALETTE.ink;   ctx.lineWidth = r.ink;  ctx.stroke();
  }
}

function _anchorRole(a) {
  if (!a) return "plain";
  const isStep = anchorIsStepFace(a);
  const side   = anchorSide(a);
  const isJct  = (junctionOfAnchor(a) >= 0);
  if (isStep && side === "top")    return "step-top";
  if (isStep && side === "bottom") return "step-bottom";
  if (isJct)                       return "corner";
  return "plain";
}

function _drawAnchorMarker(vx, vy, role, sel) {
  ctx.beginPath();
  ctx.arc(vx, vy, 7.5, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.paper;
  ctx.fill();

  if (!sel) {
    ctx.beginPath();
    ctx.arc(vx, vy, 5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill();
    return;
  }

  if (role === "step-top") {
    ctx.beginPath();
    ctx.arc(vx, vy, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.paper;
    ctx.fill();
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 2;
    ctx.stroke();
  } else if (role === "step-bottom") {
    ctx.beginPath();
    ctx.arc(vx, vy, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill();
    ctx.strokeStyle = PALETTE.paper;
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    ctx.moveTo(vx - 4.5, vy);
    ctx.lineTo(vx + 4.5, vy);
    ctx.stroke();
  } else if (role === "corner") {
    ctx.beginPath();
    ctx.arc(vx, vy, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill();
    ctx.beginPath();
    ctx.arc(vx, vy, 8.2, -Math.PI * 0.72, Math.PI * 0.72);
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 1.6;
    ctx.stroke();
  } else {
    ctx.beginPath();
    ctx.arc(vx, vy, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.ink;
    ctx.fill();
  }
}

function drawCable(c, view) {
  if (!c.anchorIds || c.anchorIds.length === 0) return;
  if (drawing && drawing.baseCableId === c.id) return;
  const project  = (view === "floor") ? anchorPlan  : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor    : w2sWall;

  const selSelf  = (c === state.selectedCable);
  const selGroup = state.selectedCable &&
                   trueCableIdOf(c) === trueCableIdOf(state.selectedCable);
  const sel = selGroup;

  const visible = (a) => {
    if (view !== "wall") return true;
    if (!a) return false;
    if (a.space !== "wall-edge") return true;
    return !isSegHidden(a.segIdx);
  };

  /* Build the cable as ONE multi-sub-path geometry: a single
     beginPath() for the whole cable, then moveTo() at each hop so the
     polyline is split into sub-paths without ever being stroked
     mid-walk.  The single _strokeCablePath() call below then applies
     the halo+ink recipe — or the four-pass selected recipe — to
     every sub-path uniformly.

     The bug this replaces: the hop branch used to call ctx.stroke()
     and ctx.beginPath() inline, which stroked the pre-hop sub-path
     with whatever canvas style happened to be live, then discarded
     it.  The post-hop sub-path was the only one the recipe saw, so a
     cable that hopped rendered half haloed, half bare — invisible on
     blank paper, glaring over any background ink. */
  ctx.beginPath();
  let started = false;
  let lastAnchor = null;
  const hops = [];
  for (const id of c.anchorIds) {
    const a = anchors.get(id);
    if (!visible(a)) { started = false; lastAnchor = null; continue; }
    const p = a ? project(a) : null;
    if (!p) { started = false; lastAnchor = null; continue; }
    const [sx, sy] = toScreen(p[0], p[1]);
    if (started && lastAnchor && isHopTransition(lastAnchor, a)) {
      const lp = project(lastAnchor);
      const [lx, ly] = toScreen(lp[0], lp[1]);
      hops.push({ fromX: lx, fromY: ly, toX: sx, toY: sy });
      ctx.moveTo(sx, sy);
    } else if (!started) {
      ctx.moveTo(sx, sy);
    } else {
      ctx.lineTo(sx, sy);
    }
    started = true;
    lastAnchor = a;
  }

  _strokeCablePath(selSelf, sel);

  for (const j of hops) {
    drawHopArc(j.fromX, j.fromY, j.toX, j.toY, sel);
  }

  for (let i = 0; i < c.anchorIds.length; i++) {
    const a = anchors.get(c.anchorIds[i]);
    if (!visible(a)) continue;
    const p = a ? project(a) : null;
    if (!p) continue;
    const [vx, vy] = toScreen(p[0], p[1]);
    _drawAnchorMarker(vx, vy, _anchorRole(a), sel);

    if (state.selectedVertex && state.selectedVertex.cable === c &&
        state.selectedVertex.index === i) {
      ctx.beginPath();
      ctx.arc(vx, vy, 11, 0, Math.PI * 2);
      ctx.strokeStyle = PALETTE.highlight;
      ctx.lineWidth = 2;
      ctx.stroke();
    }
  }
}

function drawVertexBalloons(view) {
  const c = state.selectedCable;
  if (!c) return;
  const parts = trueCableSiblings(c);
  if (!parts.length) return;

  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;

  const ordered = [];
  const seen = new Set();
  for (const p of parts) {
    for (const id of p.anchorIds) {
      if (seen.has(id)) continue;
      seen.add(id);
      ordered.push(id);
    }
  }

  ctx.save();
  ctx.font = "700 10px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  let lastX = -Infinity, lastY = -Infinity;
  let counter = 0;
  for (const id of ordered) {
    const a = anchors.get(id);
    if (!a) continue;
    if (view === "wall" && a.space === "wall-edge" &&
        isSegHidden(a.segIdx)) continue;
    const p = project(a);
    if (!p) continue;
    const [vx, vy] = toScreen(p[0], p[1]);
    if (Math.hypot(vx - lastX, vy - lastY) < BALLOON_MIN_SEP) continue;
    lastX = vx; lastY = vy;
    counter++;

    const angle = -Math.PI / 4;
    const bx = vx + Math.cos(angle) * BALLOON_LEADER;
    const by = vy + Math.sin(angle) * BALLOON_LEADER;

    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(vx, vy);
    ctx.lineTo(bx - Math.cos(angle) * (BALLOON_R + 1),
               by - Math.sin(angle) * (BALLOON_R + 1));
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(bx, by, BALLOON_R, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.paper;
    ctx.fill();
    ctx.strokeStyle = PALETTE.ink;
    ctx.lineWidth = 1.2;
    ctx.stroke();

    ctx.fillStyle = PALETTE.ink;
    ctx.fillText("V" + counter, bx, by + 0.5);
  }
  ctx.restore();
}

function drawBoundaryMarker(sx, sy, selected, onTop) {
  const r = selected ? 7 : 6;
  ctx.beginPath();
  ctx.arc(sx, sy, r + 2.5, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.paper;
  ctx.fill();

  ctx.beginPath();
  ctx.arc(sx, sy, r, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.ink;
  ctx.fill();
  ctx.strokeStyle = onTop ? PALETTE.step : PALETTE.transit;
  ctx.lineWidth = 2;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(sx, sy, 2.2, 0, Math.PI * 2);
  ctx.fillStyle = PALETTE.paper;
  ctx.fill();
}

function drawCrossMarkersFromWallCables() {
  for (const c of state.wallCables) {
    if (drawing && drawing.baseCableId === c.id) continue;
    const sel = (c === state.selectedCable);
    for (const id of c.anchorIds) {
      const a = anchors.get(id);
      if (!a || a.space !== "wall-edge") continue;
      if (isSegHidden(a.segIdx)) continue;
      const s = WALL.segments[a.segIdx];
      if (!s) continue;
      const atFloorLevel = Math.abs(a.v) < 0.5 ||
                           (isStepFace(s) && Math.abs(a.v - segTop(s)) < 0.5);
      if (!atFloorLevel) continue;
      const p = anchorPlan(a);
      if (!p) continue;
      const [mx, my] = w2sFloor(p[0], p[1]);
      const side = anchorSide(a);
      drawBoundaryMarker(mx, my, sel, side === "top");
    }
  }
}
function drawCrossMarkersFromFloorCables() {
  for (const c of state.floorCables) {
    if (drawing && drawing.baseCableId === c.id) continue;
    const sel = (c === state.selectedCable);
    for (const id of c.anchorIds) {
      const a = anchors.get(id);
      if (!a || a.space !== "wall-edge") continue;
      if (isSegHidden(a.segIdx)) continue;
      const p = anchorWall(a);
      if (!p) continue;
      const [mx, my] = w2sWall(p[0], p[1]);
      const side = anchorSide(a);
      drawBoundaryMarker(mx, my, sel, side === "top");
    }
  }
}

function drawStepBridgeBars() {
  const floorUsed = new Set();
  const wallUsed  = new Set();
  const collect = (list, view, set) => {
    for (const c of list) {
      if (drawing && drawing.baseCableId === c.id) continue;
      for (const id of c.anchorIds) set.add(id);
    }
    if (drawing && drawing.view === view) {
      for (const id of drawing.anchorIds) set.add(id);
    }
  };
  collect(state.floorCables, "floor", floorUsed);
  collect(state.wallCables,  "wall",  wallUsed);

  for (const a of anchors.values()) {
    if (!anchorIsStepFace(a))  continue;
    if (isSegHidden(a.segIdx)) continue;
    if (!floorUsed.has(a.id))  continue;
    if ( wallUsed.has(a.id))   continue;

    const s   = WALL.segments[a.segIdx];
    const top = segTop(s);
    const u   = wallAttachToU(a.segIdx, a.t);
    const [x0, y0] = w2sWall(u, 0);
    const [x1, y1] = w2sWall(u, top);
    ctx.save();
    ctx.setLineDash([3, 3]);
    ctx.strokeStyle = PALETTE.stepSoft;
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
    ctx.restore();
  }
}
"""
