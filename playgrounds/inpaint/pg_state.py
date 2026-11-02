"""Mutable state, canvas handles, view transforms, draw hook, mask
palette.

Leaf module: imports nothing from the project beyond `pg_tuning.js`.
Every other project module imports from here, and nothing here imports
from the project's feature modules.  That is what lets the graph stay
acyclic.

`board` carries every piece of user-visible state.

`setImage` and `clearImage` inline their board-resetting logic (mask
array, inpaint context rectangle) rather than importing
`pg_masks.clearMasks`.  That preserves this module as a leaf."""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

/* ============================================================
   Canvas and 2D context
   ============================================================ */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   View (zoom + pan + rotation)
   ============================================================
   `view.rotation` is a *display-only* angle.  `board.imageWidth`,
   `board.imageHeight`, `board.imageDataUrl`, and `board.imageEl`
   are always the ORIGINAL un-rotated source.  Rotation is applied
   as a 2D canvas transform at draw time (in `pg_view_image.js`),
   not by pre-rendering a rotated copy.

   This is what makes rotating the slider rotate the picture in
   place without changing its on-screen size.

   `board.inpaintContext` lives in un-rotated image pixel coords,
   which map to a fixed screen position via `fittedImageRect()`.
   The rectangle is drawn WITHOUT the rotation transform, so it
   stays visually where the user put it even while the image
   rotates underneath.  Only the drag interaction in
   `pg_dispatch.js` changes its `x/y/w/h`.

   The inpaint action materialises a rotated raster once per run
   and crops it to the rectangle's screen-fixed position. */

export const view = {
  zoom: 1,
  panX: 0,
  panY: 0,
  rotation: 0,   // degrees; clockwise on screen
};

export function resetView() {
  view.zoom = 1;
  view.panX = 0;
  view.panY = 0;
}

export function clampPan() {
  if (!board.imageWidth || !board.imageHeight) return;
  const cw = window.innerWidth, ch = window.innerHeight;
  const pad = 40;
  const sx = (cw - 2 * pad) / board.imageWidth;
  const sy = (ch - 2 * pad) / board.imageHeight;
  const baseS = Math.min(sx, sy);
  const s = baseS * view.zoom;

  // Pan clamp uses the ROTATED bounding box, so at non-cardinal
  // angles the user can still pan far enough to see the corners.
  const rad = view.rotation * Math.PI / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const bw = (board.imageWidth  * cos + board.imageHeight * sin) * s;
  const bh = (board.imageWidth  * sin + board.imageHeight * cos) * s;
  const baseX = (cw - bw) / 2;
  const baseY = (ch - bh) / 2;
  const m = VIEW_PAN_MARGIN;

  const minPanX = m - baseX - bw;
  const maxPanX = cw - m - baseX;
  view.panX = Math.max(minPanX, Math.min(maxPanX, view.panX));

  const minPanY = m - baseY - bh;
  const maxPanY = ch - m - baseY;
  view.panY = Math.max(minPanY, Math.min(maxPanY, view.panY));
}

/* Zoom around the cursor, rotation-safe. */
export function zoomAt(screenX, screenY, newZoom) {
  const [ix0, iy0] = screenToImage(screenX, screenY);
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));
  const [sx1, sy1] = imageToScreen(ix0, iy0);
  view.panX += screenX - sx1;
  view.panY += screenY - sy1;
  clampPan();
}

/* ============================================================
   Rotation
   ============================================================
   `_applyRotation` only sets the display angle.  It does not
   re-render the image, does not touch `board.imageWidth/Height`,
   does not touch `board.inpaintContext`, and does not re-rotate
   mask pixel caches.  The visual rotation happens inside
   `drawImageLayer`, which applies the
   `translate(cx,cy); rotate(rad); translate(-cx,-cy)` transform
   around the image center.

   `_rotateImage` is retained as a helper for the inpaint action,
   which needs to materialise a rotated raster exactly once per
   run.  `_rotateMask` just nulls out `rotatedCanvas`; mask pixel
   data always lives in the original image frame now.  The
   function exists so `pg_masks.py`'s `_makeMaskEntry` (which
   calls it at mask-load time) keeps working without edits. */

