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
    - `buildMaskFromContextRectB64` — an *inverse-rotated* version of
      the context rectangle, at image dims.  Used by the "create mask
      from context rectangle" button: because every mask is drawn
      under the view's rotation transform (pg_view_image.js), storing
      the rect pre-inverse-rotated makes it appear on screen as the
      axis-aligned rectangle the user actually drew.  Storing the
      plain axis-aligned rect would make it appear rotated on screen,
      out of place relative to the visible context rect.
    - `resizeMaskB64ToCanvas` — canvas-sized output from an arbitrary
      input PNG

The drag STATE (`_contextDrag = { active, handle, startIx, ... }`)
stays in `pg_dispatch.js` because it is per-interaction, not
per-document."""

INPAINT_JS = r"""
import { board, view } from "./pg_state.js";

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
   image rotation.

   NOTE: the previous version clamped x0/y0/x1/y1 to
   [0, imageWidth] × [0, imageHeight] here.  That is what produced
   the "invisible boundaries" the user reported — the rectangle
   could not be dragged past the image edge even though the screen
   has plenty of empty space around it, and even though the
   *drawn* rectangle already extends past the image whenever the
   user drags a handle outwards.

   There is no need for that clamp.  Every consumer of
   `board.inpaintContext` that cares about image bounds already
   handles out-of-image rects gracefully:

     - `contextRectScreen` and `contextHandles` just project the
       coords to screen; they do not need the rect to be inside
       the image.
     - `hitContextRect` compares against `contextRectScreen`, so
       it works with any rect.
     - `buildContextMaskB64` paints into a `W×H` canvas with
       `fillRect(c.x, c.y, c.w, c.h)`.  Any part of the rect
       outside the canvas is silently clipped by `fillRect` — so
       the mask automatically becomes the intersection of the
       visible rect and the image, which is the only region that
       could possibly be inpainted.
     - `buildRotatedContextMaskB64` paints into a `W'×H'` canvas
       the same way, with the same natural clipping.
     - `_buildInpaintPayload`'s mode-B context mask paints the
       rect into a `W×H` canvas, again with `fillRect` clipping
       the overflow.

   The only hard invariant `applyContextDrag` still enforces is
   `CONTEXT_MIN_SIZE`: a rect smaller than that is unusable for
   the handle hit-test, so the handles would vanish and the rect
   would become impossible to grab or resize. */

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

  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/* ============================================================
   Mask building
   ============================================================
   `buildContextMaskCanvas` produces the `thecontext` mask in the
   ORIGINAL image frame:

     inpaintEnabled = false  →  all-white (full canvas is the context)
     inpaintEnabled = true   →  black canvas, white rectangle

   The white rectangle is `(c.x, c.y, c.w, c.h)`.  When that
   rectangle extends past the image edge, `fillRect` clips it to
   the canvas — which is exactly the intersection of the visible
   rect and the image, and therefore exactly the region the
   workflow can crop and inpaint.

   `buildRotatedContextMaskB64` is what the inpaint action sends.
   When `view.rotation` is non-zero it produces a canvas at the
   rotated bbox dims where the white rectangle sits at the SAME
   screen position the user sees.  The rectangle is *translated*
   into the rotated canvas's coordinate space, but not rotated
   itself — matching the on-screen rectangle, which stays fixed
   while the image rotates underneath. */

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
   Mask from context rectangle (for the "+ As mask" button)
   ============================================================
   Every mask in the stack is drawn inside the rotation transform
   in `pg_view_image.js::drawImageLayer` — i.e. on screen, a mask
   pixel at image coord (mx, my) is shown at
   `rotate((mx, my) around image center by view.rotation)`.

   So a mask whose pixel data is the plain axis-aligned rect
   (c.x, c.y, c.w, c.h) will *appear on screen* as a rotated
   rectangle, not as the axis-aligned context rect the user sees.

   To make the mask *visually* match the context rect, we store
   the rect **inverse-rotated by view.rotation around the image
   centre**.  After the display applies the forward rotation, the
   inverse rotation cancels and the diamond lands exactly on the
   axis-aligned rect's screen footprint.

   When view.rotation is 0 the inverse rotation is the identity,
   so this collapses to the plain axis-aligned fill — identical
   to `buildContextMaskB64` in that case.

   If the rect extends past the image edge, `fillRect` clips it
   to the `W×H` canvas.  The resulting mask is the intersection
   of the visible rect and the image. */

export function buildMaskFromContextRectB64() {
  const W = board.imageWidth, H = board.imageHeight;
  if (!board.inpaintEnabled) return null;
  const c = board.inpaintContext;
  if (!c || c.w <= 0 || c.h <= 0) return null;

  const cvs = document.createElement("canvas");
  cvs.width = W; cvs.height = H;
  const ccx = cvs.getContext("2d");
  ccx.fillStyle = "#000";
  ccx.fillRect(0, 0, W, H);
  ccx.fillStyle = "#fff";

  const theta = view.rotation || 0;
  if (!theta) {
    ccx.fillRect(c.x, c.y, c.w, c.h);
  } else {
    const rad = -theta * Math.PI / 180;
    ccx.translate(W / 2, H / 2);
    ccx.rotate(rad);
    ccx.translate(-W / 2, -H / 2);
    ccx.fillRect(c.x, c.y, c.w, c.h);
  }

  return cvs.toDataURL("image/png")
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