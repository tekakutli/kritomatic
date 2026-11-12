"""Multimask state: image, areas, view, draw scheduling.

Leaf module.  Every other project module imports from here and
nothing here imports from them.

`applyCanvasImage` is the autosync target — updates the displayed
image in response to a `@thecanvas` spec change without wiping the
area list.  Used by `pg_autosync.js`; NOT called from any
generation path."""

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
  const baseS = Math.min(sx, sy);
  const s = baseS * view.zoom;

  const bw = board.imageWidth * s;
  const bh = board.imageHeight * s;
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

export function zoomAt(screenX, screenY, newZoom) {
  const [ix0, iy0] = screenToImage(screenX, screenY);
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));
  const [sx1, sy1] = imageToScreen(ix0, iy0);
  view.panX += screenX - sx1;
  view.panY += screenY - sy1;
  clampPan();
}

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

export function areaColorById(id) {
  let h = 0;
  for (let i = 0; i < id.length; i++) {
    h = ((h << 5) - h + id.charCodeAt(i)) | 0;
  }
  return MASK_COLORS[Math.abs(h) % MASK_COLORS.length];
}

export function _defaultGlobals() {
  return {
    combine_mode:             "combine",
    use_base_setarea:         false,
    base_area_width:          1024,
    base_area_height:         1024,
    default_strength:         0.9,
    default_set_cond_area:    "default",
    tagger_mode:              "replace",
    tagger_model:             "wd-v1-4-moat-tagger-v2",
    tagger_threshold:         0.35,
    tagger_char_threshold:    0.85,
    bbox:                     true,
    resize:                   true,
    mask_w:                   1024,
    mask_h:                   1024,
    mask_channel:             "red",
  };
}

export const board = {
  imageHash:     null,
  imageWidth:    0,
  imageHeight:   0,
  imageDataUrl:  null,
  imageEl:       null,
  originalImageEl: null,
  originalWidth:   0,
  originalHeight:  0,

  mouse: { x: 0, y: 0, inside: false },

  areas: [],                // [{id, name, prompt, useTagger, mask: entry, ...}]
  selectedAreaId: null,
  hoveredAreaId:  null,
  nextAreaNum:    1,

  globals: _defaultGlobals(),
};

export function setImage(hash, dataUrl, width, height) {
  board.imageHash       = hash;
  board.originalImageEl = null;
  board.originalWidth   = width;
  board.originalHeight  = height;

  board.imageEl      = null;
  board.imageWidth   = 0;
  board.imageHeight  = 0;
  board.imageDataUrl = null;

  board.areas          = [];
  board.selectedAreaId = null;
  board.hoveredAreaId  = null;
  board.nextAreaNum    = 1;

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
  img.onerror = () => _scheduleDraw();
  img.src = dataUrl;
  _scheduleDraw();
}

/* ============================================================
   applyCanvasImage — autosync target
   ============================================================
   Swap the displayed image in response to a `@thecanvas` spec
   change.  Unlike `setImage`, this keeps the area list intact
   (invalidating only each mask's pixel caches so the tinted
   overlays rebuild).  Warns when the new dims differ from the old,
   since area masks may no longer align. */
export function applyCanvasImage(hash, dataUrl, width, height) {
  const oldW = board.imageWidth;
  const oldH = board.imageHeight;
  const dimsMatch = (oldW === 0 || oldH === 0
                     || (width === oldW && height === oldH));

  board.imageHash       = hash;
  board.originalWidth   = width;
  board.originalHeight  = height;

  const img = new Image();
  img.onload = () => {
    board.imageEl         = img;
    board.originalImageEl = img;
    board.originalWidth   = img.naturalWidth;
    board.originalHeight  = img.naturalHeight;
    board.imageWidth      = img.naturalWidth;
    board.imageHeight     = img.naturalHeight;
    board.imageDataUrl    = dataUrl;

    for (const a of board.areas) {
      if (a.mask) {
        a.mask._pixels = null;
        a.mask._tinted = null;
        a.mask._tintedKey = null;
      }
    }

    if (!dimsMatch) {
      console.warn(
        "[multimask] @thecanvas dims changed from "
        + oldW + "\u00d7" + oldH + " to " + width + "\u00d7" + height
        + "; area masks may no longer align"
      );
    }

    _scheduleDraw();
  };
  img.onerror = () => _scheduleDraw();
  img.src = dataUrl;
  _scheduleDraw();
}

export function clearImage() {
  board.imageHash       = null;
  board.originalImageEl = null;
  board.originalWidth   = 0;
  board.originalHeight  = 0;
  board.imageEl         = null;
  board.imageWidth      = 0;
  board.imageHeight     = 0;
  board.imageDataUrl    = null;

  board.areas          = [];
  board.selectedAreaId = null;
  board.hoveredAreaId  = null;
  board.nextAreaNum    = 1;

  resetView();
  _scheduleDraw();
}

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

let _drawScheduled = false;
let _drawFn = null;

export function _setDraw(fn) { _drawFn = fn; }

export function _scheduleDraw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  const fire = () => { _drawScheduled = false; if (_drawFn) _drawFn(); };
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