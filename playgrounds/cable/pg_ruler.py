"""
pg_ruler.py — the ruler tool, self-contained.

ES module.  The full measuring tool: state, snap, step-skip state
machine, raycast, dimension-line geometry, rendering, keyboard, and
the panel-inspector mutators the panel wires its MEASURE fields to.

STATE MODEL
===========
`state.ruler` (in pg_state.js) owns:
    · the pending measurement — a first-placed point, an optional
      second-endpoint lock (pendingEnd) set by the numeric input,
      an optional axis override (axisPref), and the step-skip flags
    · the completed measurements array, plus hovered / selected index
    · the numeric-input bookkeeping — which field is open, and which
      measurement it is editing

STEP SKIP
=========
Shift held while crossing a step sets `pendingJumpedStep` to true and
the flag persists after release.  The only way back to the near side
is a deliberate return: within snap range of a step edge, meaningfully
closer to that step edge than to any wall, and moving toward it.

PANEL INSPECTOR
===============
The panel's MEASURE fields are the persistent counterpart to the
canvas's transient numeric input.  Both edit the same measurements
through the same rules — span preserves the axis, angle forces
aligned.  `_rulerApplyPanelSpan` and `_rulerApplyPanelAngle` are the
shared mutators; `_rulerSyncMeasureInspector` is the per-frame sync
called from draw().

RULER NUMERIC INPUT DOM — WINDOW SHIM
=====================================
`_RULER_NUM_INPUT_EL` is a module-scoped const pointing at the
`#rulerNumericInput` element created at module load.  It is also
exported (for any module that wants the element reference cleanly) and
assigned to `window._RULER_NUM_INPUT_EL` (a transitional compat shim
for `pg_dispatch.js`, which references the identifier as a free
variable inside its ruler-mousedown branch).  The clean fix — an
explicit import from this module — will land the next time
pg_dispatch is reprinted; until then the shim keeps the mousedown
guard working.

DRAW HOOK
=========
Every place that used to call `draw()` now goes through `callDraw()`
from pg_state.  This module does not import pg_dispatch.

KEYBOARD
========
Registered in **capture** phase on window so the ruler's keys fire
before pg_core's bubble-phase handlers (Backspace must pop a
measurement, not delete a selected cable; Escape must cancel a pending
ruler point without clearing the cable selection).  Every ruler key
requires Shift and explicitly rejects Ctrl / Meta / Alt.

DRAW CALLBACK FROM EDITS
========================
`beginDraw` and `cancelDraw` live in pg_edit; the ruler's `toggleRuler`
needs to disarm any in-progress drawing when the ruler arms.  Because
pg_edit imports pg_ruler (for `_rulerCloseNumericInput` and
`_rulerSyncMeasureInspector`), a static import of pg_edit from
pg_ruler would create a cycle.  The dynamic `import("./pg_edit.js")`
in `toggleRuler` defers the resolution.
"""

