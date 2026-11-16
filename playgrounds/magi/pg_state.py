"""Mutable state, canvas handles, view transforms, draw hook.

Leaf module.

Two view modes:
  · `viewingCharacterIdx === -1` — @thecharacter reference image.
    The scratch rectangle is visible and draggable here.
  · `viewingCharacterIdx >= 0`  — the crop of that character.  Read-
    only view; pan/zoom still work but scratch rect is hidden.

Per-character fields (stored in the `characterBboxes[i]` object):
    x, y, w, h      frozen bbox at analysis time, in @thecharacter
                    normalized coords
    label           user-editable name, browser-only
    crop_b64        base64 PNG of the crop, browser-only
    cropEl          lazily-built <img> for rendering, browser-only

`referenceEmbeddings[i]` stays a parallel array (different type,
doesn't belong in the bbox object).
"""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN,
} from "./pg_tuning.js";

/* ============================================================
   Canvas
   ============================================================ */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   View — zoom + pan only
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
  const [ix0, iy0] = screenToImage(screenX, screenY);
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, newZoom));
  const [sx1, sy1] = imageToScreen(ix0, iy0);
  view.panX += screenX - sx1;
  view.panY += screenY - sy1;
  clampPan();
}

/* ============================================================
   Helpers
   ============================================================ */

function _defaultScratchRect() {
  return { x: 0.30, y: 0.15, w: 0.40, h: 0.75 };
}

function _makeCropImage(b64) {
  const img = new Image();
  img.onload  = () => _scheduleDraw();
  img.onerror = () => _scheduleDraw();
  img.src = "data:image/png;base64," + b64;
  return img;
}

/* ============================================================
   Image loading / clearing
   ============================================================ */

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

  board.characterBboxes     = [];
  board.referenceEmbeddings = [];
  board.viewingCharacterIdx = -1;
  board.scratchRect = _defaultScratchRect();
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
  const oldHash = board.imageHash;
  const hashChanged = (oldHash !== hash);

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

    if (hashChanged) {
      // New image content invalidates every embedding.
      board.referenceEmbeddings = board.characterBboxes.map(() => null);
    }

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

  resetView();
  board.characterBboxes     = [];
  board.referenceEmbeddings = [];
  board.viewingCharacterIdx = -1;
  board.scratchRect = _defaultScratchRect();
  _scheduleDraw();
}

/* ============================================================
   Character CRUD
   ============================================================ */

export function addCharacterBbox(bbox) {
  const b = {
    x: 0, y: 0, w: 0, h: 0,
    label:    "",
    crop_b64: null,
    cropEl:   null,
    ...bbox,
  };
  if (b.crop_b64 && !b.cropEl) {
    b.cropEl = _makeCropImage(b.crop_b64);
  }
  board.characterBboxes.push(b);
  board.referenceEmbeddings.push(null);
  _scheduleDraw();
  return board.characterBboxes.length - 1;
}

export function deleteCharacterBbox(idx) {
  if (idx < 0 || idx >= board.characterBboxes.length) return false;
  board.characterBboxes.splice(idx, 1);
  board.referenceEmbeddings.splice(idx, 1);
  if (board.viewingCharacterIdx === idx) {
    board.viewingCharacterIdx = -1;
  } else if (board.viewingCharacterIdx > idx) {
    board.viewingCharacterIdx -= 1;
  }
  _scheduleDraw();
  return true;
}

/* Used only by the frozen-bbox path during analysis; kept for
   completeness so future features can adjust a character's recorded
   bbox without re-analyzing.  Preserves label / crop / cropEl. */
export function setCharacterBbox(idx, bbox) {
  if (idx < 0 || idx >= board.characterBboxes.length) return false;
  const old = board.characterBboxes[idx];
  board.characterBboxes[idx] = {
    ...old,
    x: bbox.x, y: bbox.y, w: bbox.w, h: bbox.h,
  };
  _scheduleDraw();
  return true;
}

export function clearAllBboxes() {
  board.characterBboxes     = [];
  board.referenceEmbeddings = [];
  board.viewingCharacterIdx = -1;
  board.scratchRect = _defaultScratchRect();
  _scheduleDraw();
}

export function setCharacterLabel(idx, label) {
  if (idx < 0 || idx >= board.characterBboxes.length) return false;
  board.characterBboxes[idx] = {
    ...board.characterBboxes[idx],
    label: String(label || ""),
  };
  return true;
}

/* ============================================================
   Scratch rect
   ============================================================ */

export function setScratchRect(rect) {
  const old = board.scratchRect;
  board.scratchRect = {
    x: rect.x != null ? rect.x : old.x,
    y: rect.y != null ? rect.y : old.y,
    w: rect.w != null ? rect.w : old.w,
    h: rect.h != null ? rect.h : old.h,
  };
  _scheduleDraw();
  return true;
}

export function resetScratchRect() {
  board.scratchRect = _defaultScratchRect();
  _scheduleDraw();
}

/* ============================================================
   Viewing mode
   ============================================================ */

export function setViewingCharacter(idx) {
  if (typeof idx !== "number" || idx < 0) {
    board.viewingCharacterIdx = -1;
  } else if (idx >= board.characterBboxes.length) {
    board.viewingCharacterIdx = -1;
  } else {
    board.viewingCharacterIdx = idx;
  }
  _scheduleDraw();
}

/* ============================================================
   Reference embeddings (parallel array)
   ============================================================ */

export function setReferenceEmbedding(idx, embedding) {
  if (idx < 0 || idx >= board.referenceEmbeddings.length) return false;
  board.referenceEmbeddings[idx] = embedding;
  _scheduleDraw();
  return true;
}

export function setReferenceEmbeddings(embeddings) {
  const out = [];
  for (let i = 0; i < board.characterBboxes.length; i++) {
    const e = (embeddings && i < embeddings.length) ? embeddings[i] : null;
    out.push(e);
  }
  board.referenceEmbeddings = out;
  _scheduleDraw();
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

  characterBboxes:     [],
  referenceEmbeddings: [],

  scratchRect: { x: 0.30, y: 0.15, w: 0.40, h: 0.75 },

  viewingCharacterIdx: -1,

  dragActive:    false,
  dragHandle:    null,
  dragStartRect: null,
  dragStartIx:   0,
  dragStartIy:   0,

  analyzing:    false,
  analyzeError: null,
  lastSync:     null,
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