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
   ============================================================ */

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
  const w = board.imageWidth  * s;
  const h = board.imageHeight * s;
  const baseX = (cw - w) / 2;
  const baseY = (ch - h) / 2;
  const m = VIEW_PAN_MARGIN;

  const minPanX = m - baseX - w;
  const maxPanX = cw - m - baseX;
  view.panX = Math.max(minPanX, Math.min(maxPanX, view.panX));

  const minPanY = m - baseY - h;
  const maxPanY = ch - m - baseY;
  view.panY = Math.max(minPanY, Math.min(maxPanY, view.panY));
}

export function zoomAt(screenX, screenY, newZoom) {
  const r0 = fittedImageRect();
  if (!r0) return;
  const ix = (screenX - r0.x0) / r0.s;
  const iy = (screenY - r0.y0) / r0.s;

  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));

  const r1 = fittedImageRect();
  const newSx = r1.x0 + ix * r1.s;
  const newSy = r1.y0 + iy * r1.s;

  view.panX += screenX - newSx;
  view.panY += screenY - newSy;

  clampPan();
}

/* ============================================================
   Rotation
   ============================================================
   `_rotateImage` returns a new HTMLCanvasElement holding the
   image rotated by `angleDeg` about its center, sized to the
   axis-aligned bounding box of the rotated rectangle. The
   corners of the bbox outside the rotated image are
   transparent.

   Positive `angleDeg` rotates clockwise on screen (matching
   `ctx.rotate` and Qt's `QTransform.rotate` in screen coords),
   so the daemon's un-rotation is `QTransform().rotate(-θ)` for
   the playground's `θ`.

   `_applyRotation` is idempotent — it always starts from
   `board.originalImageEl`, so setting the field to 30° twice
   produces the same result as setting it once. Every image
   import goes through it.

   `_rotateMask` mirrors this for masks. Masks are rotated at
   load time (from `_makeMaskEntry`) so `_maskPixelData` and hit
   testing operate on the rotated data. The caches (`_pixels`,
   `_bbox`, `_tinted`, `_tintedKey`) are cleared on every
   rotation change.
*/

function _rotateImage(img, angleDeg) {
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
  entry._pixels = null;
  entry._bbox = null;
  entry._tinted = null;
  entry._tintedKey = null;

  if (!entry.loaded || !entry.img) {
    entry.rotatedCanvas = null;
    entry.rotatedW = 0;
    entry.rotatedH = 0;
    return;
  }
  if (!angle || angle % 360 === 0) {
    entry.rotatedCanvas = null;
    entry.rotatedW = entry.w;
    entry.rotatedH = entry.h;
    return;
  }
  const rc = _rotateImage(entry.img, angle);
  entry.rotatedCanvas = rc;
  entry.rotatedW = rc ? rc.width : 0;
  entry.rotatedH = rc ? rc.height : 0;
}

export function _applyRotation(angle) {
  view.rotation = ((Number(angle) || 0) % 360 + 360) % 360;

  if (!board.originalImageEl) {
    // No image loaded yet; when it loads, its onload will call us.
    _scheduleDraw();
    return;
  }

  if (view.rotation === 0) {
    board.imageEl     = board.originalImageEl;
    board.imageWidth  = board.originalWidth;
    board.imageHeight = board.originalHeight;
    board.imageDataUrl = board.originalDataUrl;
  } else {
    const rc = _rotateImage(board.originalImageEl, view.rotation);
    if (!rc) {
      board.imageEl = null;
      board.imageWidth = 0;
      board.imageHeight = 0;
      board.imageDataUrl = null;
      _scheduleDraw();
      return;
    }
    board.imageEl     = rc;
    board.imageWidth  = rc.width;
    board.imageHeight = rc.height;
    board.imageDataUrl = rc.toDataURL("image/png");
  }

  for (const m of board.masks) _rotateMask(m, view.rotation);

  // Context rect coordinates were in the previous frame; reset.
  board.inpaintContext = {
    x: 0, y: 0, w: 0, h: 0, activeHandle: null,
  };

  clampPan();
  _scheduleDraw();
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

  // Until the source loads, the visible fields stay empty; a
  // rotation field change before that point is a no-op (bailed
  // above) and will be re-applied once onload fires.
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

  // The un-rotated source. `imageEl` / `imageWidth` / etc. above
  // are derived from these by `_applyRotation`.
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
   Image-fit rect — applies view.zoom and view.panX/panY
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

export function imageToScreen(x, y) {
  const r = fittedImageRect();
  if (!r) return [x, y];
  return [r.x0 + x * r.s, r.y0 + y * r.s];
}

export function screenToImage(sx, sy) {
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
