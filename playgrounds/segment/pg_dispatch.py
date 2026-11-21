"""The draw orchestrator and the canvas event listeners.

`draw()` is the panel-wide redraw entry point: it calls `drawView()`,
then syncs the panel fields, then syncs the mask list.  The canvas
event listeners own the input lifecycle — mousedown, mousemove,
mouseup, wheel, contextmenu, keydown, keyup.

`drawView()` is the canvas-only redraw.  It is used wherever the
state that changed is visible only on the canvas — pan, zoom,
region-tool interactions, hover highlight — and none of the panel
fields or the mask list need to be re-synced.  Hover in particular
runs on every mouse-move that crosses a mask boundary; using
`draw()` there would rebuild the mask DOM and re-sync every panel
field on every crossing.

REGION TOOL LIFECYCLE
=====================
Region editing is a short-lived gesture, not a latched mode.  Clicking
a Brush / Lasso / Freehand button in the Refine section (see
`pg_panel_scrubs.js`) sets `board.regionMode = true` with that tool
armed.  The tool's natural end-of-action clears `board.regionMode`
again:

  · Brush     — mouseup of a stroke (`_regionStroke.active` branch
                of the window mouseup handler)
  · Freehand  — mouseup of a drag that produced a fillable polygon
                (`_finishFreehand`, only when the polyline filters to
                ≥ 3 points)
  · Lasso     — any of:
                  · Enter with ≥ 3 vertices (keydown handler)
                  · double-click on/near the last placed vertex
                    (mousedown handler; see below for why)
                  · click within LASSO_CLOSE_TOL_PX of the first
                    vertex (mousedown handler)

Incomplete actions (a freehand drag with fewer than 3 filtered
points, a lasso with fewer than 3 vertices) do NOT exit region mode;
the user can keep adding vertices / retry the drag.  Escape and the
"Clear region" button are the explicit cancel paths.

LASSO MOUSE-RELEASE CLOSE
=========================
Lasso is click-to-place-vertex, not drag-to-trace, so "mouse release"
has no single natural vertex to fire on.  The standard equivalent
for a click-based polygon tool is a double-click: the second click of
a double-click is the user's "done" gesture.  We detect it on
mousedown and require the two clicks to be *near each other* (not
just close in time) so a fast user placing vertices at different
positions still gets every vertex placed instead of having their 4th
click swallowed.  Clicking back on the first anchor is the other
standard polygon close gesture and is handled separately.

All three lasso exit points call full `draw()` (not `drawView()`),
because exiting region mode flips the tool button's `.active` class
in the panel, which `syncPanelFields` owns.

IMPORTS SPLIT
=============
The old version imported everything from `./pg_core.js`.  This version
imports each symbol from its new home so the import line names the
module that actually owns the symbol:

    pg_state      — canvas, board, transforms, view manipulation
    pg_masks      — mask selection, points, staging points
    pg_pixels     — pixel hit test
    pg_region     — region paint + lasso fill

The inpaint context rect and its drag state were part of this module
before the split; they now live in `playgrounds/inpaint`.

WHAT'S UNCHANGED
================
The pan state (`_panActive`, `_spaceDown`), the region stroke state
(`_regionStroke`), the shift-drag resize state (`_resizeActive`), the
freehand finish-on-mouseup, the undo/redo keyboard shortcuts, the
mask-selection-on-number keys — all untouched."""