RULER_JS = r"""
import {
  WALL, WALL_HEIGHT, GEOMETRY,
  ctx, PALETTE, FONT_MONO,
  state, mouse, viewFloor, anchors,
  segTop, segBottom, isStepFace,
  pointInPolygon, pointToSegmentDist,
  fmtCm, _drawValuePill, _drawValuePillColored, drawSnapCrosshair,
  w2sFloor, s2wFloor,
  callDraw,
} from "./pg_state.js";
import { isSegHidden, anchorWall } from "./pg_core.js";
import { T } from "./pg_i18n.js";

/* ==========================================================================
   CONSTANTS
   ========================================================================== */

const RULER_SNAP_PX = 14;
const RULER_OFF_PX  = 26;

/* ==========================================================================
   NUMERIC INPUT ELEMENT (canvas, transient)
   ==========================================================================
   A small floating field that opens next to the relevant endpoint.
   The element is created once at script load; `_rulerOpenNumericInput`
   shows it, `_rulerCommitNumericInput` reads it,
   `_rulerCloseNumericInput` hides it.  Enter commits, Escape cancels;
   blur also cancels so a click elsewhere on the canvas does not leave
   a dangling field.

   This is the CANVAS-side transient input.  The panel's persistent
   inspector is a separate pair of fields, wired in pg_panel.  Both
   edit the same underlying measurements via `_rulerApplyPanelSpan` /
   `_rulerApplyPanelAngle`. */

export const _RULER_NUM_INPUT_EL = (() => {
  const el = document.createElement("div");
  el.id = "rulerNumericInput";
  el.innerHTML =
    '<span class="lbl" id="rulerNumericLabel">' + T("rlInputSpan") +
      '</span>' +
    '<input type="text" inputmode="decimal" id="rulerNumericField" ' +
      'autocomplete="off" spellcheck="false">' +
    '<span class="unit" id="rulerNumericUnit">' + T("rlUnitCm") + '</span>';
  document.body.appendChild(el);
  const field = el.querySelector("#rulerNumericField");
  field.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      _rulerCommitNumericInput();
    } else if (e.key === "Escape") {
      e.preventDefault();
      _rulerCloseNumericInput();
    }
  });
  field.addEventListener("blur", () => {
    _rulerCloseNumericInput();
  });
  return el;
})();

/* Transitional shim for pg_dispatch.js.  Will be replaced by an
   explicit import the next time dispatch is reprinted. */
if (typeof window !== "undefined") {
  window._RULER_NUM_INPUT_EL = _RULER_NUM_INPUT_EL;
}

export function _rulerCloseNumericInput() {
  state.ruler.input.mode = null;
  state.ruler.input.targetMeasure = null;
  if (_RULER_NUM_INPUT_EL) {
    _RULER_NUM_INPUT_EL.classList.remove("show");
    const f = _RULER_NUM_INPUT_EL.querySelector("#rulerNumericField");
    if (f) f.value = "";
  }
}

/* Decide the input's target and prefill, then show the field.  The
   target is: the pending measurement if a first point exists, else
   the currently hovered completed measurement, else the selected
   one.  If none, the field is not opened. */
export function _rulerOpenNumericInput(mode) {
  if (!state.ruler.active) return;
  const targetMeasure = state.ruler.pending
    ? null
    : (state.ruler.hoverIdx >= 0
        ? state.ruler.measures[state.ruler.hoverIdx]
        : (state.ruler.selected >= 0
            ? state.ruler.measures[state.ruler.selected]
            : null));
  if (!state.ruler.pending && !targetMeasure) return;

  state.ruler.input.mode          = mode;
  state.ruler.input.targetMeasure = targetMeasure;

  const labelEl = document.getElementById("rulerNumericLabel");
  const unitEl  = document.getElementById("rulerNumericUnit");
  const field   = document.getElementById("rulerNumericField");
  if (!labelEl || !unitEl || !field) return;

  labelEl.textContent = (mode === "angle") ? T("rlInputAngle")
                                           : T("rlInputSpan");
  unitEl.textContent  = (mode === "angle") ? T("rlUnitDeg")
                                           : T("rlUnitCm");

  let prefill = "";
  let anchorX = 0, anchorY = 0;

  if (state.ruler.pending) {
    const p = state.ruler.pending;
    const end = state.ruler.pendingEnd
      ? state.ruler.pendingEnd
      : (state.ruler.hoverPt || p);
    if (mode === "dist") {
      const span = Math.hypot(end.x - p.x, end.y - p.y);
      prefill = (span / 10).toFixed(1);
    } else {
      let deg = Math.atan2(end.y - p.y, end.x - p.x) * 180 / Math.PI;
      if (!isFinite(deg)) deg = 0;
      prefill = deg.toFixed(1);
    }
    const [sx, sy] = w2sFloor(end.x, end.y);
    anchorX = sx; anchorY = sy;
  } else {
    const m = targetMeasure;
    if (mode === "dist") {
      const span = Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
      prefill = (span / 10).toFixed(1);
    } else {
      let deg = Math.atan2(m.y1 - m.y0, m.x1 - m.x0) * 180 / Math.PI;
      if (!isFinite(deg)) deg = 0;
      prefill = deg.toFixed(1);
    }
    const pillPos = _rulerMeasurePillPos(m);
    anchorX = pillPos.x; anchorY = pillPos.y;
  }

  field.value = prefill;

  const w = 240, h = 32;
  let px = anchorX + 24;
  let py = anchorY - h - 12;
  if (px + w > window.innerWidth  - 12) px = anchorX - w - 24;
  if (py < 12)                          py = anchorY + 24;
  if (px < 12)                          px = 12;
  if (py > window.innerHeight - h - 12) py = window.innerHeight - h - 12;
  _RULER_NUM_INPUT_EL.style.left = px + "px";
  _RULER_NUM_INPUT_EL.style.top  = py + "px";

  _RULER_NUM_INPUT_EL.classList.add("show");
  field.focus();
  field.select();
}

export function _rulerCommitNumericInput() {
  const field = document.getElementById("rulerNumericField");
  if (!field) return;
  const mode = state.ruler.input.mode;
  const targetMeasure = state.ruler.input.targetMeasure;
  if (!mode) { _rulerCloseNumericInput(); return; }

  const raw = field.value.trim();
  const v = parseFloat(raw);
  if (!isFinite(v)) { _rulerCloseNumericInput(); return; }

  const applyingPending = !!state.ruler.pending;
  const applyingMeasure = !applyingPending && !!targetMeasure;
  if (!applyingPending && !applyingMeasure) {
    _rulerCloseNumericInput();
    return;
  }

  const origin = applyingPending
    ? state.ruler.pending
    : { x: targetMeasure.x0, y: targetMeasure.y0 };

  if (mode === "dist") {
    const distMm = v * 10;   // cm → mm
    let dirx = 1, diry = 0;
    if (applyingPending) {
      const end = state.ruler.pendingEnd
        ? state.ruler.pendingEnd
        : (state.ruler.hoverPt || origin);
      const dx = end.x - origin.x, dy = end.y - origin.y;
      const L = Math.hypot(dx, dy);
      if (L > 1e-3) { dirx = dx / L; diry = dy / L; }
    } else {
      const axis = targetMeasure.axis;
      if (axis === "ew") {
        dirx = Math.sign(targetMeasure.x1 - targetMeasure.x0) || 1;
        diry = 0;
      } else if (axis === "ns") {
        dirx = 0;
        diry = Math.sign(targetMeasure.y1 - targetMeasure.y0) || 1;
      } else {
        const dx = targetMeasure.x1 - targetMeasure.x0;
        const dy = targetMeasure.y1 - targetMeasure.y0;
        const L = Math.hypot(dx, dy);
        if (L > 1e-3) { dirx = dx / L; diry = dy / L; }
      }
    }

    let nx, ny, newAxis;
    if (applyingPending) {
      let axis = state.ruler.axisPref;
      if (!axis) {
        const hx = state.ruler.hoverPt ? state.ruler.hoverPt.x : origin.x + dirx;
        const hy = state.ruler.hoverPt ? state.ruler.hoverPt.y : origin.y + diry;
        axis = _rulerPickAxis(origin.x, origin.y, hx, hy, null, false);
      }
      newAxis = axis;
      if (axis === "ew") {
        const sgn = (dirx >= 0) ? 1 : -1;
        nx = origin.x + sgn * distMm;
        ny = origin.y;
      } else if (axis === "ns") {
        const sgn = (diry >= 0) ? 1 : -1;
        nx = origin.x;
        ny = origin.y + sgn * distMm;
      } else {
        nx = origin.x + dirx * distMm;
        ny = origin.y + diry * distMm;
      }
      state.ruler.pendingEnd  = { x: nx, y: ny };
      state.ruler.pendingAxis = newAxis;
      state.ruler.axisPref    = newAxis;
    } else {
      newAxis = targetMeasure.axis;
      if (newAxis === "ew") {
        const sgn = (dirx >= 0) ? 1 : -1;
        targetMeasure.x1 = origin.x + sgn * distMm;
        targetMeasure.y1 = origin.y;
      } else if (newAxis === "ns") {
        const sgn = (diry >= 0) ? 1 : -1;
        targetMeasure.x1 = origin.x;
        targetMeasure.y1 = origin.y + sgn * distMm;
      } else {
        targetMeasure.x1 = origin.x + dirx * distMm;
        targetMeasure.y1 = origin.y + diry * distMm;
      }
      targetMeasure.ox1 = targetMeasure.x1;
      targetMeasure.oy1 = targetMeasure.y1;
    }
  } else if (mode === "angle") {
    const theta = v * Math.PI / 180;
    const dirx = Math.cos(theta);
    const diry = Math.sin(theta);

    let distMm = 1000;
    if (applyingPending) {
      const end = state.ruler.pendingEnd
        ? state.ruler.pendingEnd
        : (state.ruler.hoverPt || origin);
      const L = Math.hypot(end.x - origin.x, end.y - origin.y);
      if (L > 1.0) distMm = L;
    } else {
      const L = Math.hypot(targetMeasure.x1 - origin.x,
                           targetMeasure.y1 - origin.y);
      if (L > 1.0) distMm = L;
    }

    const nx = origin.x + dirx * distMm;
    const ny = origin.y + diry * distMm;

    if (applyingPending) {
      state.ruler.pendingEnd  = { x: nx, y: ny };
      state.ruler.pendingAxis = "aligned";
      state.ruler.axisPref    = "aligned";
    } else {
      targetMeasure.axis = "aligned";
      targetMeasure.x1 = nx;
      targetMeasure.y1 = ny;
      targetMeasure.ox1 = nx;
      targetMeasure.oy1 = ny;
    }
  }

  _rulerCloseNumericInput();
  callDraw();
}

/* ==========================================================================
   PANEL INSPECTOR MUTATORS
   ==========================================================================
   The two functions the panel's MEASURE fields call.  They edit the
   currently selected measurement in place, using the same axis-
   preservation rules as the canvas's Shift+D / Shift+A numeric entry.
   A separate path from _rulerCommitNumericInput because the panel
   never has a pending measurement to lock — it always edits a
   completed one. */

export function _rulerApplyPanelSpan(newSpanMm) {
  const sel = state.ruler.selected;
  if (sel < 0 || sel >= state.ruler.measures.length) return;
  const m = state.ruler.measures[sel];
  if (!m) return;

  const ox = m.x0, oy = m.y0;
  let dirx = 1, diry = 0;

  if (m.axis === "ew") {
    dirx = Math.sign(m.x1 - m.x0) || 1;
    diry = 0;
  } else if (m.axis === "ns") {
    dirx = 0;
    diry = Math.sign(m.y1 - m.y0) || 1;
  } else {
    const dx = m.x1 - m.x0, dy = m.y1 - m.y0;
    const L = Math.hypot(dx, dy);
    if (L > 1e-3) { dirx = dx / L; diry = dy / L; }
  }

  let nx, ny;
  if (m.axis === "ew") {
    nx = ox + (dirx >= 0 ? newSpanMm : -newSpanMm);
    ny = oy;
  } else if (m.axis === "ns") {
    nx = ox;
    ny = oy + (diry >= 0 ? newSpanMm : -newSpanMm);
  } else {
    nx = ox + dirx * newSpanMm;
    ny = oy + diry * newSpanMm;
  }

  m.x1  = nx;  m.y1  = ny;
  m.ox1 = nx;  m.oy1 = ny;
}

export function _rulerApplyPanelAngle(deg) {
  const sel = state.ruler.selected;
  if (sel < 0 || sel >= state.ruler.measures.length) return;
  const m = state.ruler.measures[sel];
  if (!m) return;

  const ox = m.x0, oy = m.y0;
  const theta = deg * Math.PI / 180;
  const dirx = Math.cos(theta);
  const diry = Math.sin(theta);

  const span = Math.hypot(m.x1 - m.x0, m.y1 - m.y0) || 1000;

  m.axis = "aligned";
  m.x1 = ox + dirx * span;
  m.y1 = oy + diry * span;
  m.ox1 = m.x1;
  m.oy1 = m.y1;
}

/* Push the current selected-measurement state into the panel's
   MEASURE fields, and show / hide the section as appropriate.  Called
   from draw() so every state change reaches the panel within one
   frame.

   The activeElement guard is what stops the two-way loop: when the
   user is typing in the span field, its own `input` handler mutates
   the measurement and calls draw(); draw() calls this function; this
   function must not overwrite the field the user is typing in. */
export function _rulerSyncMeasureInspector() {
  const section = document.getElementById("measureSection");
  const idxEl   = document.getElementById("measureIndex");
  const spanEl  = document.getElementById("measureSpan");
  const angleEl = document.getElementById("measureAngle");
  if (!section || !idxEl || !spanEl || !angleEl) return;

  const sel   = state.ruler.selected;
  const total = state.ruler.measures.length;

  if (!state.ruler.active || sel < 0 || sel >= total) {
    section.style.display = "none";
    return;
  }
  section.style.display = "block";

  const m = state.ruler.measures[sel];
  idxEl.textContent = "#" + (sel + 1) + " / " + total;

  const spanMm   = Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
  const angleDeg = Math.atan2(m.y1 - m.y0, m.x1 - m.x0) * 180 / Math.PI;

  if (document.activeElement !== spanEl) {
    spanEl.value = (spanMm / 10).toFixed(2);
  }
  if (document.activeElement !== angleEl) {
    angleEl.value = angleDeg.toFixed(2);
  }
}

/* ==========================================================================
   SNAP CANDIDATE COLLECTION
   ========================================================================== */

function _rulerSnapCandidates(wx, wy) {
  const cands = [];

  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (state.ruler.ignoreSteps && s.kind === "step") continue;
    const ax = s.a[0], ay = s.a[1], bx = s.b[0], by = s.b[1];
    cands.push({ kind: "corner", x: ax, y: ay });
    cands.push({ kind: "corner", x: bx, y: by });
    const dx = bx - ax, dy = by - ay;
    const L2 = dx * dx + dy * dy;
    if (L2 < 1e-9) continue;
    let t = ((wx - ax) * dx + (wy - ay) * dy) / L2;
    t = Math.max(0, Math.min(1, t));
    cands.push({ kind: "wall", x: ax + t * dx, y: ay + t * dy });
  }

  for (const col of (GEOMETRY.columns || [])) {
    const [x0, y0, x1, y1] = col.box;
    const ring = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
    for (let i = 0; i < 4; i++) {
      const A = ring[i], B = ring[(i + 1) % 4];
      cands.push({ kind: "corner", x: A[0], y: A[1] });
      const dx = B[0] - A[0], dy = B[1] - A[1];
      const L2 = dx * dx + dy * dy;
      if (L2 < 1e-9) continue;
      let t = ((wx - A[0]) * dx + (wy - A[1]) * dy) / L2;
      t = Math.max(0, Math.min(1, t));
      cands.push({ kind: "column", x: A[0] + t * dx, y: A[1] + t * dy });
    }
  }

  if (!state.ruler.ignoreSteps) {
    for (const face of GEOMETRY.stepFaces) {
      const ring = face.outer;
      for (let i = 0; i < ring.length; i++) {
        const A = ring[i], B = ring[(i + 1) % ring.length];
        const dx = B[0] - A[0], dy = B[1] - A[1];
        const L2 = dx * dx + dy * dy;
        if (L2 < 1e-9) continue;
        let t = ((wx - A[0]) * dx + (wy - A[1]) * dy) / L2;
        t = Math.max(0, Math.min(1, t));
        cands.push({ kind: "step", x: A[0] + t * dx, y: A[1] + t * dy });
      }
    }
  }

  const b = GEOMETRY.bounds;
  for (const g of state.floorGrids) {
    if (g.type === "ns") {
      if (wy >= b.minY && wy <= b.maxY) {
        cands.push({ kind: "grid", x: g.pos, y: wy });
      }
    } else {
      if (wx >= b.minX && wx <= b.maxX) {
        cands.push({ kind: "grid", x: wx, y: g.pos });
      }
    }
  }

  return cands;
}

export function rulerSnapPoint(wx, wy) {
  const cands = _rulerSnapCandidates(wx, wy);
  const radiusW = RULER_SNAP_PX / Math.max(viewFloor.scale, 1e-9);
  const pri = { corner: 0, wall: 1, column: 1, step: 2, grid: 3 };
  let best = null, bestD = radiusW;
  for (const c of cands) {
    const d = Math.hypot(c.x - wx, c.y - wy);
    if (d > radiusW) continue;
    const better = !best
      || d < bestD - 1e-6
      || (Math.abs(d - bestD) < 1e-6 && pri[c.kind] < pri[best.kind]);
    if (better) { best = c; bestD = d; }
  }
  if (best) return { x: best.x, y: best.y, snap: best.kind };
  return { x: wx, y: wy, snap: "free" };
}

/* ==========================================================================
   AXIS CHOICE
   ==========================================================================
   Precedence: Alt (momentary) beats an explicit Shift+X override beats
   the auto EW/NS pick. */

export function _rulerPickAxis(x0, y0, x1, y1, override, freeMode) {
  if (freeMode) return "aligned";
  if (override) return override;
  const dx = Math.abs(x1 - x0);
  const dy = Math.abs(y1 - y0);
  return (dx >= dy) ? "ew" : "ns";
}

/* ==========================================================================
   STEP-SKIP STATE MACHINE
   ========================================================================== */

export function _rulerSegsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
  const d1x = bx - ax, d1y = by - ay;
  const d2x = dx - cx, d2y = dy - cy;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-9) return false;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / denom;
  const u = ((cx - ax) * d1y - (cy - ay) * d1x) / denom;
  return t > 1e-6 && t < 1 - 1e-6 && u > 1e-6 && u < 1 - 1e-6;
}

export function _rulerSegmentCrossesStep(px, py, qx, qy) {
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (s.kind !== "step") continue;
    if (_rulerSegsIntersect(px, py, qx, qy,
                            s.a[0], s.a[1], s.b[0], s.b[1]))
      return true;
  }
  for (const face of GEOMETRY.stepFaces) {
    const ring = face.outer;
    for (let i = 0; i < ring.length; i++) {
      const A = ring[i], B = ring[(i + 1) % ring.length];
      if (_rulerSegsIntersect(px, py, qx, qy, A[0], A[1], B[0], B[1]))
        return true;
    }
  }
  return false;
}

function _rulerNearestDistances(wx, wy) {
  let dStep = Infinity, dWall = Infinity;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    const d = pointToSegmentDist(wx, wy, s.a[0], s.a[1], s.b[0], s.b[1]);
    if (s.kind === "step") {
      if (d < dStep) dStep = d;
    } else {
      if (d < dWall) dWall = d;
    }
  }
  for (const face of GEOMETRY.stepFaces) {
    const ring = face.outer;
    for (let i = 0; i < ring.length; i++) {
      const A = ring[i], B = ring[(i + 1) % ring.length];
      const d = pointToSegmentDist(wx, wy, A[0], A[1], B[0], B[1]);
      if (d < dStep) dStep = d;
    }
  }
  return { dStep, dWall };
}

export function _rulerReturnedToStep(wx, wy) {
  const R = RULER_SNAP_PX / Math.max(viewFloor.scale, 1e-9);
  const { dStep, dWall } = _rulerNearestDistances(wx, wy);
  if (dStep >= R) return false;
  return dStep + 5.0 < dWall;
}

export function _rulerUpdateSkipState(wx, wy, shiftHeld) {
  const { dStep, dWall } = _rulerNearestDistances(wx, wy);
  const R = RULER_SNAP_PX / Math.max(viewFloor.scale, 1e-9);
  const prevD = state.ruler._lastDStep;
  state.ruler._lastDStep = dStep;

  if (shiftHeld) {
    state.ruler.ignoreSteps = true;
    if (state.ruler.pending &&
        _rulerSegmentCrossesStep(state.ruler.pending.x,
                                 state.ruler.pending.y, wx, wy)) {
      if (!state.ruler.pendingJumpedStep) {
        state.ruler.pendingJumpedStep = true;
        state.ruler._hasLeftStepEdge  = false;
      }
    }
    if (dStep >= R) state.ruler._hasLeftStepEdge = true;
    return;
  }

  if (!state.ruler.pendingJumpedStep) {
    state.ruler.ignoreSteps = false;
    if (dStep >= R) state.ruler._hasLeftStepEdge = true;
    return;
  }

  if (dStep >= R) state.ruler._hasLeftStepEdge = true;

  const nearStep    = dStep < R;
  const notOnWall   = dStep + 5.0 < dWall;
  const approaching = isFinite(prevD) && dStep < prevD - 0.5;
  const hasLeft     = state.ruler._hasLeftStepEdge;

  if (hasLeft && nearStep && notOnWall && approaching) {
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    return;
  }

  state.ruler.ignoreSteps = true;
}

/* ==========================================================================
   WALL RAYCAST
   ==========================================================================
   Cast a unit-direction ray from (px, py) and return the first
   intersection with any WALL.segment, or null.

   Step-kind segments (risers) are skipped when ignoreSteps is set:
   without this, disabling step snap freed the cursor but the raycast
   still halted the measurement at the riser's plan line. */

export function _rulerRaycastWall(px, py, dx, dy) {
  let bestT = Infinity;
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (state.ruler.ignoreSteps && s.kind === "step") continue;
    const ax = s.a[0], ay = s.a[1];
    const ex = s.b[0] - ax, ey = s.b[1] - ay;
    const rx = ax - px, ry = ay - py;
    const det = -dx * ey + dy * ex;
    if (Math.abs(det) < 1e-9) continue;
    const t = (-rx * ey + ex * ry) / det;
    const u = (dx * ry - dy * rx) / det;
    if (t > 1e-3 && u >= -0.001 && u <= 1.001 && t < bestT) {
      bestT = t;
    }
  }
  if (bestT === Infinity) return null;
  return { x: px + dx * bestT, y: py + dy * bestT, t: bestT };
}

/* ==========================================================================
   SECOND-POINT RESOLUTION
   ========================================================================== */

export function _rulerResolveSecondPoint(p, pt, axis) {
  if (axis === "aligned") return { x: pt.x, y: pt.y };

  if (axis === "ew") {
    const dir = Math.sign(pt.x - p.x) || 1;
    let x1 = pt.x;
    const hit = _rulerRaycastWall(p.x, p.y, dir, 0);
    if (hit) {
      const hitDist = Math.abs(hit.x - p.x);
      const curDist = Math.abs(pt.x - p.x);
      if (hitDist <= curDist + 0.5) x1 = hit.x;
    }
    return { x: x1, y: p.y };
  }

  if (axis === "ns") {
    const dir = Math.sign(pt.y - p.y) || 1;
    let y1 = pt.y;
    const hit = _rulerRaycastWall(p.x, p.y, 0, dir);
    if (hit) {
      const hitDist = Math.abs(hit.y - p.y);
      const curDist = Math.abs(pt.y - p.y);
      if (hitDist <= curDist + 0.5) y1 = hit.y;
    }
    return { x: p.x, y: y1 };
  }

  return { x: pt.x, y: pt.y };
}

/* ==========================================================================
   DIMENSION-LINE GEOMETRY
   ========================================================================== */

export function _rulerDimLineEndpoints(sx0, sy0, sx1, sy1, axis) {
  const off = RULER_OFF_PX;
  const b = GEOMETRY.bounds;
  const [cx, cy] = w2sFloor((b.minX + b.maxX) / 2,
                            (b.minY + b.maxY) / 2);

  if (axis === "ew") {
    const midY = (sy0 + sy1) / 2;
    const up = midY > cy;
    const oy = up ? -1 : 1;
    const yy = up ? Math.min(sy0, sy1) - off
                  : Math.max(sy0, sy1) + off;
    return { qx0: sx0, qy0: yy, qx1: sx1, qy1: yy, ox: 0, oy };
  }

  if (axis === "ns") {
    const midX = (sx0 + sx1) / 2;
    const right = midX < cx;
    const ox = right ? 1 : -1;
    const xx = right ? Math.max(sx0, sx1) + off
                     : Math.min(sx0, sx1) - off;
    return { qx0: xx, qy0: sy0, qx1: xx, qy1: sy1, ox, oy: 0 };
  }

  const dx = sx1 - sx0, dy = sy1 - sy0;
  const len = Math.hypot(dx, dy) || 1;
  let nx = -dy / len, ny = dx / len;
  const mx = (sx0 + sx1) / 2, my = (sy0 + sy1) / 2;
  if (nx * (cx - mx) + ny * (cy - my) < 0) { nx = -nx; ny = -ny; }
  return {
    qx0: sx0 + nx * off, qy0: sy0 + ny * off,
    qx1: sx1 + nx * off, qy1: sy1 + ny * off,
    ox: nx, oy: ny,
  };
}

export function _rulerMeasurePillPos(m) {
  const [sx0, sy0] = w2sFloor(m.x0, m.y0);
  const [sx1, sy1] = w2sFloor(m.x1, m.y1);
  const E = _rulerDimLineEndpoints(sx0, sy0, sx1, sy1, m.axis);
  return { x: (E.qx0 + E.qx1) / 2, y: (E.qy0 + E.qy1) / 2 };
}

export function _rulerHitMeasure(sx, sy) {
  for (let i = state.ruler.measures.length - 1; i >= 0; i--) {
    const p = _rulerMeasurePillPos(state.ruler.measures[i]);
    if (Math.hypot(sx - p.x, sy - p.y) < 14) return i;
  }
  return -1;
}

/* ==========================================================================
   RENDERING
   ========================================================================== */

function _rulerDrawOne(m, highlighted, pending, locked, selected) {
  const [sx0, sy0] = w2sFloor(m.x0, m.y0);
  const [sx1, sy1] = w2sFloor(m.x1, m.y1);
  const E = _rulerDimLineEndpoints(sx0, sy0, sx1, sy1, m.axis);

  const ink = (highlighted || pending) ? PALETTE.highlight
            : (selected)               ? PALETTE.transit
            :                            PALETTE.ink;

  ctx.save();
  ctx.lineWidth = 0.9;
  ctx.strokeStyle = ink;
  if (pending) ctx.setLineDash([5, 3]);

  ctx.beginPath();
  ctx.moveTo(sx0, sy0); ctx.lineTo(E.qx0, E.qy0);
  ctx.moveTo(sx1, sy1); ctx.lineTo(E.qx1, E.qy1);
  ctx.stroke();

  ctx.beginPath();
  ctx.moveTo(E.qx0, E.qy0); ctx.lineTo(E.qx1, E.qy1);
  ctx.stroke();

  ctx.setLineDash([]);

  const dx = E.qx1 - E.qx0, dy = E.qy1 - E.qy0;
  const len = Math.hypot(dx, dy) || 1;
  const ux = dx / len, uy = dy / len;
  const px = -uy, py = ux;
  const T = 4;
  const t1x = (ux + px) * T / Math.SQRT2;
  const t1y = (uy + py) * T / Math.SQRT2;
  const t2x = (ux - px) * T / Math.SQRT2;
  const t2y = (uy - py) * T / Math.SQRT2;
  for (const [ax2, ay2] of [[E.qx0, E.qy0], [E.qx1, E.qy1]]) {
    ctx.beginPath();
    ctx.moveTo(ax2 - t1x, ay2 - t1y);
    ctx.lineTo(ax2 + t1x, ay2 + t1y);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(ax2 - t2x, ay2 - t2y);
    ctx.lineTo(ax2 + t2x, ay2 + t2y);
    ctx.stroke();
  }

  for (const [ex, ey] of [[sx0, sy0], [sx1, sy1]]) {
    ctx.beginPath();
    ctx.arc(ex, ey, 3, 0, Math.PI * 2);
    ctx.fillStyle = ink;
    ctx.fill();
    ctx.strokeStyle = PALETTE.paper;
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  if (locked) {
    ctx.beginPath();
    ctx.arc(sx1, sy1, 6, 0, Math.PI * 2);
    ctx.strokeStyle = PALETTE.transit;
    ctx.lineWidth = 1.6;
    ctx.stroke();
  }

  ctx.restore();

  const valueMm = (m.axis === "ew")
    ? Math.abs(m.x1 - m.x0)
    : (m.axis === "ns")
      ? Math.abs(m.y1 - m.y0)
      : Math.hypot(m.x1 - m.x0, m.y1 - m.y0);
  const text = fmtCm(valueMm) + " cm";
  const mx2 = (E.qx0 + E.qx1) / 2;
  const my2 = (E.qy0 + E.qy1) / 2;

  if (highlighted || pending) {
    _drawValuePillColored(mx2, my2, text, PALETTE.highlight);
  } else if (selected) {
    _drawValuePillColored(mx2, my2, text, PALETTE.transit);
  } else {
    _drawValuePill(mx2, my2, text);
  }
}

export function drawRulers() {
  const n = state.ruler.measures.length;
  for (let i = 0; i < n; i++) {
    if (i === state.ruler.selected) continue;
    _rulerDrawOne(state.ruler.measures[i],
                  i === state.ruler.hoverIdx, false, false, false);
  }
  if (state.ruler.selected >= 0 && state.ruler.selected < n) {
    const i = state.ruler.selected;
    _rulerDrawOne(state.ruler.measures[i],
                  i === state.ruler.hoverIdx, false, false, true);
  }

  if (state.ruler.active && state.ruler.pending) {
    let r2 = null, axis = null, locked = false;
    if (state.ruler.pendingEnd) {
      r2 = state.ruler.pendingEnd;
      axis = state.ruler.pendingAxis
           || state.ruler.axisPref
           || "aligned";
      locked = true;
    } else if (state.ruler.hoverPt) {
      const p = state.ruler.pending;
      const q = state.ruler.hoverPt;
      axis = _rulerPickAxis(p.x, p.y, q.x, q.y,
                            state.ruler.axisPref, mouse.alt);
      r2 = _rulerResolveSecondPoint(p, q, axis);
    }
    if (r2 && axis) {
      _rulerDrawOne({ x0: state.ruler.pending.x,
                      y0: state.ruler.pending.y,
                      x1: r2.x, y1: r2.y, axis },
                    false, true, locked, false);
    }
  }

  if (!state.ruler.active) return;

  if (state.ruler.hoverPt && !state.ruler.pendingEnd) {
    const [hx, hy] = w2sFloor(state.ruler.hoverPt.x, state.ruler.hoverPt.y);
    ctx.save();
    if (state.ruler.hoverPt.snap !== "free") {
      ctx.beginPath();
      ctx.arc(hx, hy, 7, 0, Math.PI * 2);
      ctx.strokeStyle = PALETTE.highlight;
      ctx.lineWidth = 1.6;
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(hx, hy, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.highlight;
      ctx.fill();
    } else {
      drawSnapCrosshair(hx, hy);
      ctx.beginPath();
      ctx.arc(hx, hy, 2.5, 0, Math.PI * 2);
      ctx.fillStyle = PALETTE.ink;
      ctx.fill();
    }
    ctx.restore();
  }

  if (state.ruler.pending) {
    const [px, py] = w2sFloor(state.ruler.pending.x, state.ruler.pending.y);
    ctx.save();
    ctx.beginPath();
    ctx.arc(px, py, 5, 0, Math.PI * 2);
    ctx.fillStyle = PALETTE.paper;
    ctx.fill();
    ctx.strokeStyle = PALETTE.highlight;
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }
}

/* ==========================================================================
   MODE LIFECYCLE
   ========================================================================== */

export function toggleRuler() {
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
  } else {
    if (state.drawing) {
      import("./pg_edit.js").then(m => m.cancelDraw(true)).catch(() => {});
    }
    state.ruler.active            = true;
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    state.ruler._lastDStep        = Infinity;
    state.ruler.pendingEnd        = null;
    state.ruler.pendingAxis       = null;
    state.ruler.selected          = -1;
  }
  updateRulerButton();
  callDraw();
}

export function updateRulerButton() {
  const el = document.getElementById("rulerBtn");
  if (!el) return;
  if (state.ruler.active) {
    el.classList.add("active");
    el.textContent = T("btnExitRuler");
  } else {
    el.classList.remove("active");
    el.textContent = T("btnMeasure");
  }
}

/* ==========================================================================
   RULER KEYS
   ==========================================================================
   Registered in CAPTURE phase so it fires before pg_core's and
   pg_dispatch's bubble-phase listeners.  Every ruler key requires
   Shift and explicitly rejects Ctrl / Meta / Alt. */

window.addEventListener("keydown", (e) => {
  if (e.key === "Alt")   mouse.alt   = true;
  if (e.key === "Shift") mouse.shift = true;
});
window.addEventListener("keyup", (e) => {
  if (e.key === "Alt")   mouse.alt   = false;
  if (e.key === "Shift") {
    mouse.shift = false;
    if (state.ruler.active) {
      if (!state.ruler.pendingJumpedStep) {
        state.ruler.ignoreSteps = false;
      }
      callDraw();
    }
  }
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if ((e.key === "r" || e.key === "R") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    if (state.drawing) return;
    e.preventDefault();
    e.stopPropagation();
    toggleRuler();
    return;
  }

  if (!state.ruler.active) return;

  if (e.key === "Escape") {
    e.preventDefault();
    e.stopPropagation();
    if (state.ruler.pending) {
      state.ruler.pending           = null;
      state.ruler.axisPref          = null;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      _rulerCloseNumericInput();
    } else if (state.ruler.selected >= 0) {
      state.ruler.selected = -1;
    } else {
      state.ruler.active            = false;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      state.ruler.selected          = -1;
      _rulerCloseNumericInput();
      updateRulerButton();
    }
    callDraw();
    return;
  }

  if ((e.key === "x" || e.key === "X") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    const cycle = ["ew", "ns", "aligned"];
    if (state.ruler.pending) {
      const cur = state.ruler.axisPref;
      const idx = cur ? cycle.indexOf(cur) : -1;
      state.ruler.axisPref = cycle[(idx + 1) % cycle.length];
      if (state.ruler.pendingEnd) {
        const p = state.ruler.pending;
        const e0 = state.ruler.pendingEnd;
        const dist = Math.hypot(e0.x - p.x, e0.y - p.y);
        const dx = (e0.x - p.x) || 1;
        const dy = (e0.y - p.y) || 0;
        const dirx = dx / Math.hypot(dx, dy);
        const diry = dy / Math.hypot(dx, dy);
        const ax = state.ruler.axisPref;
        let nx, ny;
        if (ax === "ew") {
          nx = p.x + (dirx >= 0 ? dist : -dist);
          ny = p.y;
        } else if (ax === "ns") {
          nx = p.x;
          ny = p.y + (diry >= 0 ? dist : -dist);
        } else {
          nx = p.x + dirx * dist;
          ny = p.y + diry * dist;
        }
        state.ruler.pendingEnd  = { x: nx, y: ny };
        state.ruler.pendingAxis = ax;
      }
    } else if (state.ruler.selected >= 0) {
      const m = state.ruler.measures[state.ruler.selected];
      if (m) {
        const idx = cycle.indexOf(m.axis);
        m.axis = cycle[(idx + 1) % cycle.length];
        if (m.axis === "ew") {
          const dir = Math.sign(m.ox1 - m.x0) || 1;
          let x1 = m.ox1;
          const hit = _rulerRaycastWall(m.x0, m.y0, dir, 0);
          if (hit && Math.abs(hit.x - m.x0) <= Math.abs(m.ox1 - m.x0) + 0.5) {
            x1 = hit.x;
          }
          m.x1 = x1;
          m.y1 = m.y0;
        } else if (m.axis === "ns") {
          const dir = Math.sign(m.oy1 - m.y0) || 1;
          let y1 = m.oy1;
          const hit = _rulerRaycastWall(m.x0, m.y0, 0, dir);
          if (hit && Math.abs(hit.y - m.y0) <= Math.abs(m.oy1 - m.y0) + 0.5) {
            y1 = hit.y;
          }
          m.y1 = y1;
          m.x1 = m.x0;
        } else {
          m.x1 = m.ox1;
          m.y1 = m.oy1;
        }
      }
    } else if (state.ruler.hoverIdx >= 0) {
      const m = state.ruler.measures[state.ruler.hoverIdx];
      const idx = cycle.indexOf(m.axis);
      m.axis = cycle[(idx + 1) % cycle.length];
      if (m.axis === "ew") {
        const dir = Math.sign(m.ox1 - m.x0) || 1;
        let x1 = m.ox1;
        const hit = _rulerRaycastWall(m.x0, m.y0, dir, 0);
        if (hit && Math.abs(hit.x - m.x0) <= Math.abs(m.ox1 - m.x0) + 0.5) {
          x1 = hit.x;
        }
        m.x1 = x1;
        m.y1 = m.y0;
      } else if (m.axis === "ns") {
        const dir = Math.sign(m.oy1 - m.y0) || 1;
        let y1 = m.oy1;
        const hit = _rulerRaycastWall(m.x0, m.y0, 0, dir);
        if (hit && Math.abs(hit.y - m.y0) <= Math.abs(m.oy1 - m.y0) + 0.5) {
          y1 = hit.y;
        }
        m.y1 = y1;
        m.x1 = m.x0;
      } else {
        m.x1 = m.ox1;
        m.y1 = m.oy1;
      }
    }
    e.preventDefault();
    e.stopPropagation();
    callDraw();
    return;
  }

  if ((e.key === "d" || e.key === "D") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    e.stopPropagation();
    _rulerOpenNumericInput("dist");
    return;
  }

  if ((e.key === "a" || e.key === "A") &&
      e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey) {
    e.preventDefault();
    e.stopPropagation();
    _rulerOpenNumericInput("angle");
    return;
  }

  if (e.key === "Backspace") {
    if (state.ruler.selected >= 0 &&
        state.ruler.selected < state.ruler.measures.length) {
      state.ruler.measures.splice(state.ruler.selected, 1);
      state.ruler.selected = -1;
      state.ruler.hoverIdx = -1;
    } else if (state.ruler.measures.length > 0 && !state.ruler.pending) {
      state.ruler.measures.pop();
    }
    e.preventDefault();
    e.stopPropagation();
    callDraw();
    return;
  }
}, { capture: true });
"""
