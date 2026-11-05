"""The draw orchestrator and the canvas event listeners.

`draw()` is the panel-wide redraw entry point: it calls `drawView()`,
then syncs the panel fields, then syncs the mask list.  The canvas
event listeners own the input lifecycle — mousedown, mousemove,
mouseup, wheel, contextmenu, keydown, keyup.

`drawView()` is the canvas-only redraw.  It is used wherever the
state that changed is visible only on the canvas — pan, zoom,
inpaint-context drag, hover highlight — and none of the panel
fields or the mask list need to be re-synced.  Hover in particular
runs on every mouse-move that crosses a mask boundary; using
`draw()` there would rebuild the mask DOM and re-sync every panel
field on every crossing, which was the largest per-frame cost after
`syncMaskList`'s own rebuild.

Input modes:
  - pan (space+drag, middle mouse)
  - zoom (wheel at cursor)
  - alt-click mask selection
  - inpaint context rectangle drag (move / resize handles), only
    active when `board.inpaintEnabled` is true

The inpaint drag STATE (`_contextDrag = { active, handle, startIx, ... }`)
stays here because it is per-interaction, not per-document.  The pure
math lives in `pg_inpaint.js` so a second inpaint mode could carry
its own drag state without touching this module.

Screen-coordinate mapping is split into two flavours:

  - `screenToImage`         applies the rotation inverse.  Used when
                            the caller needs the actual image pixel
                            under the cursor (mask hit-test, hover).
  - `screenToImageUnrotated` skips the rotation.  Used for the inpaint
                            context rectangle, which is anchored to
                            the un-rotated fit rect and stays visually
                            fixed while the image rotates under it.

`overImage` is decided in rotated image coords (via `screenToImage`),
so alt-click mask selection works on the actual rotated extent."""

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
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

export function draw() {
  drawView();
  syncPanelFields();
  syncMaskList();
}

/* ============================================================
   Module-scope interaction state
   ============================================================ */

// Pan state. Middle mouse button, or space+left drag.
let _panActive = false;
let _panStartScreenX = 0;
let _panStartScreenY = 0;
let _panStartPanX = 0;
let _panStartPanY = 0;
let _spaceDown = false;

// Inpaint context drag state. `handle` is one of 'nw','ne','sw','se',
// 'n','s','e','w', or 'move'. startIx/startIy are in UN-ROTATED image
// coords (the rectangle is anchored to the un-rotated fit rect);
// startRect is the rect captured at mousedown so each mousemove can
// recompute from the original anchor rather than accumulating error.
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
  // Blur any focused input so keyboard shortcuts work again.
  const active = document.activeElement;
  if (active && active !== canvas
      && (active.tagName === "INPUT"
          || active.tagName === "TEXTAREA"
          || active.tagName === "SELECT")) {
    active.blur();
  }

  // Pan: middle mouse button, or space + left button.
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

  // Rotated inverse — the actual pixel under the cursor.
  const [ix, iy] = screenToImage(sx, sy);
  // Un-rotated — what the context rectangle's coords live in.
  const [uix, uiy] = screenToImageUnrotated(sx, sy);

  const overImage = ix >= 0 && ix <= board.imageWidth
                 && iy >= 0 && iy <= board.imageHeight;

  // Alt+click selects a mask.
  if (e.altKey && e.button === 0) {
    if (overImage) {
      const idx = hitTestMaskAtPoint(ix, iy);
      if (idx >= 0) {
        if (e.shiftKey)                  addMaskToSelection(idx);
        else if (e.ctrlKey || e.metaKey) toggleMaskSelection(idx);
        else                             replaceMaskSelection(idx);
        pgkitNotifyAll();
        draw();
      }
    }
    return;
  }

  // Inpaint context rectangle:
  //   - a click on one of the 8 handles starts a resize
  //   - a click on the border band OR anywhere inside the rect
  //     starts a move
  if (e.button === 0 && board.inpaintEnabled) {
    ensureDefaultContext();
    const hit = hitContextRect(r, sx, sy);
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
   ============================================================ */

canvas.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
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

  // Pan takes priority over everything else.
  if (_panActive) {
    view.panX = _panStartPanX + (e.clientX - _panStartScreenX);
    view.panY = _panStartPanY + (e.clientY - _panStartScreenY);
    clampPan();
    canvas.style.cursor = "grabbing";
    drawView();
    return;
  }

  // Space held: show grab cursor, no other interaction.
  if (_spaceDown) {
    canvas.style.cursor = "grab";
    drawView();
    return;
  }

  // Inpaint context drag in progress — uses un-rotated image coords
  // because the rectangle itself isn't rotated.
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

  // Inpaint context hover cursor.
  if (board.inpaintEnabled && r && !_contextDrag.active) {
    const hit = hitContextRect(r, sx, sy);
    if (hit) {
      canvas.style.cursor = (hit.kind === 'handle')
        ? handleCursor(hit.handle)
        : handleCursor('move');
      if (board.hoveredMaskIdx !== -1) board.hoveredMaskIdx = -1;
      drawView();
      return;
    }
  }

  // Hover state for mask overlay highlight.
  //
  // Hover affects only the canvas — drawImageLayer reads
  // `board.hoveredMaskIdx` to vary globalAlpha.  It does NOT affect
  // any panel field or the mask list DOM, so a canvas-only redraw
  // is enough.  Using draw() here (which additionally runs
  // syncPanelFields and syncMaskList) is what made hover across a
  // mask boundary the largest per-frame cost after syncMaskList's
  // own rebuild.
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
