"""The draw orchestrator and the canvas event listeners.

`draw()` is the panel-wide redraw entry point: `drawView()`,
then `syncPanelFields()`, then `syncMaskList()`.

No rotation transforms anywhere.  `screenToImage` maps screen
pixels to boundary-cropped image coords (which is what
`board.imageWidth/Height` are).  `board.context` and mask hit
testing both live in that same frame."""

DISPATCH_JS = r"""
import {
  canvas, board, screenToImage, fittedImageRect,
  view, clampPan, zoomAt, resetView,
} from "./pg_state.js";
import {
  selectedMask,
  toggleMaskSelection, addMaskToSelection, replaceMaskSelection,
  clearMaskSelection, selectAllMasks,
} from "./pg_masks.js";
import { hitTestMaskAtPoint } from "./pg_pixels.js";
import {
  hitContextRect, handleCursor, applyContextDrag,
  ensureDefaultContext,
} from "./pg_context.js";
import { drawView, invalidateDrawSig } from "./pg_view.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { scheduleSync } from "./pg_sync.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

export function draw() {
  drawView();
  syncPanelFields();
  syncMaskList();
}

/* ============================================================
   Interaction state
   ============================================================ */

let _panActive = false;
let _panStartScreenX = 0;
let _panStartScreenY = 0;
let _panStartPanX = 0;
let _panStartPanY = 0;
let _spaceDown = false;

let _contextDrag = {
  active: false,
  handle: null,
  startIx: 0,
  startIy: 0,
  startRect: null,
};

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

  if ((e.button === 1 || (e.button === 0 && _spaceDown))
      && !_contextDrag.active) {
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

  const [ix, iy] = screenToImage(sx, sy);
  const overImage = ix >= 0 && ix <= board.imageWidth
                 && iy >= 0 && iy <= board.imageHeight;

  if (e.altKey && e.button === 0) {
    if (overImage) {
      const idx = hitTestMaskAtPoint(ix, iy);
      if (idx >= 0) {
        if (e.shiftKey)                  addMaskToSelection(idx);
        else if (e.ctrlKey || e.metaKey) toggleMaskSelection(idx);
        else                             replaceMaskSelection(idx);
        pgkitNotifyAll();
        scheduleSync();
        draw();
      }
    }
    return;
  }

  if (e.button === 0 && board.contextEnabled) {
    ensureDefaultContext();
    const hit = hitContextRect(r, ix, iy);
    if (hit) {
      e.preventDefault();
      _contextDrag.active    = true;
      _contextDrag.handle    = (hit.kind === 'handle') ? hit.handle : 'move';
      _contextDrag.startIx   = ix;
      _contextDrag.startIy   = iy;
      _contextDrag.startRect = {
        x: board.context.x,
        y: board.context.y,
        w: board.context.w,
        h: board.context.h,
      };
      board.context.activeHandle = _contextDrag.handle;
      canvas.style.cursor = (_contextDrag.handle === 'move')
        ? 'grabbing'
        : handleCursor(_contextDrag.handle);
      drawView();
      return;
    }
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
});

/* ============================================================
   Mousemove
   ============================================================ */

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

  if (_panActive) {
    view.panX = _panStartPanX + (e.clientX - _panStartScreenX);
    view.panY = _panStartPanY + (e.clientY - _panStartScreenY);
    clampPan();
    canvas.style.cursor = "grabbing";
    drawView();
    return;
  }

  if (_spaceDown) {
    canvas.style.cursor = "grab";
    drawView();
    return;
  }

  const r = fittedImageRect();
  let ix = 0, iy = 0, overImage = false;
  if (r) {
    [ix, iy] = screenToImage(sx, sy);
    board.mouse.x = ix;
    board.mouse.y = iy;
    overImage = ix >= 0 && ix <= board.imageWidth
             && iy >= 0 && iy <= board.imageHeight;
    board.mouse.inside = overImage;
  }

  if (_contextDrag.active && r) {
    const newRect = applyContextDrag(
      _contextDrag.startRect,
      _contextDrag.startIx, _contextDrag.startIy,
      ix, iy,
      _contextDrag.handle,
      e.shiftKey);
    board.context.x = newRect.x;
    board.context.y = newRect.y;
    board.context.w = newRect.w;
    board.context.h = newRect.h;
    canvas.style.cursor = (_contextDrag.handle === 'move')
      ? 'grabbing'
      : handleCursor(_contextDrag.handle);
    drawView();
    return;
  }

  if (board.contextEnabled && r && !_contextDrag.active) {
    const hit = hitContextRect(r, ix, iy);
    if (hit) {
      canvas.style.cursor = (hit.kind === 'handle')
        ? handleCursor(hit.handle)
        : handleCursor('move');
      if (board.hoveredMaskIdx !== -1) board.hoveredMaskIdx = -1;
      drawView();
      return;
    }
  }

  let hoveredMask = -1;
  if (overImage) {
    hoveredMask = hitTestMaskAtPoint(ix, iy);
  }

  if (hoveredMask !== board.hoveredMaskIdx) {
    board.hoveredMaskIdx = hoveredMask;
    drawView();
  }

  if (hoveredMask >= 0) canvas.style.cursor = "pointer";
  else                  canvas.style.cursor = "crosshair";
});

/* ============================================================
   Wheel — zoom at cursor
   ============================================================ */

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;

  const dy = e.deltaY * (e.deltaMode === 1 ? 16
                       : e.deltaMode === 2 ? 100
                       : 1);
  const factor = Math.exp(-dy * 0.0015);
  zoomAt(mx, my, view.zoom * factor);

  const [ix, iy] = screenToImage(mx, my);
  board.mouse.x = ix;
  board.mouse.y = iy;
  board.mouse.inside = ix >= 0 && ix <= board.imageWidth
                    && iy >= 0 && iy <= board.imageHeight;
  drawView();
}, { passive: false });

/* ============================================================
   Mouseup / Keyup
   ============================================================ */

window.addEventListener("mouseup", () => {
  if (_panActive) {
    _panActive = false;
    canvas.style.cursor = _spaceDown ? "grab" : "crosshair";
  }
  if (_contextDrag.active) {
    _contextDrag.active = false;
    _contextDrag.handle = null;
    _contextDrag.startRect = null;
    board.context.activeHandle = null;
    canvas.style.cursor = "crosshair";
    pgkitNotifyAll();
    scheduleSync();
    drawView();
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
  if (board.hoveredMaskIdx !== -1) {
    board.hoveredMaskIdx = -1;
  }
  draw();
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

  if (e.key === " " || e.key === "Spacebar") {
    e.preventDefault();
    if (!_spaceDown) {
      _spaceDown = true;
      if (!_panActive) canvas.style.cursor = "grab";
    }
    return;
  }

  if (e.key === "Home") {
    e.preventDefault();
    resetView();
    invalidateDrawSig();
    drawView();
    return;
  }

  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") {
    e.preventDefault();
    selectAllMasks();
    pgkitNotifyAll();
    scheduleSync();
    draw();
    return;
  }

  if (e.ctrlKey || e.metaKey) return;

  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= 9 && board.masks.length >= n) {
    e.preventDefault();
    if (e.shiftKey) addMaskToSelection(n - 1);
    else            replaceMaskSelection(n - 1);
    pgkitNotifyAll();
    scheduleSync();
    draw();
    return;
  }

  if (e.key === "Escape") {
    e.preventDefault();
    clearMaskSelection();
    pgkitNotifyAll();
    scheduleSync();
    draw();
  }
});
"""