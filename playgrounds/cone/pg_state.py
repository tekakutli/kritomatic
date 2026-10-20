"""
pg_state.py — cone state, patch model, shape state, session.

ES module.  The leaf of cone's module graph: no imports of any
other project module.  Everything here is shared state that other
modules read or write.

WHY A STATE OBJECT FOR MUTABLE BINDINGS
=======================================
ES module `let` exports are read-only from the import side.  Cone
had five top-level `let`s reassigned from multiple files:

    selectedQuad, nextQuadId, selectedSquare, nextSquareId
    SHAPE_DEPTH_CONE, SHAPE_STANDING_HEIGHT_MULT
    dpr

Each now lives as a property of a `const` object (`coneState`,
`SHAPE_STATE`, `env`).  Property writes are allowed from anywhere;
the object bindings themselves are never reassigned.

DUAL APEX — INTENT vs DISPLAY
=============================
`cone` carries two apex pairs:

    ax, ay        displayed, clamped to 98% of the base radius.
                  Every projection, hit-test, and render read
                  uses these.

    axRaw, ayRaw  intended, never clamped in place.  Every write
                  site — apex drag, cone.apexX/apexY fields,
                  Center Apex, Reset view, scene load — writes
                  raw.  `clampApex()` derives display from raw.

Before this pair split, `clampApex()` overwrote the intended apex
in place.  Shrinking depth shrank the base radius and could push
the intended apex outside it; the clamp then replaced the intent
with the boundary value, and zooming back in could not restore the
original offset — the clamped value was already in the single
slot, and it fit the now-larger boundary so no further clamping
moved it.  The user-visible symptom was that squares followed the
apex into a smaller position as depth shrank and stayed there
when depth grew back.

Keeping intent and display separate makes the clamp reversible:
any depth change re-derives display from the unchanged intent, so
a zoom-out-then-in is a no-op on the shapes.

LIFECYCLE HOOKS
===============
`notifyChanged()` runs whatever handler the boot module injected
via `setAfterChange(...)`.  Every lifecycle mutator — addQuad,
cloneQuad, deleteQuad, addSquareAt, cloneSquare, deleteSquare —
calls it, so the panel and canvas refresh in one pass.

`setDraw(fn)` / `callDraw()` do the same for the draw entry point.

ADD-SQUARE COORDINATES
======================
`addSquareAt(quadIdx, uHat, vHat)` takes NORMALIZED coordinates:
uHat and vHat are in "unit" space where 0 is the patch's centre
and the patch spans roughly [-1, 1] on each axis.  The caller
converts from screen or local coordinates using the patch's
`frame.uLen` / `frame.vLen` (pg_dispatch's shift+click does this).
This keeps addSquareAt in this module (which owns floatSquares and
notifyChanged) while the frame math stays in pg_view_squares_core.
"""

