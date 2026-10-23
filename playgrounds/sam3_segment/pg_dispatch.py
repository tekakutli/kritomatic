DISPATCH_JS = r"""
import {
  canvas, board, screenToImage, fittedImageRect, selectedMask,
  addStagingPoint, clearStagingPoints, hitTestStagingPoint, removeStagingPoint,
  addPointToSelectedMask, removePointFromSelectedMask,
  hitTestPointOnSelectedMask, hitTestMaskAtPoint,
  toggleMaskSelection, addMaskToSelection, replaceMaskSelection,
  clearMaskSelection, selectAllMasks,
} from "./pg_core.js";
import { drawView } from "./pg_view.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

export function draw() {
  drawView();
  syncPanelFields();
  syncMaskList();
}

/* ============================================================
   Auto-run (debounced) — only for mask refinements
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
   Canvas events
   ============================================================ */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  if (sx < r.x0 || sx > r.x0 + r.w || sy < r.y0 || sy > r.y0 + r.h) return;

  const [ix, iy] = screenToImage(sx, sy);

  // Alt+click selects a mask regardless of context
  if (e.altKey) {
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

  // Mask selected → mask points. Otherwise → staging points.
  const selMask = selectedMask();
  const isPositive = !e.shiftKey;
  if (selMask) {
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
    // Staging points do NOT auto-run; Segment All is explicit.
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  if (sx < r.x0 || sx > r.x0 + r.w || sy < r.y0 || sy > r.y0 + r.h) return;
  const [ix, iy] = screenToImage(sx, sy);

  const tolImg = 10 / r.s;

  const selMask = selectedMask();
  if (selMask) {
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

  let hoveredMask = -1;
  let overPoint   = false;
  if (r && sx >= r.x0 && sx <= r.x0 + r.w && sy >= r.y0 && sy <= r.y0 + r.h) {
    const [ix, iy] = screenToImage(sx, sy);
    hoveredMask = hitTestMaskAtPoint(ix, iy);
    const tolImg = 10 / r.s;
    const selMask = selectedMask();
    if (selMask && selMask.points.length > 0) {
      overPoint = hitTestPointOnSelectedMask(ix, iy, tolImg) >= 0;
    } else if (!selMask && board.stagingPoints.length > 0) {
      overPoint = hitTestStagingPoint(ix, iy, tolImg) >= 0;
    }
  }

  if (hoveredMask !== board.hoveredMaskIdx) {
    board.hoveredMaskIdx = hoveredMask;
    draw();
  }

  if (overPoint)             canvas.style.cursor = "not-allowed";
  else if (hoveredMask >= 0) canvas.style.cursor = "pointer";
  else                       canvas.style.cursor = "crosshair";
});

canvas.addEventListener("mouseleave", () => {
  if (board.hoveredMaskIdx !== -1) {
    board.hoveredMaskIdx = -1;
    draw();
  }
  canvas.style.cursor = "crosshair";
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

  if (e.key === "Escape") {
    e.preventDefault();
    clearMaskSelection();
    pgkitNotifyAll();
    draw();
  }
});
"""