export function _applyRotation(angle) {
  view.rotation = ((Number(angle) || 0) % 360 + 360) % 360;
  clampPan();
  _scheduleDraw();
}

export function _rotateImage(img, angleDeg) {
  if (!img) return null;
  const W = img.naturalWidth || img.width;
  const H = img.naturalHeight || img.height;
  if (!W || !H) return null;

  const rad = (angleDeg % 360) * Math.PI / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const newW = Math.max(1, Math.round(W * cos + H * sin));
  const newH = Math.max(1, Math.round(W * sin + H * cos));

  const c = document.createElement("canvas");
  c.width = newW;
  c.height = newH;
  const cx = c.getContext("2d");
  cx.imageSmoothingEnabled = true;
  cx.imageSmoothingQuality = "high";
  cx.translate(newW / 2, newH / 2);
  cx.rotate(rad);
  cx.drawImage(img, -W / 2, -H / 2);
  return c;
}

export function _rotateMask(entry, angle) {
  // Invalidate the rotated-canvas cache.  Mask pixel data always
  // reads `mask.img` in the original image frame now, and the
  // inpaint action rotates the raw mask itself when needed.
  // Caches that depend on the original pixels (`_pixels`, `_bbox`,
  // `_tinted`) stay valid across rotation and are not cleared.
  entry.rotatedCanvas = null;
  entry.rotatedW = entry.w || 0;
  entry.rotatedH = entry.h || 0;
}

/* ============================================================
   Image loading / clearing
   ============================================================ */

export function setImage(hash, dataUrl, width, height) {
  board.imageHash     = hash;
  board.originalDataUrl = dataUrl;
  board.originalWidth   = width;
  board.originalHeight  = height;
  board.originalImageEl = null;

  board.imageEl      = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageDataUrl = null;

  board.masks = [];
  board.selectedMaskIndices = [];
  board.hoveredMaskIdx = -1;
  board.inpaintContext = {
    x: 0, y: 0, w: 0, h: 0, activeHandle: null,
  };
  resetView();

  const img = new Image();
  img.onload = () => {
    board.originalImageEl = img;
    board.originalWidth   = img.naturalWidth;
    board.originalHeight  = img.naturalHeight;
    board.imageEl         = img;
    board.imageWidth      = img.naturalWidth;
    board.imageHeight     = img.naturalHeight;
    board.imageDataUrl    = dataUrl;
    _applyRotation(view.rotation);
  };
  img.onerror = () => {
    board.originalImageEl = null;
    _scheduleDraw();
  };
  img.src = dataUrl;
  _scheduleDraw();
}

export function clearImage() {
  board.imageHash     = null;
  board.originalDataUrl = null;
  board.originalWidth   = 0;
  board.originalHeight  = 0;
  board.originalImageEl = null;

  board.imageEl      = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageDataUrl = null;

  view.rotation = 0;
  resetView();
  board.masks = [];
  board.selectedMaskIndices = [];
  board.hoveredMaskIdx = -1;
  board.inpaintContext = {
    x: 0, y: 0, w: 0, h: 0, activeHandle: null,
  };
  _scheduleDraw();
}

/* ============================================================
   Mask palette
   ============================================================ */

export const MASK_COLORS = [
  { r: 255, g:  90, b: 105 },
  { r: 120, g: 220, b: 255 },
  { r: 255, g: 200, b:  90 },
  { r: 140, g: 225, b: 160 },
  { r: 180, g: 155, b: 255 },
  { r: 255, g: 165, b: 105 },
  { r: 110, g: 220, b: 210 },
  { r: 220, g: 230, b: 120 },
];

export function maskColor(idx) {
  return MASK_COLORS[((idx % MASK_COLORS.length)
                      + MASK_COLORS.length) % MASK_COLORS.length];
}