STATE_JS = r"""
/* ==========================================================================
   CANVAS
   ========================================================================== */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

/* ==========================================================================
   DPR — mutable via env
   ========================================================================== */

export const env = {
  dpr: window.devicePixelRatio || 1,
};

/* ==========================================================================
   LAYOUT
   ========================================================================== */

export const layout = {
  coneH:     0,
  dividerY:  0,
  reservedY: 0,
  reservedH: 0,
};

/* ==========================================================================
   VIEW
   ========================================================================== */

export const view = {
  scale: 20, tx: 0, ty: 0,
  zoom: 1, panX: 0, panY: 0,
};

export function w2s(x, y) {
  return [x * view.scale + view.tx,
          -y * view.scale + view.ty];
}
export function s2w(sx, sy) {
  return [(sx - view.tx) / view.scale,
          -(sy - view.ty) / view.scale];
}

/* ==========================================================================
   MUTABLE SHAPE STATE
   ========================================================================== */

export const SHAPE_STATE = {
  depthCone:    1.00,
  standingMult: 2.00,
};

/* ==========================================================================
   CONE
   ==========================================================================
   Two apex pairs:

     ax, ay        the DISPLAYED apex — always within 98% of the base
                   radius. Every read in the rendering and projection
                   code uses these, so the cone surface never degenerates
                   and shapes never project from an out-of-bounds apex.

     axRaw, ayRaw  the user's INTENDED apex — the value set by dragging,
                   by the cone.apexX/apexY fields, by the Center Apex
                   button, and by scene load. Never clamped in place.

   `clampApex()` is the only bridge between them: it reads axRaw/ayRaw
   and writes ax/ay. Every write site MUST touch axRaw/ayRaw and then
   call clampApex; every read site in rendering uses ax/ay and stays
   unaware that the clamp exists.

   Why two pairs: when depth shrinks, the base radius shrinks with it,
   and the intended apex may fall outside the new base. If the clamp
   overwrote the intended apex, zooming back in would not restore it —
   the constraint that caused the clamping would lift, but the clamped
   value would already have replaced the original. This is exactly the
   bug observed when zooming out then in: the squares followed the
   (irreversibly shrunken) apex. Keeping intent separate from display
   makes the clamp reversible: any depth change recomputes display from
   the unchanged intent, so zoom-in restores the shape. */

export const cone = {
  ax: 0, ay: 0,            // displayed, clamped
  axRaw: 0, ayRaw: 0,      // intended, unclamped
  depth: 15,
  halfAngle: Math.PI / 6,
  ringCount: 14,
  meridianCount: 24,
};

export function clampApex() {
  const R = cone.depth * Math.tan(cone.halfAngle);
  const maxR = R * 0.98;
  const dr = Math.hypot(cone.axRaw, cone.ayRaw);
  if (dr > maxR && dr > 1e-9) {
    const s = maxR / dr;
    cone.ax = cone.axRaw * s;
    cone.ay = cone.ayRaw * s;
  } else {
    cone.ax = cone.axRaw;
    cone.ay = cone.ayRaw;
  }
}

export function coneR() { return cone.depth * Math.tan(cone.halfAngle); }

export function surfacePoint(phi, s) {
  const R = coneR();
  const r = (1 - s) * R;
  return [s * cone.ax + r * Math.cos(phi),
          s * cone.ay + r * Math.sin(phi)];
}

/* ==========================================================================
   PATCH MODEL
   ========================================================================== */

const S_MIN = 0.00;
const S_MAX = 0.90;

export const DEFAULT_PATCH_PHI_SPAN = 0.72;
export const DEFAULT_PATCH_S_SPAN   = 0.50;
export const DEFAULT_PATCH_S_CENTER = 0.47;

export const PATCH_SHAPE_EDIT_ENABLED = false;

export const quads = [];
export const coneState = {
  selectedQuad:   -1,
  selectedSquare: -1,
  nextQuadId:      1,
  nextSquareId:    1,
};

export function quadCorners(q) {
  return [
    { phi: q.phi0, s: q.s1 },
    { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 },
    { phi: q.phi0, s: q.s0 },
  ];
}

export function addQuad() {
  const id  = coneState.nextQuadId++;
  const idx = quads.length;
  const halfPhi = DEFAULT_PATCH_PHI_SPAN / 2;
  const halfS   = DEFAULT_PATCH_S_SPAN   / 2;

  let phiCenter = Math.PI / 2 + idx * 0.70;
  phiCenter = ((phiCenter % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  if (phiCenter - halfPhi < 0)               phiCenter = halfPhi;
  if (phiCenter + halfPhi > 2 * Math.PI)     phiCenter = 2 * Math.PI - halfPhi;

  quads.push({
    id, name: "Q" + id,
    phi0: phiCenter - halfPhi,
    phi1: phiCenter + halfPhi,
    s0: DEFAULT_PATCH_S_CENTER - halfS,
    s1: DEFAULT_PATCH_S_CENTER + halfS,
    mirror: false,
    mirrorAngle: Math.PI,
    mirrorFlip: true,
    warp: 0,
  });
  coneState.selectedQuad = quads.length - 1;
  notifyChanged();
}

export function cloneQuad(idx) {
  if (idx < 0 || idx >= quads.length) return;
  const src = quads[idx];
  const id  = coneState.nextQuadId++;
  const dPhi = src.phi1 - src.phi0;

  quads.push({
    id,
    name: "Q" + id,
    phi0: src.phi0 + dPhi,
    phi1: src.phi1 + dPhi,
    s0:   src.s0,
    s1:   src.s1,
    mirror: !!src.mirror,
    mirrorAngle: (typeof src.mirrorAngle === "number")
                   ? src.mirrorAngle : Math.PI,
    mirrorFlip:  src.mirrorFlip !== false,
    warp: (typeof src.warp === "number") ? src.warp : 0,
  });

  const newSquares = [];
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    if (sq.quadId !== src.id) continue;
    const newSqId = coneState.nextSquareId++;
    newSquares.push({
      id:     newSqId,
      name:   "S" + newSqId,
      quadId: id,
      u:      sq.u,
      v:      sq.v,
      scaleU: sq.scaleU,
      scaleV: sq.scaleV,
      theta:  sq.theta || 0,
      slope:  sq.slope || 0,
      visualBottom: !!sq.visualBottom,
    });
  }
  for (const s of newSquares) floatSquares.push(s);

  coneState.selectedQuad   = quads.length - 1;
  coneState.selectedSquare = -1;
  notifyChanged();
}

export function deleteQuad(idx) {
  if (idx < 0 || idx >= quads.length) return;
  const removed = quads.splice(idx, 1)[0];
  deleteSquaresForQuad(removed.id);
  if (coneState.selectedQuad >= quads.length) {
    coneState.selectedQuad = quads.length - 1;
  }
  notifyChanged();
}

/* ==========================================================================
   INVERSE PROJECTION
   ========================================================================== */

export function projectToConeSurface(wx, wy, currentS) {
  const Px = cone.ax, Py = cone.ay;
  const R  = coneR();
  const R2 = R * R;
  const p2 = Px * Px + Py * Py;
  const t2 = wx * wx + wy * wy;
  const pt = Px * wx + Py * wy;

  const A = p2 - R2;
  const B = -2 * (pt - R2);
  const C = t2 - R2;

  const roots = [];
  if (Math.abs(A) < 1e-6) {
    if (Math.abs(B) > 1e-9) roots.push(-C / B);
  } else {
    const disc = B * B - 4 * A * C;
    if (disc >= 0) {
      const sq = Math.sqrt(disc);
      roots.push((-B - sq) / (2 * A));
      roots.push((-B + sq) / (2 * A));
    }
  }

  let bestS = null, bestDelta = Infinity;
  for (const s of roots) {
    if (s < S_MIN - 0.01 || s > S_MAX + 0.01) continue;
    const c = Math.max(S_MIN, Math.min(S_MAX, s));
    const d = Math.abs(c - currentS);
    if (d < bestDelta) { bestDelta = d; bestS = c; }
  }

  if (bestS === null) {
    bestDelta = Infinity;
    for (let i = 0; i <= 40; i++) {
      const s = S_MIN + (S_MAX - S_MIN) * i / 40;
      const cx = s * Px, cy = s * Py;
      const r  = (1 - s) * R;
      const d  = Math.abs(Math.hypot(wx - cx, wy - cy) - r);
      if (d < bestDelta) { bestDelta = d; bestS = s; }
    }
    if (bestS === null) bestS = Math.max(S_MIN, Math.min(S_MAX, currentS));
  }

  const cx = bestS * Px, cy = bestS * Py;
  const dx = wx - cx, dy = wy - cy;
  const phi = (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9)
              ? 0 : Math.atan2(dy, dx);
  return { phi, s: bestS };
}

/* ==========================================================================
   SHAPE STATE
   ========================================================================== */

export const floatSquares = [];

/**
 * Add a square at a NORMALIZED (uHat, vHat) position within the
 * patch.  Both coordinates are in "unit" space where 0 is the
 * patch's centre and the patch spans roughly [-1, 1] on each axis.
 * Callers convert from local or screen coordinates using the
 * patch's `frame.uLen` / `frame.vLen` — see pg_dispatch's
 * shift+click, which does:
 *
 *     const lc = screenToFrame(f, sx, sy);
 *     addSquareAt(patchIdx, lc.u / f.uLen, lc.v / f.vLen);
 */
export function addSquareAt(quadIdx, uHat, vHat) {
  if (quadIdx < 0 || quadIdx >= quads.length) return;
  const q = quads[quadIdx];
  const id = coneState.nextSquareId++;
  floatSquares.push({
    id,
    name: "S" + id,
    quadId: q.id,
    u: uHat,
    v: clampCloneV(quadIdx, vHat),
    scaleU: 1.0,
    scaleV: 1.0,
    theta: 0,
    slope: 0,
    visualBottom: false,
  });
  coneState.selectedSquare = floatSquares.length - 1;
  notifyChanged();
}

export function cloneSquare(idx) {
  if (idx < 0 || idx >= floatSquares.length) return;
  const src = floatSquares[idx];
  const newId = coneState.nextSquareId++;
  floatSquares.push({
    id: newId, name: "S" + newId,
    quadId: src.quadId,
    u: src.u, v: src.v,
    scaleU: src.scaleU, scaleV: src.scaleV,
    theta: src.theta || 0,
    slope: src.slope || 0,
    visualBottom: !!src.visualBottom,
  });
  coneState.selectedSquare = floatSquares.length - 1;
  notifyChanged();
}

export function deleteSquare(idx) {
  if (idx < 0 || idx >= floatSquares.length) return;
  floatSquares.splice(idx, 1);
  if (coneState.selectedSquare === idx) coneState.selectedSquare = -1;
  else if (coneState.selectedSquare > idx) coneState.selectedSquare -= 1;
  notifyChanged();
}

export function deleteSquaresForQuad(quadId) {
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    if (floatSquares[i].quadId === quadId) {
      floatSquares.splice(i, 1);
      if (coneState.selectedSquare === i) coneState.selectedSquare = -1;
      else if (coneState.selectedSquare > i) coneState.selectedSquare -= 1;
    }
  }
}

/* ==========================================================================
   SMALL HELPERS
   ========================================================================== */

export function quadIdxById(id) {
  for (let i = 0; i < quads.length; i++) {
    if (quads[i].id === id) return i;
  }
  return -1;
}

export const WEDGE_BASE_REACH = 2.50;

export function horizonLocal(qi) {
  if (qi < 0 || qi >= quads.length) return null;
  const q = quads[qi];
  const ds = q.s1 - q.s0;
  if (Math.abs(ds) < 1e-9) return null;
  const sAvg = (q.s0 + q.s1) / 2;
  return { vHat: (1 - sAvg) / ds };
}

export function clampCloneV(qi, vHat) {
  const h = horizonLocal(qi);
  if (!h) return vHat;
  const vMin = -WEDGE_BASE_REACH;
  const vMax = h.vHat + WEDGE_BASE_REACH;
  if (vMax <= vMin) return vMin;
  return Math.max(vMin, Math.min(vMax, vHat));
}

export function _reclampShapesOnPatch(q) {
  if (!q) return;
  const qi = quadIdxById(q.id);
  if (qi < 0) return;
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    if (sq.quadId === q.id) {
      sq.v = clampCloneV(qi, sq.v);
    }
  }
}

/* ==========================================================================
   PHI DISPLAY CONVERSION + PANEL PATCH SETTERS
   ========================================================================== */

export const _TAU = 2 * Math.PI;

export function _phiToDisplay(phi) {
  let d = (phi + Math.PI / 2) / Math.PI;
  while (d >  1) d -= 2;
  while (d < -1) d += 2;
  return d;
}

export function _displayToPhi(display) {
  let d = display;
  while (d >  1) d -= 2;
  while (d < -1) d += 2;
  let phi = d * Math.PI - Math.PI / 2;
  phi = ((phi % _TAU) + _TAU) % _TAU;
  return phi;
}

export function _patchPhiCenter(q) {
  let c = (q.phi0 + q.phi1) / 2;
  c = ((c % _TAU) + _TAU) % _TAU;
  return c;
}

export function _patchSCenter(q) {
  return (q.s0 + q.s1) / 2;
}

export function _setPatchPhiCenter(q, target) {
  const cur = _patchPhiCenter(q);
  let delta = target - cur;
  while (delta >  Math.PI) delta -= _TAU;
  while (delta < -Math.PI) delta += _TAU;
  q.phi0 += delta;
  q.phi1 += delta;
  _reclampShapesOnPatch(q);
}

export function _setPatchSCenter(q, target) {
  const cur = _patchSCenter(q);
  const halfSpan = (q.s1 - q.s0) / 2;
  const lo = 0.0 + halfSpan;
  const hi = 1.0 - halfSpan;
  let t;
  if (hi < lo) t = 0.5;
  else         t = Math.max(lo, Math.min(hi, target));
  const delta = t - cur;
  q.s0 += delta;
  q.s1 += delta;
  _reclampShapesOnPatch(q);
}

/* ==========================================================================
   SLIDER SYNC
   ========================================================================== */

export function syncPanelSliders() {
  const d  = document.getElementById("depthSlider");
  const a  = document.getElementById("angleSlider");
  const r  = document.getElementById("ringsSlider");
  const m  = document.getElementById("meridiansSlider");
  const sd = document.getElementById("shapeDepthSlider");
  if (d)  d.value  = cone.depth;
  if (a)  a.value  = Math.round(cone.halfAngle * 180 / Math.PI);
  if (r)  r.value  = cone.ringCount;
  if (m)  m.value  = cone.meridianCount;
  if (sd) sd.value = SHAPE_STATE.depthCone;
  updatePanelLabels();
}

export function updatePanelLabels() {
  const dv  = document.getElementById("depthVal");
  const av  = document.getElementById("angleVal");
  const rv  = document.getElementById("ringsVal");
  const mv  = document.getElementById("meridiansVal");
  const sdv = document.getElementById("shapeDepthVal");
  if (dv)  dv.textContent  = cone.depth.toFixed(1);
  if (av)  av.textContent  =
    Math.round(cone.halfAngle * 180 / Math.PI) + "\u00B0";
  if (rv)  rv.textContent  = String(cone.ringCount);
  if (mv)  mv.textContent  = String(cone.meridianCount);
  if (sdv) sdv.textContent = SHAPE_STATE.depthCone.toFixed(2);
}

/* ==========================================================================
   DRAW TOOL STATE
   ========================================================================== */

export const drawTool = {
  active: false,
  points: [],
  hover: null,
};

export function toggleDrawTool() {
  drawTool.active = !drawTool.active;
  drawTool.points = [];
  drawTool.hover = null;
  const btn = document.getElementById("drawQuadBtn");
  if (btn) {
    btn.textContent = drawTool.active ? "Cancel Draw" : "Draw Quad";
    btn.style.borderColor = drawTool.active ? "#00e5ff" : "";
    btn.style.color = drawTool.active ? "#00e5ff" : "";
  }
  canvas.style.cursor = "crosshair";
  callDraw();
}

export function cancelDrawTool() {
  drawTool.active = false;
  drawTool.points = [];
  drawTool.hover = null;
  const btn = document.getElementById("drawQuadBtn");
  if (btn) {
    btn.textContent = "Draw Quad";
    btn.style.borderColor = "";
    btn.style.color = "";
  }
  canvas.style.cursor = "crosshair";
  callDraw();
}

/* ==========================================================================
   HOOKS
   ========================================================================== */

let _afterChange = null;
export function setAfterChange(fn) { _afterChange = fn; }
export function notifyChanged() { if (_afterChange) _afterChange(); }

let _drawFn = null;
export function setDraw(fn) { _drawFn = fn; }
export function callDraw() { if (_drawFn) _drawFn(); }

/* ==========================================================================
   FIT VIEW / RESET VIEW
   ========================================================================== */

export function fitView() {
  view.scale = 20 * view.zoom;
  view.tx = window.innerWidth / 2 + view.panX;
  view.ty = layout.coneH / 2 + view.panY;
}

export function resetView() {
  view.zoom = 1; view.panX = 0; view.panY = 0;
  cone.axRaw = 0; cone.ayRaw = 0;
  cone.depth = 15;
  cone.halfAngle = Math.PI / 6;
  cone.ringCount = 14;
  cone.meridianCount = 24;
  clampApex();
  syncPanelSliders();
  fitView();
  callDraw();
}

/* ==========================================================================
   SESSION DRAG STATE
   ==========================================================================
   Written by pg_dispatch, cleared by pg_scene._clearTransientState. */

export const state = {
  dragApex:       null,
  dragQuadVertex: null,
  dragSquare:     null,
  flatDrag:       null,
  flatSquareDrag: null,
  dragPatchBody:  null,
  mouse: { sx: 0, sy: 0, inside: false },
};
"""
