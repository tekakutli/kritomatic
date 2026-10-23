CORE_JS = r"""
export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export let   dpr    = window.devicePixelRatio || 1;

export const view = { zoom: 1, panX: 0, panY: 0 };

export const board = {
  // Image (uploaded)
  imageHash:     null,
  imageWidth:    0,
  imageHeight:   0,
  imageDataUrl:  null,
  imageEl:       null,   // HTMLImageElement, once loaded

  // Prompt
  textPrompt:    "",
  points:        [],     // [{x, y, isPositive}]
  positive:      [],     // [{x, y}]
  negative:      [],

  // Settings
  threshold:        0.5,
  refineIterations: 2,
  individualMasks:  true,

  // Results
  masks:         [],     // [{b64, img, w, h}]
  selectedMaskIdx: -1,

  // Runtime
  running:       false,
  lastTiming:    null,
};

/* ---- image ---- */

export function setImage(hash, dataUrl, width, height) {
  board.imageHash    = hash;
  board.imageDataUrl = dataUrl;
  board.imageWidth   = width;
  board.imageHeight  = height;

  const img = new Image();
  img.onload = () => { board.imageEl = img; _scheduleDraw(); };
  img.onerror = () => { board.imageEl = null; _scheduleDraw(); };
  img.src = dataUrl;

  clearMasks();
  clearPoints();
}

export function clearImage() {
  board.imageHash = null;
  board.imageDataUrl = null;
  board.imageWidth = 0;
  board.imageHeight = 0;
  board.imageEl = null;
  clearPoints();
  clearMasks();
}

/* ---- points ---- */

export function addPoint(x, y, isPositive) {
  if (x < 0 || y < 0 || x >= board.imageWidth || y >= board.imageHeight) return;
  board.points.push({x, y, isPositive});
  _recount();
}

export function clearPoints() {
  board.points = [];
  _recount();
}

function _recount() {
  board.positive = board.points.filter(p => p.isPositive)
                                  .map(p => ({x: p.x, y: p.y}));
  board.negative = board.points.filter(p => !p.isPositive)
                                  .map(p => ({x: p.x, y: p.y}));
}

/* ---- masks ---- */

export function setMasks(b64List) {
  board.masks = b64List.map(b64 => {
    const img = new Image();
    const entry = { b64, img, w: 0, h: 0, loaded: false };
    img.onload = () => {
      entry.w = img.width; entry.h = img.height; entry.loaded = true;
      _scheduleDraw();
    };
    img.src = "data:image/png;base64," + b64;
    return entry;
  });
  board.selectedMaskIdx = board.masks.length > 0 ? 0 : -1;
}

export function clearMasks() {
  board.masks = [];
  board.selectedMaskIdx = -1;
}

export function setSelectedMask(idx) {
  if (idx < -1 || idx >= board.masks.length) return;
  board.selectedMaskIdx = idx;
  _scheduleDraw();
}

export function getSelectedMask() {
  if (board.selectedMaskIdx < 0) return null;
  return board.masks[board.selectedMaskIdx] || null;
}

/* ---- image-fit rect ---- */

export function fittedImageRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (!board.imageWidth || !board.imageHeight) return null;
  const pad = 40;
  const sx = (cw - 2 * pad) / board.imageWidth;
  const sy = (ch - 2 * pad) / board.imageHeight;
  const s  = Math.min(sx, sy, 1.0);
  const w  = board.imageWidth  * s;
  const h  = board.imageHeight * s;
  const x0 = (cw - w) / 2;
  const y0 = (ch - h) / 2;
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

/* ---- draw scheduling ---- */

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

/* ---- resize ---- */

export function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);
"""
