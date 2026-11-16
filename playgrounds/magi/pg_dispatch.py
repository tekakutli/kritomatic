"""Mouse handling and drag math for the scratch rectangle.

No character-bbox drags — those bboxes are frozen historical records.
The only draggable thing on the canvas is the scratch rect, and only
when `viewingCharacterIdx === -1`.

Pan (space / middle mouse) and zoom (wheel) work in both view modes.
"""

DISPATCH_JS = r"""
import {
  canvas, board, view,
  screenToImage, fittedImageRect, clampPan, zoomAt, resetView,
  setScratchRect, setViewingCharacter, _scheduleDraw,
} from "./pg_state.js";
import { BBOX_MIN_SIZE, HANDLE_TOL_PX } from "./pg_tuning.js";

/* ============================================================
   Screen-space geometry helpers
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

export function scratchRectScreen() {
  const r = fittedImageRect();
  if (!r) return null;
  const s = board.scratchRect;
  return {
    x0: r.x0 + s.x * r.w,
    y0: r.y0 + s.y * r.h,
    x1: r.x0 + (s.x + s.w) * r.w,
    y1: r.y0 + (s.y + s.h) * r.h,
  };
}

export function scratchRectHandles() {
  const rect = scratchRectScreen();
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

function _cursorFor(name) {
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
   Hit testing
   ============================================================ */

function _hitScratchHandle(sx, sy) {
  if (board.viewingCharacterIdx >= 0) return null;
  const hs = scratchRectHandles();
  if (!hs) return null;
  const tol = HANDLE_TOL_PX;
  for (const h of hs) {
    if (Math.abs(sx - h.x) <= tol && Math.abs(sy - h.y) <= tol) {
      return h.name;
    }
  }
  return null;
}

function _hitScratchInterior(cx, cy) {
  if (board.viewingCharacterIdx >= 0) return false;
  const s = board.scratchRect;
  const x1 = s.x * board.imageWidth;
  const y1 = s.y * board.imageHeight;
  const x2 = (s.x + s.w) * board.imageWidth;
  const y2 = (s.y + s.h) * board.imageHeight;
  return cx >= x1 && cx <= x2 && cy >= y1 && cy <= y2;
}

/* ============================================================
   Drag math (image-normalized space)
   ============================================================ */

function _applyRectDrag(startRect, handle, dxNorm, dyNorm) {
  let x1 = startRect.x, y1 = startRect.y;
  let x2 = startRect.x + startRect.w, y2 = startRect.y + startRect.h;

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
  if (board.viewingCharacterIdx >= 0) return;  // read-only crop view

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const r = fittedImageRect();
  if (!r) return;
  const [cx, cy] = screenToImage(sx, sy);

  const hname = _hitScratchHandle(sx, sy);
  if (hname) {
    board.dragActive    = true;
    board.dragHandle    = hname;
    board.dragStartRect = { ...board.scratchRect };
    board.dragStartIx   = cx;
    board.dragStartIy   = cy;
    canvas.style.cursor = _cursorFor(hname);
    e.preventDefault();
    return;
  }

  if (_hitScratchInterior(cx, cy)) {
    board.dragActive    = true;
    board.dragHandle    = 'move';
    board.dragStartRect = { ...board.scratchRect };
    board.dragStartIx   = cx;
    board.dragStartIy   = cy;
    canvas.style.cursor = 'move';
    e.preventDefault();
    return;
  }
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

  if (_spaceDown) { canvas.style.cursor = "grab"; return; }
  if (!board.imageEl) { canvas.style.cursor = "default"; return; }

  const r = fittedImageRect();
  if (!r) return;
  const [cx, cy] = screenToImage(sx, sy);

  if (board.dragActive) {
    const dxNorm = (cx - board.dragStartIx) / board.imageWidth;
    const dyNorm = (cy - board.dragStartIy) / board.imageHeight;
    const next = _applyRectDrag(
      board.dragStartRect, board.dragHandle, dxNorm, dyNorm,
    );
    setScratchRect(next);
    canvas.style.cursor = _cursorFor(board.dragHandle);
    return;
  }

  if (board.viewingCharacterIdx >= 0) {
    canvas.style.cursor = "default";
    return;
  }

  const hname = _hitScratchHandle(sx, sy);
  if (hname) {
    canvas.style.cursor = _cursorFor(hname);
    return;
  }
  if (_hitScratchInterior(cx, cy)) {
    canvas.style.cursor = 'move';
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
  if (board.dragActive) {
    board.dragActive    = false;
    board.dragHandle    = null;
    board.dragStartRect = null;
    canvas.style.cursor = "crosshair";
  }
});

window.addEventListener("keyup", (e) => {
  if (e.key === " " || e.key === "Spacebar") {
    _spaceDown = false;
    if (!_panActive) canvas.style.cursor = "crosshair";
  }
});

canvas.addEventListener("mouseleave", () => {
  // no persistent hover state to clear in v2
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
    if (board.viewingCharacterIdx >= 0) {
      setViewingCharacter(-1);
    }
    return;
  }
});
"""