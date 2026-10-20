"""
pg_core.py — stable half of the cable playground HTML/JS bundle.

Everything that defines the model, the storage format, and the passive
infrastructure: anchor registry, junction graph, wall-edge geometry, view
transforms, focus mode, true-cable grouping, save/load.  This file is not
expected to change as features are added to the playground.

The interactive half — rendering decisions, drawing gestures, event
handlers, snap policy — lives in cable_live.py and is expected to be
rewritten whenever we iterate on the playground's UX.

Translation
-----------
The HTML_HEAD's button labels, the panel header's title, and the snap
pill's text are first-paint placeholders; installVerticalMenu (pg_panel.py)
and updateSnapPill (below) rewrite them at load from the translation
table in pg_i18n.py.  The <div id="hint"> instructions block that used to
sit under the panel is gone — its content now lives in
T("popInstructionsHTML"), read by installInstructionsPopover.

The four runtime strings in CORE_JS itself — the snap pill's two states,
the draw button's two labels, the two save-flash messages, and the clear
confirmation — route through T().  See those functions below.

Assumed by cable_live (all defined here):
    model        — anchors, makeAnchor, resolveSpecToAnchorId
    geometry     — WALL, WALL_HEIGHT, wallAttachToPlan/U, uToWallAttach,
                   nearestSegmentToUV, findSegmentAtU
    junctions    — JUNCTIONS, SEGMENT_ADJ, ADJ_DETAIL, junctionOfAnchor
    transforms   — w2sFloor/s2wFloor, w2sWall/s2wWall, whichView
    state        — state, mouse, drawing, drawingPreview, routeCache,
                   snapEnabled, focusSeg, focusSavedU
    focus        — enterFocus, exitFocus, isSegHidden
    grouping     — recomputeTrueCables, trueCableIdOf, trueCableSiblings,
                   allCables
    persistence  — serialize, applyLoaded, doSave, loadFromServer
    panel        — collapsePanel, restorePanel
    keyboard     — keydown/keyup/blur listeners
    ui           — addFloorNsGrid, addWallVGrid, ..., updateDrawButton,
                   flashStatus

Called BY pg_core but defined in cable_live (forward references are
resolved at call time, not parse time):
    draw, finishDraw, deleteSelectedCable, deleteSelectedVertex,
    beginDraw, cancelDraw, fitViews, T
"""


