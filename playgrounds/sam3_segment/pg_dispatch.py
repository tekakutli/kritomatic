DISPATCH_JS = r"""
import {
  canvas, board, addPoint, screenToImage, fittedImageRect,
  hitTestMaskAtPoint, toggleMaskSelection, addMaskToSelection,
  replaceMaskSelection, clearMaskSelection, selectAllMasks,
  setMasks, getSelectedMasks,
} from "./pg_core.js";
import { drawView } from "./pg_view.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { runSegment } from "./pg_comfy_client.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

export function draw() {
  drawView();
  syncPanelFields();
  syncMaskList();
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

  // Alt+click on a mask region: pick that mask (or toggle/add).
  if (e.altKey) {
    const idx = hitTestMaskAtPoint(ix, iy);
    if (idx >= 0) {
      if (e.shiftKey)         addMaskToSelection(idx);
      else if (e.ctrlKey || e.metaKey) toggleMaskSelection(idx);
      else                    replaceMaskSelection(idx);
      pgkitNotifyAll();
      draw();
    }
    return;
  }

  // Plain click = positive point; Shift+click = negative point.
  const isPositive = !e.shiftKey;
  addPoint(ix, iy, isPositive);
  pgkitNotifyAll();
  draw();
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  if (sx < r.x0 || sx > r.x0 + r.w || sy < r.y0 || sy > r.y0 + r.h) return;
  const [ix, iy] = screenToImage(sx, sy);
  addPoint(ix, iy, false);
  pgkitNotifyAll();
  draw();
});

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();

  let hovered = -1;
  if (r && sx >= r.x0 && sx <= r.x0 + r.w && sy >= r.y0 && sy <= r.y0 + r.h) {
    const [ix, iy] = screenToImage(sx, sy);
    hovered = hitTestMaskAtPoint(ix, iy);
  }

  if (hovered !== board.hoveredMaskIdx) {
    board.hoveredMaskIdx = hovered;
    canvas.style.cursor = hovered >= 0 ? "pointer" : "crosshair";
    draw();
  }
});

canvas.addEventListener("mouseleave", () => {
  if (board.hoveredMaskIdx !== -1) {
    board.hoveredMaskIdx = -1;
    canvas.style.cursor = "crosshair";
    draw();
  }
});

/* ============================================================
   Keyboard
   ============================================================ */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
  if (e.altKey) return;

  // Ctrl+A: select all masks
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    selectAllMasks();
    pgkitNotifyAll();
    draw();
    return;
  }
  if (e.ctrlKey || e.metaKey) return;

  // 1-9: replace selection with that mask
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
    if (!board.running) {
      runSegment().then(payload => {
        if (payload) {
          setMasks(payload.masks);
          pgkitNotifyAll();
          draw();
        }
      });
    }
    return;
  }

  if (e.key.toLowerCase() === "a") {
    e.preventDefault();
    if (!board.running) {
      runSegment({ skipPoints: true }).then(payload => {
        if (payload) {
          setMasks(payload.masks);
          pgkitNotifyAll();
          draw();
        }
      });
    }
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
