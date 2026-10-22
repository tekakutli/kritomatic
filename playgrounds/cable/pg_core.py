"""
pg_core.py — anchor registry, wall geometry, junction graph, save/load,
keyboard, grid adders, and UI feedback helpers.

ES module.  Imports from `pg_state.js` and nothing else from the
project (all the numeric helpers, palette, canvas, transforms, and
mutable state live in state).

Module docstring in the source lists what used to live here in the
concatenation model but is now dead or moved:

    resize / fitViews      were here but overridden by pg_navigation's
                           identical definitions later in the bundle.
                           They are dropped here; navigation owns them.
    _setDraw / _draw       same story — replaced by state's hooks.

WHAT'S LEFT
===========
    • anchor registry — makeAnchor, resolveSpecToAnchorId,
      clampAnchorToSegment, anchorPlan, anchorWall, anchorIsStepFace,
      anchorSide
    • wall-edge geometry — wallAttachToPlan, wallAttachToU,
      uToWallAttach, nearestSegmentToUV, findSegmentAtU
    • segment-reference round-trip — segRefOf, findSegmentByRef,
      findSegmentByWorld
    • junction graph — buildJunctionGraph, junctionOfTerminal,
      junctionOfAnchor, wallEdgeKey
    • save/load — serializeAnchor, deserializeAnchor, serialize,
      migrateV1Cable, applyLoaded, saveToServer, loadFromServer, doSave
    • true-cable grouping — allCables, trueCableIdOf, trueCableSiblings,
      recomputeTrueCables
    • anchor GC — gcAnchors
    • panel collapse/restore — collapsePanel, restorePanel
    • keyboard listeners — installKeyboard
    • grid adders — addFloorNsGrid, addFloorEwGrid, addWallVGrid,
      addWallHGrid
    • UI feedback — flashStatus, updateSnapPill, updateDrawButton

CROSS-MODULE CALLS
==================
The keyboard handlers reach into pg_edit for `finishDraw`,
`deleteSelectedCable`, `deleteSelectedVertex`, and into pg_ruler for
`_rulerCloseNumericInput`, `_rulerSyncMeasureInspector`, and
`toggleRuler`.  Static imports would produce cycles because
pg_edit / pg_ruler both reach back for state and (via draw hook)
dispatch.  Dynamic `import(...)` defers resolution until after the
boot module has run; a keyboard event is slow relative to a
microtask, so the deferral is invisible.

The grid adders reach into `pg_dispatch` for `draw` through the
`callDraw` hook — no dynamic import needed.
"""

