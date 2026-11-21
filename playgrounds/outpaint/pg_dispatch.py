"""Mouse handling: viewer drag/resize + pan/zoom.

Hit priority: viewer corner handle → viewer interior → nothing.
Pan (space / middle mouse) and zoom (wheel) unchanged.
"""

DISPATCH_JS = r"""
import {
  canvas, board, view,
  screenToImage, fittedImageRect,
  clampPan, zoomAt, resetView,
} from "./pg_state.js";
import {
  hitTestViewerHandle, hitTestViewerInterior,
  viewerCursorFor, applyViewerMove, applyViewerResize,
} from "./pg_viewer.js";

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
  if (!board.areaWidth || !board.areaHeight) return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  const [cx, cy] = screenToImage(sx, sy);

  // 1) Viewer corner handle
  const viewerHandle = hitTestViewerHandle(sx, sy, r);
  if (viewerHandle) {
    board.viewerDragActive = true;
    board.viewerDragHandle = viewerHandle;
    board.viewerDragStart  = { ...board.viewer };
    board.viewerDragStartIx = cx;
    board.viewerDragStartIy = cy;
    canvas.style.cursor = viewerCursorFor(viewerHandle);
    e.preventDefault();
    return;
  }

  // 2) Viewer interior
  if (hitTestViewerInterior(cx, cy)) {
    board.viewerDragActive = true;
    board.viewerDragHandle = 'move';
    board.viewerDragStart  = { ...board.viewer };
    board.viewerDragStartIx = cx;
    board.viewerDragStartIy = cy;
    canvas.style.cursor = 'move';
    e.preventDefault();
    return;
  }
});

canvas.addEventListener("contextmenu", (e) => e.preventDefault());

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

  if (_panActive) {
    view.panX = _panStartX + (e.clientX - _panStartSx);
    view.panY = _panStartY + (e.clientY - _panStartSy);
    clampPan();
    canvas.style.cursor = "grabbing";
    _scheduleDraw();
    return;
  }

  if (_spaceDown) {
    canvas.style.cursor = "grab";
    return;
  }

  if (!board.areaWidth || !board.areaHeight) {
    canvas.style.cursor = "default";
    return;
  }

  const r = fittedImageRect();
  if (!r) return;
  const [cx, cy] = screenToImage(sx, sy);

  if (board.viewerDragActive) {
    if (board.viewerDragHandle === 'move') {
      const dx = cx - board.viewerDragStartIx;  // area pixels
      const dy = cy - board.viewerDragStartIy;
      applyViewerMove(dx, dy);
    } else {
      applyViewerResize(board.viewerDragHandle, cx, cy);
    }
    _scheduleDraw();
    return;
  }

  // Hover
  const viewerHandle = hitTestViewerHandle(sx, sy, r);
  if (viewerHandle) {
    canvas.style.cursor = viewerCursorFor(viewerHandle);
    return;
  }
  if (hitTestViewerInterior(cx, cy)) {
    canvas.style.cursor = "move";
    return;
  }
  canvas.style.cursor = "crosshair";
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
  _scheduleDraw();
}, { passive: false });

window.addEventListener("mouseup", () => {
  if (_panActive) {
    _panActive = false;
    canvas.style.cursor = _spaceDown ? "grab" : "crosshair";
  }
  if (board.viewerDragActive) {
    board.viewerDragActive = false;
    board.viewerDragHandle = null;
    board.viewerDragStart  = null;
    canvas.style.cursor = "crosshair";
    if (window._outpaintOnDragEnd) window._outpaintOnDragEnd();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panActive) canvas.style.cursor = "crosshair";
  }
});

canvas.addEventListener("mouseleave", () => {
  board.mouse.inside = false;
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA"
            || t.tagName === "SELECT")) return;

  if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (!_spaceDown) {
      _spaceDown = true;
      if (!_panActive) canvas.style.cursor = "grab";
    }
    return;
  }
  if (e.key === "Home" || e.key === "0") {
    e.preventDefault();
    resetView();
    _scheduleDraw();
    return;
  }
});

import { _scheduleDraw } from "./pg_state.js";
"""