"""Mouse handling and drag math.

Bbox math is in normalized [0,1] coordinates on the wire and in
`board`, but drag deltas are computed in image pixels (from
`screenToImage`) and converted to normalized units.  That keeps the
math exact regardless of zoom or viewport size.
"""

DISPATCH_JS = r"""
import {
  canvas, board, view,
  screenToImage, fittedImageRect, imageToScreen,
  clampPan, zoomAt, resetView,
  setBbox, selectBbox,
} from "./pg_state.js";
import { BBOX_MIN_SIZE, HANDLE_TOL_PX } from "./pg_tuning.js";

/* ============================================================
   Public helpers (used by pg_view_image and pg_panel)
   ============================================================ */

export function bboxScreenRect(bbox) {
  const r = fittedImageRect();
  if (!r) return null;
  return {
    x0: r.x0 + bbox.x * r.w,
    y0: r.y0 + bbox.y * r.h,
    x1: r.x0 + (bbox.x + bbox.w) * r.w,
    y1: r.y0 + (bbox.y + bbox.h) * r.h,
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

function _handleCursor(name) {
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
   Hit-testing (screen px, with slop)
   ============================================================ */

function _hitHandleOfSelected(sx, sy) {
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

function _hitAnyBbox(sx, sy) {
  // Iterate topmost first.
  for (let i = board.characterBboxes.length - 1; i >= 0; i--) {
    const r = bboxScreenRect(board.characterBboxes[i]);
    if (r && sx >= r.x0 && sx <= r.x1 && sy >= r.y0 && sy <= r.y1) {
      return { kind: 'character', idx: i };
    }
  }
  for (let i = board.textBboxes.length - 1; i >= 0; i--) {
    const r = bboxScreenRect(board.textBboxes[i]);
    if (r && sx >= r.x0 && sx <= r.x1 && sy >= r.y0 && sy <= r.y1) {
      return { kind: 'text', idx: i };
    }
  }
  return null;
}

/* ============================================================
   Drag math
   ============================================================ */

function _applyDrag(bbox, handle, dxNorm, dyNorm) {
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

  const minW = BBOX_MIN_SIZE, minH = BBOX_MIN_SIZE;
  if (x2 - x1 < minW) {
    if (handle.includes('w')) x1 = Math.max(0, x2 - minW);
    else                     x2 = Math.min(1, x1 + minW);
  }
  if (y2 - y1 < minH) {
    if (handle.includes('n')) y1 = Math.max(0, y2 - minH);
    else                     y2 = Math.min(1, y1 + minH);
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

  // Handle of selected bbox?
  const handle = _hitHandleOfSelected(sx, sy);
  if (handle) {
    const list = board.selectedKind === 'text'
      ? board.textBboxes : board.characterBboxes;
    const bbox = list[board.selectedIdx];
    const [ix, iy] = screenToImage(sx, sy);
    board.dragActive = true;
    board.dragHandle = handle;
    board.dragKind = board.selectedKind;
    board.dragIdx = board.selectedIdx;
    board.dragStartBbox = { ...bbox };
    board.dragStartIx = ix;
    board.dragStartIy = iy;
    canvas.style.cursor = _handleCursor(handle);
    e.preventDefault();
    return;
  }

  // Any bbox interior?
  const hit = _hitAnyBbox(sx, sy);
  if (hit) {
    selectBbox(hit.kind, hit.idx);
    const list = hit.kind === 'text'
      ? board.textBboxes : board.characterBboxes;
    const bbox = list[hit.idx];
    const [ix, iy] = screenToImage(sx, sy);
    board.dragActive = true;
    board.dragHandle = 'move';
    board.dragKind = hit.kind;
    board.dragIdx = hit.idx;
    board.dragStartBbox = { ...bbox };
    board.dragStartIx = ix;
    board.dragStartIy = iy;
    canvas.style.cursor = 'move';
    e.preventDefault();
    return;
  }

  // Empty click: deselect.
  selectBbox(null, -1);
});

/* ============================================================
   Contextmenu
   ============================================================ */

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

  if (board.dragActive) {
    if (!board.imageEl) return;
    const [ix, iy] = screenToImage(sx, sy);
    const dx = (ix - board.dragStartIx) / board.imageWidth;
    const dy = (iy - board.dragStartIy) / board.imageHeight;
    const next = _applyDrag(board.dragStartBbox, board.dragHandle, dx, dy);
    setBbox(board.dragKind, board.dragIdx, next);
    canvas.style.cursor = _handleCursor(board.dragHandle);
    return;
  }

  // Hover state
  if (!board.imageEl) {
    canvas.style.cursor = "default";
    return;
  }
  const handle = _hitHandleOfSelected(sx, sy);
  if (handle) {
    canvas.style.cursor = _handleCursor(handle);
    return;
  }
  const hit = _hitAnyBbox(sx, sy);
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
  canvas.style.cursor = "crosshair";
});

/* ============================================================
   Wheel
   ============================================================ */

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
  if (board.dragActive) {
    board.dragActive = false;
    board.dragHandle = null;
    board.dragKind = null;
    board.dragIdx = -1;
    board.dragStartBbox = null;
    canvas.style.cursor = "crosshair";
    // Sync happens via the panel hook set up in pg_panel.js
    if (window._layoutOnDragEnd) window._layoutOnDragEnd();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panActive) canvas.style.cursor = "crosshair";
  }
});

/* ============================================================
   Mouseleave
   ============================================================ */

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

// Need _scheduleDraw imported from pg_state
import { _scheduleDraw } from "./pg_state.js";
"""