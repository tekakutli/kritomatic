"""
pg_dispatch.py — the draw() orchestrator and the canvas event listeners.

ES module.  `draw()` is the orchestrator: it fills the canvas, runs
each renderer in the correct z-order, and then syncs the panel
regions.  The canvas event listeners own the input lifecycle.

DRAW ORDER
==========
    1. paper-fill the whole canvas
    2. clip to the floor band, run drawFloorView
    3. clip to the wall band, run drawWallView
    4. the wall→plan arrow field (from pg_arrows_entry)
    5. the cross-view ghost marker (only during a Shift-held draw)
    6. the hover callout, on top of everything
    7. the divider rule and the section ribbon
    8. the plan-band scale bar
    9. the sheet frame, margin ticks, registration marks
   10. status / buttons / title block / ruler + cable inspector sync

Items 4, 7, 8 are positioned for z-order, not ownership: the arrow
field crosses the two bands, and the ribbon / scale bar carry
paper-coloured backing plates that must sit on top of the arrows.

PANEL SYNC LIVES HERE
=====================
`updateStatus`, `updateCursor`, `updateTitleBlock`, and
`_cableSyncInspector` are called once per frame from `draw()`.  They
live in this file rather than `pg_panel.py` because `pg_panel` is a
leaf: it must not import from `pg_dispatch`, which imports from
almost everything else.  The panel's own repaint requests go through
`callDraw()` (the injected hook from `pg_state`).

RENDERER FAILURE ISOLATION
==========================
Every renderer is invoked through two helpers:

    _try(label, fn)               plain try/catch + log
    _tryClipped(clipFn, fn, label) try/finally around ctx.save()/restore()
                                   + try/catch on the draw call

`_tryClipped` is the important one: `drawFloorView` and `drawWallView`
each run inside a save + clip + restore cycle.  A throw from inside the
renderer used to leave the clip active — `ctx.restore()` was on the
line after `drawFloorView()` and never ran — so every subsequent frame
was silently clipped to the floor band, blanking the wall band.  With
`try/finally`, the clip is always released, so a single throwing
renderer can no longer corrupt the canvas state for the whole session.

`_try` wraps everything else (arrow field, callouts, panel sync).  A
throw there is logged with the renderer's label so the console shows
`[draw] drawFloorView threw: ReferenceError: isHopTransition is not
defined` — a one-line diagnosis — instead of a blank canvas and a
handful of unrelated rendering glitches.  Each label is logged once
per page load; further throws of the same renderer stay silent to
avoid 60 Hz console spam.

MOUSEMOVE FAST PATH
===================
A pure hover — the mouse moving over the canvas with no button held,
the cursor staying inside the same band, the hovered route
unchanged — only updates the status line's coordinate readout and
the CSS cursor.  It does NOT call `draw()`.

The decision is made at the mousemove call site, not inside `draw()`,
because mousemove is the only place where "this event is a pure hover"
is a safe conclusion: it is the only place that knows no drag, pan,
ruler, drawing, or preview branch was taken.

CURSOR SET-ONLY-ON-CHANGE
=========================
`canvas.style.cursor = X` triggers a style recalc even when X equals
the current value.  `_setCursor` caches the last value in a module
variable and only writes on change.

DOM-WRITE GUARDS
================
`updateStatus`, `updateTitleBlock`, and `_cableSyncInspector` each
compare the new string against the DOM's current value before
assigning.

HOVER-HIT
=========
`hitTestRoute` is a static import from `pg_arrows_render.js` and runs
on every mousemove.  An earlier refactor lost this call; hover
highlights only updated on wheel events.

REMOTE NOTIFICATION ON VIEW CHANGE
==================================
The wheel, pan, and Home-key handlers call `pgkitNotifyLocal` for
every view-state field they touch.  Without this, a remote client
would not see zoom, pan, or reset changes made on the playground
side.  The notifications batch per animation frame inside
`pgkit/remote_client.js`.
"""

