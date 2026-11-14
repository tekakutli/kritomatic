"""Mutable state, canvas handles, view transforms, draw hook, mask
palette.

Leaf module: imports nothing from the project beyond `pg_tuning.js`.
Every other project module imports from here, and nothing here imports
from the project's feature modules.  That is what lets the graph stay
acyclic.

`board` carries every piece of user-visible state.

`setImage` and `clearImage` inline their board-resetting logic (mask
array, staging points, region stroke, op log) rather than importing
`pg_masks.clearMasks`.  That preserves this module as a leaf.  The
behaviour is identical to the original `clearMasks()` call chain — the
whole synchronous reset block completes before the scheduled
`requestAnimationFrame` fires.

THREE CANVASES
==============
The render surface is split into three stacked canvases:

    #c              image layer: dark fill, image, tinted masks
    #regionCanvas   region layer: live stroke preview OR edge dots
    #overlayCanvas  overlay layer: points, brush cursor, drafts, chrome

`pg_view.js` computes a per-layer content signature on every
`drawView()` call and repaints a layer only when its signature
changed.  The sigs are stored in `_layerSigs`; `resize()` clears them
because `canvas.width = …` wipes the pixel buffer regardless of
whether the sig would have changed.

The split exists to exploit disjoint change triggers:

  · Brush-mode mousemove (no button): only the overlay's brush cursor
    moves.  Image, tints, region, points, chrome are unchanged.
  · Brush stroke painting: `regionCanvas` mutates per frame; image,
    tints, points, chrome unchanged.
  · Hover across a mask boundary: image alpha changes; region, points,
    chrome unchanged.
  · Pan: all three translate.
  · Zoom: all three translate; chrome text changes.

Why not more layers: drafts-following-cursor and chrome-text are both
on the overlay; they were split into separate layers in one design but
they share the "rarely changes" property and separating them would
quadruple per-frame layer bookkeeping for no additional saving.

`_masksVersion` is a monotonic counter, bumped by `pg_masks.js`
whenever a mask's visible contribution changes without moving any
other sig input: `img.onload`, `img.onerror`, `setMasks`, `replaceMaskAt`,
`restoreMasks`, `addMaskFromB64`.  It's part of the image-layer sig.

`_scheduleDraw`      → full `draw()` (canvas + panel syncs).
`_scheduleDrawView`  → `drawView()` only (canvas-only repaint).

A pending `_scheduleDraw` upgrades a queued `_scheduleDrawView`
(full draw implies view draw); a pending view-only scheduler is a
no-op if a full draw is already queued.

`fittedImageRect()` is memoized on the tuple of inputs that fully
determine its result: `(innerWidth, innerHeight, imageWidth,
imageHeight, zoom, panX, panY)`.  The function runs many times per
frame — once explicitly in `pg_dispatch`, once inside each
`imageToScreen` / `screenToImage`, and once per point / lasso vertex
that the view layers project.  Cache hits become string compares.

Callers must treat the returned object as read-only — none of them
mutate it, verified across `pg_view.py`, `pg_view_image.py`,
`pg_view_region.py`, `pg_view_points.py`, `pg_view_brush.py`,
`pg_dispatch.py`."""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

/* ============================================================
   Canvases and 2D contexts
   ============================================================ */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

export const regionCanvas = document.createElement("canvas");
regionCanvas.id = "regionCanvas";
document.body.appendChild(regionCanvas);
export const regionCtx = regionCanvas.getContext("2d");

export const overlayCanvas = document.createElement("canvas");
overlayCanvas.id = "overlayCanvas";
document.body.appendChild(overlayCanvas);
export const overlayCtx = overlayCanvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   Per-layer sig caches — shared with resize().
   ============================================================ */

export const _layerSigs = { image: null, region: null, overlay: null };

/* ============================================================
   Masks version — see the module docstring.
   ============================================================ */

let _masksVersion = 0;
export function _bumpMasksVersion() { _masksVersion++; }
export function _getMasksVersion()   { return _masksVersion; }

