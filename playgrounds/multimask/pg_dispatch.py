"""draw() and canvas event listeners.

Drag modes: pan (space+drag or middle).  Zoom at cursor on wheel.
Alt+click selects an area.  Clean click on a mask selects it.
Click on empty deselects.  No rotation, no context drag."""

DISPATCH_JS = r"""
import {
  canvas, board, view, screenToImage, fittedImageRect,
  clampPan, zoomAt, resetView,
} from "./pg_state.js";
import { hitTestAreaAtPoint } from "./pg_pixels.js";
import { selectArea, hoverArea } from "./pg_areas.js";
import { drawView } from "./pg_view.js";
import { syncAreaList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";

export function draw() {
  drawView();
  syncPanelFields();
  syncAreaList();
}

let _panActive = false;
let _panStartSx = 0, _panStartSy = 0;
let _panStartX = 0, _panStartY = 0;
let _spaceDown = false;

canvas.addEventListener("mousedown", (e) => {
  const active = document.activeElement;
  if (active && active !== canvas
      && (active.tagName === "INPUT"
          || active.tagName === "TEXTAREA"
          || active.tagName === "SELECT")) {
    active.blur();
  }

  if (e.button === 1 || (e.button === 0 && _spaceDown)) {
    e.preventDefault();
    _panActive = true;
    _panStartSx = e.clientX;
    _panStartSy = e.clientY;
    _panStartX = view.panX;
    _panStartY = view.panY;
    canvas.style.cursor = "grabbing";
    return;
  }

  if (e.button !== 0) return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;

  const [ix, iy] = screenToImage(sx, sy);
  const overImage = ix >= 0 && ix <= board.imageWidth
                 && iy >= 0 && iy <= board.imageHeight;
  const hitId = overImage ? hitTestAreaAtPoint(ix, iy) : null;

  if (e.altKey) {
    if (hitId) { selectArea(hitId); draw(); }
    return;
  }

  selectArea(hitId);
  draw();
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

  if (_panActive) {
    view.panX = _panStartX + (e.clientX - _panStartSx);
    view.panY = _panStartY + (e.clientY - _panStartSy);
    clampPan();
    drawView();
    return;
  }

  if (_spaceDown) { canvas.style.cursor = "grab"; return; }

  const r = fittedImageRect();
  if (!r) { hoverArea(null); canvas.style.cursor = "default"; return; }

  const [ix, iy] = screenToImage(sx, sy);
  const overImage = ix >= 0 && ix <= board.imageWidth
                 && iy >= 0 && iy <= board.imageHeight;
  const hitId = overImage ? hitTestAreaAtPoint(ix, iy) : null;

  hoverArea(hitId);
  canvas.style.cursor = hitId ? "pointer" : "default";
});

window.addEventListener("mouseup", () => {
  if (_panActive) {
    _panActive = false;
    canvas.style.cursor = _spaceDown ? "grab" : "default";
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const dy = e.deltaY * (e.deltaMode === 1 ? 16
                       : e.deltaMode === 2 ? 100
                       : 1);
  const factor = Math.exp(-dy * 0.0015);
  zoomAt(mx, my, view.zoom * factor);
  drawView();
}, { passive: false });

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
            || t.tagName === "SELECT")) return;

  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = true;
    if (!_panActive) canvas.style.cursor = "grab";
    e.preventDefault();
    return;
  }
  if (e.key === "Home" || e.key === "0") {
    e.preventDefault();
    resetView();
    drawView();
    return;
  }
  if (e.key === "Escape") {
    e.preventDefault();
    selectArea(null);
    draw();
    return;
  }
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= 9 && board.areas.length >= n) {
    e.preventDefault();
    selectArea(board.areas[n - 1].id);
    draw();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panActive) canvas.style.cursor = "default";
  }
});

canvas.style.cursor = "default";
"""