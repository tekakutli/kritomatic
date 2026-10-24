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
  board.inpaintContext = { x: 0, y: 0, w: 0, h: 0, activeHandle: null };
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
  board.inpaintContext = { x: 0, y: 0, w: 0, h: 0, activeHandle: null };
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

  mouse: { x: 0, y: 0, inside: false },

  masks:               [],
  selectedMaskIndices: [],
  hoveredMaskIdx:      -1,

  running:       false,
  lastTiming:    null,

  // Inpainting section.  The section is open by default; enabling
  // `inpaintEnabled` shows the context rectangle on the canvas.  The
  // rect coordinates live in image space so it pans/zooms with the
  // image.  `activeHandle` is set by the dispatch layer during a drag
  // so the view can highlight the handle being manipulated.
  //
  // If `inpaintEnabled` is false the context mask fed to the workflow
  // is the full canvas (all white); if true it is the rectangle in
  // black-outside-white-inside form.
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