/* ============================================================
   View (zoom + pan)
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
   Image loading / clearing
   ============================================================ */

export function setImage(hash, dataUrl, width, height) {
  board.imageHash    = hash;
  board.imageDataUrl = dataUrl;
  board.imageWidth   = width;
  board.imageHeight  = height;

  const img = new Image();
  img.onload  = () => { board.imageEl = img;  _scheduleDraw(); };
  img.onerror = () => { board.imageEl = null; _scheduleDraw(); };
  img.src = dataUrl;

  resetView();
  board.masks = [];
  board.selectedMaskIndices = [];
  board.hoveredMaskIdx = -1;
  board.stagingPoints = [];
  board.lassoPoints = [];
  board.freehandPoints = [];
  board.freehandActive = false;
  board.freehandIsErase = false;
  board.regionStrokeActive = false;
  board.opLog = [];
  board.redoLog = [];
  _bumpMasksVersion();
  _scheduleDraw();
}

export function clearImage() {
  board.imageHash    = null;
  board.imageDataUrl = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageEl      = null;
  resetView();
  board.masks = [];
  board.selectedMaskIndices = [];
  board.hoveredMaskIdx = -1;
  board.stagingPoints = [];
  board.lassoPoints = [];
  board.freehandPoints = [];
  board.freehandActive = false;
  board.freehandIsErase = false;
  board.regionStrokeActive = false;
  board.opLog = [];
  board.redoLog = [];
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

  textPrompt:    "",
  stagingPoints: [],
  refineModel:   'sam2',

  threshold:        0.5,
  refineIterations: 2,
  individualMasks:  true,
  autoRun:          false,

  regionMode:          false,
  regionTool:          'brush',
  regionSubtract:      false,
  regionSize:          30,
  regionStrokeActive:  false,
  regionStrokeIsErase: false,
  freehandActive:      false,
  freehandIsErase:     false,
  freehandPoints:      [],
  resizeAnchor:        null,
  lassoPoints:         [],

  mouse: { x: 0, y: 0, inside: false },

  masks:               [],
  selectedMaskIndices: [],
  hoveredMaskIdx:      -1,

  opLog:    [],
  redoLog:  [],

  running:       false,
  lastTiming:    null,

  kritaFeather:        0,
  kritaExpand:         0,
  kritaContract:       0,
  kritaSmooth:         0,
  kritaInvert:         false,
  kritaSelMode:        "replace",
  kritaBlendMode:      "",
  kritaOpacity:        100,
  kritaOpacityEnabled: false,
  kritaGroup:          "",
};

/* ============================================================
   Image-fit rect — MEMOIZED on the input tuple.
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
   Draw scheduling — two levels
   ============================================================ */

let _drawScheduled = false;
let _drawFull      = false;
let _drawFn        = null;
let _drawViewFn    = null;

export function _setDraw(fn)     { _drawFn = fn; }
export function _setDrawView(fn) { _drawViewFn = fn; }

function _queue() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  const fire = () => {
    const full = _drawFull;
    _drawScheduled = false;
    _drawFull = false;
    if (full && _drawFn) _drawFn();
    else if (!full && _drawViewFn) _drawViewFn();
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(fire);
  else setTimeout(fire, 16);
}

export function _scheduleDraw() {
  _drawFull = true;
  _queue();
}

export function _scheduleDrawView() {
  _queue();
}

/* ============================================================
   Resize — sizes all three canvases and clears sigs.
   ============================================================ */

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;

  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  regionCanvas.width  = Math.round(cw * _dpr);
  regionCanvas.height = Math.round(ch * _dpr);
  regionCanvas.style.width  = cw + "px";
  regionCanvas.style.height = ch + "px";
  regionCtx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  overlayCanvas.width  = Math.round(cw * _dpr);
  overlayCanvas.height = Math.round(ch * _dpr);
  overlayCanvas.style.width  = cw + "px";
  overlayCanvas.style.height = ch + "px";
  overlayCtx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  _layerSigs.image   = null;
  _layerSigs.region  = null;
  _layerSigs.overlay = null;

  clampPan();
  if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);
"""