HTML_HEAD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cable / pipe layout planner</title>
<style>
  html, body { margin:0; padding:0; height:100%; overflow:hidden; }
  #ui {
    position:absolute; top:12px; left:12px; z-index:10;
    background:rgba(255,255,255,0.96); padding:12px 14px;
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:13px; user-select:none; width:460px;
  }
  #ui.collapsed { display: none; }
  #ui h3 { margin:0 0 8px; font-size:14px; font-weight:600;
           display:flex; align-items:center; gap:6px; }
  #collapseBtn {
    margin-left:auto;
    padding:1px 8px; font-size:13px; font-weight:700; line-height:1.4;
    cursor:pointer; border:1px solid #bbb; background:#f7f7f7;
    border-radius:4px; color:#374151;
  }
  #collapseBtn:hover { background:#e5e7eb; }
  #restoreBtn {
    position:absolute; top:12px; left:12px; z-index:10;
    display:none;
    padding:6px 12px; cursor:pointer;
    border:1px solid #bbb; background:rgba(255,255,255,0.96);
    border-radius:8px; box-shadow:0 2px 10px rgba(0,0,0,0.18);
    font-size:12px; font-weight:600; color:#374151;
  }
  #restoreBtn:hover { background:#f3f4f6; }
  #restoreBtn.visible { display:block; }
  #ui .row { margin:6px 0; display:flex; gap:6px; flex-wrap:wrap; align-items:center; }
  #ui button { padding:5px 9px; cursor:pointer; border:1px solid #bbb;
               background:#f7f7f7; border-radius:4px; font-size:12px; }
  #ui button:hover { background:#eee; }
  #ui button.primary { background:#000000; color:#ffffff; border-color:#000000;
                       font-weight:600; }
  #ui button.primary:hover { background:#222222; }
  #ui button.danger { background:#fee2e2; border-color:#fca5a5; color:#991b1b; }
  #ui button.active { background:#d97706; border-color:#b45309; color:#ffffff;
                      font-weight:600; }
  #ui button.active:hover { background:#b45309; }
  #ui hr { border:0; border-top:1px solid #eee; margin:9px 0; }
  #status { margin-top:8px; font-size:12px; min-height:16px; font-weight:600; }
  #status.ok  { color:#0f766e; }
  #status.bad { color:#b91c1c; }
  #status.warn{ color:#b45309; }
  #snapPill {
    display:inline-block; padding:2px 8px; border-radius:10px;
    font-size:11px; font-weight:700; margin-left:6px;
    background:#e5e7eb; color:#6b7280; vertical-align:middle;
  }
  #snapPill.on { background:#ccfbf1; color:#0f766e; }
  canvas { display:block; touch-action:none; background:#ffffff; }
</style>
</head>
<body>
<div id="ui">
  <h3>Cable / pipe layout planner <span id="snapPill">snap: off</span>
      <button id="collapseBtn" title="Collapse panel (H)">−</button></h3>
  <div class="row">
    <button id="addNsGrid">+ N–S grid</button>
    <button id="addEwGrid">+ E–W grid</button>
    <button id="addWallV">+ Wall |</button>
    <button id="addWallH">+ Wall ─</button>
  </div>
  <div class="row">
    <button id="drawBtn">✏️ Draw cable</button>
  </div>
  <div class="row">
    <button id="saveBtn" class="primary">💾 Save</button>
    <button id="delVertexBtn" class="danger">✂️ Delete vertex</button>
    <button id="deleteBtn" class="danger">🗑️ Delete cable</button>
    <button id="clearBtn">Clear all</button>
  </div>
  <hr>
  <div id="status"></div>
</div>
<button id="restoreBtn" title="Show panel (H)">☰ Show panel</button>
<canvas id="c"></canvas>
"""


CORE_JS = r"""
/* ==========================================================================
   CONSTANTS
   ========================================================================== */

const STATE_FILE = "room_layout.json";
const WALL       = GEOMETRY.unfoldedWall;
const WALL_HEIGHT = WALL.height;

/* ==========================================================================
   BASIC GEOMETRY
   ========================================================================== */

function pointInPolygon(p, poly) {
  const x = p[0], y = p[1];
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > y) !== (yj > y)) &&
        (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

function pointToSegmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx*dx + dy*dy;
  if (len2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax)*dx + (py - ay)*dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t*dx), py - (ay + t*dy));
}

function polylineHit(wx, wy, pts, tol) {
  for (let i = 0; i < pts.length - 1; i++) {
    if (pointToSegmentDist(wx, wy,
        pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]) < tol) return true;
  }
  return false;
}

