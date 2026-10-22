"""
pg_edit.py — cable lifecycle: draw, delete, drag, hit test.

ES module.  The interaction layer for cables.  Owns:

    • the drawing lifecycle — beginDraw, finishDraw, cancelDraw, and
      the per-click addDrawPoint with its wormhole-route planner, its
      cross-view reuse policy, and its Alt+click adopt/merge path
    • anchor resolution helpers — findAnchorAtScreen,
      anchorOwnerCable, isReusableAnchor
    • the merge operations — adoptMergeBase, mergeDrawingInto
    • cable removal — removeCableById, deleteSelectedCable,
      deleteSelectedVertex, clearAll
    • vertex and grid dragging — convertAnchor, computeCornerInfo,
      the two per-view appliers, and the two handlers the event
      dispatcher calls
    • hit testing for selection — nearestFloorVertex,
      nearestWallVertex, the two grid-line tests, and polylineHitView

EXCLUSION WITH pg_ruler
=======================
The two modes are mutually exclusive.  `beginDraw` disarms the ruler's
state and clears its selection; `toggleRuler` (in pg_ruler) calls
`cancelDraw(true)`.  Both are top-level function calls; no deeper
coupling.

DRAW CALLS
==========
Every place that used to call `draw()` now calls `callDraw()` from
pg_state.  Importing `draw` statically from pg_dispatch would create
the cycle pg_dispatch → pg_edit → pg_dispatch; the injected hook
breaks it and is what the boot module wires.

`registryState` and `flashStatus` are static imports.  `finishDraw`
and `mergeDrawingInto` are therefore fully synchronous: they allocate
an id from `registryState.idCounter`, push the cable onto the list,
sync the model, and repaint, all in one call.  The earlier version
used an awkward `import(...).then(...)` chain to defer the id
allocation, which worked but made the finish path racy against
`state.drawing` clearing.

`computeCornerInfo` is synchronous too: it reads the populated
`junctionGraph.JUNCTIONS` directly, not via a dynamic import.
"""

