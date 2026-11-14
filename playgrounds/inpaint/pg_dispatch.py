"""The draw orchestrator and the canvas event listeners.

`draw()` is the panel-wide redraw entry point: `drawView()`, then
`syncPanelFields()`, then `syncMaskList()`.  `drawView()` is the
canvas-only redraw, used wherever the state that changed is visible
only on the canvas.

HOVER REPAINT IS CANVAS-ONLY
============================
`board.hoveredMaskIdx` is read by exactly one consumer: `pg_view_image.
js::drawImageLayer`, to pick `globalAlpha` (0.40 vs 0.55) on selected
masks' tint.  Neither `syncPanelFields` nor `syncMaskList` reads it —
mask rows reflect `selectedMaskIndices` only, and the panel fields
never look at hover.  The hover-change branch in `mousemove` therefore
calls `drawView()`, not `draw()`.  On a dense mask stack, sweeping the
cursor across mask boundaries used to trigger dozens of pointless
panel-diff runs per second; this eliminates them.

COORDINATE SYSTEMS
==================
Two projections to image coords exist:

  · `screenToImage`         applies the rotation inverse.  Used for
                            mask hit-testing and hover — mask pixel
                            data is stored un-rotated, so the cursor
                            must be mapped back through the inverse
                            rotation.

  · `screenToImageUnrotated` skips the rotation.  Used for the inpaint
                            context rectangle, which is anchored to
                            the un-rotated fit rect and stays visually
                            fixed on screen while the image rotates
                            underneath.  Using `screenToImage` here
                            would make drags at θ≠0 jump because the
                            rect's stored coords are un-rotated but the
                            delta would be in rotated space.

The `overImage` decision is made in ROTATED image coords (via
`screenToImage`), so alt-click mask selection works on the actual
rotated extent.

PROJECTION DEFERRED DURING PAN / SPACE-DRAG
===========================================
`screenToImage` and `screenToImageUnrotated` both call
`fittedImageRect()` internally.  The pan branch and the space-drag
branch of `mousemove` only update `view.panX/Y` and repaint — they
never touch `(ix, iy)` or `(uix, uiy)`.  Computing the projections
before those early-returns means paying for two `fittedImageRect()`
calls (`pg_state.py` now memoizes, so it's a string compare each, but
that was three calls before the memoization landed and it's still
strictly wasted work).  The projections are moved below the
early-returns.

`hitContextRect` now takes `(r, ix, iy)` — pre-projected un-rotated
image coords — instead of `(r, sx, sy)`.  That removes the third
internal `fittedImageRect()` call the old signature carried.
"""

DISPATCH_JS = r"""
import {
  canvas, board, screenToImage, screenToImageUnrotated,
  fittedImageRect, view, clampPan, zoomAt, resetView,
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
} from "./pg_inpaint.js";
import { drawView } from "./pg_view.js";
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
   Module-scope interaction state
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
  const [uix, uiy] = screenToImageUnrotated(sx, sy);
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

  if (e.button === 0 && board.inpaintEnabled) {
    ensureDefaultContext();
    const hit = hitContextRect(r, uix, uiy);
    if (hit) {
      e.preventDefault();
      _contextDrag.active    = true;
      _contextDrag.handle    = (hit.kind === 'handle') ? hit.handle : 'move';
      _contextDrag.startIx   = uix;
      _contextDrag.startIy   = uiy;
      _contextDrag.startRect = {
        x: board.inpaintContext.x,
        y: board.inpaintContext.y,
        w: board.inpaintContext.w,
        h: board.inpaintContext.h,
      };
      board.inpaintContext.activeHandle = _contextDrag.handle;
      canvas.style.cursor = (_contextDrag.handle === 'move')
        ? 'grabbing'
        : handleCursor(_contextDrag.handle);
      drawView();
      return;
    }
  }
});

/* ============================================================
   Contextmenu
   ============================================================ */

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
});

/* ============================================================
   Mousemove
   ============================================================
   Pan and space-drag early-return before the projections — see the
   module docstring, PROJECTION DEFERRED DURING PAN / SPACE-DRAG.
   Hover change repaints canvas-only — see HOVER REPAINT IS
   CANVAS-ONLY. */

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
  let ix = 0, iy = 0, uix = 0, uiy = 0, overImage = false;
  if (r) {
    [ix, iy] = screenToImage(sx, sy);
    [uix, uiy] = screenToImageUnrotated(sx, sy);
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
      uix, uiy,
      _contextDrag.handle,
      e.shiftKey);
    board.inpaintContext.x = newRect.x;
    board.inpaintContext.y = newRect.y;
    board.inpaintContext.w = newRect.w;
    board.inpaintContext.h = newRect.h;
    canvas.style.cursor = (_contextDrag.handle === 'move')
      ? 'grabbing'
      : handleCursor(_contextDrag.handle);
    drawView();
    return;
  }

  if (board.inpaintEnabled && r && !_contextDrag.active) {
    const hit = hitContextRect(r, uix, uiy);
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
    board.inpaintContext.activeHandle = null;
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

/* ============================================================
   Mouseleave
   ============================================================ */

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