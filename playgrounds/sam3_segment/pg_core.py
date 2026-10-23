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
  imageEl:       null,

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
  masks:               [],   // [{b64, img, w, h, loaded}]
  selectedMaskIndices: [],   // sorted int array
  hoveredMaskIdx:      -1,

  // Runtime
  running:       false,
  lastTiming:    null,
};

/* ============================================================
   Mask palette — one color per mask, cycled by index
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
   Image
   ============================================================ */

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

/* ============================================================
   Points
   ============================================================ */

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
  board.positive = board.points.filter(p =>  p.isPositive)
                                  .map(p => ({x: p.x, y: p.y}));
  board.negative = board.points.filter(p => !p.isPositive)
                                  .map(p => ({x: p.x, y: p.y}));
}

/* ============================================================
   Masks
   ============================================================ */

export function setMasks(b64List) {
  board.masks = b64List.map(b64 => {
    const img = new Image();
    const entry = { b64, img, w: 0, h: 0, loaded: false,
                    _pixels: null, _tinted: null, _tintedKey: null };
    img.onload = () => {
      entry.w = img.width; entry.h = img.height; entry.loaded = true;
      _scheduleDraw();
    };
    img.src = "data:image/png;base64," + b64;
    return entry;
  });
  board.selectedMaskIndices = board.masks.length > 0 ? [0] : [];
  board.hoveredMaskIdx = -1;
  _scheduleDraw();
}

export function clearMasks() {
  board.masks = [];
  board.selectedMaskIndices = [];
  board.hoveredMaskIdx = -1;
  _scheduleDraw();
}

export function isMaskSelected(idx) {
  return board.selectedMaskIndices.indexOf(idx) >= 0;
}

export function toggleMaskSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  const pos = board.selectedMaskIndices.indexOf(idx);
  if (pos >= 0) board.selectedMaskIndices.splice(pos, 1);
  else          board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function addMaskToSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  if (board.selectedMaskIndices.indexOf(idx) >= 0) return;
  board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function replaceMaskSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) {
    clearMaskSelection();
    return;
  }
  board.selectedMaskIndices = [idx];
  _scheduleDraw();
}

export function clearMaskSelection() {
  board.selectedMaskIndices = [];
  _scheduleDraw();
}

export function selectAllMasks() {
  board.selectedMaskIndices = board.masks.map((_, i) => i);
  _scheduleDraw();
}

export function getSelectedMasks() {
  return board.selectedMaskIndices
    .map(i => board.masks[i])
    .filter(m => m && m.loaded);
}

/* ---- pixel access (cached) ---- */

function _maskPixelData(mask) {
  if (mask._pixels) return mask._pixels;
  if (!mask.img || !mask.img.width) return null;
  const off = document.createElement("canvas");
  off.width  = mask.img.width;
  off.height = mask.img.height;
  const octx = off.getContext("2d");
  octx.drawImage(mask.img, 0, 0);
  try {
    mask._pixels = octx.getImageData(0, 0, off.width, off.height).data;
    return mask._pixels;
  } catch (e) {
    return null;
  }
}

export function maskPixelData(mask) {
  return _maskPixelData(mask);
}

export function hitTestMaskAtPoint(ix, iy) {
  const x = Math.floor(ix), y = Math.floor(iy);
  if (x < 0 || y < 0) return -1;
  // Iterate reversed: the last mask in the list wins if multiple
  // overlap. In practice SAM3 returns masks in detection order, so
  // this gives "most recent detection" priority, which is usually
  // what the user wants on Alt+click. Shift+Alt adds instead of
  // replacing, so multiple overlapping masks are reachable.
  for (let i = board.masks.length - 1; i >= 0; i--) {
    const m = board.masks[i];
    if (!m || !m.loaded) continue;
    if (x >= m.img.width || y >= m.img.height) continue;
    const px = _maskPixelData(m);
    if (!px) continue;
    const off = (y * m.img.width + x) * 4;
    if (px[off] >= 128) return i;
  }
  return -1;
}

/* ============================================================
   Image-fit rect
   ============================================================ */

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