CORE_JS = r"""
import {
  GEOMETRY, WALL, WALL_HEIGHT, STATE_FILE, ANCHOR_NEARBY_PX,
  canvas, ctx, env, layout, viewFloor, viewWall, router,
  state, mouse, anchors, registryState, junctionGraph,
  PALETTE, FONT_MONO, FONT_SANS,
  segTop, segBottom, isStepFace,
  pointInPolygon, pointToSegmentDist, polylineHit, fmtCm,
  callDraw, notifyChanged,
} from "./pg_state.js";
import { T } from "./pg_i18n.js";

/* ==========================================================================
   JUNCTION GRAPH
   ========================================================================== */

export function wallEdgeKey(segIdx, side) { return segIdx + ":" + side; }

export function buildJunctionGraph() {
  const TOL = 3.0;
  const N   = WALL.segments.length;

  const parent = new Map();
  const ensure = (k) => { if (!parent.has(k)) parent.set(k, k); };
  const find = (k) => {
    while (parent.get(k) !== k) {
      parent.set(k, parent.get(parent.get(k)));
      k = parent.get(k);
    }
    return k;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(ra, rb);
  };

  for (let i = 0; i < N; i++) {
    ensure(wallEdgeKey(i, 0));
    ensure(wallEdgeKey(i, 1));
  }

  const endpointOf = (segIdx, side) => {
    const s = WALL.segments[segIdx];
    return side === 0 ? s.a : s.b;
  };

  for (let i = 0; i < N; i++) {
    for (let j = i + 1; j < N; j++) {
      for (const si of [0, 1]) {
        const pi = endpointOf(i, si);
        for (const sj of [0, 1]) {
          const pj = endpointOf(j, sj);
          if (Math.hypot(pi[0]-pj[0], pi[1]-pj[1]) < TOL) {
            union(wallEdgeKey(i, si), wallEdgeKey(j, sj));
          }
        }
      }
    }
  }

  const groups = new Map();
  for (const k of parent.keys()) {
    const r = find(k);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r).push(k);
  }

  junctionGraph.JUNCTIONS = [];
  junctionGraph.JUNCTION_OF_TERMINAL = new Map();
  for (const [, terminals] of groups) {
    const parts0 = terminals[0].split(":");
    const p = endpointOf(Number(parts0[0]), Number(parts0[1]));
    const id = junctionGraph.JUNCTIONS.length;
    junctionGraph.JUNCTIONS.push({
      id,
      x: p[0], y: p[1],
      terminals: terminals.map(k => {
        const [si, ss] = k.split(":").map(Number);
        return { segIdx: si, side: ss };
      }),
    });
    for (const k of terminals) junctionGraph.JUNCTION_OF_TERMINAL.set(k, id);
  }

  junctionGraph.SEGMENT_ADJ = Array.from({ length: N }, () => new Set());
  for (const j of junctionGraph.JUNCTIONS) {
    for (const t1 of j.terminals) {
      for (const t2 of j.terminals) {
        if (t1.segIdx === t2.segIdx) continue;
        junctionGraph.SEGMENT_ADJ[t1.segIdx].add(t2.segIdx);
      }
    }
  }
  junctionGraph.SEGMENT_ADJ = junctionGraph.SEGMENT_ADJ.map(s => Array.from(s));

  junctionGraph.ADJ_DETAIL = new Map();
  for (const j of junctionGraph.JUNCTIONS) {
    for (const t1 of j.terminals) {
      for (const t2 of j.terminals) {
        if (t1.segIdx === t2.segIdx) continue;
        const key = t1.segIdx + ":" + t2.segIdx;
        if (!junctionGraph.ADJ_DETAIL.has(key)) {
          junctionGraph.ADJ_DETAIL.set(key, {
            junctionId: j.id,
            side: t1.side,
            other: t2.side,
          });
        }
      }
    }
  }
}

export function junctionOfTerminal(segIdx, side) {
  return junctionGraph.JUNCTION_OF_TERMINAL.get(wallEdgeKey(segIdx, side));
}

export function junctionOfAnchor(a) {
  if (!a || a.space !== "wall-edge") return -1;
  const T_END = 0.01;
  let side = -1;
  if (Math.abs(a.t) < T_END) side = 0;
  else if (Math.abs(a.t - 1) < T_END) side = 1;
  else return -1;
  const jid = junctionOfTerminal(a.segIdx, side);
  return (typeof jid === "number") ? jid : -1;
}

/* ==========================================================================
   ANCHOR REGISTRY
   ========================================================================== */

export function makeAnchor(space, fields) {
  const a = Object.assign({ id: registryState.idCounter++, space }, fields);
  anchors.set(a.id, a);
  return a;
}

export function anchorPlan(a) {
  if (!a) return null;
  if (a.space === "floor")     return [a.x, a.y];
  if (a.space === "wall-edge") return wallAttachToPlan(a.segIdx, a.t);
  return null;
}

export function anchorWall(a) {
  if (!a) return null;
  if (a.space === "wall-edge") return [wallAttachToU(a.segIdx, a.t), a.v];
  return null;
}

export function anchorIsStepFace(a) {
  if (!a || a.space !== "wall-edge") return false;
  const s = WALL.segments[a.segIdx];
  return !!(s && isStepFace(s));
}

export function anchorSide(a) {
  if (!anchorIsStepFace(a)) return null;
  const s = WALL.segments[a.segIdx];
  const top = segTop(s);
  if (Math.abs(a.v - top) < 0.5) return "top";
  if (Math.abs(a.v)      < 0.5) return "bottom";
  return null;
}

export function clampAnchorToSegment(a) {
  if (a.space !== "wall-edge") return;
  const s = WALL.segments[a.segIdx];
  if (!s) return;
  if (isStepFace(s)) {
    const top = segTop(s);
    a.v = (a.v > top * 0.5) ? top : 0;
  } else {
    a.v = Math.max(segBottom(s), Math.min(segTop(s), a.v));
  }
}

export function resolveSpecToAnchorId(spec, tolMm, reuseFilter) {
  if (!spec) return null;

  if (spec.space === "floor") {
    for (const a of anchors.values()) {
      if (a.space !== "floor") continue;
      if (reuseFilter && !reuseFilter(a)) continue;
      if (Math.hypot(a.x - spec.x, a.y - spec.y) < tolMm) return a.id;
    }
    return makeAnchor("floor", {
      x: spec.x, y: spec.y,
      gridId: spec.gridId, gridAxis: spec.gridAxis,
    }).id;
  }

  if (spec.space === "wall-edge") {
    const s = WALL.segments[spec.segIdx];
    if (!s) return null;
    let best = null, bestD = Infinity;
    for (const a of anchors.values()) {
      if (a.space !== "wall-edge") continue;
      if (a.segIdx !== spec.segIdx) continue;
      if (reuseFilter && !reuseFilter(a)) continue;
      if (Math.abs(a.v - spec.v) > tolMm) continue;
      const dt = Math.abs(a.t - spec.t) * s.len;
      if (dt < tolMm && dt < bestD) { bestD = dt; best = a; }
    }
    if (best) return best.id;
    const a = makeAnchor("wall-edge", {
      segIdx: spec.segIdx, t: spec.t, v: spec.v,
      gridId: spec.gridId, gridAxis: spec.gridAxis,
    });
    clampAnchorToSegment(a);
    return a.id;
  }

  return null;
}

/* ==========================================================================
   WALL-EDGE GEOMETRY
   ========================================================================== */

export function wallAttachToPlan(segIdx, t) {
  const s = WALL.segments[segIdx];
  if (!s) return null;
  return [s.a[0] + t * (s.b[0] - s.a[0]),
          s.a[1] + t * (s.b[1] - s.a[1])];
}
export function wallAttachToU(segIdx, t) {
  const s = WALL.segments[segIdx];
  return s.u0 + t * s.len;
}
export function uToWallAttach(u, v) {
  const cands = [];
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (u >= s.u0 - 0.01 && u <= s.u1 + 0.01) {
      const t = (u - s.u0) / s.len;
      cands.push({ segIdx: i, t: Math.max(0, Math.min(1, t)) });
    }
  }
  if (cands.length === 0) return null;
  if (cands.length === 1 || typeof v !== "number") return cands[0];
  let best = cands[0], bestD = Infinity;
  for (const c of cands) {
    const s = WALL.segments[c.segIdx];
    if (v >= s.z_lo - 0.5 && v <= s.z_hi + 0.5) return c;
    const d = Math.min(Math.abs(v - s.z_lo), Math.abs(v - s.z_hi));
    if (d < bestD) { bestD = d; best = c; }
  }
  return best;
}
export function nearestSegmentToUV(u, v) {
  let best = -1, bestD = Infinity;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    const du = Math.max(s.u0 - u, u - s.u1, 0);
    const dv = Math.max(segBottom(s) - v, v - segTop(s), 0);
    const d = Math.hypot(du, dv);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}
export function findSegmentAtU(u) {
  let wallHit = null;
  for (let i = 0; i < WALL.segments.length; i++) {
    if (isSegHidden(i)) continue;
    const s = WALL.segments[i];
    if (u >= s.u0 - 0.01 && u <= s.u1 + 0.01) {
      if (isStepFace(s)) return { segIdx: i, seg: s };
      if (!wallHit) wallHit = { segIdx: i, seg: s };
    }
  }
  return wallHit;
}

/* ---- Visibility (focus mode) ---- */

export function isSegHidden(i) {
  return WALL.segments[i] && WALL.segments[i]._hidden === true;
}

/* ==========================================================================
   SEGMENT-REFERENCE HELPERS
   ========================================================================== */

export function segRefOf(seg) {
  return { a: [seg.a[0], seg.a[1]], b: [seg.b[0], seg.b[1]] };
}

export function findSegmentByRef(ref) {
  if (!ref || !ref.a || !ref.b) return -1;
  const TOL = 2.0;
  const same = (p, q) => Math.hypot(p[0]-q[0], p[1]-q[1]) < TOL;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (same(s.a, ref.a) && same(s.b, ref.b)) return i;
    if (same(s.a, ref.b) && same(s.b, ref.a)) return i;
  }
  return -1;
}

export function findSegmentByWorld(world) {
  if (!world) return -1;
  let best = -1, bestD = 50;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1];
    const len2 = dx * dx + dy * dy;
    if (len2 < 1e-9) continue;
    let t = ((world[0] - s.a[0]) * dx + (world[1] - s.a[1]) * dy) / len2;
    t = Math.max(0, Math.min(1, t));
    const px = s.a[0] + t * dx, py = s.a[1] + t * dy;
    const d = Math.hypot(world[0] - px, world[1] - py);
    if (d < bestD) { bestD = d; best = i; }
  }
  return best;
}

/* ==========================================================================
   TRUE-CABLE GROUPING
   ========================================================================== */

export function allCables() {
  return state.floorCables.concat(state.wallCables);
}

export function trueCableIdOf(cable) {
  return (cable && cable.trueCableId != null) ? cable.trueCableId : cable.id;
}

export function trueCableSiblings(cable) {
  const tid = trueCableIdOf(cable);
  return allCables().filter(c => trueCableIdOf(c) === tid);
}

export function recomputeTrueCables() {
  const all = allCables();
  if (all.length === 0) { state.trueCables = []; return; }

  const parent = new Map();
  for (const c of all) parent.set(c.id, c.id);
  const find = (x) => {
    while (parent.get(x) !== x) {
      parent.set(x, parent.get(parent.get(x)));
      x = parent.get(x);
    }
    return x;
  };
  const union = (a, b) => {
    const ra = find(a), rb = find(b);
    if (ra !== rb) parent.set(Math.max(ra, rb), Math.min(ra, rb));
  };

  const anchorOwner = new Map();
  for (const c of all) {
    for (const aid of c.anchorIds) {
      if (anchorOwner.has(aid)) union(c.id, anchorOwner.get(aid));
      else                      anchorOwner.set(aid, c.id);
    }
  }

  for (const j of junctionGraph.JUNCTIONS) {
    const here = [];
    for (const t of j.terminals) {
      for (const a of anchors.values()) {
        if (a.space !== "wall-edge") continue;
        if (a.segIdx !== t.segIdx) continue;
        const side = Math.abs(a.t) < 0.01 ? 0
                   : Math.abs(a.t - 1) < 0.01 ? 1 : -1;
        if (side !== t.side) continue;
        here.push(a);
      }
    }
    for (let i = 0; i < here.length; i++) {
      for (let k = i + 1; k < here.length; k++) {
        if (Math.abs((here[i].v || 0) - (here[k].v || 0)) > 5.0) continue;
        const ci = anchorOwner.get(here[i].id);
        const ck = anchorOwner.get(here[k].id);
        if (ci != null && ck != null) union(ci, ck);
      }
    }
  }

  for (const c of all) c.trueCableId = find(c.id);

  const byRoot = new Map();
  for (const c of all) {
    const r = c.trueCableId;
    if (!byRoot.has(r)) byRoot.set(r, []);
    byRoot.get(r).push(c.id);
  }
  state.trueCables = Array.from(byRoot, ([id, parts]) => ({ id, parts }));
}

/* ==========================================================================
   ANCHOR GARBAGE COLLECTOR
   ========================================================================== */

export function gcAnchors() {
  const referenced = new Set();
  for (const c of state.floorCables) for (const id of c.anchorIds) referenced.add(id);
  for (const c of state.wallCables)  for (const id of c.anchorIds) referenced.add(id);
  for (const id of Array.from(anchors.keys())) {
    if (!referenced.has(id)) anchors.delete(id);
  }
}

/* ==========================================================================
   PANEL COLLAPSE / RESTORE
   ========================================================================== */

const uiEl         = document.getElementById("ui");
const restoreBtnEl = document.getElementById("restoreBtn");
const collapseBtnEl = document.getElementById("collapseBtn");

export function collapsePanel() {
  uiEl.classList.add("collapsed");
  restoreBtnEl.classList.add("visible");
}
export function restorePanel() {
  uiEl.classList.remove("collapsed");
  restoreBtnEl.classList.remove("visible");
}
if (collapseBtnEl) collapseBtnEl.onclick = collapsePanel;
if (restoreBtnEl)  restoreBtnEl.onclick  = restorePanel;

/* ==========================================================================
   SAVE / LOAD
   ========================================================================== */

export function serializeAnchor(a) {
  if (a.space === "floor") {
    return { id: a.id, space: "floor", x: a.x, y: a.y,
             gridId: a.gridId, gridAxis: a.gridAxis };
  }
  if (a.space === "wall-edge") {
    const s = WALL.segments[a.segIdx];
    const p = wallAttachToPlan(a.segIdx, a.t);
    const out = { id: a.id, space: "wall-edge",
                  t: a.t, v: a.v, world: [p[0], p[1]] };
    if (s) out.segRef = segRefOf(s);
    if (a.gridId != null) { out.gridId = a.gridId; out.gridAxis = a.gridAxis; }
    return out;
  }
  return null;
}

export function deserializeAnchor(data) {
  if (!data || typeof data !== "object") return null;
  if (data.space === "floor") {
    return { id: data.id, space: "floor", x: data.x, y: data.y,
             gridId: data.gridId, gridAxis: data.gridAxis };
  }
  if (data.space === "wall-edge" || data.space === "wall") {
    let segIdx, t, v;
    if (data.space === "wall-edge") {
      segIdx = findSegmentByRef(data.segRef);
      if (segIdx < 0 && Array.isArray(data.world)) {
        segIdx = findSegmentByWorld(data.world);
      }
      if (segIdx < 0 && typeof data.segIdx === "number") {
        segIdx = data.segIdx;
      }
      if (segIdx < 0 || !WALL.segments[segIdx]) return null;
      t = (typeof data.t === "number") ? data.t : 0;
      if (Array.isArray(data.world)) {
        const s = WALL.segments[segIdx];
        const dx = s.b[0] - s.a[0], dy = s.b[1] - s.a[1];
        const len2 = dx * dx + dy * dy;
        if (len2 > 1e-9) {
          t = ((data.world[0] - s.a[0]) * dx +
               (data.world[1] - s.a[1]) * dy) / len2;
          t = Math.max(0, Math.min(1, t));
        }
      }
      v = data.v;
    } else {
      const attach = uToWallAttach(data.u, data.v);
      if (!attach) return null;
      segIdx = attach.segIdx;
      t = attach.t;
      v = data.v;
    }
    const out = { id: data.id, space: "wall-edge", segIdx, t, v };
    if (data.gridId != null) { out.gridId = data.gridId; out.gridAxis = data.gridAxis; }
    clampAnchorToSegment(out);
    return out;
  }
  return null;
}

export function serialize() {
  const anchorArr = [];
  for (const a of anchors.values()) {
    const s = serializeAnchor(a);
    if (s) anchorArr.push(s);
  }
  return {
    version: 3,
    idCounter: registryState.idCounter,
    floorGrids: state.floorGrids,
    wallGrids:  state.wallGrids,
    anchors: anchorArr,
    floorCables: state.floorCables.map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })),
    wallCables: state.wallCables.map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })),
  };
}

export function migrateV1Cable(c, view) {
  if (c.role === "step-bridge") return null;
  const ids = [];
  for (let i = 0; i < c.points.length; i++) {
    const cap = c.captured && c.captured[i];
    let a = null;
    if (cap && cap.type === "wall-edge") {
      a = makeAnchor("wall-edge", {
        segIdx: cap.segIdx, t: cap.t,
        v: (typeof cap.v === "number") ? cap.v : 0,
      });
      clampAnchorToSegment(a);
    } else if (cap && (cap.type === "ns" || cap.type === "ew")) {
      a = makeAnchor("floor", {
        x: c.points[i][0], y: c.points[i][1],
        gridId: cap.gridId, gridAxis: cap.type,
      });
    } else if (cap && (cap.type === "v" || cap.type === "h")) {
      const attach = uToWallAttach(c.points[i][0], c.points[i][1]);
      if (attach) {
        a = makeAnchor("wall-edge", {
          segIdx: attach.segIdx, t: attach.t, v: c.points[i][1],
          gridId: cap.gridId, gridAxis: cap.type,
        });
        clampAnchorToSegment(a);
      }
    } else if (view === "floor") {
      a = makeAnchor("floor", { x: c.points[i][0], y: c.points[i][1] });
    } else {
      const attach = uToWallAttach(c.points[i][0], c.points[i][1]);
      if (attach) {
        a = makeAnchor("wall-edge", {
          segIdx: attach.segIdx, t: attach.t, v: c.points[i][1],
        });
        clampAnchorToSegment(a);
      }
    }
    if (a) ids.push(a.id);
  }
  return { id: c.id, name: c.name, anchorIds: ids };
}

export function applyLoaded(data) {
  if (!data || typeof data !== "object") return;

  state.floorGrids = Array.isArray(data.floorGrids) ? data.floorGrids : [];
  state.wallGrids  = Array.isArray(data.wallGrids)  ? data.wallGrids  : [];

  let maxId = data.idCounter || 0;
  if (Array.isArray(data.anchors)) {
    for (const a of data.anchors)
      if (typeof a.id === "number") maxId = Math.max(maxId, a.id);
  }
  for (const c of (data.floorCables || []))
    if (typeof c.id === "number") maxId = Math.max(maxId, c.id);
  for (const c of (data.wallCables || []))
    if (typeof c.id === "number") maxId = Math.max(maxId, c.id);
  for (const g of state.floorGrids) if (typeof g.id === "number") maxId = Math.max(maxId, g.id);
  for (const g of state.wallGrids)  if (typeof g.id === "number") maxId = Math.max(maxId, g.id);
  registryState.idCounter = maxId + 1;

  anchors.clear();
  state.floorCables = [];
  state.wallCables  = [];

  const v = data.version || 1;

  if (v >= 2 && Array.isArray(data.anchors)) {
    for (const raw of data.anchors) {
      const a = deserializeAnchor(raw);
      if (a) anchors.set(a.id, a);
    }
    state.floorCables = (data.floorCables || []).map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })).filter(c => c.anchorIds.every(id => anchors.has(id)));
    state.wallCables = (data.wallCables || []).map(c => ({
      id: c.id, name: c.name, anchorIds: c.anchorIds.slice(),
    })).filter(c => c.anchorIds.every(id => anchors.has(id)));
  } else {
    for (const c of (data.floorCables || [])) {
      const migrated = migrateV1Cable(c, "floor");
      if (migrated) state.floorCables.push(migrated);
    }
    for (const c of (data.wallCables || [])) {
      const migrated = migrateV1Cable(c, "wall");
      if (migrated) state.wallCables.push(migrated);
    }
    gcAnchors();
  }

  recomputeTrueCables();
}

export async function saveToServer() {
  try {
    const r = await fetch("/save", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(serialize()),
    });
    return r.ok;
  } catch (e) { return false; }
}
export async function loadFromServer() {
  try {
    const r = await fetch(STATE_FILE, {cache: "no-store"});
    if (!r.ok) return null;
    const txt = await r.text();
    if (!txt.trim()) return null;
    return JSON.parse(txt);
  } catch (e) { return null; }
}
export async function doSave() {
  const ok = await saveToServer();
  if (ok) flashStatus(T("msgSavedOk")(STATE_FILE), "ok");
  else    flashStatus(T("msgSaveFailed"),        "bad");
}

/* ==========================================================================
   STATUS FLASH + SNAP PILL + DRAW BUTTON
   ========================================================================== */

let _statusTimer = null;
const _statusEl = document.getElementById("status");
export function flashStatus(msg, cls) {
  if (!_statusEl) return;
  _statusEl.textContent = msg;
  _statusEl.className   = cls || "";
  if (_statusTimer) clearTimeout(_statusTimer);
  _statusTimer = setTimeout(() => {
    _statusTimer = null;
    callDraw();
  }, 1400);
}

export function updateSnapPill() {
  const el = document.getElementById("snapPill");
  if (!el) return;
  if (state.snapEnabled) { el.textContent = T("snapOn");  el.classList.add("on"); }
  else                   { el.textContent = T("snapOff"); el.classList.remove("on"); }
}

export const drawBtnEl = document.getElementById("drawBtn");
export function updateDrawButton() {
  if (!drawBtnEl) return;
  if (state.drawing) {
    drawBtnEl.classList.add("active");
    drawBtnEl.textContent = T("btnCancelDraw");
  } else {
    drawBtnEl.classList.remove("active");
    drawBtnEl.textContent = T("btnDrawCable");
  }
}

/* ==========================================================================
   GRID ADDERS
   ========================================================================== */

export function addFloorNsGrid() {
  const cx = (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2;
  state.floorGrids.push({ id: registryState.idCounter++, type: "ns", pos: cx });
  callDraw();
}
export function addFloorEwGrid() {
  const cy = (GEOMETRY.bounds.minY + GEOMETRY.bounds.maxY) / 2;
  state.floorGrids.push({ id: registryState.idCounter++, type: "ew", pos: cy });
  callDraw();
}
export function addWallVGrid() {
  state.wallGrids.push({ id: registryState.idCounter++, type: "v", pos: WALL.totalU * 0.5 });
  callDraw();
}
export function addWallHGrid() {
  state.wallGrids.push({ id: registryState.idCounter++, type: "h", pos: WALL_HEIGHT * 0.5 });
  callDraw();
}

/* ==========================================================================
   KEYBOARD
   ==========================================================================
   Cross-module calls to pg_edit and pg_ruler go through dynamic
   imports; see the module docstring for the cycle-avoidance
   reasoning. */

export function installKeyboard() {
  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;

    if (e.key === "Shift" && !state.snapEnabled) {
      state.snapEnabled = true;
      updateSnapPill();
      callDraw();
      return;
    }
    if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
      e.preventDefault();
      doSave();
      return;
    }
    if (e.key === "Enter") {
      if (state.drawing) {
        e.preventDefault();
        import("./pg_edit.js").then(m => m.finishDraw()).catch(() => {});
        return;
      }
    }
    if (e.key === "Delete" || e.key === "Backspace") {
      if (state.drawing) return;
      if (state.selectedVertex) {
        e.preventDefault();
        import("./pg_edit.js").then(m => m.deleteSelectedVertex()).catch(() => {});
        return;
      }
      if (state.selectedCable) {
        e.preventDefault();
        import("./pg_edit.js").then(m => m.deleteSelectedCable()).catch(() => {});
        return;
      }
    }
    if (e.key === "Escape") {
      if (state.drawing) {
        import("./pg_edit.js").then(m => m.finishDraw()).catch(() => {});
        return;
      }
      state.selectedCable  = null;
      state.selectedVertex = null;
      callDraw();
    }
    if (e.key === "h" || e.key === "H") {
      if (uiEl.classList.contains("collapsed")) restorePanel();
      else                                       collapsePanel();
    }
  });

  window.addEventListener("keyup", (e) => {
    if (e.key === "Shift" && state.snapEnabled) {
      state.snapEnabled = false;
      updateSnapPill();
      callDraw();
    }
  });

  window.addEventListener("blur", () => {
    if (state.snapEnabled) {
      state.snapEnabled = false;
      updateSnapPill();
      callDraw();
    }
    if (state.spaceHeld) {
      state.spaceHeld = false;
    }
  });
}
"""
