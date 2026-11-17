"""Controlnets state: image, controlnets, apply nodes, view, draw hook.

Leaf module.  Every other JS module imports from here; nothing here
imports from them.

`board.controlnets` holds the results of "Preprocess images".  Each
entry has shape:

    {
      title:  str,                    // the preprocessor's title
      status: "done" | "error",
      hash:   str,                    // hex digest, for state sync
      dataUrl: str,                   // "data:image/png;base64,..."
      width:  int, height: int,
      el:     HTMLImageElement|null,  // lazily loaded
      error:  str | null,
    }

`board.applyNodes` holds the ControlNetApplyAdvanced entries from
`controlnets_workflow.json`, as returned by `/state`'s `apply_nodes`
field.  Each entry has shape:

    {
      title:              str,       // ControlNetApplyAdvanced's title
      preprocessor_title: str,       // upstream preprocessor's title
      controlnet_name:    str,       // ControlNetLoader's name
      enabled:            bool,
    }

The node iterates its chain (built from the same file) and skips any
entry whose `apply_title` maps to a `false` value in the state's
`apply_enabled` dict.  The panel renders a checkbox per entry and
toggles `enabled`; the next state sync carries the updated dict.

`board.viewingControlnetTag` is the title of the controlnet currently
being viewed, or `null` when `@thecontrolnet` itself is on the canvas.
Preprocessed images are not draggable; clicking a tag swaps the canvas
image, clicking "Back to @thecontrolnet" restores `@thecontrolnet`.
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

export function zoomAt(screenX, screenY, newZoom) {
  const [ix0, iy0] = screenToImage(screenX, screenY);
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));
  const [sx1, sy1] = imageToScreen(ix0, iy0);
  view.panX += screenX - sx1;
  view.panY += screenY - sy1;
  clampPan();
}

export function setImage(hash, dataUrl, width, height) {
  board.imageHash       = hash;
  board.originalDataUrl = dataUrl;
  board.originalWidth   = width;
  board.originalHeight  = height;
  board.originalImageEl = null;

  board.imageEl      = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageDataUrl = null;

  board.controlnets           = [];
  board.viewingControlnetTag  = null;
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
    _scheduleDraw();
  };
  img.onerror = () => {
    board.originalImageEl = null;
    _scheduleDraw();
  };
  img.src = dataUrl;
  _scheduleDraw();
}

export function applyCanvasImage(hash, dataUrl, width, height) {
  const hashChanged = (board.imageHash !== hash);

  board.imageHash       = hash;
  board.originalDataUrl = dataUrl;
  board.originalWidth   = width;
  board.originalHeight  = height;

  if (hashChanged) {
    board.controlnets          = [];
    board.viewingControlnetTag = null;
  }

  const img = new Image();
  img.onload = () => {
    board.originalImageEl = img;
    board.originalWidth   = img.naturalWidth;
    board.originalHeight  = img.naturalHeight;
    board.imageEl         = img;
    board.imageWidth      = img.naturalWidth;
    board.imageHeight     = img.naturalHeight;
    board.imageDataUrl    = dataUrl;
    _scheduleDraw();
  };
  img.onerror = () => _scheduleDraw();
  img.src = dataUrl;
  _scheduleDraw();
}

export function clearImage() {
  board.imageHash       = null;
  board.originalDataUrl = null;
  board.originalWidth   = 0;
  board.originalHeight  = 0;
  board.originalImageEl = null;

  board.imageEl      = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageDataUrl = null;

  board.controlnets          = [];
  board.viewingControlnetTag = null;
  resetView();
  _scheduleDraw();
}

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

  controlnets:          [],
  viewingControlnetTag: null,
  preprocessRunning:    false,

  // ControlNetApplyAdvanced entries from controlnets_workflow.json,
  // populated from `/state`'s apply_nodes field.  See module docstring.
  applyNodes: [],

  lastSync:  null,
  syncError: null,
};

/* ============================================================
   Image-fit rect — memoized
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
   Draw scheduling
   ============================================================ */

let _drawScheduled = false;
let _drawFn = null;
let _drawViewFn = null;

export function _setDraw(fn)     { _drawFn = fn; }
export function _setDrawView(fn) { _drawViewFn = fn; }

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

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;

  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  clampPan();
  if (_drawViewFn) _drawViewFn();
  else if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);
"""