/* ============================================================
   The board
   ============================================================ */

export const board = {
  imageHash:     null,
  imageWidth:    0,
  imageHeight:   0,
  imageDataUrl:  null,
  imageEl:       null,

  // The un-rotated source.  Mirrors the visible fields above.
  originalImageEl:  null,
  originalWidth:    0,
  originalHeight:   0,
  originalDataUrl:  null,

  mouse: { x: 0, y: 0, inside: false },

  masks:               [],
  selectedMaskIndices: [],
  hoveredMaskIdx:      -1,

  running:       false,
  lastTiming:    null,

  inpaintEnabled: false,
  inpaintPrompt:  "",
  inpaintContext: {
    x: 0, y: 0, w: 0, h: 0,
    activeHandle: null,
  },
};

/* ============================================================
   Image-fit rect — the axis-aligned screen rect the ORIGINAL,
   un-rotated image occupies.  Rotation is applied on top of this
   by the draw layer; the rect itself never changes with rotation.
   ============================================================ */

export function fittedImageRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (!board.imageWidth || !board.imageHeight) return null;
  const pad = 40;
  const sx = (cw - 2 * pad) / board.imageWidth;
  const sy = (ch - 2 * pad) / board.imageHeight;
  const baseS = Math.min(sx, sy);
  const s = baseS * view.zoom;
  const w = board.imageWidth  * s;
  const h = board.imageHeight * s;
  const x0 = (cw - w) / 2 + view.panX;
  const y0 = (ch - h) / 2 + view.panY;
  return { x0, y0, w, h, s };
}

/* Image -> screen, WITH rotation.  Used for mask pixel hit-testing
   (mask data is in the original frame) and for drawing the image
   layer itself. */
export function imageToScreen(x, y) {
  const r = fittedImageRect();
  if (!r) return [x, y];
  const cx = r.x0 + r.w / 2;
  const cy = r.y0 + r.h / 2;
  const dx = (x - board.imageWidth  / 2) * r.s;
  const dy = (y - board.imageHeight / 2) * r.s;
  const rad = view.rotation * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const rx = dx * cos - dy * sin;
  const ry = dx * sin + dy * cos;
  return [cx + rx, cy + ry];
}

/* Screen -> image, WITH rotation inverse.  Use this when the caller
   needs the actual pixel under the cursor (mask hit-test, hover). */
export function screenToImage(sx, sy) {
  const r = fittedImageRect();
  if (!r) return [0, 0];
  const cx = r.x0 + r.w / 2;
  const cy = r.y0 + r.h / 2;
  const dx = sx - cx;
  const dy = sy - cy;
  const rad = view.rotation * Math.PI / 180;
  const cos = Math.cos(rad);
  const sin = Math.sin(rad);
  const rx =  dx * cos + dy * sin;
  const ry = -dx * sin + dy * cos;
  return [board.imageWidth  / 2 + rx / r.s,
          board.imageHeight / 2 + ry / r.s];
}

/* Screen -> image, WITHOUT rotation.  Use this for anything anchored
   to the un-rotated fit rect — notably the inpaint context
   rectangle, whose position is fixed on screen even while the image
   rotates underneath it. */
export function screenToImageUnrotated(sx, sy) {
  const r = fittedImageRect();
  if (!r) return [0, 0];
  return [(sx - r.x0) / r.s, (sy - r.y0) / r.s];
}

/* ============================================================
   Draw scheduling
   ============================================================ */

let _drawScheduled = false;
let _drawFn = null;

export function _setDraw(fn) { _drawFn = fn; }

export function _scheduleDraw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      _drawScheduled = false;
      if (_drawFn) _drawFn();
    });
  } else {
    setTimeout(() => {
      _drawScheduled = false;
      if (_drawFn) _drawFn();
    }, 16);
  }
}

/* ============================================================
   Resize
   ============================================================ */

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);
  clampPan();
  if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);
"""