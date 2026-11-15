"""Mouse handling and drag math.

Bbox drags operate in viewer-normalized coords. Viewer drags operate
in canvas-normalized coords. Hit priority is:
  1. Viewer corner handle      → viewer resize
  2. Selected bbox corner      → bbox resize
  3. Bbox interior             → bbox drag
  4. Viewer interior           → viewer drag
  5. Nothing                   → deselect

Pan (space / middle mouse) and zoom (wheel) behave as before.
"""

DISPATCH_JS = r"""
import {
  canvas, board, view,
  screenToImage, fittedImageRect, imageToScreen,
  clampPan, zoomAt, resetView,
  setBbox, selectBbox,
  canvasPxToViewerNorm,
} from "./pg_state.js";
import {
  hitTestViewerHandle, hitTestViewerInterior,
  viewerCursorFor, applyViewerMove, applyViewerResize,
} from "./pg_viewer.js";
import { BBOX_MIN_SIZE, HANDLE_TOL_PX } from "./pg_tuning.js";

/* ============================================================
   Public helpers
   ============================================================ */

export function bboxScreenRect(bbox) {
  const r = fittedImageRect();
  if (!r) return null;
  const v = board.viewer;
  return {
    x0: r.x0 + (v.x + bbox.x * v.w)          * r.w,
    y0: r.y0 + (v.y + bbox.y * v.h)          * r.h,
    x1: r.x0 + (v.x + (bbox.x + bbox.w) * v.w) * r.w,
    y1: r.y0 + (v.y + (bbox.y + bbox.h) * v.h) * r.h,
  };
}

export function bboxHandles(bbox) {
  const rect = bboxScreenRect(bbox);
  if (!rect) return null;
  const { x0, y0, x1, y1 } = rect;
  return [
    { name: 'nw', x: x0, y: y0 },
    { name: 'ne', x: x1, y: y0 },
    { name: 'sw', x: x0, y: y1 },
    { name: 'se', x: x1, y: y1 },
    { name: 'n',  x: (x0 + x1) / 2, y: y0 },
    { name: 's',  x: (x0 + x1) / 2, y: y1 },
    { name: 'w',  x: x0, y: (y0 + y1) / 2 },
    { name: 'e',  x: x1, y: (y0 + y1) / 2 },
  ];
}

function _bboxCursorFor(name) {
  switch (name) {
    case 'nw': case 'se': return 'nwse-resize';
    case 'ne': case 'sw': return 'nesw-resize';
    case 'n':  case 's':  return 'ns-resize';
    case 'e':  case 'w':  return 'ew-resize';
    case 'move':          return 'move';
    default:              return 'default';
  }
}

/* ============================================================
   Hit-testing (bbox side)
   ============================================================ */

function _hitBboxHandleOfSelected(sx, sy) {
  if (board.selectedKind === null) return null;
  const list = board.selectedKind === 'text'
    ? board.textBboxes : board.characterBboxes;
  const bbox = list[board.selectedIdx];
  if (!bbox) return null;
  const hs = bboxHandles(bbox);
  if (!hs) return null;
  const tol = HANDLE_TOL_PX;
  for (const h of hs) {
    if (Math.abs(sx - h.x) <= tol && Math.abs(sy - h.y) <= tol) {
      return h.name;
    }
  }
  return null;
}

function _hitAnyBbox(vnx, vny) {
  for (let i = board.characterBboxes.length - 1; i >= 0; i--) {
    const b = board.characterBboxes[i];
    if (vnx >= b.x && vnx <= b.x + b.w && vny >= b.y && vny <= b.y + b.h) {
      return { kind: 'character', idx: i };
    }
  }
  for (let i = board.textBboxes.length - 1; i >= 0; i--) {
    const b = board.textBboxes[i];
    if (vnx >= b.x && vnx <= b.x + b.w && vny >= b.y && vny <= b.y + b.h) {
      return { kind: 'text', idx: i };
    }
  }
  return null;
}

/* ============================================================
   Bbox drag math (operates in viewer-normalized space)
   ============================================================ */

function _applyBboxDrag(bbox, handle, dxNorm, dyNorm) {
  let x1 = bbox.x, y1 = bbox.y;
  let x2 = bbox.x + bbox.w, y2 = bbox.y + bbox.h;

  if (handle === 'move') {
    const w = x2 - x1, h = y2 - y1;
    x1 = Math.max(0, Math.min(1 - w, x1 + dxNorm));
    y1 = Math.max(0, Math.min(1 - h, y1 + dyNorm));
    return { x: x1, y: y1, w, h };
  }

  if (handle.includes('w')) x1 += dxNorm;
  if (handle.includes('e')) x2 += dxNorm;
  if (handle.includes('n')) y1 += dyNorm;
  if (handle.includes('s')) y2 += dyNorm;

  x1 = Math.max(0, Math.min(1, x1));
  x2 = Math.max(0, Math.min(1, x2));
  y1 = Math.max(0, Math.min(1, y1));
  y2 = Math.max(0, Math.min(1, y2));

  if (x1 > x2) [x1, x2] = [x2, x1];
  if (y1 > y2) [y1, y2] = [y2, y1];

  if (x2 - x1 < BBOX_MIN_SIZE) {
    if (handle.includes('w')) x1 = Math.max(0, x2 - BBOX_MIN_SIZE);
    else                     x2 = Math.min(1, x1 + BBOX_MIN_SIZE);
  }
  if (y2 - y1 < BBOX_MIN_SIZE) {
    if (handle.includes('n')) y1 = Math.max(0, y2 - BBOX_MIN_SIZE);
    else                     y2 = Math.min(1, y1 + BBOX_MIN_SIZE);
  }

  return { x: x1, y: y1, w: x2 - x1, h: y2 - y1 };
}

/* ============================================================
   Pan state
   ============================================================ */

let _panActive = false;
let _panStartSx = 0, _panStartSy = 0;
let _panStartX = 0, _panStartY = 0;
let _spaceDown = false;

/* ============================================================
   Mousedown
   ============================================================ */

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
  if (!board.imageEl) return;

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

  // 2) Selected bbox handle
  const bboxHandle = _hitBboxHandleOfSelected(sx, sy);
  if (bboxHandle) {
    const list = board.selectedKind === 'text'
      ? board.textBboxes : board.characterBboxes;
    const bbox = list[board.selectedIdx];
    board.dragActive = true;
    board.dragHandle = bboxHandle;
    board.dragKind = board.selectedKind;
    board.dragIdx = board.selectedIdx;
    board.dragStartBbox = { ...bbox };
    board.dragStartIx = cx;
    board.dragStartIy = cy;
    canvas.style.cursor = _bboxCursorFor(bboxHandle);
    e.preventDefault();
    return;
  }

  // 3) Any bbox interior
  const [vnx, vny] = canvasPxToViewerNorm(cx, cy);
  const hit = _hitAnyBbox(vnx, vny);
  if (hit) {
    selectBbox(hit.kind, hit.idx);
    const list = hit.kind === 'text'
      ? board.textBboxes : board.characterBboxes;
    const bbox = list[hit.idx];
    board.dragActive = true;
    board.dragHandle = 'move';
    board.dragKind = hit.kind;
    board.dragIdx = hit.idx;
    board.dragStartBbox = { ...bbox };
    board.dragStartIx = cx;
    board.dragStartIy = cy;
    canvas.style.cursor = 'move';
    e.preventDefault();
    return;
  }

  // 4) Viewer interior
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

  // 5) Nothing: deselect
  selectBbox(null, -1);
});

canvas.addEventListener("contextmenu", (e) => e.preventDefault());

/* ============================================================
   Mousemove
   ============================================================ */

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

  if (!board.imageEl) { canvas.style.cursor = "default"; return; }

  const r = fittedImageRect();
  if (!r) return;
  const [cx, cy] = screenToImage(sx, sy);

  if (board.viewerDragActive) {
    if (board.viewerDragHandle === 'move') {
      const dx = (cx - board.viewerDragStartIx) / board.imageWidth;
      const dy = (cy - board.viewerDragStartIy) / board.imageHeight;
      applyViewerMove(dx, dy);
    } else {
      applyViewerResize(board.viewerDragHandle, cx, cy);
    }
    _scheduleDraw();
    return;
  }

  if (board.dragActive) {
    const dxCanvasNorm = (cx - board.dragStartIx) / board.imageWidth;
    const dyCanvasNorm = (cy - board.dragStartIy) / board.imageHeight;
    const dxViewerNorm = dxCanvasNorm / board.viewer.w;
    const dyViewerNorm = dyCanvasNorm / board.viewer.h;
    const next = _applyBboxDrag(
      board.dragStartBbox, board.dragHandle, dxViewerNorm, dyViewerNorm,
    );
    setBbox(board.dragKind, board.dragIdx, next);
    canvas.style.cursor = _bboxCursorFor(board.dragHandle);
    return;
  }

  // Hover state (priority order matches mousedown)
  const viewerHandle = hitTestViewerHandle(sx, sy, r);
  if (viewerHandle) {
    canvas.style.cursor = viewerCursorFor(viewerHandle);
    return;
  }
  const bboxHandle = _hitBboxHandleOfSelected(sx, sy);
  if (bboxHandle) {
    canvas.style.cursor = _bboxCursorFor(bboxHandle);
    return;
  }
  const [vnx, vny] = canvasPxToViewerNorm(cx, cy);
  const hit = _hitAnyBbox(vnx, vny);
  if (hit) {
    if (board.hoveredKind !== hit.kind || board.hoveredIdx !== hit.idx) {
      board.hoveredKind = hit.kind;
      board.hoveredIdx = hit.idx;
      _scheduleDraw();
    }
    canvas.style.cursor = "move";
    return;
  }
  if (board.hoveredKind !== null || board.hoveredIdx !== -1) {
    board.hoveredKind = null;
    board.hoveredIdx = -1;
    _scheduleDraw();
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

/* ============================================================
   Mouseup / Keyup
   ============================================================ */

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
    if (window._layoutOnDragEnd) window._layoutOnDragEnd();
  }
  if (board.dragActive) {
    board.dragActive = false;
    board.dragHandle = null;
    board.dragKind = null;
    board.dragIdx = -1;
    board.dragStartBbox = null;
    canvas.style.cursor = "crosshair";
    if (window._layoutOnDragEnd) window._layoutOnDragEnd();
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
  if (board.hoveredKind !== null) {
    board.hoveredKind = null;
    board.hoveredIdx = -1;
    _scheduleDraw();
  }
});

/* ============================================================
   Keyboard
   ============================================================ */

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
  if (e.key === "Escape") {
    e.preventDefault();
    selectBbox(null, -1);
    return;
  }
});

import { _scheduleDraw } from "./pg_state.js";
"""