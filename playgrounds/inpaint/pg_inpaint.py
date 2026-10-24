"""Inpainting context rectangle helpers.

`inpaint` is the primary feature of this playground and expected to
grow.  This file contains:

    - the tolerance constants
    - lazy-default initialisation of the rect
    - image→screen and screen-handle projections
    - the click classifier (handle vs border vs interior)
    - the pure drag math (resize with optional Shift symmetry + move)
    - `buildContextMaskB64` — full-canvas mask of the context region
    - `resizeMaskB64ToCanvas` — canvas-sized output from an arbitrary
      input PNG

The drag STATE (`_contextDrag = { active, handle, startIx, ... }`)
stays in `pg_dispatch.js` because it is per-interaction, not
per-document."""

INPAINT_JS = r"""
import { board } from "./pg_state.js";

/* ============================================================
   Tolerances
   ============================================================
   Handles win over the border band.  The border band is *additive*:
   interior points still count as "move", and the band extends a few
   pixels outside the visible dashed outline so the outline itself
   is easy to grab without pixel precision. */

export const CONTEXT_MIN_SIZE     = 16;   // image px
export const CONTEXT_HANDLE_HALF  = 10;   // screen px
export const CONTEXT_BORDER_HALF  = 6;    // screen px

/* ============================================================
   Lazy default
   ============================================================ */

/* Default to a centered 50%-of-image rect the first time the section
   is enabled against a loaded image.  Idempotent: no-op once the rect
   has non-zero dimensions. */
export function ensureDefaultContext() {
  const c = board.inpaintContext;
  if (c.w > 0 && c.h > 0) return;
  if (!board.imageWidth || !board.imageHeight) return;
  c.x = Math.round(board.imageWidth  * 0.25);
  c.y = Math.round(board.imageHeight * 0.25);
  c.w = Math.round(board.imageWidth  * 0.50);
  c.h = Math.round(board.imageHeight * 0.50);
}

/* ============================================================
   Projections
   ============================================================ */

export function contextRectScreen(r) {
  const c = board.inpaintContext;
  if (!r || c.w === 0 || c.h === 0) return null;
  return {
    x0: r.x0 + c.x * r.s,
    y0: r.y0 + c.y * r.s,
    x1: r.x0 + (c.x + c.w) * r.s,
    y1: r.y0 + (c.y + c.h) * r.s,
  };
}

export function contextHandles(r) {
  const rect = contextRectScreen(r);
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

/* ============================================================
   Cursor
   ============================================================ */

export function handleCursor(name) {
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
   Click classification
   ============================================================ */

export function hitContextRect(r, sx, sy) {
  if (!r) return null;
  const crect = contextRectScreen(r);
  if (!crect) return null;

  const handles = contextHandles(r);
  if (handles) {
    for (const hd of handles) {
      if (Math.abs(sx - hd.x) <= CONTEXT_HANDLE_HALF &&
          Math.abs(sy - hd.y) <= CONTEXT_HANDLE_HALF) {
        return { kind: 'handle', handle: hd.name };
      }
    }
  }

  const { x0, y0, x1, y1 } = crect;
  if (sx >= x0 - CONTEXT_BORDER_HALF && sx <= x1 + CONTEXT_BORDER_HALF &&
      sy >= y0 - CONTEXT_BORDER_HALF && sy <= y1 + CONTEXT_BORDER_HALF) {
    return { kind: 'move' };
  }
  return null;
}

/* ============================================================
   Pure drag math
   ============================================================ */

export function applyContextDrag(startRect, startIx, startIy,
                                  currentIx, currentIy,
                                  handle, shiftKey) {
  const dx = currentIx - startIx;
  const dy = currentIy - startIy;
  const { x, y, w, h } = startRect;

  let x0 = x, x1 = x + w, y0 = y, y1 = y + h;

  if (handle === 'move') {
    x0 += dx; x1 += dx;
    y0 += dy; y1 += dy;
  } else {
    const movesW = handle.includes('w');
    const movesE = handle.includes('e');
    const movesN = handle.includes('n');
    const movesS = handle.includes('s');

    if (shiftKey) {
      if (movesW) { x0 += dx; x1 -= dx; }
      if (movesE) { x0 -= dx; x1 += dx; }
      if (movesN) { y0 += dy; y1 -= dy; }
      if (movesS) { y0 -= dy; y1 += dy; }
    } else {
      if (movesW) x0 += dx;
      if (movesE) x1 += dx;
      if (movesN) y0 += dy;
      if (movesS) y1 += dy;
    }
  }

  if (x1 - x0 < CONTEXT_MIN_SIZE) {
    const cx = (x0 + x1) / 2;
    x0 = cx - CONTEXT_MIN_SIZE / 2;
    x1 = cx + CONTEXT_MIN_SIZE / 2;
  }
  if (y1 - y0 < CONTEXT_MIN_SIZE) {
    const cy = (y0 + y1) / 2;
    y0 = cy - CONTEXT_MIN_SIZE / 2;
    y1 = cy + CONTEXT_MIN_SIZE / 2;
  }

  x0 = Math.max(0, Math.min(board.imageWidth,  x0));
  y0 = Math.max(0, Math.min(board.imageHeight, y0));
  x1 = Math.max(0, Math.min(board.imageWidth,  x1));
  y1 = Math.max(0, Math.min(board.imageHeight, y1));

  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/* ============================================================
   Mask building
   ============================================================
   `buildContextMaskB64` produces the `thecontext` blob fed to the
   inpaint workflow: black canvas, white rectangle at the current
   context rect position.  The daemon's InpaintCropImproved reads the
   mask to decide the crop region.

   To use the full canvas as context, the user drags the rectangle
   handles to the image edges.  The drag math clamps to image bounds,
   so dragging past an edge gives a rect flush with that edge.

   Output dimensions match the canvas (not the rect's dimensions), so
   the workflow gets a mask of the same extent as `thecanvas`. */

export function buildContextMaskB64() {
  ensureDefaultContext();
  const W = board.imageWidth, H = board.imageHeight;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.fillStyle = "#000"; cx.fillRect(0, 0, W, H);
  const r = board.inpaintContext;
  cx.fillStyle = "#fff"; cx.fillRect(r.x, r.y, r.w, r.h);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Mask resize
   ============================================================
   Masks imported from Krita may have dimensions that differ from the
   canvas. The daemon's `_decode_mask_to_binary` resizes anyway, but
   producing a canvas-sized blob in JS keeps the wire payload honest
   and makes the daemon's job trivial. */

export function resizeMaskB64ToCanvas(b64, img) {
  if (img.width === board.imageWidth && img.height === board.imageHeight) {
    return b64;
  }
  const c = document.createElement("canvas");
  c.width = board.imageWidth; c.height = board.imageHeight;
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}
"""
