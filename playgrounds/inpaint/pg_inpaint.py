"""Inpainting context rectangle helpers.

Two responsibilities remain, since the daemon owns the rotation step:

  - the drag interaction (tolerances, click classification, pure drag
    math), and
  - building the un-rotated context mask that `pg_sync.js` pushes into
    the Krita `thecontext` layer, and the inverse-rotated mask that the
    "+ As mask" button materialises.

The rotated-payload construction, crop projection, and rotation
disambiguation that used to live here moved to the daemon side when
rotation became a state value the daemon queries.  See
`kritomatic_daemon/utils/comfy_apply.py`'s rotated prepare path.

NO BOUNDS CLAMP
===============
`applyContextDrag` does NOT clamp the rect to the image bounds.  The
user can drag the rect anywhere on the canvas — including off the
image, and (during rotation) past the rotated diamond's corners.  The
daemon extends the crop frame with transparent pixels to cover any
out-of-bounds crop region (see `_extend_and_crop` in
`comfy_apply.py`), so an off-image rect is a supported case, not a
malformed one.

Clamping at θ=0 is preserved in `inpaint_server._compute_crop_frame`
(clamp to the image intersection), since at θ=0 there is no rotated
frame to extend into."""

INPAINT_JS = r"""
import { board, view, screenToImageUnrotated } from "./pg_state.js";

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
  const c = board.inpaintContext;
  if (c.w === 0 || c.h === 0) return null;

  const [ix, iy] = screenToImageUnrotated(sx, sy);
  const handleTol = CONTEXT_HANDLE_HALF / r.s;
  const borderTol = CONTEXT_BORDER_HALF / r.s;

  const handles = [
    ['nw', c.x,          c.y],
    ['ne', c.x + c.w,    c.y],
    ['sw', c.x,          c.y + c.h],
    ['se', c.x + c.w,    c.y + c.h],
    ['n',  c.x + c.w/2,  c.y],
    ['s',  c.x + c.w/2,  c.y + c.h],
    ['w',  c.x,          c.y + c.h/2],
    ['e',  c.x + c.w,    c.y + c.h/2],
  ];
  for (const [name, hx, hy] of handles) {
    if (Math.abs(ix - hx) <= handleTol && Math.abs(iy - hy) <= handleTol) {
      return { kind: 'handle', handle: name };
    }
  }

  if (ix >= c.x - borderTol && ix <= c.x + c.w + borderTol &&
      iy >= c.y - borderTol && iy <= c.y + c.h + borderTol) {
    return { kind: 'move' };
  }
  return null;
}

/* ============================================================
   Pure drag math
   ============================================================
   No bounds clamp.  The user can drag the rect anywhere in the
   image coordinate system — including into negative coordinates,
   and past (imageWidth, imageHeight).  The rotated-crop path in
   the daemon extends the crop frame with transparent pixels for
   the out-of-bounds part; the un-rotated path in
   `inpaint_server._compute_crop_frame` clamps to the image
   intersection (there is no frame to extend into at θ=0).

   The minimum-size clamp is preserved so that a resize handle
   dragged past its opposite edge (or a shrink below 16×16 px)
   still yields a well-defined rect; when the drag would invert
   the rect, the min-size clamp symmetrises around the midpoint
   rather than swapping the edges (which would flip the handle's
   role mid-drag). */

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

  // No clamp to [0, imageWidth] × [0, imageHeight] here.  The
  // rotated-crop path in the daemon extends the crop frame with
  // transparent pixels for out-of-bounds regions; the θ=0 path
  // in `inpaint_server._compute_crop_frame` clamps to the image
  // intersection since there is no rotated frame to extend into.

  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/* ============================================================
   Un-rotated context mask (pushed to Krita)
   ============================================================
   Produces the `thecontext` mask that `pg_sync.js` pushes.  It is in
   the un-rotated image frame at W × H:

     inpaintEnabled = false  →  all-white (whole canvas as context)
     inpaintEnabled = true   →  black canvas, white rect at the user's
                                context rectangle

   The daemon at prepare time translates this un-rotated rect into the
   crop frame accounting for rotation — it does not rotate the
   rectangle itself.  See `comfy_apply.py`'s rotated prepare path.

   `fillRect` clips to the W×H canvas, so a rect that extends past the
   image only contributes mask values inside the image — which is the
   only place there are pixels to mask.  The out-of-image part of the
   crop is handled by the daemon's `_extend_and_crop`. */

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

/* ============================================================
   Mask from context rectangle (for the "+ As mask" button)
   ============================================================
   Every mask in the stack is drawn inside the view's rotation
   transform (`pg_view_image.js::drawImageLayer`).  To make the mask
   *visually* match the context rect, we store the rect **inverse-
   rotated by view.rotation around the image centre**.  After the
   display applies the forward rotation, the inverse cancels and the
   mask lands exactly on the axis-aligned rect's screen footprint.

   When view.rotation is 0, this collapses to the plain fill. */

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