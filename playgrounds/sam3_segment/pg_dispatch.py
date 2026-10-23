DISPATCH_JS = r"""
import {
  canvas, board, addPoint, screenToImage, fittedImageRect,
  setSelectedMask, setMasks,
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

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  if (sx < r.x0 || sx > r.x0 + r.w || sy < r.y0 || sy > r.y0 + r.h) return;

  const [ix, iy] = screenToImage(sx, sy);
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

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  // number keys select a mask
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= 9 && board.masks.length >= n) {
    e.preventDefault();
    setSelectedMask(n - 1);
    draw();
    return;
  }

  if (e.key === "Enter") {
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

  if (e.key === "Escape") {
    e.preventDefault();
    setSelectedMask(-1);
    draw();
  }
});
"""
