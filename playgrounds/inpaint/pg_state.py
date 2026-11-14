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
`pg_sync.js` — they drive the panel's status line and nothing else.

TWO CANVASES
============
The render surface is split into two stacked canvases:

    #c              image layer: dark fill, image, mask tints
                    (under the rotation transform)
    #overlayCanvas  UI layer: context rect, chrome text, empty-state
                    text (no rotation transform; transparent
                    background; pointer-events:none so all mouse
                    events still land on #c)

The overlay is created and appended here at module load.  Its CSS
(`background:transparent`, `position:absolute; top:0; left:0`,
`pointer-events:none`) is in `pg_base.py`.

The point of the split is that the two layers' input sets are
mostly disjoint:

    image layer reads: board.imageEl/Hash/W/H, view.zoom/panX/panY/
                       rotation, board.masks + selection + hover
    UI  layer reads:   view.zoom/panX/panY, board.imageEl/Hash/W/H,
                       board.inpaintEnabled, board.inpaintContext.*,
                       board.masks.length, board.selectedMaskIndices.length

`view.rotation` is only in the image-layer input set.  Rotating
therefore repaints the image layer only — the context rect and
chrome text never repaint on rotation, which is exactly the property
the previous single-canvas `drawView()` could not exploit.
`board.inpaintContext.*` and `board.hoveredMaskIdx` similarly
belong to disjoint layers, so dragging the context rect or moving
across a mask boundary repaints exactly one canvas.

`pg_view.py` computes a content signature of each layer's input
set per `drawView()` call and repaints a layer only when its sig
changed.  The sig caches live in `_layerSigs`, exported from here so
`resize()` can invalidate them (`canvas.width = …` clears the pixel
buffer regardless of whether the sig would have changed).

MASKS VERSION
=============
`_masksVersion` is a monotonic counter, bumped by `pg_masks.js`
whenever a mask entry's visible contribution changes without any
other sig input changing:

    · a mask's `img.onload` — `loaded` flips false→true, w/h become
      real values.  The mask's pixel data enters the draw for the
      first time.  `board.masks.length` was already 1 before this
      event; no other sig input moves.
    · `setMasks(b64List)` at constant length — entry *identity*
      changes (new Image objects), but `masks.length` and
      `selectedMaskIndices` are unchanged.
    · `replaceMaskAt(idx, b64)` — same.
    · `applyCanvasImage` / `setImage` / `clearImage` invalidate
      `_pixels`/`_tinted` caches on every mask; the tint must be
      rebuilt from the same underlying bytes but against the (new)
      image frame.

It is exported here so `pg_view.py` can include it in the image
sig, and so `pg_state.js`'s own `setImage` / `clearImage` /
`applyCanvasImage` can bump it inline (they mutate `board.masks` or
its caches directly and cannot import `pg_masks.js` without
inverting the leaf-module dependency).

`fittedImageRect()` is memoized on the tuple of inputs that fully
determine its result: `(innerWidth, innerHeight, imageWidth,
imageHeight, zoom, panX, panY)`.  The function runs 2–4 times per
`mousemove` frame (once explicitly in `pg_dispatch`, once inside
`screenToImage`, once inside `screenToImageUnrotated`, once inside
`drawView`), and each call recomputes two divisions, `Math.min`,
six multiplies, and allocates a fresh object.  The cache turns all
but the first into a string compare.

Cache miss allocation cost is unchanged; cache hit is one string
compare plus a field read.  Callers must treat the returned object
as read-only — none of them mutate it, verified across
`pg_view.py`, `pg_view_image.py`, `pg_view_inpaint.py`,
`pg_dispatch.py`, `pg_inpaint.py`."""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

/* ============================================================
   Canvas and 2D contexts
   ============================================================
   #c              image layer  (bottom, dark background)
   #overlayCanvas  UI layer     (top, transparent, pointer-events:none)
   ============================================================ */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

export const overlayCanvas = document.createElement("canvas");
overlayCanvas.id = "overlayCanvas";
document.body.appendChild(overlayCanvas);
export const overlayCtx = overlayCanvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   Draw-sig caches, shared between pg_view.js (writer) and resize()
   (invalidator).  Kept here rather than in pg_view.js so resize()
   can clear them without importing pg_view.js (which imports this
   module — a cycle).
   ============================================================ */

export const _layerSigs = { image: null, overlay: null };

/* ============================================================
   Masks version — see the module docstring, MASKS VERSION.
   ============================================================ */

export let _masksVersion = 0;
export function _bumpMasksVersion() { _masksVersion++; }

/* ============================================================
   View (zoom + pan + rotation)
   ============================================================
   `view.rotation` is a *display-only* angle.  `board.imageWidth`,
   `board.imageHeight`, `board.imageDataUrl`, and `board.imageEl`
   are always the ORIGINAL un-rotated source.  Rotation is applied
   as a 2D canvas transform at draw time (in `pg_view_image.js`),
   not by pre-rendering a rotated copy.

   The inpaint workflow's rotated behavior is produced daemon-side:
   `pg_sync.js` pushes the un-rotated state into Krita scratch layers
   and separately pushes the rotation value; the daemon queries the
   rotation value on demand and, when θ≠0, rotates the canvas and
   selection and defines the context mask as a translated axis-aligned
   rect before submitting to ComfyUI.

   `board.inpaintContext` lives in original-image pixel coords.
   Because the image never moves in that frame, the user's rect
   stays over the same pixels no matter what the rotation is.
   Rotating the view visually rotates the image (which is drawn under
   the rotation transform); the rect is drawn on the overlay layer
   WITHOUT the rotation transform and therefore stays put on screen.
   Its `x/y/w/h` never change except when the user drags a handle in
   `pg_dispatch.js`.  `_applyRotation` is not allowed to touch it.
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

   `_rotateImage` is retained as a helper for tests.  `_rotateMask`
   invalidates only the stale rotated-canvas cache; mask pixel data
   always lives in the original image frame.
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
  _bumpMasksVersion();
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
    _bumpMasksVersion();

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
  _bumpMasksVersion();
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
   by the image layer; the rect itself never changes with rotation.

   MEMOIZED on the input tuple.  See the module docstring for the
   rationale.  Callers must not mutate the returned object.
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
  const baseS = Math.min(sx, sy);
  const s = baseS * view.zoom;
  const w = board.imageWidth  * s;
  const h = board.imageHeight * s;
  const x0 = (cw - w) / 2 + view.panX;
  const y0 = (ch - h) / 2 + view.panY;

  const val = { x0, y0, w, h, s };
  _firCacheKey = key;
  _firCacheVal = val;
  return val;
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
   Resize — sizes BOTH canvases and clears the sig caches.
   Setting `canvas.width` clears the pixel buffer regardless of
   whether the sig would have changed (redundant resize at identical
   dims), so the sigs MUST be reset here, unconditionally.
   ============================================================ */

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;

  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  overlayCanvas.width  = Math.round(cw * _dpr);
  overlayCanvas.height = Math.round(ch * _dpr);
  overlayCanvas.style.width  = cw + "px";
  overlayCanvas.style.height = ch + "px";
  overlayCtx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  _layerSigs.image = null;
  _layerSigs.overlay = null;

  clampPan();
  if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);
"""