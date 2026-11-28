"""Mutable state, canvas handles, view transforms, draw hook, mask
palette, and the alpha-boundary computation.

Leaf module: imports nothing from the project beyond `pg_tuning.js`.

BOARD SHAPE
===========
Unlike inpaint, rmbg keeps TWO image references:

  board.imageEl / imageWidth / imageHeight / imageDataUrl
      The DISPLAYED, boundary-cropped image.  Everything the
      user sees and interacts with (masks, context rectangle,
      hit testing, drawing) works in this frame.  Dims are
      boundary.w × boundary.h.

  board.fullImageEl / fullImageWidth / fullImageHeight / fullImageDataUrl
      The ORIGINAL image as pulled from Krita.  This is what
      gets synced to the rmbg server via `canvas_b64` — the
      daemon needs the full canvas so it can crop to the
      requested frame and paste results back at the right
      offset in Krita.

  board.boundary   { x, y, w, h }
      Where the displayed crop sits inside the full image.
      `x, y` are the offset; `w, h` are the tight non-
      transparent extent computed from the alpha channel on
      every canvas load.

ALPHA BOUNDARY
==============
On every `applyCanvasImage`, we read the image's alpha channel
and compute the tight bounding box of non-zero alpha pixels.
When the image is fully transparent (degenerate), we fall back
to the full image bounds so nothing breaks.  When the image is
fully opaque (typical Krita composite), the boundary equals the
full image — visually identical to inpaint with no rotation.

The whole point of the boundary: an rmbg workflow takes an
input, produces a cutout-with-alpha.  Loading that cutout back
into the playground naturally wants the display tight to the
content, not surrounded by empty transparent margins.  The
context rectangle then constrains to that tight region.

APPLYING A NEW CANVAS
=====================
`applyCanvasImage` is the sole image-install path.  It:
  * computes the alpha boundary,
  * crops the display image to it,
  * preserves masks (invalidating their per-mask pixel/tint
    caches so overlays rebuild),
  * clears the context rectangle if boundary dims changed,
  * preserves `view.zoom/panX/panY` (same rationale as
    inpaint — the user's zoom survives refreshes).
"""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

/* ============================================================
   Canvas / context
   ============================================================ */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   Masks version (see `pg_masks.js`'s module docstring)
   ============================================================ */

export let _masksVersion = 0;
export function _bumpMasksVersion() { _masksVersion++; }

/* ============================================================
   View
   ============================================================ */

export const view = { zoom: 1, panX: 0, panY: 0 };

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
  const s = Math.min(sx, sy) * view.zoom;
  const w = board.imageWidth  * s;
  const h = board.imageHeight * s;
  const baseX = (cw - w) / 2;
  const baseY = (ch - h) / 2;
  const m = VIEW_PAN_MARGIN;
  view.panX = Math.max(m - baseX - w, Math.min(cw - m - baseX, view.panX));
  view.panY = Math.max(m - baseY - h, Math.min(ch - m - baseY, view.panY));
}

export function zoomAt(screenX, screenY, newZoom) {
  const r0 = fittedImageRect();
  if (!r0) return;
  const ix = (screenX - r0.x0) / r0.s;
  const iy = (screenY - r0.y0) / r0.s;
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));
  const r1 = fittedImageRect();
  view.panX += screenX - (r1.x0 + ix * r1.s);
  view.panY += screenY - (r1.y0 + iy * r1.s);
  clampPan();
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
   Board
   ============================================================ */

export const board = {
  /* Displayed (boundary-cropped) image. */
  imageHash:     null,
  imageWidth:    0,
  imageHeight:   0,
  imageDataUrl:  null,
  imageEl:       null,

  /* Full source image (what the server syncs to the daemon). */
  fullImageEl:      null,
  fullImageWidth:   0,
  fullImageHeight:  0,
  fullImageDataUrl: null,

  /* Alpha-boundary within the full image. */
  boundary: { x: 0, y: 0, w: 0, h: 0 },

  mouse: { x: 0, y: 0, inside: false },

  masks:               [],
  selectedMaskIndices: [],
  hoveredMaskIdx:      -1,

  lastSync:  null,
  syncing:   false,
  syncError: null,

  contextEnabled: false,
  context: {
    x: 0, y: 0, w: 0, h: 0,
    activeHandle: null,
  },
};