DISPATCH_JS = r"""
import {
  canvas, board, screenToImage, fittedImageRect,
  view, clampPan, zoomAt, resetView,
} from "./pg_state.js";
import {
  selectedMask,
  addStagingPoint, hitTestStagingPoint, removeStagingPoint,
  addPointToSelectedMask, removePointFromSelectedMask,
  hitTestPointOnSelectedMask,
  toggleMaskSelection, addMaskToSelection, replaceMaskSelection,
  clearMaskSelection, selectAllMasks,
} from "./pg_masks.js";
import { hitTestMaskAtPoint } from "./pg_pixels.js";
import {
  paintRegionStroke, fillLassoPolygonInRegion,
} from "./pg_region.js";
import { drawView } from "./pg_view.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

export function draw() {
  drawView();
  syncPanelFields();
  syncMaskList();
}

/* ============================================================
   Auto-run
   ============================================================ */

let _autoRunTimer = null;

function _maybeAutoRun() {
  if (!board.autoRun) return;
  if (!selectedMask()) return;
  if (_autoRunTimer) clearTimeout(_autoRunTimer);
  _autoRunTimer = setTimeout(() => {
    _autoRunTimer = null;
    import("./pg_panel_bindings.js")
      .then(m => m.runRefineAction())
      .catch(() => {});
  }, 400);
}

/* ============================================================
   Module-scope interaction state
   ============================================================ */

let _regionStroke = {
  active: false,
  isErase: false,
  lastX: 0,
  lastY: 0,
};

// Resize: shift + left-mousedown enters resize mode. The brush
// cursor is anchored at that point; dragging changes size only.
// Ends when the mouse button is released OR shift is released.
let _resizeActive = false;
let _resizeStartX = 0;         // screen coords of the anchor
let _resizeStartSize = 0;

// Screen-pixel → size-delta scale for the shift+drag resize. Higher
// = more responsive. (Was 0.5; 2.0 feels direct without overshoot.)
const _RESIZE_SCALE = 2.0;

// Pan state. Middle mouse button, or space+left drag. Captured at
// mousedown so a mid-drag release of space doesn't end the pan.
let _panActive = false;
let _panStartScreenX = 0;
let _panStartScreenY = 0;
let _panStartPanX = 0;
let _panStartPanY = 0;
let _spaceDown = false;

// Lasso close-on-mouse-release state.  `_lastLassoClickTime` is the
// timestamp of the most recent lasso mousedown; a new mousedown that
// is (a) within LASSO_DOUBLE_CLICK_MS of it and (b) within
// LASSO_CLOSE_TOL_PX of the last placed vertex counts as the "second
// click" of a double-click, i.e. the user's "done" gesture.  Both
// thresholds are in the units their names say: milliseconds and
// screen pixels (converted to image pixels via the fitted-rect scale
// at the point of use).
let _lastLassoClickTime = 0;
const LASSO_DOUBLE_CLICK_MS = 320;
const LASSO_CLOSE_TOL_PX    = 12;

function _endResize() {
  if (!_resizeActive) return;
  _resizeActive = false;
  board.resizeAnchor = null;
  canvas.style.cursor = "crosshair";
  drawView();
}

function _isBusyDrawing() {
  return _regionStroke.active || board.freehandActive || _resizeActive;
}

/* ============================================================
   Lasso — close paths
   ============================================================
   Three ways to complete a lasso: Enter (keydown), double-click on
   the last vertex (mousedown, below), click near the first anchor
   (mousedown, below).  All three funnel through `_closeLasso`.

   `_closeLasso` filters near-duplicate vertices (< 2 image px apart)
   before filling so a double-click's "phantom" vertex that lands on
   top of the previous one doesn't corrupt the polygon.  It then
   exits region mode and calls full `draw()` because the tool
   button's `.active` class lives in the panel. */

function _closeLasso(asErase) {
  if (board.lassoPoints.length < 3) {
    // Cannot close a polygon with fewer than 3 vertices; ignore.
    return;
  }
  const filtered = [];
  for (const p of board.lassoPoints) {
    if (filtered.length === 0) { filtered.push(p); continue; }
    const last = filtered[filtered.length - 1];
    if (Math.hypot(p.x - last.x, p.y - last.y) > 2) filtered.push(p);
  }
  if (filtered.length < 3) {
    return;
  }
  fillLassoPolygonInRegion(filtered, asErase);
  board.lassoPoints = [];
  board.regionMode = false;              // completion exits region mode
  _lastLassoClickTime = 0;               // don't bleed into a new lasso
  pgkitNotifyAll();
  draw();
}

function _cancelLasso() {
  if (board.lassoPoints.length === 0) return false;
  board.lassoPoints = [];
  _lastLassoClickTime = 0;
  pgkitNotifyAll();
  drawView();
  return true;
}

/* ============================================================
   Freehand — closes automatically on mouseup
   ============================================================
   The freehand tool's completion event is mouseup of a drag that
   produced a fillable polygon.  A too-short drag does NOT exit
   region mode — the user can try again.  Full `draw()` for the
   same reason as `_closeLasso`. */

function _finishFreehand() {
  if (!board.freehandActive) return;
  const pts = board.freehandPoints || [];
  const isErase = board.freehandIsErase;
  board.freehandActive  = false;
  board.freehandIsErase = false;
  board.freehandPoints  = [];

  const filtered = [];
  for (const p of pts) {
    if (filtered.length === 0) { filtered.push(p); continue; }
    const last = filtered[filtered.length - 1];
    if (Math.hypot(p.x - last.x, p.y - last.y) >= 2) filtered.push(p);
  }

  if (filtered.length >= 3) {
    fillLassoPolygonInRegion(filtered, isErase);
    board.regionMode = false;            // completion exits region mode
  }
  pgkitNotifyAll();
  draw();
}

/* ============================================================
   Mousedown
   ============================================================ */

canvas.addEventListener("mousedown", (e) => {
  // Canvas is not focusable, and the lasso branch below calls
  // preventDefault() on mousedown — which would otherwise leave focus
  // stuck in whatever INPUT/TEXTAREA/SELECT was last focused. That
  // makes the global keydown handler return early, swallowing Enter
  // (which is still a lasso close path). Blur explicitly so keyboard
  // shortcuts target the body again.
  const active = document.activeElement;
  if (active && active !== canvas
      && (active.tagName === "INPUT"
          || active.tagName === "TEXTAREA"
          || active.tagName === "SELECT")) {
    active.blur();
  }

  // Pan: middle mouse button, or space + left button. Must come
  // before any other branch. Suppressed while a stroke / point
  // placement / brush resize is in flight, so it can't collide.
  if ((e.button === 1 || (e.button === 0 && _spaceDown))
      && !_isBusyDrawing()) {
    e.preventDefault();
    _panActive = true;
    _panStartScreenX = e.clientX;
    _panStartScreenY = e.clientY;
    _panStartPanX = view.panX;
    _panStartPanY = view.panY;
    canvas.style.cursor = "grabbing";
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;

  const overImage = sx >= r.x0 && sx <= r.x0 + r.w
                    && sy >= r.y0 && sy <= r.y0 + r.h;
  const [ix, iy] = screenToImage(sx, sy);

  // Resize: shift + left-click in brush region mode. Anchors the ring
  // at (ix, iy); the brush itself does not move during resize.
  if (e.shiftKey && e.button === 0 && overImage
      && board.regionMode && board.regionTool === 'brush'
      && selectedMask()) {
    e.preventDefault();
    _resizeActive = true;
    _resizeStartX = sx;
    _resizeStartSize = board.regionSize;
    board.resizeAnchor = { x: ix, y: iy };
    canvas.style.cursor = "ew-resize";
    drawView();
    return;
  }

  // Alt+click selects a mask
  if (e.altKey && e.button === 0) {
    if (overImage) {
      const idx = hitTestMaskAtPoint(ix, iy);
      if (idx >= 0) {
        if (e.shiftKey)                  addMaskToSelection(idx);
        else if (e.ctrlKey || e.metaKey) toggleMaskSelection(idx);
        else                             replaceMaskSelection(idx);
        pgkitNotifyAll();
        draw();
      }
    }
    return;
  }

  // Lasso drafting. Adds a vertex on left click. Closes on:
  //   · Enter with >= 3 vertices (keydown handler)
  //   · double-click on/near the last placed vertex
  //   · click within LASSO_CLOSE_TOL_PX (screen) of the first vertex
  // Right click is a no-op (Escape cancels the draft).
  //
  // Double-click is the mouse-release equivalent for a click-based
  // tool. We require the two clicks to be near each other — not just
  // close in time — so a user placing vertices rapidly at different
  // positions still gets every vertex placed. Only a fast click *at
  // the same spot* closes, which is the standard double-click
  // gesture.
  if (board.regionMode && board.regionTool === 'lasso' && selectedMask()) {
    if (e.button === 2) {
      e.preventDefault();
      return;
    }
    if (e.button === 0) {
      e.preventDefault();
      // First click: require over-image. Subsequent vertices: anywhere
      // in the canvas widget, including outside the image.
      if (board.lassoPoints.length === 0 && !overImage) return;

      const now = performance.now();
      const lastPt = board.lassoPoints[board.lassoPoints.length - 1];
      const tolImg = LASSO_CLOSE_TOL_PX / r.s;

      const isDouble = !!lastPt
        && (now - _lastLassoClickTime) < LASSO_DOUBLE_CLICK_MS
        && Math.hypot(ix - lastPt.x, iy - lastPt.y) <= tolImg;
      _lastLassoClickTime = now;

      if (isDouble && board.lassoPoints.length >= 3) {
        _closeLasso(board.regionSubtract);
        return;
      }

      // Click near the first anchor closes (standard polygon UX).
      if (board.lassoPoints.length >= 3) {
        const first = board.lassoPoints[0];
        if (Math.hypot(ix - first.x, iy - first.y) <= tolImg) {
          _closeLasso(board.regionSubtract);
          return;
        }
      }

      board.lassoPoints.push({x: ix, y: iy});
      drawView();
      return;
    }
  }

  if (!overImage) return;

  // Freehand lasso: hold-and-drag. Left button = add (or subtract if
  // Subtract is checked), right = erase. Starts only inside the
  // image; the drag path may wander outside.
  if (board.regionMode && board.regionTool === 'freehand' && selectedMask()) {
    if (e.button === 0 || e.button === 2) {
      e.preventDefault();
      board.freehandActive  = true;
      board.freehandIsErase = (e.button === 2) || board.regionSubtract;
      board.freehandPoints  = [{ x: ix, y: iy }];
      drawView();
      return;
    }
  }

  // Region brush painting. Left button = add (or subtract if Subtract
  // is checked), right = erase.
  if (board.regionMode && board.regionTool === 'brush' && selectedMask()) {
    if (e.button === 0 || e.button === 2) {
      e.preventDefault();
      _regionStroke.active = true;
      _regionStroke.isErase = (e.button === 2) || board.regionSubtract;
      _regionStroke.lastX = ix;
      _regionStroke.lastY = iy;
      paintRegionStroke(ix, iy, _regionStroke.isErase, ix, iy);
      drawView();
      return;
    }
  }

  // Regular point adding
  if (e.button !== 0) return;
  const sm = selectedMask();
  const isPositive = !e.shiftKey;
  if (sm) {
    const added = addPointToSelectedMask(ix, iy, isPositive);
    if (!added) return;
    pgkitNotifyAll();
    draw();
    _maybeAutoRun();
  } else {
    const added = addStagingPoint(ix, iy, isPositive);
    if (!added) return;
    pgkitNotifyAll();
    draw();
  }
});

/* ============================================================
   Contextmenu — no-op during lasso draft or region mode
   ============================================================ */

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (board.regionMode) return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  if (sx < r.x0 || sx > r.x0 + r.w || sy < r.y0 || sy > r.y0 + r.h) return;
  const [ix, iy] = screenToImage(sx, sy);

  const tolImg = 10 / r.s;
  const sm = selectedMask();
  if (sm) {
    const existingIdx = hitTestPointOnSelectedMask(ix, iy, tolImg);
    const changed = (existingIdx >= 0)
      ? removePointFromSelectedMask(existingIdx)
      : addPointToSelectedMask(ix, iy, false);
    if (!changed) return;
    pgkitNotifyAll();
    draw();
    _maybeAutoRun();
  } else {
    const existingIdx = hitTestStagingPoint(ix, iy, tolImg);
    const changed = (existingIdx >= 0)
      ? removeStagingPoint(existingIdx)
      : addStagingPoint(ix, iy, false);
    if (!changed) return;
    pgkitNotifyAll();
    draw();
  }
});

/* ============================================================
   Mousemove
   ============================================================ */

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  const overImage = r && sx >= r.x0 && sx <= r.x0 + r.w
                       && sy >= r.y0 && sy <= r.y0 + r.h;

  if (r) {
    const [ix, iy] = screenToImage(sx, sy);
    board.mouse.x = ix;
    board.mouse.y = iy;
    board.mouse.inside = !!overImage;
  }

  // Pan takes priority over everything else.
  if (_panActive) {
    view.panX = _panStartPanX + (e.clientX - _panStartScreenX);
    view.panY = _panStartPanY + (e.clientY - _panStartScreenY);
    clampPan();
    canvas.style.cursor = "grabbing";
    drawView();
    return;
  }

  // Space held: show grab cursor, no other interaction.
  if (_spaceDown) {
    canvas.style.cursor = "grab";
    drawView();
    return;
  }

  // Resize in progress. The brush cursor is locked to the anchor;
  // only the size changes. Ends on button-up or shift-up.
  if (_resizeActive) {
    if (!e.shiftKey || !(e.buttons & 1)) {
      _endResize();
      return;
    }
    const dx = sx - _resizeStartX;
    const newSize = Math.max(2, Math.min(500,
      Math.round(_resizeStartSize + dx * _RESIZE_SCALE)));
    if (newSize !== board.regionSize) {
      board.regionSize = newSize;
      const sizeEl = document.getElementById("regionSize");
      if (sizeEl && document.activeElement !== sizeEl) {
        sizeEl.value = String(board.regionSize);
      }
      syncPanelFields();
    }
    drawView();
    return;
  }

  // Freehand lasso: append a sample on every move while holding.
  if (board.freehandActive && r) {
    const [ix, iy] = screenToImage(sx, sy);
    const pts = board.freehandPoints;
    if (pts.length === 0) {
      board.freehandPoints = [{ x: ix, y: iy }];
    } else {
      const last = pts[pts.length - 1];
      if (Math.hypot(ix - last.x, iy - last.y) >= 1) {
        pts.push({ x: ix, y: iy });
      }
    }
    drawView();
    return;
  }

  // Brush stroke painting
  if (_regionStroke.active && overImage) {
    const [ix, iy] = screenToImage(sx, sy);
    paintRegionStroke(ix, iy, _regionStroke.isErase,
                      _regionStroke.lastX, _regionStroke.lastY);
    _regionStroke.lastX = ix;
    _regionStroke.lastY = iy;
    drawView();
    return;
  }

  // Brush cursor hint
  if (board.regionMode && board.regionTool === 'brush'
      && overImage && selectedMask()) {
    canvas.style.cursor = e.shiftKey ? "ew-resize" : "crosshair";
    drawView();
    return;
  }

  // Lasso pending edge
  if (board.lassoPoints.length > 0) {
    drawView();
    return;
  }

  let hoveredMask = -1;
  let overPoint   = false;
  if (overImage) {
    const [ix, iy] = screenToImage(sx, sy);
    hoveredMask = hitTestMaskAtPoint(ix, iy);
    const tolImg = 10 / r.s;
    const sm = selectedMask();
    if (sm && sm.points.length > 0) {
      overPoint = hitTestPointOnSelectedMask(ix, iy, tolImg) >= 0;
    } else if (!sm && board.stagingPoints.length > 0) {
      overPoint = hitTestStagingPoint(ix, iy, tolImg) >= 0;
    }
  }

  // Hover state for mask overlay highlight.
  //
  // Hover affects only the canvas — drawImageLayer reads
  // `board.hoveredMaskIdx` to vary globalAlpha.  It does NOT
  // affect any panel field or the mask-list DOM, so a canvas-only
  // redraw is enough.  Using draw() here (which additionally
  // runs syncPanelFields and syncMaskList) is what made hover
  // across a mask boundary the largest per-frame cost after
  // syncMaskList's own rebuild.
  if (hoveredMask !== board.hoveredMaskIdx) {
    board.hoveredMaskIdx = hoveredMask;
    drawView();
  }

  if (board.regionMode)      canvas.style.cursor = "crosshair";
  else if (overPoint)        canvas.style.cursor = "not-allowed";
  else if (hoveredMask >= 0) canvas.style.cursor = "pointer";
  else                       canvas.style.cursor = "crosshair";
});

/* ============================================================
   Wheel — zoom at cursor
   ============================================================ */

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;

  // Normalize deltaMode (Firefox lines, others pixels).
  const dy = e.deltaY * (e.deltaMode === 1 ? 16
                       : e.deltaMode === 2 ? 100
                       : 1);
  const factor = Math.exp(-dy * 0.0015);
  zoomAt(mx, my, view.zoom * factor);

  // Refresh mouse position / inside flag for the new transform.
  const r = fittedImageRect();
  if (r) {
    const [ix, iy] = screenToImage(mx, my);
    board.mouse.x = ix;
    board.mouse.y = iy;
    board.mouse.inside = mx >= r.x0 && mx <= r.x0 + r.w
                      && my >= r.y0 && my <= r.y0 + r.h;
  }
  drawView();
}, { passive: false });

/* ============================================================
   Mouseup / Keyup
   ============================================================
   The brush-stroke branch here is the brush tool's completion
   event, so it exits region mode.  Freehand's completion is
   handled inside `_finishFreehand` (only when the polyline was
   fillable).  Lasso has no mouseup path — its "mouse release"
   equivalent is the double-click detection in the mousedown
   handler, because lasso is click-to-place rather than
   drag-to-trace.  All exit paths call full `draw()` because
   region-mode flips the tool button's `.active` class, which
   lives in the panel. */

window.addEventListener("mouseup", () => {
  if (_panActive) {
    _panActive = false;
    canvas.style.cursor = _spaceDown ? "grab" : "crosshair";
  }
  if (board.freehandActive) {
    _finishFreehand();
  }
  if (_regionStroke.active) {
    _regionStroke.active = false;
    board.regionStrokeActive = false;
    board.regionMode = false;            // completion exits region mode
    pgkitNotifyAll();
    draw();
  }
  if (_resizeActive) {
    _endResize();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panActive) canvas.style.cursor = "crosshair";
  }
  if (e.key === "Shift" && _resizeActive) {
    _endResize();
  }
});

/* ============================================================
   Mouseleave
   ============================================================ */

canvas.addEventListener("mouseleave", () => {
  board.mouse.inside = false;
  if (board.hoveredMaskIdx !== -1) {
    board.hoveredMaskIdx = -1;
  }
  if (board.regionMode || board.lassoPoints.length > 0
      || board.freehandActive) {
    drawView();
  } else {
    draw();
  }
  if (_panActive) {
    canvas.style.cursor = "grabbing";
  } else if (_spaceDown) {
    canvas.style.cursor = "grab";
  } else {
    canvas.style.cursor = "crosshair";
  }
});

/* ============================================================
   Keyboard
   ============================================================ */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
            || t.tagName === "SELECT")) return;

  // Space: press-and-hold pan modifier. Handled before everything
  // else so it can't be swallowed by a ctrl guard or another branch.
  if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (!_spaceDown) {
      _spaceDown = true;
      if (!_panActive) canvas.style.cursor = "grab";
    }
    return;
  }

  // Home: reset zoom + pan to fit-to-window
  if (e.key === "Home") {
    e.preventDefault();
    resetView();
    drawView();
    return;
  }

  // Enter also closes the lasso — kept as a keyboard-only path for
  // users who prefer it.  Mouse-only users now have double-click and
  // anchor-click as alternatives (see mousedown lasso branch).
  // Subtract mode decides whether the close adds or erases.
  if (e.key === "Enter" && board.regionMode && board.regionTool === 'lasso'
      && board.lassoPoints.length >= 3) {
    e.preventDefault();
    _closeLasso(board.regionSubtract);
    return;
  }

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    selectAllMasks();
    pgkitNotifyAll();
    draw();
    return;
  }

  // Ctrl+Shift+Z and Ctrl+Y are redo. Checked BEFORE the plain Ctrl+Z
  // undo path so that Ctrl+Shift+Z doesn't fall through to undo.
  // e.key.toLowerCase() === "z" matches both "z" and "Z"; the shift
  // state disambiguates.
  if ((e.ctrlKey || e.metaKey) && e.shiftKey && e.key.toLowerCase() === "z") {
    e.preventDefault();
    import("./pg_panel_bindings.js")
      .then(m => m.redoAction())
      .catch(() => {});
    return;
  }
  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "y") {
    e.preventDefault();
    import("./pg_panel_bindings.js")
      .then(m => m.redoAction())
      .catch(() => {});
    return;
  }

  if ((e.ctrlKey || e.metaKey) && !e.shiftKey && e.key.toLowerCase() === "z") {
    e.preventDefault();
    import("./pg_panel_bindings.js")
      .then(m => m.undoAction())
      .catch(() => {});
    return;
  }

  if (e.ctrlKey || e.metaKey) return;

  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= 9 && board.masks.length >= n) {
    e.preventDefault();
    if (e.shiftKey) addMaskToSelection(n - 1);
    else            replaceMaskSelection(n - 1);
    pgkitNotifyAll();
    draw();
    return;
  }

  if (e.key === "Enter" || e.key.toLowerCase() === "r") {
    e.preventDefault();
    if (board.running) return;
    import("./pg_panel_bindings.js")
      .then(m => {
        if (e.shiftKey || !selectedMask()) return m.runSegmentAllAction();
        return m.runRefineAction();
      })
      .catch(() => {});
    return;
  }

  if (e.key.toLowerCase() === "m") {
    e.preventDefault();
    board.refineModel = (board.refineModel === 'sam2') ? 'sam3' : 'sam2';
    pgkitNotifyAll();
    draw();
    return;
  }

  if (e.key.toLowerCase() === "b") {
    e.preventDefault();
    if (!selectedMask()) return;
    const order = ['brush', 'lasso', 'freehand'];
    const idx = order.indexOf(board.regionTool);
    board.regionTool = order[(idx + 1) % order.length];
    board.regionMode = true;
    board.lassoPoints = [];
    board.freehandPoints = [];
    board.freehandActive = false;
    board.freehandIsErase = false;
    _lastLassoClickTime = 0;
    pgkitNotifyAll();
    draw();
    return;
  }

  if (e.key === "Escape") {
    e.preventDefault();
    // Escape cancels any in-flight draft state, then exits region
    // mode, then clears the mask selection, in that order.
    if (board.freehandActive) {
      board.freehandActive  = false;
      board.freehandIsErase = false;
      board.freehandPoints  = [];
      pgkitNotifyAll();
      draw();
      return;
    }
    if (_cancelLasso()) return;
    if (board.regionMode) {
      board.regionMode = false;
      pgkitNotifyAll();
      draw();
      return;
    }
    clearMaskSelection();
    pgkitNotifyAll();
    draw();
  }
});
"""