DISPATCH_JS = r"""
import {
  GEOMETRY, WALL, WALL_HEIGHT,
  canvas, ctx, env,
  layout, viewFloor, viewWall, router,
  state, mouse, anchors,
  PALETTE, FONT_MONO, FONT_SANS,
  segTop, segBottom, isStepFace,
  pointOnStep, fmtCm,
  w2sFloor, s2wFloor, w2sWall, s2wWall, whichView,
} from "./pg_state.js";
import {
  isSegHidden, anchorWall, anchorIsStepFace, anchorSide, junctionOfAnchor,
  trueCableIdOf, trueCableSiblings,
  updateDrawButton,
} from "./pg_core.js";
import { T } from "./pg_i18n.js";
import { drawFloorView, drawScaleBar } from "./pg_view_floor.js";
import { drawWallView } from "./pg_view_wall.js";
import {
  drawSheetFrame, drawSectionRibbon, drawHoverCallout,
  drawCrossViewGhosts,
} from "./pg_view_sheet.js";
import {
  beginDraw, finishDraw, cancelDraw, addDrawPoint,
  nearestFloorVertex, nearestWallVertex,
  nearestFloorGridLine, nearestWallGridLine,
  polylineHitView, computeCornerInfo, handleVertexDrag, handleGridDrag,
} from "./pg_edit.js";
import {
  zoomViewAt, stretchWallX, panView, resetZoomPan,
} from "./pg_navigation.js";
import {
  updateRulerButton, toggleRuler,
  _rulerCloseNumericInput, _rulerSyncMeasureInspector,
  _rulerUpdateSkipState, _rulerHitMeasure, _rulerPickAxis,
  _rulerResolveSecondPoint, rulerSnapPoint,
  _RULER_NUM_INPUT_EL,
} from "./pg_ruler.js";
import { drawWallToPlanArrows } from "./pg_arrows_entry.js";
import { hitTestRoute } from "./pg_arrows_render.js";
import { pgkitNotifyLocal } from "./pgkit/remote_client.js";

/* ==========================================================================
   RENDERER FAILURE ISOLATION
   ==========================================================================
   `_try` logs once per label per page load; further throws of the same
   renderer stay silent so a per-frame throw doesn't spam the console
   at 60 Hz.

   `_tryClipped` closes the clip in `finally` no matter what the
   renderer does — a throw from inside `drawFloorView` used to leak
   the floor-band clip and blank the wall band for the rest of the
   session. */

const _throwLogged = Object.create(null);

function _try(label, fn) {
  try {
    fn();
  } catch (e) {
    if (!_throwLogged[label]) {
      _throwLogged[label] = true;
      console.error("[draw]", label, "threw:", e);
    }
  }
}

function _tryClipped(clipFn, drawFn, label) {
  ctx.save();
  try {
    clipFn();
    drawFn();
  } catch (e) {
    if (!_throwLogged[label]) {
      _throwLogged[label] = true;
      console.error("[draw]", label, "threw:", e);
    }
  } finally {
    ctx.restore();
  }
}

/* ==========================================================================
   DRAW — the orchestrator
   ========================================================================== */

export function draw() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(0, 0, cw, ch);

  /* ---- 1. floor band (clipped, failure-isolated) ---- */
  _tryClipped(
    () => {
      ctx.beginPath();
      ctx.rect(0, 0, cw, layout.floorH);
      ctx.clip();
    },
    drawFloorView,
    "drawFloorView"
  );

  /* ---- 2. wall band (clipped, failure-isolated) ---- */
  _tryClipped(
    () => {
      ctx.beginPath();
      ctx.rect(0, layout.wallY, cw, layout.wallH);
      ctx.clip();
    },
    drawWallView,
    "drawWallView"
  );

  /* ---- 3. arrow field ---- */
  _try("drawWallToPlanArrows", drawWallToPlanArrows);

  /* ---- 4. cross-view ghost / hover callout ---- */
  _try("drawCrossViewGhosts", drawCrossViewGhosts);
  _try("drawHoverCallout",    drawHoverCallout);

  /* ---- 5. divider + ribbon + scale bar (on top of the arrows) ---- */
  ctx.fillStyle = PALETTE.ink;
  ctx.fillRect(0, layout.dividerY,     cw, 1);
  ctx.fillRect(0, layout.dividerY + 2, cw, 1);
  _try("drawSectionRibbon", () => drawSectionRibbon(cw));
  _try("drawScaleBar",      drawScaleBar);

  /* ---- 6. sheet frame ---- */
  _try("drawSheetFrame", () => drawSheetFrame(cw, ch));

  /* ---- 7. panel sync ---- */
  _try("updateStatus",               updateStatus);
  _try("updateDrawButton",           updateDrawButton);
  _try("updateRulerButton",          updateRulerButton);
  _try("updateTitleBlock",           updateTitleBlock);
  _try("_rulerSyncMeasureInspector", _rulerSyncMeasureInspector);
  _try("_cableSyncInspector",        _cableSyncInspector);
}

/* ==========================================================================
   STATUS — write guards
   ========================================================================== */

function _setStatus(el, text, cls) {
  const t = text || "";
  const c = cls  || "";
  if (el.textContent !== t) el.textContent = t;
  if (el.className   !== c) el.className   = c;
}

function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;

  if (state.pan) {
    _setStatus(el, T("stPanning")(state.pan.view), "");
    return;
  }

  if (state.ruler.active) {
    const n   = state.ruler.measures.length;
    const sel = state.ruler.selected;
    const step = state.ruler.pending
      ? (state.ruler.pendingEnd
          ? T("stRulerStepLocked")
          : T("stRulerStepSecond"))
      : (sel >= 0
          ? T("stRulerStepEditing")(sel + 1)
          : T("stRulerStepNew"));
    let hints = "";
    if (state.ruler.pending && !state.ruler.pendingEnd) {
      if (state.ruler.axisPref) {
        hints = T("stRulerAxisFixed")(state.ruler.axisPref);
      } else {
        hints = T("stRulerAxisAuto");
      }
    }
    const numeric  = state.ruler.pending ? T("stRulerNumeric") : "";
    const stepHint = state.ruler.ignoreSteps
      ? T("stRulerSkipOn")
      : T("stRulerSkipOff");
    _setStatus(el,
      T("stRulerCount")(n) + " · " + step +
      hints + numeric + stepHint + T("stRulerExit"),
      "warn");
    return;
  }

  if (state.drawing) {
    const n = state.drawing.anchorIds.length;
    const snapHint = state.snapEnabled ? T("stDrawingSnapOn")
                                       : T("stDrawingSnapHint");
    let text;
    if (state.drawing.view === null) {
      text = T("stDrawingFirst") + snapHint;
    } else {
      const base = state.drawing.baseCableId != null
        ? T("stDrawingAdopted") : "";
      let focusHint = "";
      if (state.drawing.view === "wall" && state.focusSeg >= 0) {
        const s = WALL.segments[state.focusSeg];
        const tag = s && s.tag ? s.tag : ("segment " + (state.focusSeg + 1));
        focusHint = T("stDrawingFocus")(tag);
      }
      text =
        T("stDrawingN")(state.drawing.view, n) + base + focusHint +
        snapHint + T("stDrawingFinish");
    }
    _setStatus(el, text, "warn");
    return;
  }

  if (state.dragGrid) {
    const g = state.dragGrid.grid;
    _setStatus(el,
      T("stGridDrag")(state.dragGrid.view, g.type, g.pos.toFixed(0)),
      "");
    return;
  }

  if (state.dragVertex) {
    const { cable, index } = state.dragVertex;
    const a = anchors.get(cable.anchorIds[index]);
    let tag = "";
    if (a) {
      if (a.space === "wall-edge") {
        const s = anchorSide(a);
        if (s === "top")         tag = T("stVertexStepTop");
        else if (s === "bottom") tag = T("stVertexStepBot");
        else                     tag = T("stVertexWallEdge");
        if (junctionOfAnchor(a) >= 0) tag += T("stVertexCorner");
      } else if (a.gridId != null) {
        tag = T("stVertexGrid");
      } else {
        tag = T("stVertexFree");
      }
    }
    const snapHint = state.snapEnabled ? "" : T("stVertexSnapHint");
    _setStatus(el,
      T("stVertexTagBase")(index) + tag + snapHint,
      (a && a.space === "wall-edge") ? "warn" : "");
    return;
  }

  if (state.hoveredRoute) {
    const seg = WALL.segments[state.hoveredRoute.segIdx];
    const tag = seg && seg.tag ? seg.tag
              : ("segment " + (state.hoveredRoute.segIdx + 1));
    const len  = seg ? fmtCm(seg.len) : "?";
    const kind = isStepFace(seg) ? T("stHoverStep") : "";
    _setStatus(el,
      T("stHoverSeg")(tag) + kind + T("stHoverLen")(len),
      "warn");
    return;
  }

  if (state.selectedCable) {
    const sibs = trueCableSiblings(state.selectedCable);
    if (sibs.length > 1) {
      const tid    = trueCableIdOf(state.selectedCable);
      const floors = sibs.filter(c => state.floorCables.includes(c)).length;
      const walls  = sibs.filter(c => state.wallCables.includes(c)).length;
      _setStatus(el,
        T("stCableGroup")(tid, sibs.length, floors, walls),
        "ok");
      return;
    }
  }

  if (!mouse.inside || !mouse.view) {
    _setStatus(el, "", "");
    return;
  }
  if (mouse.view === "floor") {
    const [wx, wy] = s2wFloor(mouse.sx, mouse.sy);
    const onStep  = pointOnStep(wx, wy) ? T("stOnStep") : "";
    const zSuffix = Math.abs(viewFloor.zoom - 1) > 0.02
      ? T("stZoom")(Math.round(viewFloor.zoom * 100)) : "";
    _setStatus(el,
      T("stFloorPos")(wx.toFixed(0), wy.toFixed(0)) + onStep + zSuffix,
      "ok");
  } else {
    const [u, v] = s2wWall(mouse.sx, mouse.sy);
    const zSuffix = Math.abs(viewWall.zoom - 1) > 0.02
      ? T("stZoom")(Math.round(viewWall.zoom * 100)) : "";
    _setStatus(el,
      T("stWallPos")(u.toFixed(0), v.toFixed(0)) + zSuffix,
      "ok");
  }
}

/* ==========================================================================
   CURSOR
   ========================================================================== */

let _lastCursor = "default";
function updateCursor() {
  let next;
  if (!mouse.inside)           next = "default";
  else if (state.pan)          next = "grabbing";
  else if (state.ruler.active) next = "crosshair";
  else if (state.spaceHeld && !state.drawing) next = "grab";
  else if (state.drawing)      next = "crosshair";
  else if (state.hoveredRoute) next = "pointer";
  else                         next = "crosshair";

  if (next !== _lastCursor) {
    canvas.style.cursor = next;
    _lastCursor = next;
  }
}

/* ==========================================================================
   TITLE BLOCK — write guards
   ========================================================================== */

const _TB_REFS = {
  project: null, sheet: null, ceiling: null, scale: null, selection: null,
};
function updateTitleBlock() {
  if (!_TB_REFS.project) {
    _TB_REFS.project   = document.getElementById("tb_project");
    _TB_REFS.sheet     = document.getElementById("tb_sheet");
    _TB_REFS.ceiling   = document.getElementById("tb_ceiling");
    _TB_REFS.scale     = document.getElementById("tb_scale");
    _TB_REFS.selection = document.getElementById("tb_selection");
    if (!_TB_REFS.project) return;
  }

  const scaleN = Math.max(1, Math.round(1 / Math.max(viewFloor.scale, 1e-6)));

  let selText = T("tbSelNone");
  if (state.selectedCable) {
    const tid  = trueCableIdOf(state.selectedCable);
    const sibs = trueCableSiblings(state.selectedCable);
    selText = T("tbSelCable")(tid, sibs.length);
  } else if (state.selectedVertex) {
    selText = T("tbSelVertex")(state.selectedVertex.index + 1);
  } else if (state.drawing) {
    selText = T("tbSelDrawing")(state.drawing.anchorIds.length);
  }

  const projectText = T("tbProjectVal");
  if (_TB_REFS.project.textContent !== projectText) {
    _TB_REFS.project.textContent = projectText;
  }

  const sheetText = T("tbSheetVal")(GEOMETRY.cutZ);
  if (_TB_REFS.sheet.textContent !== sheetText) {
    _TB_REFS.sheet.textContent = sheetText;
  }

  const ceilingText = T("tbCeilingVal")(WALL_HEIGHT);
  if (_TB_REFS.ceiling.textContent !== ceilingText) {
    _TB_REFS.ceiling.textContent = ceilingText;
  }

  const scaleText = T("tbScaleVal")(scaleN, viewFloor.zoom);
  if (_TB_REFS.scale.textContent !== scaleText) {
    _TB_REFS.scale.textContent = scaleText;
  }

  if (_TB_REFS.selection.textContent !== selText) {
    _TB_REFS.selection.textContent = selText;
  }
}

/* ==========================================================================
   CABLE INSPECTOR — write guards
   ========================================================================== */

function _cableSyncInspector() {
  const section   = document.getElementById("cableSection");
  const idxEl     = document.getElementById("cableIndex");
  const vertexEl  = document.getElementById("cableVertexInfo");
  const delVertEl = document.getElementById("cableDeleteVertexBtn");
  if (!section || !idxEl || !vertexEl || !delVertEl) return;

  const cable = state.selectedCable;
  if (!cable) {
    if (section.style.display !== "none") section.style.display = "none";
    return;
  }
  if (section.style.display !== "block") section.style.display = "block";

  const tid    = trueCableIdOf(cable);
  const sibs   = trueCableSiblings(cable);
  const floors = sibs.filter(c => state.floorCables.includes(c)).length;
  const walls  = sibs.filter(c => state.wallCables.includes(c)).length;

  const headerText = T("ciHeader")(tid, sibs.length, floors, walls);
  if (idxEl.textContent !== headerText) idxEl.textContent = headerText;

  const sv = state.selectedVertex;
  if (sv && sv.cable === cable) {
    const a = anchors.get(cable.anchorIds[sv.index]);
    let tag = T("ciVertexBase")(sv.index + 1);
    if (a) {
      if (a.space === "wall-edge") {
        const side = anchorSide(a);
        if (side === "top")         tag += "  ·  " + T("ciVertexStepTop");
        else if (side === "bottom") tag += "  ·  " + T("ciVertexStepBot");
        else                        tag += "  ·  " + T("ciVertexWallEdge");
        if (junctionOfAnchor(a) >= 0)
          tag += "  ·  " + T("ciVertexCorner");
      } else if (a.gridId != null) {
        tag += "  ·  " + T("ciVertexGrid");
      } else {
        tag += "  ·  " + T("ciVertexFree");
      }
    }
    if (vertexEl.textContent !== tag) vertexEl.textContent = tag;
    if (delVertEl.style.display !== "block") delVertEl.style.display = "block";
  } else {
    const emptyText = T("ciVertexNone");
    if (vertexEl.textContent !== emptyText) vertexEl.textContent = emptyText;
    if (delVertEl.style.display !== "none") delVertEl.style.display = "none";
  }
}

/* ==========================================================================
   EVENT HANDLERS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (document.activeElement && document.activeElement.blur) {
    document.activeElement.blur();
  }

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const view = whichView(sy);

  if (e.button === 1) {
    if (!view) return;
    e.preventDefault();
    state.pan = { view, lastSx: sx, lastSy: sy };
    canvas.style.cursor = "grabbing";
    return;
  }

  if (e.button === 0 && state.spaceHeld) {
    if (!view) return;
    e.preventDefault();
    state.pan = { view, lastSx: sx, lastSy: sy };
    canvas.style.cursor = "grabbing";
    return;
  }

  if (e.button !== 0) return;
  if (!view) return;

  /* ---- ruler mode intercepts the left click ---- */
  if (state.ruler.active) {
    if (view !== "floor") return;

    if (_RULER_NUM_INPUT_EL &&
        _RULER_NUM_INPUT_EL.classList.contains("show")) {
      _rulerCloseNumericInput();
      return;
    }

    const [wx, wy] = s2wFloor(sx, sy);
    _rulerUpdateSkipState(wx, wy, e.shiftKey);
    const pt = rulerSnapPoint(wx, wy);

    if (state.ruler.pending) {
      const p = state.ruler.pending;
      let axis, r2;
      if (state.ruler.pendingEnd) {
        axis = state.ruler.pendingAxis || state.ruler.axisPref || "aligned";
        r2 = state.ruler.pendingEnd;
      } else {
        axis = _rulerPickAxis(p.x, p.y, pt.x, pt.y,
                              state.ruler.axisPref, e.altKey);
        r2 = _rulerResolveSecondPoint(p, pt, axis);
      }
      if (Math.hypot(r2.x - p.x, r2.y - p.y) < 1.0) { draw(); return; }
      state.ruler.measures.push({
        x0: p.x, y0: p.y,
        x1: r2.x, y1: r2.y,
        ox1: pt.x, oy1: pt.y,
        axis,
      });
      state.ruler.selected          = state.ruler.measures.length - 1;
      state.ruler.pending           = null;
      state.ruler.axisPref          = null;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      _rulerCloseNumericInput();
      draw();
      return;
    }

    const hi = _rulerHitMeasure(sx, sy);
    if (hi >= 0) {
      state.ruler.selected = hi;
      _rulerSyncMeasureInspector();
      draw();
      return;
    }

    state.ruler.selected          = -1;
    state.ruler.pending           = pt;
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    state.ruler._lastDStep        = Infinity;
    state.ruler.pendingEnd        = null;
    state.ruler.pendingAxis       = null;
    _rulerCloseNumericInput();
    _rulerUpdateSkipState(wx, wy, e.shiftKey);
    draw();
    return;
  }

  if (state.drawing) {
    if (state.drawing.view === null) state.drawing.view = view;
    else if (state.drawing.view !== view) return;
    addDrawPoint(sx, sy, e.altKey);
    return;
  }

  if (view === "floor") {
    const g = nearestFloorGridLine(sx, sy);
    if (g) { state.dragGrid = { grid: g, view: "floor" }; return; }
    const v = nearestFloorVertex(sx, sy);
    if (v) {
      state.selectedCable  = v.cable;
      state.selectedVertex = v;
      state.dragVertex = { ...v, view: "floor", corner: null };
      draw();
      return;
    }
    const [wx, wy] = s2wFloor(sx, sy);
    const tol = 12 / viewFloor.scale;
    for (let i = state.floorCables.length - 1; i >= 0; i--) {
      const c = state.floorCables[i];
      if (state.drawing && state.drawing.baseCableId === c.id) continue;
      if (polylineHitView("floor", wx, wy, c, tol)) {
        state.selectedCable  = c;
        state.selectedVertex = null;
        draw();
        return;
      }
    }
    state.selectedCable  = null;
    state.selectedVertex = null;
    draw();
  } else {
    const g = nearestWallGridLine(sx, sy);
    if (g) { state.dragGrid = { grid: g, view: "wall" }; return; }
    const v = nearestWallVertex(sx, sy);
    if (v) {
      state.selectedCable  = v.cable;
      state.selectedVertex = v;
      const a = anchors.get(v.cable.anchorIds[v.index]);
      const corner = computeCornerInfo(a);
      state.dragVertex = { ...v, view: "wall", corner };
      draw();
      return;
    }
    const [u, vv] = s2wWall(sx, sy);
    const tol = 12 / Math.min(viewWall.scaleX, viewWall.scaleY);
    for (let i = state.wallCables.length - 1; i >= 0; i--) {
      const c = state.wallCables[i];
      if (state.drawing && state.drawing.baseCableId === c.id) continue;
      if (polylineHitView("wall", u, vv, c, tol)) {
        state.selectedCable  = c;
        state.selectedVertex = null;
        draw();
        return;
      }
    }
    state.selectedCable  = null;
    state.selectedVertex = null;
    draw();
  }
});

canvas.addEventListener("contextmenu", (e) => {
  if (state.ruler.active) {
    e.preventDefault();
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
    draw();
    return;
  }
  if (!state.drawing) return;
  e.preventDefault();
  if (state.drawing.anchorIds.length > 0) {
    state.drawing.anchorIds.pop();
    if (state.drawing.view === "wall" && state.drawing.anchorIds.length > 0) {
      const lastId = state.drawing.anchorIds[state.drawing.anchorIds.length - 1];
      const a = anchors.get(lastId);
      if (a && a.space === "wall-edge") {
        import("./pg_edit.js").then(m => m.enterFocus(a.segIdx)).catch(() => {});
      }
    } else if (state.drawing.view === "wall") {
      import("./pg_edit.js").then(m => m.exitFocus()).catch(() => {});
    }
    draw();
  }
});

canvas.addEventListener("wheel", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const view = whichView(sy);
  if (!view) return;
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.10 : 1 / 1.10;
  if (view === "floor") {
    zoomViewAt(viewFloor, s2wFloor, w2sFloor, sx, sy, factor);
    pgkitNotifyLocal("view.zoom");
    pgkitNotifyLocal("view.panX");
    pgkitNotifyLocal("view.panY");
  } else if (e.shiftKey) {
    stretchWallX(sx, factor);
    pgkitNotifyLocal("wall.zoomX");
    pgkitNotifyLocal("wall.panX");
  } else {
    zoomViewAt(viewWall, s2wWall, w2sWall, sx, sy, factor);
    pgkitNotifyLocal("wall.zoom");
    pgkitNotifyLocal("wall.panX");
    pgkitNotifyLocal("wall.panY");
  }
  state.hoveredRoute = null;
  draw();
  if (mouse.inside && !state.drawing && !state.ruler.active) {
    state.hoveredRoute = hitTestRoute(sx, sy);
    draw();
  }
}, { passive: false });

/* ==========================================================================
   MOUSEMOVE — fast path
   ========================================================================== */

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

  const prevInside  = mouse.inside;
  const prevView    = mouse.view;
  const prevHovered = state.hoveredRoute;

  mouse.sx = sx; mouse.sy = sy;
  mouse.alt   = e.altKey;
  mouse.shift = e.shiftKey;
  mouse.inside = (sx >= 0 && sy >= 0 &&
                  sx < window.innerWidth && sy < window.innerHeight);
  mouse.view = mouse.inside ? whichView(sy) : null;

  if (state.pan) {
    const dx = sx - state.pan.lastSx;
    const dy = sy - state.pan.lastSy;
    state.pan.lastSx = sx;
    state.pan.lastSy = sy;
    if (state.pan.view === "floor") {
      panView(viewFloor, dx, dy);
      pgkitNotifyLocal("view.panX");
      pgkitNotifyLocal("view.panY");
    } else {
      panView(viewWall, dx, dy);
      pgkitNotifyLocal("wall.panX");
      pgkitNotifyLocal("wall.panY");
    }
    state.hoveredRoute = null;
    draw();
    return;
  }

  if (state.ruler.active) {
    if (mouse.inside && mouse.view === "floor") {
      const [wx, wy] = s2wFloor(sx, sy);
      _rulerUpdateSkipState(wx, wy, e.shiftKey);
      state.ruler.hoverPt  = rulerSnapPoint(wx, wy);
      state.ruler.hoverIdx = state.ruler.pending
        ? -1
        : _rulerHitMeasure(sx, sy);
    } else {
      state.ruler.hoverPt  = null;
      state.ruler.hoverIdx = -1;
    }
    updateCursor();
    draw();
    return;
  }

  if (state.dragGrid)   { handleGridDrag(sx, sy);   draw(); return; }
  if (state.dragVertex) { handleVertexDrag(sx, sy); draw(); return; }

  /* ---- hover hit test ---- */
  if (!state.drawing && mouse.inside) {
    state.hoveredRoute = hitTestRoute(sx, sy);
  } else {
    state.hoveredRoute = null;
  }

  /* ---- drawing preview ---- */
  if (state.drawing) {
    if (state.drawing.view === null) {
      state.drawingPreview = mouse.inside ? { sx, sy } : null;
    } else if (mouse.inside && mouse.view === state.drawing.view) {
      state.drawingPreview = { sx, sy };
    } else {
      state.drawingPreview = null;
    }
  }

  updateCursor();

  const viewChanged  = (mouse.view !== prevView) || (mouse.inside !== prevInside);
  const hoverChanged = (state.hoveredRoute !== prevHovered);
  const anyActive    = state.drawing || state.ruler.active;

  if (!anyActive && !viewChanged && !hoverChanged) {
    updateStatus();
    return;
  }

  draw();
});

window.addEventListener("mouseup", (e) => {
  if (state.pan) {
    state.pan = null;
    updateCursor();
    return;
  }
  state.dragGrid   = null;
  state.dragVertex = null;
  updateCursor();
  draw();
});

/* ==========================================================================
   KEYBOARD
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if (e.code === "Space" && !state.spaceHeld) {
    state.spaceHeld = true;
    if (mouse.inside && !state.drawing) canvas.style.cursor = "grab";
    e.preventDefault();
    return;
  }

  if (e.key === "Home") {
    resetZoomPan();
    pgkitNotifyLocal("view.zoom");
    pgkitNotifyLocal("view.panX");
    pgkitNotifyLocal("view.panY");
    pgkitNotifyLocal("wall.zoom");
    pgkitNotifyLocal("wall.zoomX");
    pgkitNotifyLocal("wall.panX");
    pgkitNotifyLocal("wall.panY");
    e.preventDefault();
    return;
  }

  if (e.key === "n" || e.key === "N") {
    if (!mouse.inside || !mouse.view) return;
    if (state.drawing) return;
    e.preventDefault();
    import("./pg_view_sheet.js").then(m => m.addNote()).catch(() => {});
  }
});

window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    state.spaceHeld = false;
    if (!state.pan) updateCursor();
  }
});

window.addEventListener("blur", () => {
  state.spaceHeld = false;
  if (state.pan) {
    state.pan = null;
    updateCursor();
  }
});
"""
