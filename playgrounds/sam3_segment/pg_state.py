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
`requestAnimationFrame` fires."""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

/* ============================================================
   Canvas and 2D context
   ============================================================ */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

/* `dpr` is module-private.  The original pg_core exported it, but
   nothing outside the module ever read it; it only fed `resize()`. */
let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   View (zoom + pan)
   ============================================================ */

export const view = { zoom: 1, panX: 0, panY: 0 };

export function resetView() {
  view.zoom = 1;
  view.panX = 0;
  view.panY = 0;
}

/* Clamp pan so at least VIEW_PAN_MARGIN screen pixels of image stay
   inside the viewport on each axis.  Called after any pan/zoom change
   and on window resize. */
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

  // x0 = baseX + panX.  Constraints:
  //   x0 + w >= m        (right edge visible)
  //   x0     <= cw - m   (left edge visible)
  const minPanX = m - baseX - w;
  const maxPanX = cw - m - baseX;
  view.panX = Math.max(minPanX, Math.min(maxPanX, view.panX));

  const minPanY = m - baseY - h;
  const maxPanY = ch - m - baseY;
  view.panY = Math.max(minPanY, Math.min(maxPanY, view.panY));
}

/* Zoom to `newZoom`, anchored at the given screen coordinate so the
   image point currently under the cursor stays under the cursor. */
export function zoomAt(screenX, screenY, newZoom) {
  const r0 = fittedImageRect();
  if (!r0) return;
  const ix = (screenX - r0.x0) / r0.s;
  const iy = (screenY - r0.y0) / r0.s;

  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));

  // Where does (ix, iy) land on screen with the new zoom, old pan?
  const r1 = fittedImageRect();
  const newSx = r1.x0 + ix * r1.s;
  const newSy = r1.y0 + iy * r1.s;

  // Compensate pan so the anchored image point stays under the cursor.
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

  // Krita export options. Persist across runs so a user who tunes
  // feather once doesn't re-tune it on every mask; the values are
  // copied into every applyToKrita / extractMaskToLayer call.
  //
  // kritaOpacity is in PERCENT (0-100), not Krita's native 0-255.
  // Every UI surface here — the scrub input, the field registration,
  // the number a remote client sees — uses percent. The single
  // conversion to 0-255 happens at the boundary in
  // pg_panel_bindings._getKritaExtractOptions, right before the
  // extract payload is built. That keeps the daemon's byte-range
  // contract the daemon's problem, and keeps the user's mental model
  // ("I want 50% opacity") straightforward.
  //
  // kritaOpacityEnabled gates the opacity value: when false, no
  // set_layer_opacity command is issued during extraction, so the
  // new layer inherits whatever opacity the source has (relevant for
  // duplicate mode where the dup inherits from the source). When
  // true, the value is applied explicitly, overriding whatever the
  // source carried.
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
    requestAnimationFrame(() => { _drawScheduled = false; if (_drawFn) _drawFn(); });
  } else {
    setTimeout(() => { _drawScheduled = false; if (_drawFn) _drawFn(); }, 16);
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
