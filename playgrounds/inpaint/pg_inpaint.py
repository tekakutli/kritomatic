"""Inpainting context rectangle helpers.

`inpaint` is the primary feature of this playground and expected to
grow.  This file contains:

    - the tolerance constants
    - lazy-default initialisation of the rect
    - image→screen and screen-handle projections
    - the click classifier (handle vs border vs interior) — screen-space
    - the pure drag math (resize with optional Shift symmetry + move)
    - `buildContextMaskCanvas` / `buildContextMaskB64` — full-canvas
      mask of the context region in the ORIGINAL image frame
    - `buildRotatedContextMaskB64` — the same mask, translated into
      the rotated canvas's coords so the daemon crops the rotated
      raster to the SAME screen region the user sees
    - `resizeMaskB64ToCanvas` — canvas-sized output from an arbitrary
      input PNG

The drag STATE (`_contextDrag = { active, handle, startIx, ... }`)
stays in `pg_dispatch.js` because it is per-interaction, not
per-document."""

INPAINT_JS = r"""
import { board } from "./pg_state.js";

/* ============================================================
   Tolerances
   ============================================================ */

export const CONTEXT_MIN_SIZE     = 16;   // image px
export const CONTEXT_HANDLE_HALF  = 10;   // screen px
export const CONTEXT_BORDER_HALF  = 6;    // screen px

/* ============================================================
   Lazy default
   ============================================================ */

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
   ============================================================
   `contextRectScreen` gives the rectangle's fixed screen position
   under the un-rotated fit rect.  The rectangle never rotates with
   the image, so this projection is what the draw and hit-test both
   use. */

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
   ============================================================
   Screen-space.  The rectangle is drawn without rotation, so its
   hit area is exactly the screen rect `contextRectScreen(r)`
   plus/minus the tolerance bands. */

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
   ============================================================
   Inputs are un-rotated image coords (see
   `screenToImageUnrotated`).  The rectangle is anchored to the
   un-rotated fit rect, so it moves/resizes independently of the
   image rotation. */

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
   `buildContextMaskCanvas` produces the `thecontext` mask in the
   ORIGINAL image frame:

     inpaintEnabled = false  →  all-white (full canvas is the context)
     inpaintEnabled = true   →  black canvas, white rectangle

   `buildRotatedContextMaskB64` is what the inpaint action sends.
   When `view.rotation` is non-zero it produces a canvas at the
   rotated bbox dims where the white rectangle sits at the SAME
   screen position the user sees.  The rectangle is *translated*
   into the rotated canvas's coordinate space, but not rotated
   itself — matching the on-screen rectangle, which stays fixed
   while the image rotates underneath.

   Derivation: screen point `(sx, sy)` maps to rotated-canvas pixel
   `((sx - cx)/r.s + newW/2, (sy - cy)/r.s + newH/2)` where `cx, cy`
   is the un-rotated image center in screen coords.  The rect's
   screen top-left is `(r.x0 + c.x*r.s, r.y0 + c.y*r.s)`; plugging
   in gives `(c.x - W/2 + newW/2, c.y - H/2 + newH/2)` with the
   same `c.w` × `c.h` size. */

export function buildContextMaskCanvas() {
  const W = board.imageWidth, H = board.imageHeight;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  if (!board.inpaintEnabled) {
    cx.fillStyle = "#fff"; cx.fillRect(0, 0, W, H);
  } else {
    ensureDefaultContext();
    cx.fillStyle = "#000"; cx.fillRect(0, 0, W, H);
    const r = board.inpaintContext;
    cx.fillStyle = "#fff"; cx.fillRect(r.x, r.y, r.w, r.h);
  }
  return c;
}

export function buildContextMaskB64() {
  return buildContextMaskCanvas()
    .toDataURL("image/png")
    .replace(/^data:image\/png;base64,/, "");
}

export function buildRotatedContextMaskB64(angle) {
  const W = board.imageWidth, H = board.imageHeight;
  if (!angle || angle % 360 === 0) {
    return buildContextMaskB64();
  }
  const rad = (angle % 360) * Math.PI / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const newW = Math.max(1, Math.round(W * cos + H * sin));
  const newH = Math.max(1, Math.round(W * sin + H * cos));

  const c = document.createElement("canvas");
  c.width = newW; c.height = newH;
  const cx = c.getContext("2d");

  if (!board.inpaintEnabled) {
    // Full-canvas mode: entire rotated canvas is context.
    cx.fillStyle = "#fff";
    cx.fillRect(0, 0, newW, newH);
    return c.toDataURL("image/png")
      .replace(/^data:image\/png;base64,/, "");
  }

  cx.fillStyle = "#000";
  cx.fillRect(0, 0, newW, newH);

  const r = board.inpaintContext;
  // Translate, don't rotate.  Size stays c.w x c.h; position shifts
  // by the half-difference between rotated bbox dims and original
  // dims (same derivation as in the docstring above).
  const rx = r.x - W / 2 + newW / 2;
  const ry = r.y - H / 2 + newH / 2;
  cx.fillStyle = "#fff";
  cx.fillRect(rx, ry, r.w, r.h);
  return c.toDataURL("image/png")
    .replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Mask resize
   ============================================================ */

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