function fmtCm(v_mm) {
  const cm = v_mm / 10;
  const r = Math.round(cm * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

function segTop(s) { return (s && typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT; }
function segBottom(s) { return (s && typeof s.z_lo === "number") ? s.z_lo : 0; }
function isStepFace(s) { return !!(s && s.kind === "step"); }

function pointOnStep(x, y) {
  const p = [x, y];
  for (const face of GEOMETRY.stepFaces) {
    if (!pointInPolygon(p, face.outer)) continue;
    let inHole = false;
    for (const h of face.holes) {
      if (pointInPolygon(p, h)) { inHole = true; break; }
    }
    if (!inHole) return true;
  }
  return false;
}

/* ==========================================================================
   JUNCTION GRAPH (computed once at load)
   ==========================================================================
   Nodes are physical room corners.  Terminals are (segIdx, side) pairs,
   side ∈ {0, 1}.  Two terminals are co-located when their plan XY is within
   TOL mm — meaning the two walls physically meet at that corner in the room.
   Every "do these two anchors share a physical point" question routes
   through this graph. */

let JUNCTIONS = [];              // [{ id, x, y, terminals: [{segIdx, side}] }]
let JUNCTION_OF_TERMINAL = null; // Map<"segIdx:side", junctionId>
let SEGMENT_ADJ = [];            // Array<Array<segIdx>>
let ADJ_DETAIL = null;           // Map<"a:b", {junctionId, side, other}>

function wallEdgeKey(segIdx, side) { return segIdx + ":" + side; }

function buildJunctionGraph() {
  const TOL = 3.0;
  const N = WALL.segments.length;

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

  JUNCTIONS = [];
  JUNCTION_OF_TERMINAL = new Map();
  for (const [, terminals] of groups) {
    const parts0 = terminals[0].split(":");
    const p = endpointOf(Number(parts0[0]), Number(parts0[1]));
    const id = JUNCTIONS.length;
    JUNCTIONS.push({
      id,
      x: p[0], y: p[1],
      terminals: terminals.map(k => {
        const [si, ss] = k.split(":").map(Number);
        return { segIdx: si, side: ss };
      }),
    });
    for (const k of terminals) JUNCTION_OF_TERMINAL.set(k, id);
  }

  SEGMENT_ADJ = Array.from({ length: N }, () => new Set());
  for (const j of JUNCTIONS) {
    for (const t1 of j.terminals) {
      for (const t2 of j.terminals) {
        if (t1.segIdx === t2.segIdx) continue;
        SEGMENT_ADJ[t1.segIdx].add(t2.segIdx);
      }
    }
  }
  SEGMENT_ADJ = SEGMENT_ADJ.map(s => Array.from(s));

  ADJ_DETAIL = new Map();
  for (const j of JUNCTIONS) {
    for (const t1 of j.terminals) {
      for (const t2 of j.terminals) {
        if (t1.segIdx === t2.segIdx) continue;
        const key = t1.segIdx + ":" + t2.segIdx;
        if (!ADJ_DETAIL.has(key)) {
          ADJ_DETAIL.set(key, {
            junctionId: j.id,
            side: t1.side,
            other: t2.side,
          });
        }
      }
    }
  }
}

function junctionOfTerminal(segIdx, side) {
  return JUNCTION_OF_TERMINAL.get(wallEdgeKey(segIdx, side));
}

function junctionOfAnchor(a) {
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

let idCounter = 1;
const anchors = new Map();

function makeAnchor(space, fields) {
  const a = Object.assign({ id: idCounter++, space }, fields);
  anchors.set(a.id, a);
  return a;
}

function anchorPlan(a) {
  if (!a) return null;
  if (a.space === "floor")     return [a.x, a.y];
  if (a.space === "wall-edge") return wallAttachToPlan(a.segIdx, a.t);
  return null;
}

function anchorWall(a) {
  if (!a) return null;
  if (a.space === "wall-edge") return [wallAttachToU(a.segIdx, a.t), a.v];
  return null;
}

function anchorIsStepFace(a) {
  if (!a || a.space !== "wall-edge") return false;
  const s = WALL.segments[a.segIdx];
  return !!(s && isStepFace(s));
}

function anchorSide(a) {
  if (!anchorIsStepFace(a)) return null;
  const s = WALL.segments[a.segIdx];
  const top = segTop(s);
  if (Math.abs(a.v - top) < 0.5) return "top";
  if (Math.abs(a.v)      < 0.5) return "bottom";
  return null;
}

function clampAnchorToSegment(a) {
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

/* Resolve a spec to an anchor id, preferring an existing match.  The single
   funnel through which every anchor is created or reused.  An optional
   reuseFilter gates which existing anchors are eligible — the anchor reuse
   policy lives at the call site. */
function resolveSpecToAnchorId(spec, tolMm, reuseFilter) {
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

function wallAttachToPlan(segIdx, t) {
  const s = WALL.segments[segIdx];
  if (!s) return null;
  return [s.a[0] + t * (s.b[0] - s.a[0]),
          s.a[1] + t * (s.b[1] - s.a[1])];
}
function wallAttachToU(segIdx, t) {
  const s = WALL.segments[segIdx];
  return s.u0 + t * s.len;
}
function uToWallAttach(u, v) {
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
function nearestSegmentToUV(u, v) {
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
function findSegmentAtU(u) {
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

/* ==========================================================================
   SEGMENT-REFERENCE HELPERS (save format v3)
   ========================================================================== */

function segRefOf(seg) {
  return { a: [seg.a[0], seg.a[1]], b: [seg.b[0], seg.b[1]] };
}

function findSegmentByRef(ref) {
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

function findSegmentByWorld(world) {
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
   VIEW STATE / TRANSFORMS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

const layout = { floorH: 0, dividerY: 0, wallY: 0, wallH: 0 };
const viewFloor = { scale: 1, tx: 0, ty: 0 };
const viewWall  = { scaleX: 1, scaleY: 1, tx: 0, ty: 0, stripTopY: 0 };
const router = { ESCAPE_DROP: 30, leftBaseX: 0, rightBaseX: 0 };
const ANCHOR_NEARBY_PX = 14;

function w2sFloor(x, y) { return [x*viewFloor.scale + viewFloor.tx, -y*viewFloor.scale + viewFloor.ty]; }
function s2wFloor(sx, sy) { return [(sx - viewFloor.tx)/viewFloor.scale, -(sy - viewFloor.ty)/viewFloor.scale]; }
function w2sWall(u, v) { return [u*viewWall.scaleX + viewWall.tx, -v*viewWall.scaleY + viewWall.ty]; }
function s2wWall(sx, sy) { return [(sx - viewWall.tx)/viewWall.scaleX, -(sy - viewWall.ty)/viewWall.scaleY]; }
function whichView(sy) {
  if (sy < layout.dividerY) return "floor";
  if (sy >= layout.wallY)   return "wall";
  return null;
}

/* ==========================================================================
   GLOBAL STATE
   ========================================================================== */

let snapEnabled = false;

const state = {
  floorGrids: [], wallGrids: [],
  floorCables: [], wallCables: [],
  trueCables: [],
  selectedCable: null, selectedVertex: null,
  dragGrid: null, dragVertex: null, hoveredRoute: null,
};

let drawing = null;
let drawingPreview = null;
let routeCache = [];
const mouse = { sx: 0, sy: 0, view: null, inside: false, alt: false };

/* Snap pill.  The two labels come from the translation table; the pill's
   dot indicator is a CSS ::after pseudo-element that the stylesheet
   switches on the .on class. */
function updateSnapPill() {
  const el = document.getElementById("snapPill");
  if (!el) return;
  if (snapEnabled) { el.textContent = T("snapOn");  el.classList.add("on"); }
  else             { el.textContent = T("snapOff"); el.classList.remove("on"); }
}

/* ==========================================================================
   FOCUS MODE
   ========================================================================== */

let focusSeg    = -1;
let focusSavedU = null;

function isSegHidden(i) {
  return WALL.segments[i] && WALL.segments[i]._hidden === true;
}

function enterFocus(segIdx) {
  exitFocus();
  if (segIdx < 0 || segIdx >= WALL.segments.length) return;

  const S = WALL.segments[segIdx];
  const leftNbrs = [], rightNbrs = [];

  for (const j of JUNCTIONS) {
    const t_this = j.terminals.find(t => t.segIdx === segIdx);
    if (!t_this) continue;
    for (const o of j.terminals) {
      if (o.segIdx === segIdx) continue;
      if (t_this.side === 0) leftNbrs.push(o.segIdx);
      else                   rightNbrs.push(o.segIdx);
    }
  }

  focusSavedU = new Map();
  const save = (i) => focusSavedU.set(i, { u0: WALL.segments[i].u0,
                                            u1: WALL.segments[i].u1 });
  save(segIdx); leftNbrs.forEach(save); rightNbrs.forEach(save);

  let cursor = S.u0;
  for (const i of leftNbrs) {
    const T = WALL.segments[i];
    const L = T.len;
    T.u0 = cursor - L;
    T.u1 = cursor;
    cursor -= L;
  }
  cursor = S.u1;
  for (const i of rightNbrs) {
    const T = WALL.segments[i];
    const L = T.len;
    T.u0 = cursor;
    T.u1 = cursor + L;
    cursor += L;
  }

  const vis = new Set([segIdx, ...leftNbrs, ...rightNbrs]);
  for (let i = 0; i < WALL.segments.length; i++) {
    if (vis.has(i)) delete WALL.segments[i]._hidden;
    else            WALL.segments[i]._hidden = true;
  }

  focusSeg = segIdx;
  refitFocusView();
}

function exitFocus() {
  if (focusSavedU) {
    for (const [i, u] of focusSavedU) {
      WALL.segments[i].u0 = u.u0;
      WALL.segments[i].u1 = u.u1;
    }
  }
  for (const s of WALL.segments) delete s._hidden;
  focusSavedU = null;
  focusSeg = -1;
  fitViews();
}

function refitFocusView() {
  if (!focusSavedU) return;
  let uMin = Infinity, uMax = -Infinity;
  for (const [i] of focusSavedU) {
    const s = WALL.segments[i];
    uMin = Math.min(uMin, s.u0);
    uMax = Math.max(uMax, s.u1);
  }
  const W = Math.max(uMax - uMin, 1);
  const cw = window.innerWidth;
  const sideChan = 40, topPad = 20;
  const bottomPad = router.ESCAPE_DROP + 30;
  const gutterTop = 140;
  const availW = cw - 2 * topPad - 2 * sideChan;
  const availH = layout.wallH - topPad - bottomPad - gutterTop;
  const H = WALL_HEIGHT || 1;
  viewWall.scaleX = availW / W;
  viewWall.scaleY = availH / H;
  viewWall.tx     = topPad + sideChan - uMin * viewWall.scaleX;
  viewWall.ty     = layout.wallY + layout.wallH - bottomPad;
  viewWall.stripTopY = viewWall.ty - H * viewWall.scaleY;
  router.leftBaseX  = viewWall.tx - 12;
  router.rightBaseX = viewWall.tx + W * viewWall.scaleX + 12;
}

/* ==========================================================================
   TRUE-CABLE GROUPING
   ========================================================================== */

function allCables() {
  return state.floorCables.concat(state.wallCables);
}

function trueCableIdOf(cable) {
  return (cable && cable.trueCableId != null) ? cable.trueCableId : cable.id;
}

function trueCableSiblings(cable) {
  const tid = trueCableIdOf(cable);
  return allCables().filter(c => trueCableIdOf(c) === tid);
}

function recomputeTrueCables() {
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

  for (const j of JUNCTIONS) {
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
   RESIZE / FIT
   ========================================================================== */

function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  layout.floorH   = Math.round(ch * 0.54);
  layout.dividerY = layout.floorH;
  layout.wallY    = layout.floorH + 4;
  layout.wallH    = ch - layout.wallY;
  fitViews();
  draw();
}
window.addEventListener("resize", resize);

function fitViews() {
  const cw = window.innerWidth;
  const b = GEOMETRY.bounds;
  const pad = 50;
  const bw = (b.maxX - b.minX) || 1;
  const bh = (b.maxY - b.minY) || 1;
  const sf = Math.min((cw - 2*pad) / bw, (layout.floorH - 2*pad) / bh);
  viewFloor.scale = sf;
  const mx = (b.minX + b.maxX) / 2, my = (b.minY + b.maxY) / 2;
  viewFloor.tx = cw / 2 - mx * sf;
  viewFloor.ty = layout.floorH / 2 + my * sf;

  const sideChan = 40, topPad = 20;
  const bottomPad = router.ESCAPE_DROP + 30;
  const gutterTop = 140;
  const W = WALL.totalU || 1, H = WALL_HEIGHT || 1;
  const availW = cw - 2 * topPad - 2 * sideChan;
  const availH = layout.wallH - topPad - bottomPad - gutterTop;
  viewWall.scaleX    = availW / W;
  viewWall.scaleY    = availH / H;
  viewWall.tx        = topPad + sideChan;
  viewWall.ty        = layout.wallY + layout.wallH - bottomPad;
  viewWall.stripTopY = viewWall.ty - H * viewWall.scaleY;
  router.leftBaseX  = viewWall.tx - 12;
  router.rightBaseX = viewWall.tx + WALL.totalU * viewWall.scaleX + 12;
}

/* ==========================================================================
   SAVE / LOAD (v3; wall-edge anchors carry segRef + world)
   ========================================================================== */

function serializeAnchor(a) {
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

function deserializeAnchor(data) {
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

function serialize() {
  const anchorArr = [];
  for (const a of anchors.values()) {
    const s = serializeAnchor(a);
    if (s) anchorArr.push(s);
  }
  return {
    version: 3,
    idCounter: idCounter,
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

function migrateV1Cable(c, view) {
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

function applyLoaded(data) {
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
  idCounter = maxId + 1;

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

async function saveToServer() {
  try {
    const r = await fetch("/save", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(serialize()),
    });
    return r.ok;
  } catch (e) { return false; }
}
async function loadFromServer() {
  try {
    const r = await fetch(STATE_FILE, {cache: "no-store"});
    if (!r.ok) return null;
    const txt = await r.text();
    if (!txt.trim()) return null;
    return JSON.parse(txt);
  } catch (e) { return null; }
}
async function doSave() {
  const ok = await saveToServer();
  if (ok) flashStatus(T("msgSavedOk")(STATE_FILE), "ok");
  else    flashStatus(T("msgSaveFailed"),        "bad");
}

/* ==========================================================================
   PANEL COLLAPSE / RESTORE
   ========================================================================== */

const uiEl = document.getElementById("ui");
const restoreBtnEl = document.getElementById("restoreBtn");
const collapseBtnEl = document.getElementById("collapseBtn");

function collapsePanel() {
  uiEl.classList.add("collapsed");
  restoreBtnEl.classList.add("visible");
}
function restorePanel() {
  uiEl.classList.remove("collapsed");
  restoreBtnEl.classList.remove("visible");
}
collapseBtnEl.onclick = collapsePanel;
restoreBtnEl.onclick = restorePanel;

/* ==========================================================================
   KEYBOARD
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (e.key === "Shift" && !snapEnabled) {
    snapEnabled = true; updateSnapPill(); draw();
  }
  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault(); doSave(); return;
  }
  if (e.key === "Enter") {
    if (drawing) { e.preventDefault(); finishDraw(); return; }
  }
  if (e.key === "Delete" || e.key === "Backspace") {
    if (drawing) return;
    if (state.selectedVertex) { e.preventDefault(); deleteSelectedVertex(); return; }
    if (state.selectedCable)  { e.preventDefault(); deleteSelectedCable();  return; }
  }
  if (e.key === "Escape") {
    if (drawing) { finishDraw(); return; }
    state.selectedCable = null; state.selectedVertex = null; draw();
  }
  if (e.key === "h" || e.key === "H") {
    if (uiEl.classList.contains("collapsed")) restorePanel();
    else                                       collapsePanel();
  }
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Shift" && snapEnabled) {
    snapEnabled = false; updateSnapPill(); draw();
  }
});
window.addEventListener("blur", () => {
  if (snapEnabled) { snapEnabled = false; updateSnapPill(); draw(); }
});

/* ==========================================================================
   STABLE UI WIRING
   ========================================================================== */

function addFloorNsGrid() {
  const cx = (GEOMETRY.bounds.minX + GEOMETRY.bounds.maxX) / 2;
  state.floorGrids.push({ id: idCounter++, type: "ns", pos: cx }); draw();
}
function addFloorEwGrid() {
  const cy = (GEOMETRY.bounds.minY + GEOMETRY.bounds.maxY) / 2;
  state.floorGrids.push({ id: idCounter++, type: "ew", pos: cy }); draw();
}
function addWallVGrid() {
  state.wallGrids.push({ id: idCounter++, type: "v", pos: WALL.totalU * 0.5 }); draw();
}
function addWallHGrid() {
  state.wallGrids.push({ id: idCounter++, type: "h", pos: WALL_HEIGHT * 0.5 }); draw();
}

const drawBtnEl = document.getElementById("drawBtn");
function updateDrawButton() {
  if (!drawBtnEl) return;
  if (drawing) {
    drawBtnEl.classList.add("active");
    drawBtnEl.textContent = T("btnCancelDraw");
  } else {
    drawBtnEl.classList.remove("active");
    drawBtnEl.textContent = T("btnDrawCable");
  }
}

let statusTimer = null;
const statusEl = document.getElementById("status");
function flashStatus(msg, cls) {
  statusEl.textContent = msg; statusEl.className = cls;
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { statusTimer = null; draw(); }, 1400);
}
"""


BOOT_JS = r"""
/* ==========================================================================
   BOOTSTRAP
   ==========================================================================

   The previous ordering called resize() — and therefore draw(), which
   blocks on the wall→plan arrow solver — synchronously, on an EMPTY
   model, and only then started the async fetch.  The user saw: canvas
   appears, pause, cables appear.  Two paints, and the second one had
   to wait for both the arrow solve that had already happened AND the
   fetch.

   The ordering below does the fetch first.  resize() still runs before
   draw() — it is what sizes the canvas, computes layout.floorH /
   layout.wallH, and sets the transforms fitViews reads — but no draw()
   has happened yet, so the arrow solver runs exactly once, on the
   real, cable-loaded model, and the user sees the canvas and its
   cables in a single paint.

   What changed vs. the original:

       - the synchronous resize() / updateSnapPill() /
         updateDrawButton() calls are gone
       - the async IIFE now owns the whole boot: fetch → resize →
         panel refresh → draw()
       - the flashStatus call is unconditional in the loaded case (no
         separate draw() — resize()'s internal draw() already painted
         the loaded model) */

document.title = T("pageTitle");

document.getElementById("addNsGrid").onclick    = addFloorNsGrid;
document.getElementById("addEwGrid").onclick    = addFloorEwGrid;
document.getElementById("addWallV").onclick     = addWallVGrid;
document.getElementById("addWallH").onclick     = addWallHGrid;
document.getElementById("saveBtn").onclick      = doSave;
document.getElementById("delVertexBtn").onclick = deleteSelectedVertex;
document.getElementById("deleteBtn").onclick    = deleteSelectedCable;
document.getElementById("clearBtn").onclick     = clearAll;
drawBtnEl.onclick = () => { if (drawing) cancelDraw(false); else beginDraw(); };

/* Build the junction graph once, before anything that reads it. */
buildJunctionGraph();

(async () => {
  const data = await loadFromServer();
  if (data) applyLoaded(data);

  /* resize() sizes the canvas, computes the layout bands, sets the
     view transforms, and calls draw() — so the very first paint the
     user sees already has the loaded cables in it. */
  resize();
  updateSnapPill();
  updateDrawButton();

  if (data) {
    const n = state.floorGrids.length + state.wallGrids.length +
              state.floorCables.length + state.wallCables.length;
    if (n) flashStatus(`Loaded ${n} item(s) from ${STATE_FILE}`, "ok");
  }
})();
"""


HTML_TAIL = r"""</script>
</body>
</html>
"""
