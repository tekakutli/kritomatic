"""Layout state: image, viewer, bboxes, view, drags.

Leaf module — no imports from other project modules.

Coordinate systems
==================
  · Canvas image pixels     — what `screenToImage()` returns. Range [0, W]×[0, H].
  · Canvas-normalized       — [0, 1] of canvas image (x / W, y / H).
  · Viewer-normalized       — [0, 1] of the viewer rectangle. What the model receives.

The viewer is stored as `{x, y, w, h}` in canvas-normalized coords.
Bboxes are stored as `{x, y, w, h}` in viewer-normalized coords. So
dragging the viewer carries all bboxes with it (their canvas position
is `viewer.pos + bbox.pos * viewer.size`); dragging a bbox moves it
within the frame while the frame stays put.

Image loading has two paths:
  · `setImage` — full reset. Clear bboxes, reset viewer to fit, reset
    view. Used when the user explicitly loads a new image (pick file,
    import from Krita).
  · `applyCanvasImage` — partial update. Preserve bboxes, reset viewer
    only when image dims change, preserve view. Used by autosync and
    the manual "Refresh @thelayout" button.
"""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

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

export function zoomAt(sx, sy, nz) {
  const r0 = fittedImageRect();
  if (!r0) return;
  const ix = (sx - r0.x0) / r0.s;
  const iy = (sy - r0.y0) / r0.s;
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, nz));
  const r1 = fittedImageRect();
  view.panX += sx - (r1.x0 + ix * r1.s);
  view.panY += sy - (r1.y0 + iy * r1.s);
  clampPan();
}

/* ============================================================
   Board
   ============================================================ */

export const board = {
  imageHash:    null,
  imageWidth:   0,
  imageHeight:  0,
  imageEl:      null,
  imageDataUrl: null,

  // Viewer rectangle: normalized [0, 1] of canvas.
  viewer: {
    x: 0.05, y: 0.05, w: 0.9, h: 0.9,
    model: "PixArtMS_Sigma_XL_2_512",
    ratio: "1.00",
  },

  // Bboxes are normalized [0, 1] OF VIEWER, not of canvas.
  textBboxes:          [],
  characterBboxes:     [],
  referenceEmbeddings: [],

  selectedKind:  null,
  selectedIdx:   -1,
  hoveredKind:   null,
  hoveredIdx:    -1,

  dragActive:    false,
  dragHandle:    null,
  dragKind:      null,
  dragIdx:       -1,
  dragStartBbox: null,
  dragStartIx:   0,
  dragStartIy:   0,

  viewerDragActive:  false,
  viewerDragHandle:  null,
  viewerDragStart:   null,
  viewerDragStartIx: 0,
  viewerDragStartIy: 0,

  mouse: { x: 0, y: 0, inside: false },
};

/* ============================================================
   Viewer aspect / fit helpers
   ============================================================ */

export function viewerAspect() {
  const r = parseFloat(board.viewer.ratio);
  return (isFinite(r) && r > 0) ? r : 1.0;
}

export function resetViewerToFit() {
  const v = board.viewer;
  const r = viewerAspect();
  const cw = board.imageWidth;
  const ch = board.imageHeight;
  if (!cw || !ch) return;

  // Try 90% of the height first; fall back to 90% of width if too wide.
  const targetPxH_fromH = 0.9 * ch;
  const targetPxW_fromH = targetPxH_fromH * r;
  if (targetPxW_fromH <= 0.9 * cw) {
    v.h = 0.9;
    v.w = targetPxW_fromH / cw;
  } else {
    v.w = 0.9;
    v.h = (0.9 * cw) / (r * ch);
  }
  v.x = (1 - v.w) / 2;
  v.y = (1 - v.h) / 2;
  _scheduleDraw();
}

/* ============================================================
   Image loading
   ============================================================ */

export function setImage(hash, dataUrl, width, height) {
  board.imageHash    = hash;
  board.imageDataUrl = dataUrl;
  board.imageWidth   = width;
  board.imageHeight  = height;
  board.imageEl      = null;

  const img = new Image();
  img.onload  = () => { board.imageEl = img;  _scheduleDraw(); };
  img.onerror = () => { board.imageEl = null; _scheduleDraw(); };
  img.src = dataUrl;

  resetView();
  board.textBboxes          = [];
  board.characterBboxes     = [];
  board.referenceEmbeddings = [];
  board.selectedKind = null;
  board.selectedIdx  = -1;
  board.hoveredKind  = null;
  board.hoveredIdx   = -1;
  resetViewerToFit();
  _scheduleDraw();
}

export function applyCanvasImage(hash, dataUrl, width, height) {
  const dimsMatch = (
    board.imageWidth === width && board.imageHeight === height
  );

  board.imageHash    = hash;
  board.imageDataUrl = dataUrl;
  board.imageWidth   = width;
  board.imageHeight  = height;

  const img = new Image();
  img.onload = () => {
    board.imageEl = img;
    if (!dimsMatch) {
      // New aspect/size invalidates viewer position; reset it.
      resetViewerToFit();
    }
    _scheduleDraw();
  };
  img.onerror = () => { board.imageEl = null; _scheduleDraw(); };
  img.src = dataUrl;
  _scheduleDraw();
}

