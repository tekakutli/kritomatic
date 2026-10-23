DISPATCH_JS = r"""
import {
  canvas, board, screenToImage, fittedImageRect, selectedMask,
  addStagingPoint, hitTestStagingPoint, removeStagingPoint,
  addPointToSelectedMask, removePointFromSelectedMask,
  hitTestPointOnSelectedMask, hitTestMaskAtPoint,
  paintRegionStroke, fillLassoPolygonInRegion,
  toggleMaskSelection, addMaskToSelection, replaceMaskSelection,
  clearMaskSelection, selectAllMasks,
} from "./pg_core.js";
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
   Auto-run (debounced) — points on selected mask only
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
   Region stroke state
   ============================================================ */

let _regionStroke = {
  active: false,
  isErase: false,
  lastX: 0,
  lastY: 0,
};

/* ============================================================
   Lasso helpers
   ============================================================ */

function _closeLasso(asErase) {
  if (board.lassoPoints.length < 3) {
    board.lassoPoints = [];
    return;
  }
  // Dedupe near-identical consecutive vertices (from dblclick's two mousedowns)
  const filtered = [];
  for (const p of board.lassoPoints) {
    if (filtered.length === 0) { filtered.push(p); continue; }
    const last = filtered[filtered.length - 1];
    if (Math.hypot(p.x - last.x, p.y - last.y) > 2) filtered.push(p);
  }
  if (filtered.length < 3) {
    board.lassoPoints = [];
    return;
  }
  fillLassoPolygonInRegion(filtered, asErase);
  board.lassoPoints = [];
  pgkitNotifyAll();
  drawView();
}

function _cancelLasso() {
  if (board.lassoPoints.length === 0) return false;
  board.lassoPoints = [];
  pgkitNotifyAll();
  drawView();
  return true;
}

/* ============================================================
   Canvas events
   ============================================================ */

canvas.addEventListener("mousedown", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  if (sx < r.x0 || sx > r.x0 + r.w || sy < r.y0 || sy > r.y0 + r.h) return;

  const [ix, iy] = screenToImage(sx, sy);

  // Alt+click selects a mask, always takes priority
  if (e.altKey && e.button === 0) {
    const idx = hitTestMaskAtPoint(ix, iy);
    if (idx >= 0) {
      if (e.shiftKey)                  addMaskToSelection(idx);
      else if (e.ctrlKey || e.metaKey) toggleMaskSelection(idx);
      else                             replaceMaskSelection(idx);
      pgkitNotifyAll();
      draw();
    }
    return;
  }

  // Region mode active with a selected mask
  if (board.regionMode && selectedMask()) {
    if (board.regionTool === 'brush') {
      if (e.button === 0 || e.button === 2) {
        e.preventDefault();
        _regionStroke.active = true;
        _regionStroke.isErase = (e.button === 2);
        _regionStroke.lastX = ix;
        _regionStroke.lastY = iy;
        paintRegionStroke(ix, iy, _regionStroke.isErase, ix, iy);
        drawView();
        return;
      }
    } else if (board.regionTool === 'lasso') {
      if (e.button === 0) {
        e.preventDefault();
        if (board.lassoPoints.length === 0) {
          board.lassoPoints.push({x: ix, y: iy});
        } else if (board.lassoPoints.length >= 3) {
          const first = board.lassoPoints[0];
          const closeThresholdImg = 12 / r.s;
          if (Math.hypot(ix - first.x, iy - first.y) < closeThresholdImg) {
            _closeLasso(false);
            return;
          }
          board.lassoPoints.push({x: ix, y: iy});
        } else {
          board.lassoPoints.push({x: ix, y: iy});
        }
        drawView();
        return;
      } else if (e.button === 2) {
        e.preventDefault();
        if (board.lassoPoints.length >= 3) {
          _closeLasso(true);   // close as erase
        } else {
          _cancelLasso();
        }
        return;
      }
    }
  }

  if (e.button !== 0) return;

  // Regular point-adding path
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

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  // Lasso right-click is fully handled in mousedown (close-as-erase);
  // brush right-click is handled as start-of-erase-stroke.
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

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();

  const overImage = r && sx >= r.x0 && sx <= r.x0 + r.w
                       && sy >= r.y0 && sy <= r.y0 + r.h;

  // Always update tracked mouse position
  if (overImage) {
    const [ix, iy] = screenToImage(sx, sy);
    board.mouse.x = ix;
    board.mouse.y = iy;
    board.mouse.inside = true;
  } else {
    board.mouse.inside = false;
  }

  // Brush stroke in progress — paint and refresh view
  if (_regionStroke.active && overImage) {
    const [ix, iy] = screenToImage(sx, sy);
    paintRegionStroke(ix, iy, _regionStroke.isErase,
                      _regionStroke.lastX, _regionStroke.lastY);
    _regionStroke.lastX = ix;
    _regionStroke.lastY = iy;
    drawView();
    return;
  }

  // Brush cursor preview
  if (board.regionMode && board.regionTool === 'brush'
      && overImage && selectedMask()) {
    drawView();
    return;
  }

  // Lasso pending-edge preview
  if (board.lassoPoints.length > 0 && overImage) {
    drawView();
    return;
  }

  // Hover logic
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

  if (hoveredMask !== board.hoveredMaskIdx) {
    board.hoveredMaskIdx = hoveredMask;
    draw();
  }

  if (board.regionMode)      canvas.style.cursor = "crosshair";
  else if (overPoint)        canvas.style.cursor = "not-allowed";
  else if (hoveredMask >= 0) canvas.style.cursor = "pointer";
  else                       canvas.style.cursor = "crosshair";
});

window.addEventListener("mouseup", () => {
  if (_regionStroke.active) {
    _regionStroke.active = false;
    pgkitNotifyAll();
    drawView();
  }
});

canvas.addEventListener("mouseleave", () => {
  board.mouse.inside = false;
  if (board.hoveredMaskIdx !== -1) {
    board.hoveredMaskIdx = -1;
  }
  // Redraw to clear brush cursor / lasso pending edge
  if (board.regionMode || board.lassoPoints.length > 0) {
    drawView();
  } else if (board.hoveredMaskIdx === -1) {
    draw();
  }
  canvas.style.cursor = "crosshair";
});

canvas.addEventListener("dblclick", (e) => {
  if (board.regionMode && board.regionTool === 'lasso'
      && board.lassoPoints.length >= 3) {
    e.preventDefault();
    _closeLasso(false);
  }
});

/* ============================================================
   Keyboard
   ============================================================ */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    selectAllMasks();
    pgkitNotifyAll();
    draw();
    return;
  }

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "z") {
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
    board.regionTool = (board.regionTool === 'brush') ? 'lasso' : 'brush';
    board.lassoPoints = [];
    pgkitNotifyAll();
    // Sync radios in the panel
    const rB = document.getElementById("regionToolBrush");
    const rL = document.getElementById("regionToolLasso");
    if (rB) rB.checked = (board.regionTool === 'brush');
    if (rL) rL.checked = (board.regionTool === 'lasso');
    draw();
    return;
  }

  if (e.key === "Escape") {
    e.preventDefault();
    // Priority: cancel lasso > exit region mode > clear selection
    if (_cancelLasso()) return;
    if (board.regionMode) {
      board.regionMode = false;
      const cb = document.getElementById("regionMode");
      if (cb) cb.checked = false;
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