/* ============================================================
   Fitted image rect — memoized on input tuple
   ============================================================ */

let _firCacheKey = null;
let _firCacheVal = null;

export function fittedImageRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (!board.imageWidth || !board.imageHeight) return null;

  const key = cw + "," + ch + ","
            + board.imageWidth + "," + board.imageHeight + ","
            + view.zoom + "," + view.panX + "," + view.panY;
  if (key === _firCacheKey) return _firCacheVal;

  const pad = 40;
  const sx = (cw - 2 * pad) / board.imageWidth;
  const sy = (ch - 2 * pad) / board.imageHeight;
  const s = Math.min(sx, sy) * view.zoom;
  const w = board.imageWidth  * s;
  const h = board.imageHeight * s;
  const x0 = (cw - w) / 2 + view.panX;
  const y0 = (ch - h) / 2 + view.panY;

  const val = { x0, y0, w, h, s };
  _firCacheKey = key;
  _firCacheVal = val;
  return val;
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
   applyCanvasImage — sole image-install path
   ============================================================ */

function _computeAlphaBBox(img) {
  const W = img.naturalWidth || img.width;
  const H = img.naturalHeight || img.height;
  if (!W || !H) return null;

  let data;
  try {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const cx = c.getContext("2d", { willReadFrequently: true });
    cx.drawImage(img, 0, 0);
    data = cx.getImageData(0, 0, W, H).data;
  } catch (e) {
    return null;
  }

  let minX = W, minY = H, maxX = -1, maxY = -1;
  for (let y = 0; y < H; y++) {
    const rowBase = y * W * 4;
    for (let x = 0; x < W; x++) {
      if (data[rowBase + x * 4 + 3] > 0) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < minX || maxY < minY) return null;
  return { x: minX, y: minY, w: maxX - minX + 1, h: maxY - minY + 1 };
}

function _cropToBoundary(img, boundary) {
  const c = document.createElement("canvas");
  c.width  = boundary.w;
  c.height = boundary.h;
  const cx = c.getContext("2d");
  cx.drawImage(
    img,
    boundary.x, boundary.y, boundary.w, boundary.h,
    0,          0,          boundary.w, boundary.h,
  );
  return c;
}

export function applyCanvasImage(hash, dataUrl, _width, _height) {
  const oldBW = board.boundary.w;
  const oldBH = board.boundary.h;

  const img = new Image();
  img.onload = () => {
    const fullW = img.naturalWidth;
    const fullH = img.naturalHeight;

    const bbox = _computeAlphaBBox(img);
    const boundary = bbox || { x: 0, y: 0, w: fullW, h: fullH };

    const cropped = _cropToBoundary(img, boundary);
    const croppedUrl = cropped.toDataURL("image/png");

    const croppedImg = new Image();
    croppedImg.onload = () => {
      board.fullImageEl      = img;
      board.fullImageWidth   = fullW;
      board.fullImageHeight  = fullH;
      board.fullImageDataUrl = dataUrl;

      board.boundary = boundary;

      board.imageEl      = croppedImg;
      board.imageWidth   = boundary.w;
      board.imageHeight  = boundary.h;
      board.imageDataUrl = croppedUrl;
      board.imageHash    = hash;

      for (const m of board.masks) {
        m._pixels    = null;
        m._bbox      = null;
        m._tinted    = null;
        m._tintedKey = null;
      }
      _bumpMasksVersion();

      if (oldBW !== boundary.w || oldBH !== boundary.h) {
        board.context = {
          x: 0, y: 0, w: 0, h: 0, activeHandle: null,
        };
      }

      _scheduleDraw();
    };
    croppedImg.onerror = () => _scheduleDraw();
    croppedImg.src = croppedUrl;
  };
  img.onerror = () => _scheduleDraw();
  img.src = dataUrl;
  _scheduleDraw();
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