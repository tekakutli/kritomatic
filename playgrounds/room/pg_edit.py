"""
pg_edit.py — cable lifecycle: draw, delete, drag, hit test.

The interaction layer for cables.  Owns:

    • the drawing lifecycle — beginDraw, finishDraw, cancelDraw, and
      the per-click addDrawPoint with its wormhole-route planner, its
      cross-view reuse policy, and its Alt+click adopt/merge path
    • anchor resolution helpers — findAnchorAtScreen,
      anchorOwnerCable, isReusableAnchor
    • the merge operations — adoptMergeBase, mergeDrawingInto
    • cable removal — removeCableById, deleteSelectedCable,
      deleteSelectedVertex, plus gcAnchors, the anchor garbage
      collector, and clearAll
    • vertex and grid dragging — convertAnchor, computeCornerInfo,
      the two per-view appliers, and the two handlers the event
      dispatcher calls
    • hit testing for selection — nearestFloorVertex,
      nearestWallVertex, the two grid-line tests, and
      polylineHitView

The exclusion between this module and pg_ruler is deliberate and
one-directional in each direction: the ruler's toggle calls
cancelDraw(true) to disarm any in-progress drawing, and beginDraw
disarms the ruler's state and clears its selection.  Both are top-
level calls; there is no deeper coupling.

Translation
-----------
Every flashStatus message in this module routes through T().  The
transient feedback ("✓ Cable created (3 points)", "No cable selected",
etc.) appears in the panel's STATUS line and is user-facing prose.
"""