CABLE_EDIT_JS = r"""
import {
  WALL, WALL_HEIGHT,
  viewFloor, viewWall,
  state, anchors, junctionGraph, registryState,
  segTop, segBottom, isStepFace, pointToSegmentDist, polylineHit,
  callDraw,
  ANCHOR_NEARBY_PX,
} from "./pg_state.js";
import {
  isSegHidden, anchorPlan, anchorWall,
  anchorIsStepFace, anchorSide, junctionOfAnchor,
  clampAnchorToSegment, resolveSpecToAnchorId,
  wallAttachToPlan, wallAttachToU, uToWallAttach, nearestSegmentToUV,
  gcAnchors, recomputeTrueCables,
  flashStatus,
} from "./pg_core.js";
import {
  snapFloorSpec, snapWallSpec, findWallAttachSpec,
  findWallEdgeSnapSpec,
} from "./pg_snap.js";
import { planWormholeRoute, findMergeTarget } from "./pg_route.js";
import { T } from "./pg_i18n.js";
import {
  _rulerCloseNumericInput, _rulerSyncMeasureInspector,
} from "./pg_ruler.js";

/* ==========================================================================
   DRAWING LIFECYCLE
   ========================================================================== */

export function beginDraw() {
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
  state.drawing = {
    view: null,
    anchorIds: [],
    id: null,
    name: null,
    baseCableId: null,
  };
  state.drawingPreview = null;
  state.selectedCable  = null;
  state.selectedVertex = null;
  flashStatus(T("msgDrawStart"), "ok");
  callDraw();
}

export function cancelDraw(silent) {
  if (!state.drawing) return;
  state.drawing = null;
  state.drawingPreview = null;
  if (!silent) flashStatus(T("msgDrawCancelled"), "warn");
  callDraw();
}

export function finishDraw() {
  if (!state.drawing) return;
  if (state.drawing.view === null) {
    cancelDraw();
    return;
  }
  if (state.drawing.anchorIds.length < 2) {
    flashStatus(T("msgNeedTwoPoints"), "warn");
    state.drawing = null;
    state.drawingPreview = null;
    callDraw();
    return;
  }
  const list = (state.drawing.view === "floor")
    ? state.floorCables
    : state.wallCables;
  if (state.drawing.baseCableId != null) {
    removeCableById(state.drawing.baseCableId);
  }
  const c = {
    id: (state.drawing.id != null)
      ? state.drawing.id
      : registryState.idCounter++,
    name: state.drawing.name || ("Cable " +
      (state.floorCables.length + state.wallCables.length + 1)),
    anchorIds: state.drawing.anchorIds.slice(),
  };
  list.push(c);
  state.selectedCable  = c;
  state.drawing        = null;
  state.drawingPreview = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus(T("msgCableCreated")(c.anchorIds.length), "ok");
  callDraw();
}

/* ==========================================================================
   ADD DRAW POINT
   ========================================================================== */

export function addDrawPoint(sx, sy, altKey) {
  if (!state.drawing) return;
  const view = state.drawing.view;

  if (altKey) {
    const target = findMergeTarget(view, sx, sy);
    if (target) {
      if (state.drawing.anchorIds.length === 0) adoptMergeBase(target);
      else                                     mergeDrawingInto(target);
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
      const [wx, wy] = (function () {
        return [(sx - viewFloor.tx) / viewFloor.scale,
                -(sy - viewFloor.ty) / viewFloor.scale];
      })();
      spec = snapFloorSpec(wx, wy, state.snapEnabled);
    } else {
      const [u, v] = (function () {
        return [(sx - viewWall.tx) / viewWall.scaleX,
                -(sy - viewWall.ty) / viewWall.scaleY];
      })();
      spec = snapWallSpec(u, v, state.snapEnabled);
      if (!spec) { callDraw(); return; }
    }
  }

  const scale = (view === "floor")
    ? viewFloor.scale
    : Math.min(viewWall.scaleX, viewWall.scaleY);
  const tolMm = ANCHOR_NEARBY_PX / scale;

  if (view === "wall" && anchorId == null && spec &&
      spec.space === "wall-edge" && state.drawing.anchorIds.length > 0) {
    const lastId = state.drawing.anchorIds[state.drawing.anchorIds.length - 1];
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
          const lastInList =
            state.drawing.anchorIds[state.drawing.anchorIds.length - 1];
          if (lastInList !== rid) {
            state.drawing.anchorIds.push(rid);
            inserted++;
          }
        }
        if (inserted > 0) {
          const lastAddedId =
            state.drawing.anchorIds[state.drawing.anchorIds.length - 1];
          const la = anchors.get(lastAddedId);
          if (la && la.space === "wall-edge" && la.segIdx !== state.focusSeg) {
            enterFocus(la.segIdx);
          }
          callDraw();
          return;
        }
      }
    }
  }

  if (anchorId == null) {
    anchorId = resolveSpecToAnchorId(spec, tolMm, reuseFilter);
    if (anchorId == null) { callDraw(); return; }
  }
  const last = state.drawing.anchorIds[state.drawing.anchorIds.length - 1];
  if (last === anchorId) { callDraw(); return; }
  state.drawing.anchorIds.push(anchorId);

  if (view === "wall") {
    const a = anchors.get(anchorId);
    let segIdx = -1;
    if (a && a.space === "wall-edge") segIdx = a.segIdx;
    if (segIdx >= 0 && segIdx !== state.focusSeg) {
      enterFocus(segIdx);
    }
  }

  callDraw();
}

/* ==========================================================================
   ANCHOR RESOLUTION
   ========================================================================== */

export function findAnchorAtScreen(view, sx, sy, reuseFilter) {
  const project  = (view === "floor") ? anchorPlan : anchorWall;
  const toScreen = (view === "floor")
    ? ((x, y) => [x * viewFloor.scale + viewFloor.tx,
                  -y * viewFloor.scale + viewFloor.ty])
    : ((u, v) => [u * viewWall.scaleX + viewWall.tx,
                  -v * viewWall.scaleY + viewWall.ty]);
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

export function anchorOwnerCable(anchorId) {
  for (const c of state.floorCables) {
    if (c.anchorIds.includes(anchorId)) return { cable: c, view: "floor" };
  }
  for (const c of state.wallCables) {
    if (c.anchorIds.includes(anchorId)) return { cable: c, view: "wall" };
  }
  return null;
}

export function isReusableAnchor(a, altHeld, currentView) {
  if (!a) return false;
  if (altHeld) return true;
  if (state.drawing && state.drawing.anchorIds.includes(a.id)) return true;
  if (currentView) {
    const owner = anchorOwnerCable(a.id);
    if (owner && owner.view !== currentView) return true;
  }
  return false;
}

/* ==========================================================================
   MERGE OPERATIONS
   ========================================================================== */

function adoptMergeBase(target) {
  const E = target.cable;
  const ids = (target.endpointIdx === 0)
    ? E.anchorIds.slice().reverse()
    : E.anchorIds.slice();
  state.drawing.id = E.id;
  state.drawing.name = E.name;
  state.drawing.anchorIds = ids;
  state.drawing.baseCableId = E.id;
  if (state.drawing.view === "wall") {
    const lastId = ids[ids.length - 1];
    const a = anchors.get(lastId);
    if (a && a.space === "wall-edge") enterFocus(a.segIdx);
  }
  flashStatus(T("msgAdopted"), "ok");
  callDraw();
}

function mergeDrawingInto(target) {
  const E = target.cable;
  const otherIds = (target.endpointIdx === 0)
    ? E.anchorIds.slice()
    : E.anchorIds.slice().reverse();
  let tail = otherIds;
  if (state.drawing.anchorIds.length > 0 && tail.length > 0 &&
      state.drawing.anchorIds[state.drawing.anchorIds.length - 1] === tail[0]) {
    tail = tail.slice(1);
  }
  const mergedIds = state.drawing.anchorIds.concat(tail);

  if (state.drawing.baseCableId != null && state.drawing.baseCableId !== E.id)
    removeCableById(state.drawing.baseCableId);
  removeCableById(E.id);

  const list = (state.drawing.view === "floor")
    ? state.floorCables
    : state.wallCables;
  const c = {
    id: (state.drawing.id != null)
      ? state.drawing.id
      : registryState.idCounter++,
    name: state.drawing.name || ("Cable " +
      (state.floorCables.length + state.wallCables.length + 1)),
    anchorIds: mergedIds,
  };
  list.push(c);
  state.selectedCable  = c;
  state.selectedVertex = null;
  state.drawing        = null;
  state.drawingPreview = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus(T("msgMerged")(c.anchorIds.length), "ok");
  callDraw();
}

/* ==========================================================================
   CABLE REMOVAL
   ========================================================================== */

export function removeCableById(id) {
  let i = state.floorCables.findIndex(c => c.id === id);
  if (i >= 0) { state.floorCables.splice(i, 1); return; }
  i = state.wallCables.findIndex(c => c.id === id);
  if (i >= 0) { state.wallCables.splice(i, 1); }
}

export function deleteSelectedCable() {
  const c = state.selectedCable;
  if (!c) {
    flashStatus(T("msgNoCableSelected"), "warn");
    return;
  }
  removeCableById(c.id);
  state.selectedCable  = null;
  state.selectedVertex = null;
  gcAnchors();
  recomputeTrueCables();
  flashStatus(T("msgCableDeleted"), "ok");
  callDraw();
}

export function deleteSelectedVertex() {
  const sv = state.selectedVertex;
  if (!sv) {
    flashStatus(T("msgNoVertexSelected"), "warn");
    return;
  }
  const { cable, index } = sv;
  if (!cable || !cable.anchorIds ||
      index < 0 || index >= cable.anchorIds.length) {
    state.selectedVertex = null;
    callDraw();
    return;
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
  callDraw();
}

export function clearAll() {
  if (!confirm(T("msgConfirmClear"))) return;
  state.focusSeg    = -1;
  state.focusSavedU = null;
  anchors.clear();
  state.floorGrids  = [];
  state.wallGrids   = [];
  state.floorCables = [];
  state.wallCables  = [];
  state.selectedCable  = null;
  state.selectedVertex = null;
  state.drawing        = null;
  state.drawingPreview = null;
  import("./pg_state.js").then(m => { m.NOTES.length = 0; }).catch(() => {});
  callDraw();
}

/* ==========================================================================
   FOCUS MODE
   ========================================================================== */

export function enterFocus(segIdx) {
  exitFocus();
  if (segIdx < 0 || segIdx >= WALL.segments.length) return;

  const S = WALL.segments[segIdx];
  const leftNbrs = [], rightNbrs = [];

  for (const j of junctionGraph.JUNCTIONS) {
    const t_this = j.terminals.find(t => t.segIdx === segIdx);
    if (!t_this) continue;
    for (const o of j.terminals) {
      if (o.segIdx === segIdx) continue;
      if (t_this.side === 0) leftNbrs.push(o.segIdx);
      else                   rightNbrs.push(o.segIdx);
    }
  }

  state.focusSavedU = new Map();
  const save = (i) => state.focusSavedU.set(i, {
    u0: WALL.segments[i].u0,
    u1: WALL.segments[i].u1,
  });
  save(segIdx); leftNbrs.forEach(save); rightNbrs.forEach(save);

  let cursor = S.u0;
  for (const i of leftNbrs) {
    const T2 = WALL.segments[i];
    const L = T2.len;
    T2.u0 = cursor - L;
    T2.u1 = cursor;
    cursor -= L;
  }
  cursor = S.u1;
  for (const i of rightNbrs) {
    const T2 = WALL.segments[i];
    const L = T2.len;
    T2.u0 = cursor;
    T2.u1 = cursor + L;
    cursor += L;
  }

  const vis = new Set([segIdx, ...leftNbrs, ...rightNbrs]);
  for (let i = 0; i < WALL.segments.length; i++) {
    if (vis.has(i)) delete WALL.segments[i]._hidden;
    else            WALL.segments[i]._hidden = true;
  }

  state.focusSeg = segIdx;
  import("./pg_navigation.js").then(m => {
    if (typeof m.refitFocusView === "function") m.refitFocusView();
  }).catch(() => {});
}

export function exitFocus() {
  if (state.focusSavedU) {
    for (const [i, u] of state.focusSavedU) {
      WALL.segments[i].u0 = u.u0;
      WALL.segments[i].u1 = u.u1;
    }
  }
  for (const s of WALL.segments) delete s._hidden;
  state.focusSavedU = null;
  state.focusSeg    = -1;
  import("./pg_navigation.js").then(m => {
    if (typeof m.fitViews === "function") m.fitViews();
  }).catch(() => {});
}

/* ==========================================================================
   VERTEX DRAGGING
   ========================================================================== */

export function convertAnchor(a, spec) {
  delete a.x; delete a.y; delete a.segIdx; delete a.t;
  delete a.gridId; delete a.gridAxis;
  a.space = spec.space;
  for (const k of Object.keys(spec)) {
    if (k === "space") continue;
    a[k] = spec[k];
  }
  if (a.space === "wall-edge") clampAnchorToSegment(a);
}

export function computeCornerInfo(a) {
  const jid = junctionOfAnchor(a);
  if (jid < 0) return null;
  const side = Math.abs(a.t) < 0.01 ? 0 : 1;

  const vSource = a.v || 0;
  const partners = [];
  const JUNCTIONS = junctionGraph.JUNCTIONS;
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

export function _applyCornerDrag(a, corner, sx, sy) {
  const s = WALL.segments[a.segIdx];
  if (!s) return;
  const v = -(sy - viewWall.ty) / viewWall.scaleY;

  let nv;
  if (isStepFace(s)) {
    nv = (v > segTop(s) * 0.5) ? segTop(s) : 0;
  } else {
    nv = Math.max(segBottom(s), Math.min(segTop(s), v));
  }
  if (state.snapEnabled) {
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

export function _applyVertexDrag(a, view, sx, sy) {
  if (view === "floor") {
    const wx = (sx - viewFloor.tx) / viewFloor.scale;
    const wy = -(sy - viewFloor.ty) / viewFloor.scale;
    if (state.snapEnabled) {
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

  const u = (sx - viewWall.tx) / viewWall.scaleX;
  const v = -(sy - viewWall.ty) / viewWall.scaleY;

  if (state.snapEnabled) {
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

export function handleVertexDrag(sx, sy) {
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

export function handleGridDrag(sx, sy) {
  const { grid, view } = state.dragGrid;
  if (view === "floor") {
    const wx = (sx - viewFloor.tx) / viewFloor.scale;
    const wy = -(sy - viewFloor.ty) / viewFloor.scale;
    grid.pos = (grid.type === "ns") ? wx : wy;
    for (const a of anchors.values()) {
      if (a.space !== "floor") continue;
      if (a.gridId !== grid.id) continue;
      if (grid.type === "ns") a.x = grid.pos;
      else                    a.y = grid.pos;
    }
  } else {
    const u = (sx - viewWall.tx) / viewWall.scaleX;
    const v = -(sy - viewWall.ty) / viewWall.scaleY;
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
   ========================================================================== */

export function nearestFloorVertex(sx, sy) {
  for (let ci = state.floorCables.length - 1; ci >= 0; ci--) {
    const c = state.floorCables[ci];
    if (state.drawing && state.drawing.baseCableId === c.id) continue;
    for (let i = 0; i < c.anchorIds.length; i++) {
      const a = anchors.get(c.anchorIds[i]);
      const p = a ? anchorPlan(a) : null;
      if (!p) continue;
      const [vx, vy] = [p[0] * viewFloor.scale + viewFloor.tx,
                        -p[1] * viewFloor.scale + viewFloor.ty];
      if (Math.hypot(sx - vx, sy - vy) < 9) return { cable: c, index: i };
    }
  }
  return null;
}

export function nearestWallVertex(sx, sy) {
  for (let ci = state.wallCables.length - 1; ci >= 0; ci--) {
    const c = state.wallCables[ci];
    if (state.drawing && state.drawing.baseCableId === c.id) continue;
    for (let i = 0; i < c.anchorIds.length; i++) {
      const a = anchors.get(c.anchorIds[i]);
      if (!a) continue;
      if (a.space === "wall-edge" && isSegHidden(a.segIdx)) continue;
      const p = anchorWall(a);
      if (!p) continue;
      const [vx, vy] = [p[0] * viewWall.scaleX + viewWall.tx,
                        -p[1] * viewWall.scaleY + viewWall.ty];
      if (Math.hypot(sx - vx, sy - vy) < 9) return { cable: c, index: i };
    }
  }
  return null;
}

export function nearestFloorGridLine(sx, sy) {
  for (const g of state.floorGrids) {
    if (g.type === "ns") {
      const gx = g.pos * viewFloor.scale + viewFloor.tx;
      if (Math.abs(sx - gx) < 7) return g;
    } else {
      const gy = -g.pos * viewFloor.scale + viewFloor.ty;
      if (Math.abs(sy - gy) < 7) return g;
    }
  }
  return null;
}

export function nearestWallGridLine(sx, sy) {
  if (state.focusSavedU) return null;
  for (const g of state.wallGrids) {
    if (g.type === "v") {
      const gx = g.pos * viewWall.scaleX + viewWall.tx;
      if (Math.abs(sx - gx) < 7) return g;
    } else {
      const gy = -g.pos * viewWall.scaleY + viewWall.ty;
      if (Math.abs(sy - gy) < 7) return g;
    }
  }
  return null;
}

export function polylineHitView(view, x, y, c, tol) {
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

/* `findMergeTarget` is defined in pg_route.js.  Re-export it here so
   pg_edit reads as the single editing surface. */
export { findMergeTarget };
"""
