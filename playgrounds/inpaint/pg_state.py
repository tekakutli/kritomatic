"""Mutable state, canvas handles, view transforms, draw hook, mask
palette.

Leaf module: imports nothing from the project beyond `pg_tuning.js`.
Every other project module imports from here, and nothing here imports
from the project's feature modules.  That is what lets the graph stay
acyclic.

`board` carries every piece of user-visible state.

`setImage` and `clearImage` inline their board-resetting logic (mask
array, inpaint context rectangle) rather than importing
`pg_masks.clearMasks`.  That preserves this module as a leaf.

`applyCanvasImage` is the autosync target: updates the displayed
image without clearing masks or the inpaint context rect (unless
image dimensions changed).  Used by `pg_autosync.js` when the
daemon's `@thecanvas` spec changes, and by the manual refresh action.

The `lastSync`, `syncing`, and `syncError` fields are set by
`pg_sync.js` — they drive the panel's status line and nothing else."""

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
   as a 2D canvas transform at draw time (in `pg_view_image.js`
   and `pg_view_inpaint.js`), not by pre-rendering a rotated copy.

   The inpaint workflow's rotated behavior is produced daemon-side:
   `pg_sync.js` pushes the un-rotated state into Krita scratch layers
   and separately pushes the rotation value; the daemon queries the
   rotation value on demand and, when θ≠0, rotates the canvas and
   selection and defines the context mask as a translated axis-aligned
   rect before submitting to ComfyUI.

   `board.inpaintContext` lives in original-image pixel coords.
   Because the image never moves in that frame, the user's rect
   stays over the same pixels no matter what the rotation is.
   Rotating the view visually rotates the rect (it is drawn under
   the same transform), but its `x/y/w/h` never change except
   when the user drags a handle in `pg_dispatch.js`.
   `_applyRotation` is no longer allowed to touch it.
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

/* Zoom around the cursor, rotation-safe.  We remember the image
   point under the cursor, change the zoom, then adjust pan so
   the same image point returns to the same screen point. */
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
   mask pixel caches.

   `_rotateImage` is retained as a helper for `pg_view_image.js`'s
   draw path (used via the ctx.rotate transform, not by
   pre-rendering) and for tests.  `_rotateMask` invalidates only the
   stale rotated-canvas cache; mask pixel data always lives in the
   original image frame.
   ============================================================ */

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
  // reads `mask.img` in the original image frame.
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

/* ============================================================
   applyCanvasImage — autosync / manual-refresh target
   ============================================================
   Swap the displayed image in response to a `@thecanvas` resource
   spec change or a manual refresh.  Unlike `setImage`, this:

     * keeps the mask stack (invalidating only their pixel caches so
       the tinted overlays rebuild);
     * keeps the inpaint context rectangle IF the new image has the
       same dimensions, else clears it;
     * keeps view.rotation, view.zoom, view.panX/Y.

   Used by `pg_autosync.js` and by the panel's "Refresh @thecanvas"
   button in `pg_panel_bindings.js`. */
export function applyCanvasImage(hash, dataUrl, width, height) {
  const oldW = board.imageWidth;
  const oldH = board.imageHeight;
  const dimsMatch = (oldW === 0 || oldH === 0
                     || (width === oldW && height === oldH));

  board.imageHash       = hash;
  board.originalDataUrl = dataUrl;
  board.originalWidth   = width;
  board.originalHeight  = height;

  const img = new Image();
  img.onload = () => {
    board.originalImageEl = img;
    board.originalWidth   = img.naturalWidth;
    board.originalHeight  = img.naturalHeight;
    board.imageEl         = img;
    board.imageWidth      = img.naturalWidth;
    board.imageHeight     = img.naturalHeight;
    board.imageDataUrl    = dataUrl;

    for (const m of board.masks) {
      m._pixels = null;
      m._bbox = null;
      m._tinted = null;
      m._tintedKey = null;
    }

    if (!dimsMatch) {
      board.inpaintContext = {
        x: 0, y: 0, w: 0, h: 0, activeHandle: null,
      };
    }

    _scheduleDraw();
  };
  img.onerror = () => _scheduleDraw();
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

  originalImageEl:  null,
  originalWidth:    0,
  originalHeight:   0,
  originalDataUrl:  null,

  mouse: { x: 0, y: 0, inside: false },

  masks:               [],
  selectedMaskIndices: [],
  hoveredMaskIdx:      -1,

  // Sync state, set by pg_sync.js.  Purely panel-facing.
  lastSync:  null,
  syncing:   false,
  syncError: null,

  inpaintEnabled: false,
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

/* Image → screen.  Rotation is around the image center.  Positive
   angle follows `ctx.rotate` — clockwise in screen (y-down) coords. */
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

/* Screen → image.  Inverse of `imageToScreen`. */
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
   to the un-rotated fit rect — notably the inpaint context rectangle,
   whose position is fixed on screen even while the image rotates
   underneath it. */
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