CABLE_EDIT_JS = r"""
/* ==========================================================================
   DRAWING LIFECYCLE
   ========================================================================== */

function beginDraw() {
  /* The two tool modes are exclusive: arming the drawing tool disarms
     the ruler.  The ruler's pending point, axis override, selection,
     and step-skip state are cleared so the next arm starts fresh;
     completed measurements survive, because they are independent of
     the mode. */
  if (state.ruler.active) {
    state.ruler.active            = false;
    state.ruler.pending           = null;
    state.ruler.axisPref          = null;
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    state.ruler._lastDStep        = Infinity;
    state.ruler.pendingEnd        = null;
    state.ruler.pendingAxis       = null;
    state.ruler.selected          = -1;
    _rulerCloseNumericInput();
    _rulerSyncMeasureInspector();
  }
  drawing = { view: null, anchorIds: [], id: null, name: null, baseCableId: null };
  drawingPreview = null;
  state.selectedCable = null;
  state.selectedVertex = null;
  flashStatus(T("msgDrawStart"), "ok");
  draw();
}

function cancelDraw(silent) {
  if (!drawing) return;
  exitFocus();
  drawing = null; drawingPreview = null;
  if (!silent) flashStatus(T("msgDrawCancelled"), "warn");
  draw();
}

function finishDraw() {
  if (!drawing) return;
  if (drawing.view === null) { cancelDraw(); return; }
  if (drawing.anchorIds.length < 2) {
    flashStatus(T("msgNeedTwoPoints"), "warn");
    exitFocus();
    drawing = null; drawingPreview = null; draw();
    return;
  }
  const list = (drawing.view === "floor") ? state.floorCables : state.wallCables;
  if (drawing.baseCableId != null) removeCableById(drawing.baseCableId);
  const c = {
    id: (drawing.id != null) ? drawing.id : idCounter++,
    name: drawing.name || ("Cable " + (state.floorCables.length + state.wallCables.length + 1)),
    anchorIds: drawing.anchorIds.slice(),
  };
  list.push(c);
  state.selectedCable = c;
  gcAnchors();
  recomputeTrueCables();
  exitFocus();
  drawing = null; drawingPreview = null;
  flashStatus(T("msgCableCreated")(c.anchorIds.length), "ok");
  draw();
}

function addDrawPoint(sx, sy, altKey) {
  if (!drawing) return;
  const view = drawing.view;

  if (altKey) {
    const target = findMergeTarget(view, sx, sy);
    if (target) {
      if (drawing.anchorIds.length === 0) adoptMergeBase(target);
      else                               mergeDrawingInto(target);
      return;
    }
  }

  const reuseFilter = (a) => isReusableAnchor(a, altKey, view);

  let anchorId = null;
  const nearby = findAnchorAtScreen(view, sx, sy, reuseFilter);
  if (nearby) anchorId = nearby.id;

  let spec = null;
  if (anchorId == null) {
    if (view === "floor") {
      const [wx, wy] = s2wFloor(sx, sy);
      spec = snapFloorSpec(wx, wy, snapEnabled);
    } else {
      const [u, v] = s2wWall(sx, sy);
      spec = snapWallSpec(u, v, snapEnabled);
      if (!spec) { draw(); return; }
    }
  }

  const scale = (view === "floor")
    ? viewFloor.scale
    : Math.min(viewWall.scaleX, viewWall.scaleY);
  const tolMm = ANCHOR_NEARBY_PX / scale;

  if (view === "wall" && anchorId == null && spec &&
      spec.space === "wall-edge" && drawing.anchorIds.length > 0) {
    const lastId = drawing.anchorIds[drawing.anchorIds.length - 1];
    const last = anchors.get(lastId);
    if (last && last.space === "wall-edge" && last.segIdx !== spec.segIdx) {
      const route = planWormholeRoute(
        { segIdx: last.segIdx, t: last.t, v: last.v },
        { segIdx: spec.segIdx, t: spec.t, v: spec.v }
      );
      if (route && route.length > 1) {
        let inserted = 0;
        for (let i = 1; i < route.length; i++) {
          const rid = resolveSpecToAnchorId(route[i], 5, null);
          if (rid == null) continue;
          const lastInList = drawing.anchorIds[drawing.anchorIds.length - 1];
          if (lastInList !== rid) {
            drawing.anchorIds.push(rid);
            inserted++;
          }
        }
        if (inserted > 0) {
          const lastAddedId = drawing.anchorIds[drawing.anchorIds.length - 1];
          const la = anchors.get(lastAddedId);
          if (la && la.space === "wall-edge" && la.segIdx !== focusSeg) {
            enterFocus(la.segIdx);
          }
          draw();
          return;
        }
      }
    }
  }

  if (anchorId == null) {
    anchorId = resolveSpecToAnchorId(spec, tolMm, reuseFilter);
    if (anchorId == null) { draw(); return; }
  }
  const last = drawing.anchorIds[drawing.anchorIds.length - 1];
  if (last === anchorId) { draw(); return; }
  drawing.anchorIds.push(anchorId);

  if (view === "wall") {
    const a = anchors.get(anchorId);
    let segIdx = -1;
    if (a && a.space === "wall-edge") segIdx = a.segIdx;
    if (segIdx >= 0 && segIdx !== focusSeg) {
      enterFocus(segIdx);
    }
  }

  draw();
}

function findAnchorAtScreen(view, sx, sy, reuseFilter) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor") ? w2sFloor   : w2sWall;
  let best = null, bestD = ANCHOR_NEARBY_PX;
  for (const a of anchors.values()) {
    if (view === "wall" && a.space === "wall-edge" && isSegHidden(a.segIdx))
      continue;
    if (reuseFilter && !reuseFilter(a)) continue;
    const p = project(a);
    if (!p) continue;
    const [px, py] = toScreen(p[0], p[1]);
    const d = Math.hypot(px - sx, py - sy);
    if (d < bestD) { bestD = d; best = a; }
  }
  return best;
}

function anchorOwnerCable(anchorId) {
  for (const c of state.floorCables) {
    if (c.anchorIds.includes(anchorId)) return { cable: c, view: "floor" };
  }
  for (const c of state.wallCables) {
    if (c.anchorIds.includes(anchorId)) return { cable: c, view: "wall" };
  }
  return null;
}

function isReusableAnchor(a, altHeld, currentView) {
  if (!a) return false;
  if (altHeld) return true;
  if (drawing && drawing.anchorIds.includes(a.id)) return true;
  if (currentView) {
    const owner = anchorOwnerCable(a.id);
    if (owner && owner.view !== currentView) return true;
  }
  return false;
}

function adoptMergeBase(target) {
  const E = target.cable;
  const ids = (target.endpointIdx === 0)
    ? E.anchorIds.slice().reverse()
    : E.anchorIds.slice();
  drawing.id = E.id;
  drawing.name = E.name;
  drawing.anchorIds = ids;
  drawing.baseCableId = E.id;
  if (drawing.view === "wall") {
    const lastId = ids[ids.length - 1];
    const a = anchors.get(lastId);
    if (a && a.space === "wall-edge") enterFocus(a.segIdx);
  }
  flashStatus(T("msgAdopted"), "ok");
  draw();
}

function mergeDrawingInto(target) {
  const E = target.cable;
  const otherIds = (target.endpointIdx === 0)
    ? E.anchorIds.slice()
    : E.anchorIds.slice().reverse();
  let tail = otherIds;
  if (drawing.anchorIds.length > 0 && tail.length > 0 &&
      drawing.anchorIds[drawing.anchorIds.length - 1] === tail[0]) {
    tail = tail.slice(1);
  }
  const mergedIds = drawing.anchorIds.concat(tail);

  if (drawing.baseCableId != null && drawing.baseCableId !== E.id)
    removeCableById(drawing.baseCableId);
  removeCableById(E.id);

  const list = (drawing.view === "floor") ? state.floorCables : state.wallCables;
  const c = {
    id: (drawing.id != null) ? drawing.id : idCounter++,
    name: drawing.name || ("Cable " + (state.floorCables.length + state.wallCables.length + 1)),
    anchorIds: mergedIds,
  };
  list.push(c);
  state.selectedCable = c;
  state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  exitFocus();
  drawing = null; drawingPreview = null;
  flashStatus(T("msgMerged")(c.anchorIds.length), "ok");
  draw();
}

function removeCableById(id) {
  let i = state.floorCables.findIndex(c => c.id === id);
  if (i >= 0) { state.floorCables.splice(i, 1); return; }
  i = state.wallCables.findIndex(c => c.id === id);
  if (i >= 0) { state.wallCables.splice(i, 1); }
}

function gcAnchors() {
  const referenced = new Set();
  for (const c of state.floorCables) for (const id of c.anchorIds) referenced.add(id);
  for (const c of state.wallCables)  for (const id of c.anchorIds) referenced.add(id);
  for (const id of Array.from(anchors.keys())) {
    if (!referenced.has(id)) anchors.delete(id);
  }
}

function clearAll() {
  if (!confirm(T("msgConfirmClear"))) return;
  exitFocus();
  anchors.clear();
  state.floorGrids = []; state.wallGrids = [];
  state.floorCables = []; state.wallCables = [];
  state.trueCables = [];
  state.selectedCable = null; state.selectedVertex = null;
  drawing = null; drawingPreview = null;
  NOTES.length = 0;
  draw();
}

/* ==========================================================================
   DELETION
   ========================================================================== */

function deleteSelectedCable() {
  const c = state.selectedCable;
  if (!c) { flashStatus(T("msgNoCableSelected"), "warn"); return; }
  removeCableById(c.id);
  state.selectedCable = null; state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus(T("msgCableDeleted"), "ok");
  draw();
}

function deleteSelectedVertex() {
  const sv = state.selectedVertex;
  if (!sv) { flashStatus(T("msgNoVertexSelected"), "warn"); return; }
  const { cable, index } = sv;
  if (!cable || !cable.anchorIds ||
      index < 0 || index >= cable.anchorIds.length) {
    state.selectedVertex = null; draw(); return;
  }
  cable.anchorIds.splice(index, 1);
  if (cable.anchorIds.length < 2) {
    removeCableById(cable.id);
    state.selectedCable = null;
  }
  state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus(T("msgVertexDeleted"), "ok");
  draw();
}

/* ==========================================================================
   DRAGGING
   ==========================================================================
   A vertex drag can take one of three forms:

     • a floor-space drag — the anchor is converted to a floor anchor
       at the cursor's world position, snapped to a grid line or wall
       attach when Shift is held
     • a wall-space drag on a non-corner anchor — the anchor is
       converted to a wall-edge anchor on the segment under the
       cursor, at the snapped t and clamped v
     • a wall-space drag on a corner anchor — the anchor is pinned to
       the corner's own side (t = 0 or 1), v follows the cursor, and
       every partner anchor at the same physical corner tracks the
       same v

   The same convertAnchor primitive performs the first two: it strips
   the anchor's old space-specific fields and copies the new spec's
   fields on, clamping the v if the new segment is a step.  The third
   form is _applyCornerDrag, which does not call convertAnchor at all
   because the anchor stays on the same segment and only its v moves. */

function convertAnchor(a, spec) {
  delete a.x; delete a.y; delete a.segIdx; delete a.t;
  delete a.gridId; delete a.gridAxis;
  a.space = spec.space;
  for (const k of Object.keys(spec)) {
    if (k === "space") continue;
    a[k] = spec[k];
  }
  if (a.space === "wall-edge") clampAnchorToSegment(a);
}

function computeCornerInfo(a) {
  const jid = junctionOfAnchor(a);
  if (jid < 0) return null;
  const side = Math.abs(a.t) < 0.01 ? 0 : 1;

  const vSource = a.v || 0;
  const partners = [];
  const j = JUNCTIONS[jid];
  if (j) {
    for (const t of j.terminals) {
      if (t.segIdx === a.segIdx) continue;
      for (const other of anchors.values()) {
        if (other === a) continue;
        if (other.space !== "wall-edge") continue;
        if (other.segIdx !== t.segIdx) continue;
        const oside = Math.abs(other.t) < 0.01 ? 0
                    : Math.abs(other.t - 1) < 0.01 ? 1 : -1;
        if (oside !== t.side) continue;
        if (Math.abs((other.v || 0) - vSource) > 5.0) continue;
        partners.push(other);
      }
    }
  }
  return { junctionId: jid, side, partners };
}

function _applyCornerDrag(a, corner, sx, sy) {
  const s = WALL.segments[a.segIdx];
  if (!s) return;
  const [u, v] = s2wWall(sx, sy);

  let nv;
  if (isStepFace(s)) {
    nv = (v > segTop(s) * 0.5) ? segTop(s) : 0;
  } else {
    nv = Math.max(segBottom(s), Math.min(segTop(s), v));
  }
  if (snapEnabled) {
    const SNAP = 22 / Math.min(viewWall.scaleX, viewWall.scaleY);
    if (Math.abs(v) < SNAP) nv = 0;
    if (isStepFace(s) && Math.abs(v - segTop(s)) < SNAP) nv = segTop(s);
  }

  a.t = corner.side;
  a.v = nv;
  clampAnchorToSegment(a);

  for (const p of corner.partners) {
    p.v = a.v;
    clampAnchorToSegment(p);
  }
}

function _applyVertexDrag(a, view, sx, sy) {
  if (view === "floor") {
    const [wx, wy] = s2wFloor(sx, sy);
    if (snapEnabled) {
      const SNAP = 14 / viewFloor.scale;
      for (const g of state.floorGrids) {
        if (g.type === "ns" && Math.abs(wx - g.pos) < SNAP) {
          convertAnchor(a, { space: "floor", x: g.pos, y: wy,
                             gridId: g.id, gridAxis: "ns" });
          return;
        }
        if (g.type === "ew" && Math.abs(wy - g.pos) < SNAP) {
          convertAnchor(a, { space: "floor", x: wx, y: g.pos,
                             gridId: g.id, gridAxis: "ew" });
          return;
        }
      }
      const wall = findWallAttachSpec(wx, wy);
      if (wall) { convertAnchor(a, wall); return; }
    }
    convertAnchor(a, { space: "floor", x: wx, y: wy });
    return;
  }

  const [u, v] = s2wWall(sx, sy);

  if (snapEnabled) {
    const edge = findWallEdgeSnapSpec(u, v);
    if (edge) { convertAnchor(a, edge); return; }
  }

  const attach = uToWallAttach(u, v);
  if (attach) {
    const s = WALL.segments[attach.segIdx];
    const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
    convertAnchor(a, { space: "wall-edge", segIdx: attach.segIdx,
                       t: attach.t, v: vv });
    return;
  }
  const i = nearestSegmentToUV(u, v);
  if (i < 0) return;
  const s = WALL.segments[i];
  const t = Math.max(0, Math.min(1, (u - s.u0) / s.len));
  const vv = Math.max(segBottom(s), Math.min(segTop(s), v));
  convertAnchor(a, { space: "wall-edge", segIdx: i, t, v: vv });
}

function handleVertexDrag(sx, sy) {
  const { cable, index, view, corner } = state.dragVertex;
  const anchorId = cable.anchorIds[index];
  const a = anchors.get(anchorId);
  if (!a) return;

  if (view === "wall" && corner) {
    _applyCornerDrag(a, corner, sx, sy);
    return;
  }

  _applyVertexDrag(a, view, sx, sy);
}

function handleGridDrag(sx, sy) {
  const { grid, view } = state.dragGrid;
  if (view === "floor") {
    const [wx, wy] = s2wFloor(sx, sy);
    grid.pos = (grid.type === "ns") ? wx : wy;
    for (const a of anchors.values()) {
      if (a.space !== "floor") continue;
      if (a.gridId !== grid.id) continue;
      if (grid.type === "ns") a.x = grid.pos;
      else                    a.y = grid.pos;
    }
  } else {
    const [u, v] = s2wWall(sx, sy);
    grid.pos = (grid.type === "v") ? u : v;
    for (const a of anchors.values()) {
      if (a.space !== "wall-edge") continue;
      if (a.gridId !== grid.id) continue;
      const uv = anchorWall(a);
      if (!uv) continue;
      if (grid.type === "v") {
        const attach = uToWallAttach(grid.pos, uv[1]);
        if (attach) {
          a.segIdx = attach.segIdx;
          a.t = attach.t;
        }
      } else {
        a.v = grid.pos;
        clampAnchorToSegment(a);
      }
    }
  }
}

/* ==========================================================================
   HIT TESTING
   ==========================================================================
   Five separate tests, run in a fixed priority order by the event
   dispatcher:

     1. a floor grid line within 7 px of the cursor → drag the line
     2. a floor vertex within 9 px                     → select/drag vertex
     3. a floor cable polyline within 12 px            → select cable
     4. a wall grid line, then a wall vertex, then a wall cable
        polyline, same thresholds but in strip coordinates

   The cable polyline test walks the cable's projected vertices and
   calls polylineHit against the resulting point list, so a cable with
   a hop between distant walls still hit-tests on both sides of the
   hop. */

function nearestFloorVertex(sx, sy) {
  for (let ci = state.floorCables.length - 1; ci >= 0; ci--) {
    const c = state.floorCables[ci];
    if (drawing && drawing.baseCableId === c.id) continue;
    for (let i = 0; i < c.anchorIds.length; i++) {
      const a = anchors.get(c.anchorIds[i]);
      const p = a ? anchorPlan(a) : null;
      if (!p) continue;
      const [vx, vy] = w2sFloor(p[0], p[1]);
      if (Math.hypot(sx - vx, sy - vy) < 9) return { cable: c, index: i };
    }
  }
  return null;
}
function nearestWallVertex(sx, sy) {
  for (let ci = state.wallCables.length - 1; ci >= 0; ci--) {
    const c = state.wallCables[ci];
    if (drawing && drawing.baseCableId === c.id) continue;
    for (let i = 0; i < c.anchorIds.length; i++) {
      const a = anchors.get(c.anchorIds[i]);
      if (!a) continue;
      if (a.space === "wall-edge" && isSegHidden(a.segIdx)) continue;
      const p = anchorWall(a);
      if (!p) continue;
      const [vx, vy] = w2sWall(p[0], p[1]);
      if (Math.hypot(sx - vx, sy - vy) < 9) return { cable: c, index: i };
    }
  }
  return null;
}
function nearestFloorGridLine(sx, sy) {
  for (const g of state.floorGrids) {
    if (g.type === "ns") {
      const [gx] = w2sFloor(g.pos, 0);
      if (Math.abs(sx - gx) < 7) return g;
    } else {
      const [, gy] = w2sFloor(0, g.pos);
      if (Math.abs(sy - gy) < 7) return g;
    }
  }
  return null;
}
function nearestWallGridLine(sx, sy) {
  if (focusSavedU) return null;
  for (const g of state.wallGrids) {
    if (g.type === "v") {
      const [gx] = w2sWall(g.pos, 0);
      if (Math.abs(sx - gx) < 7) return g;
    } else {
      const [, gy] = w2sWall(0, g.pos);
      if (Math.abs(sy - gy) < 7) return g;
    }
  }
  return null;
}

function polylineHitView(view, x, y, c, tol) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const pts = [];
  for (const id of c.anchorIds) {
    const a = anchors.get(id);
    if (view === "wall" && a && a.space === "wall-edge" && isSegHidden(a.segIdx))
      continue;
    const p = a ? project(a) : null;
    if (p) pts.push(p);
  }
  return polylineHit(x, y, pts, tol);
}
"""
