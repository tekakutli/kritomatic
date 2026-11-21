"""Outpaint state: area (plain rect + image), viewer (pixel-space rect), view.

The viewer rect is stored in **area pixel coordinates**
(`{pxX, pxY, pxW, pxH, ratio}`).  Area resize does NOT alter the viewer
— it only clamps the viewer's position if the new area would otherwise
not contain it, and clamps the area dims up to at least the viewer size.

The fetched Krita image lives *inside* the viewer (contain-fit,
centred).  The area outside the viewer is a plain filled rectangle.
"""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN, VIEWER_MIN_SIZE_PX,
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
  if (!board.areaWidth || !board.areaHeight) return;
  const cw = window.innerWidth, ch = window.innerHeight;
  const pad = 40;
  const sx = (cw - 2 * pad) / board.areaWidth;
  const sy = (ch - 2 * pad) / board.areaHeight;
  const s = Math.min(sx, sy) * view.zoom;
  const w = board.areaWidth  * s;
  const h = board.areaHeight * s;
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

export const board = {
  areaWidth:  1024,
  areaHeight: 1024,

  // Fetched Krita image — drawn INSIDE the viewer (contain-fit).
  areaImageEl:      null,
  areaImageHash:    null,
  areaImageDataUrl: null,
  areaImageW:       0,
  areaImageH:       0,

  // Viewer: absolute pixel coords in area space.  Aspect locked to
  // `ratio`.  NEVER resized by area-dims changes.
  viewer: {
    pxX: 50, pxY: 50, pxW: 900, pxH: 900,
    ratio: "1.00",
  },

  viewerDragActive:  false,
  viewerDragHandle:  null,
  viewerDragStart:   null,
  viewerDragStartIx: 0,
  viewerDragStartIy: 0,

  mouse: { x: 0, y: 0, inside: false },
};

export function viewerAspect() {
  const r = parseFloat(board.viewer.ratio);
  return (isFinite(r) && r > 0) ? r : 1.0;
}

/* Clamp viewer position (not size) so it fits inside the area. */
export function clampViewerPos() {
  const v = board.viewer;
  v.pxX = Math.max(0, Math.min(board.areaWidth  - v.pxW, v.pxX));
  v.pxY = Math.max(0, Math.min(board.areaHeight - v.pxH, v.pxY));
}

/* Fit the viewer inside the current area at ~90% of the larger
 * fit dimension, aspect-locked and centred.  Called once at boot only;
 * NOT called on area resize (that would violate "area resize must not
 * change viewer proportions"). */
export function resetViewerToFit() {
  const v = board.viewer;
  const r = viewerAspect();
  const cw = board.areaWidth;
  const ch = board.areaHeight;
  if (!cw || !ch) return;

  const maxW = 0.9 * cw;
  const maxH = 0.9 * ch;
  let w = maxW;
  let h = w / r;
  if (h > maxH) {
    h = maxH;
    w = h * r;
  }
  v.pxW = Math.max(VIEWER_MIN_SIZE_PX, w);
  v.pxH = Math.max(VIEWER_MIN_SIZE_PX, h);
  v.pxX = Math.max(0, (cw - v.pxW) / 2);
  v.pxY = Math.max(0, (ch - v.pxH) / 2);
  _scheduleDraw();
}

/* Change ratio → keep the current viewer centre, re-fit width/height
 * inside the old bounding box and clamp to area. */
export function refitViewerAspect() {
  const v = board.viewer;
  const r = viewerAspect();
  const aw = board.areaWidth;
  const ah = board.areaHeight;

  const cx = v.pxX + v.pxW / 2;
  const cy = v.pxY + v.pxH / 2;

  let w = v.pxW;
  let h = w / r;
  if (h > v.pxH) {
    h = v.pxH;
    w = h * r;
  }
  if (w > aw) { w = aw; h = w / r; }
  if (h > ah) { h = ah; w = h * r; }
  w = Math.max(VIEWER_MIN_SIZE_PX, w);
  h = Math.max(VIEWER_MIN_SIZE_PX, h);

  v.pxW = w;
  v.pxH = h;
  v.pxX = Math.max(0, Math.min(aw - w, cx - w / 2));
  v.pxY = Math.max(0, Math.min(ah - h, cy - h / 2));
  _scheduleDraw();
}

/* Change area dims.  Enforces:
 *   · areaW ≥ viewer.pxW     (constraint 4: area ≥ viewer size)
 *   · areaH ≥ viewer.pxH
 *   · viewer.pxX/Y clamped so viewer is fully inside the new area.
 * Does NOT touch viewer.pxW / viewer.pxH (constraint 3).
 * Returns the actual new dims (may be larger than requested). */
export function setAreaDims(w, h) {
  const v = board.viewer;
  const nw = Math.max(
    VIEWER_MIN_SIZE_PX,
    Math.floor(Number.isFinite(w) ? w : board.areaWidth),
    Math.ceil(v.pxW),
  );
  const nh = Math.max(
    VIEWER_MIN_SIZE_PX,
    Math.floor(Number.isFinite(h) ? h : board.areaHeight),
    Math.ceil(v.pxH),
  );
  board.areaWidth  = nw;
  board.areaHeight = nh;
  clampViewerPos();
  clampPan();
  _scheduleDraw();
  return { w: nw, h: nh };
}

export function applyCanvasImage(hash, dataUrl, width, height) {
  board.areaImageHash    = hash;
  board.areaImageDataUrl = dataUrl;
  board.areaImageW       = Math.max(0, Number(width)  || 0);
  board.areaImageH       = Math.max(0, Number(height) || 0);

  const img = new Image();
  img.onload = () => {
    board.areaImageEl = img;
    if (!board.areaImageW) board.areaImageW = img.naturalWidth;
    if (!board.areaImageH) board.areaImageH = img.naturalHeight;
    _scheduleDraw();
  };
  img.onerror = () => { board.areaImageEl = null; _scheduleDraw(); };
  img.src = dataUrl;
  _scheduleDraw();
}

export function clearAreaImage() {
  board.areaImageEl      = null;
  board.areaImageHash    = null;
  board.areaImageDataUrl = null;
  board.areaImageW       = 0;
  board.areaImageH       = 0;
  _scheduleDraw();
}

/* Viewer-normalised → area-pixel.  Kept for compatibility with code
 * that still asks for viewer-normalised coordinates. */
export function canvasPxToViewerNorm(cx, cy) {
  const v = board.viewer;
  if (!v.pxW || !v.pxH) return [0, 0];
  return [(cx - v.pxX) / v.pxW, (cy - v.pxY) / v.pxH];
}

export function viewerNormToCanvasPx(vnx, vny) {
  const v = board.viewer;
  return [v.pxX + vnx * v.pxW, v.pxY + vny * v.pxH];
}

let _firKey = null;
let _firVal = null;

export function fittedImageRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (!board.areaWidth || !board.areaHeight) return null;

  const key = cw + "," + ch + "," + board.areaWidth + ","
            + board.areaHeight + "," + view.zoom + ","
            + view.panX + "," + view.panY;
  if (key === _firKey) return _firVal;

  const pad = 40;
  const sx = (cw - 2 * pad) / board.areaWidth;
  const sy = (ch - 2 * pad) / board.areaHeight;
  const s = Math.min(sx, sy) * view.zoom;
  const w = board.areaWidth  * s;
  const h = board.areaHeight * s;
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