export function clearImage() {
  board.imageHash    = null;
  board.imageDataUrl = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageEl      = null;
  resetView();
  board.textBboxes          = [];
  board.characterBboxes     = [];
  board.referenceEmbeddings = [];
  board.selectedKind = null;
  board.selectedIdx  = -1;
  board.hoveredKind  = null;
  board.hoveredIdx   = -1;
  resetViewerToFit();
  _scheduleDraw();
}

/* ============================================================
   Bbox CRUD
   ============================================================ */

function _listFor(kind) {
  return kind === 'text' ? board.textBboxes : board.characterBboxes;
}

export function addBbox(kind, bbox) {
  const list = _listFor(kind);
  list.push({ ...bbox });
  if (kind === 'character') board.referenceEmbeddings.push(null);
  _scheduleDraw();
  return list.length - 1;
}

export function deleteBbox(kind, idx) {
  const list = _listFor(kind);
  if (idx < 0 || idx >= list.length) return false;
  list.splice(idx, 1);
  if (kind === 'character') board.referenceEmbeddings.splice(idx, 1);
  if (board.selectedKind === kind && board.selectedIdx === idx) {
    board.selectedKind = null;
    board.selectedIdx = -1;
  } else if (board.selectedKind === kind && board.selectedIdx > idx) {
    board.selectedIdx -= 1;
  }
  if (board.hoveredKind === kind && board.hoveredIdx === idx) {
    board.hoveredKind = null;
    board.hoveredIdx = -1;
  } else if (board.hoveredKind === kind && board.hoveredIdx > idx) {
    board.hoveredIdx -= 1;
  }
  _scheduleDraw();
  return true;
}

export function setBbox(kind, idx, bbox) {
  const list = _listFor(kind);
  if (idx < 0 || idx >= list.length) return false;
  list[idx] = { ...bbox };
  _scheduleDraw();
  return true;
}

export function clearAllBboxes() {
  board.textBboxes          = [];
  board.characterBboxes     = [];
  board.referenceEmbeddings = [];
  board.selectedKind = null;
  board.selectedIdx = -1;
  board.hoveredKind = null;
  board.hoveredIdx = -1;
  _scheduleDraw();
}

export function seedDefault() {
  board.textBboxes = [{ x: 0.10, y: 0.02, w: 0.80, h: 0.10 }];
  board.characterBboxes = [{ x: 0.30, y: 0.15, w: 0.40, h: 0.75 }];
  board.referenceEmbeddings = [null];
  board.selectedKind = null;
  board.selectedIdx = -1;
  _scheduleDraw();
}

export function selectBbox(kind, idx) {
  if (kind !== 'text' && kind !== 'character') {
    board.selectedKind = null;
    board.selectedIdx = -1;
  } else {
    board.selectedKind = kind;
    board.selectedIdx = idx;
  }
  _scheduleDraw();
}

/* ============================================================
   Coordinate conversions
   ============================================================ */

/* canvas image pixels → viewer-normalized [0,1] */
export function canvasPxToViewerNorm(cx, cy) {
  const v = board.viewer;
  const vPxX = v.x * board.imageWidth;
  const vPxY = v.y * board.imageHeight;
  const vPxW = v.w * board.imageWidth;
  const vPxH = v.h * board.imageHeight;
  if (!vPxW || !vPxH) return [0, 0];
  return [(cx - vPxX) / vPxW, (cy - vPxY) / vPxH];
}

/* viewer-normalized [0,1] → canvas image pixels */
export function viewerNormToCanvasPx(vnx, vny) {
  const v = board.viewer;
  const vPxX = v.x * board.imageWidth;
  const vPxY = v.y * board.imageHeight;
  const vPxW = v.w * board.imageWidth;
  const vPxH = v.h * board.imageHeight;
  return [vPxX + vnx * vPxW, vPxY + vny * vPxH];
}

/* ============================================================
   Image-fit rect — memoized
   ============================================================ */

let _firKey = null;
let _firVal = null;

export function fittedImageRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (!board.imageWidth || !board.imageHeight) return null;

  const key = cw + "," + ch + "," + board.imageWidth + ","
            + board.imageHeight + "," + view.zoom + ","
            + view.panX + "," + view.panY;
  if (key === _firKey) return _firVal;

  const pad = 40;
  const sx = (cw - 2 * pad) / board.imageWidth;
  const sy = (ch - 2 * pad) / board.imageHeight;
  const s = Math.min(sx, sy) * view.zoom;
  const w = board.imageWidth  * s;
  const h = board.imageHeight * s;
  const x0 = (cw - w) / 2 + view.panX;
  const y0 = (ch - h) / 2 + view.panY;

  _firKey = key;
  _firVal = { x0, y0, w, h, s };
  return _firVal;
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
  const fire = () => {
    _drawScheduled = false;
    if (_drawFn) _drawFn();
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(fire);
  else setTimeout(fire, 16);
}

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