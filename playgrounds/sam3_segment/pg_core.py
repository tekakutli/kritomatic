CORE_JS = r"""
export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export let   dpr    = window.devicePixelRatio || 1;

export const view = { zoom: 1, panX: 0, panY: 0 };

export const OP_LOG_MAX = 20;

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

  regionMode:       false,
  regionTool:       'brush',   // 'brush' | 'lasso'
  regionSize:       30,
  lassoPoints:      [],        // [{x,y}] draft vertices in image coords

  mouse: { x: 0, y: 0, inside: false },   // image coords, for cursor overlay

  masks:               [],
  selectedMaskIndices: [],
  hoveredMaskIdx:      -1,

  opLog:    [],

  running:       false,
  lastTiming:    null,
};

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
  board.stagingPoints = [];
  board.lassoPoints = [];
  board.opLog = [];
}

export function clearImage() {
  board.imageHash = null;
  board.imageDataUrl = null;
  board.imageWidth = 0;
  board.imageHeight = 0;
  board.imageEl = null;
  clearMasks();
  board.stagingPoints = [];
  board.lassoPoints = [];
  board.opLog = [];
}

/* ============================================================
   Mask entries
   ============================================================ */

function _makeMaskEntry(b64, points, textPrompt, regionCanvas) {
  const img = new Image();
  const entry = {
    b64, img, w: 0, h: 0, loaded: false,
    points: points ? points.map(p => ({ x: p.x, y: p.y, isPositive: p.isPositive })) : [],
    textPrompt: textPrompt != null ? textPrompt : "",
    regionCanvas: regionCanvas || null,
    _pixels: null, _tinted: null, _tintedKey: null, _bbox: null,
  };
  img.onload = () => {
    entry.w = img.width; entry.h = img.height; entry.loaded = true;
    _scheduleDraw();
  };
  img.src = "data:image/png;base64," + b64;
  return entry;
}

export function setMasks(b64List) {
  board.masks = b64List.map(b64 => _makeMaskEntry(b64, [], "", null));
  board.selectedMaskIndices = b64List.length > 0 ? [0] : [];
  board.hoveredMaskIdx = -1;
  board.lassoPoints = [];
  _scheduleDraw();
}

export function replaceMaskAt(idx, b64, points) {
  if (idx < 0 || idx >= board.masks.length) return;
  const old = board.masks[idx];
  const prevPoints = (points !== undefined) ? points : old.points;
  board.masks[idx] = _makeMaskEntry(b64, prevPoints, old.textPrompt, old.regionCanvas);
  board.hoveredMaskIdx = -1;
  _scheduleDraw();
}

export function restoreMasks(maskSnapshots, selection) {
  board.masks = maskSnapshots.map(m =>
    _makeMaskEntry(m.b64, m.points, m.textPrompt, null));
  board.selectedMaskIndices = selection.slice();
  board.hoveredMaskIdx = -1;
  board.lassoPoints = [];
  _scheduleDraw();
}

export function clearMasks() {
  board.masks = [];
  board.selectedMaskIndices = [];
  board.hoveredMaskIdx = -1;
  board.lassoPoints = [];
  _scheduleDraw();
}

/* ============================================================
   Selection (each cancels any in-progress lasso draft)
   ============================================================ */

export function isMaskSelected(idx) {
  return board.selectedMaskIndices.indexOf(idx) >= 0;
}

export function toggleMaskSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  board.lassoPoints = [];
  const pos = board.selectedMaskIndices.indexOf(idx);
  if (pos >= 0) board.selectedMaskIndices.splice(pos, 1);
  else          board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function addMaskToSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  if (board.selectedMaskIndices.indexOf(idx) >= 0) return;
  board.lassoPoints = [];
  board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function replaceMaskSelection(idx) {
  board.lassoPoints = [];
  if (idx < 0 || idx >= board.masks.length) {
    board.selectedMaskIndices = [];
  } else {
    board.selectedMaskIndices = [idx];
  }
  _scheduleDraw();
}

export function clearMaskSelection() {
  board.lassoPoints = [];
  board.selectedMaskIndices = [];
  _scheduleDraw();
}

export function selectAllMasks() {
  board.lassoPoints = [];
  board.selectedMaskIndices = board.masks.map((_, i) => i);
  _scheduleDraw();
}

export function selectedMaskIdx() {
  return board.selectedMaskIndices.length === 1
    ? board.selectedMaskIndices[0] : -1;
}

export function selectedMask() {
  const idx = selectedMaskIdx();
  return idx >= 0 ? board.masks[idx] : null;
}

/* ============================================================
   Staging points
   ============================================================ */

export function addStagingPoint(x, y, isPositive) {
  if (x < 0 || y < 0 || x >= board.imageWidth || y >= board.imageHeight) return false;
  board.stagingPoints.push({ x, y, isPositive });
  return true;
}

export function clearStagingPoints() {
  if (board.stagingPoints.length === 0) return false;
  board.stagingPoints = [];
  return true;
}

export function hitTestStagingPoint(ix, iy, tolerancePx) {
  const tol2 = tolerancePx * tolerancePx;
  for (let i = board.stagingPoints.length - 1; i >= 0; i--) {
    const p = board.stagingPoints[i];
    const dx = p.x - ix, dy = p.y - iy;
    if (dx * dx + dy * dy <= tol2) return i;
  }
  return -1;
}

export function removeStagingPoint(idx) {
  if (idx < 0 || idx >= board.stagingPoints.length) return false;
  board.stagingPoints.splice(idx, 1);
  return true;
}

/* ============================================================
   Per-mask points + prompt
   ============================================================ */

export function addPointToSelectedMask(x, y, isPositive) {
  const m = selectedMask();
  if (!m) return false;
  if (x < 0 || y < 0 || x >= board.imageWidth || y >= board.imageHeight) return false;
  m.points.push({ x, y, isPositive });
  return true;
}

export function clearPointsOnSelectedMask() {
  const m = selectedMask();
  if (!m) return false;
  if (m.points.length === 0) return false;
  m.points = [];
  return true;
}

export function hitTestPointOnSelectedMask(ix, iy, tolerancePx) {
  const m = selectedMask();
  if (!m) return -1;
  const tol2 = tolerancePx * tolerancePx;
  for (let i = m.points.length - 1; i >= 0; i--) {
    const p = m.points[i];
    const dx = p.x - ix, dy = p.y - iy;
    if (dx * dx + dy * dy <= tol2) return i;
  }
  return -1;
}

export function removePointFromSelectedMask(idx) {
  const m = selectedMask();
  if (!m) return false;
  if (idx < 0 || idx >= m.points.length) return false;
  m.points.splice(idx, 1);
  return true;
}

export function setMaskPrompt(idx, text) {
  if (idx < 0 || idx >= board.masks.length) return;
  board.masks[idx].textPrompt = String(text || "");
}

/* ============================================================
   Region canvas
   ============================================================ */

export function _ensureRegionCanvas(mask) {
  if (mask.regionCanvas) return mask.regionCanvas;
  const c = document.createElement("canvas");
  c.width = board.imageWidth;
  c.height = board.imageHeight;
  mask.regionCanvas = c;
  return c;
}

export function clearSelectedMaskRegion() {
  const m = selectedMask();
  if (!m || !m.regionCanvas) return false;
  m.regionCanvas = null;
  _scheduleDraw();
  return true;
}

/* Brush stroke — paints or erases a circular disc at (ix, iy) interpolated
   from (lastX, lastY). Does not render; caller calls drawView(). */
export function paintRegionStroke(ix, iy, isErase, lastX, lastY) {
  const m = selectedMask();
  if (!m) return false;
  const c = _ensureRegionCanvas(m);
  const rctx = c.getContext("2d");
  const r = Math.max(1, board.regionSize) / 2;
  const color = isErase ? "rgba(0,0,0,1)" : "rgba(255, 200, 80, 1)";
  rctx.globalCompositeOperation = isErase ? "destination-out" : "source-over";

  const dist = Math.hypot(ix - lastX, iy - lastY);
  const stepPx = Math.max(1, r * 0.5);
  const steps = Math.max(1, Math.ceil(dist / stepPx));
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    const px = lastX + (ix - lastX) * t;
    const py = lastY + (iy - lastY) * t;
    rctx.beginPath();
    rctx.arc(px, py, r, 0, Math.PI * 2);
    rctx.fillStyle = color;
    rctx.fill();
  }
  return true;
}

/* Lasso polygon fill — fills a polygon (or erases it) into the region.
   Does not render; caller calls drawView(). */
export function fillLassoPolygonInRegion(points, isErase) {
  const m = selectedMask();
  if (!m || points.length < 3) return false;
  const c = _ensureRegionCanvas(m);
  const rctx = c.getContext("2d");
  rctx.globalCompositeOperation = isErase ? "destination-out" : "source-over";
  rctx.beginPath();
  rctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) rctx.lineTo(points[i].x, points[i].y);
  rctx.closePath();
  rctx.fillStyle = isErase ? "rgba(0,0,0,1)" : "rgba(255, 200, 80, 1)";
  rctx.fill();
  return true;
}

/* ============================================================
   Pixel access
   ============================================================ */

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
   Bbox
   ============================================================ */

const BBOX_PAD = 4;
const BBOX_MIN_SIZE = 8;
const POINT_BBOX_PAD = 50;

export function computeMaskBBox(idx) {
  if (idx < 0 || idx >= board.masks.length) return null;
  const m = board.masks[idx];
  if (!m || !m.loaded) return null;
  if (m._bbox) return m._bbox;

  const px = _maskPixelData(m);
  if (!px) return null;

  const W = m.img.width, H = m.img.height;
  let minX = W, minY = H, maxX = -1, maxY = -1;
  for (let y = 0; y < H; y++) {
    const rowBase = y * W * 4;
    for (let x = 0; x < W; x++) {
      if (px[rowBase + x * 4] >= 128) {
        if (x < minX) minX = x;
        if (y < minY) minY = y;
        if (x > maxX) maxX = x;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < minX || maxY < minY) { m._bbox = null; return null; }

  minX = Math.max(0, minX - BBOX_PAD);
  minY = Math.max(0, minY - BBOX_PAD);
  maxX = Math.min(W - 1, maxX + BBOX_PAD);
  maxY = Math.min(H - 1, maxY + BBOX_PAD);

  const sx = board.imageWidth  / W;
  const sy = board.imageHeight / H;

  let bx = Math.round(minX * sx);
  let by = Math.round(minY * sy);
  let bw = Math.round((maxX - minX + 1) * sx);
  let bh = Math.round((maxY - minY + 1) * sy);

  if (bw < BBOX_MIN_SIZE) { bx -= Math.ceil((BBOX_MIN_SIZE - bw) / 2); bw = BBOX_MIN_SIZE; }
  if (bh < BBOX_MIN_SIZE) { by -= Math.ceil((BBOX_MIN_SIZE - bh) / 2); bh = BBOX_MIN_SIZE; }

  bx = Math.max(0, Math.min(board.imageWidth  - 1, bx));
  by = Math.max(0, Math.min(board.imageHeight - 1, by));
  bw = Math.max(1, Math.min(board.imageWidth  - bx, bw));
  bh = Math.max(1, Math.min(board.imageHeight - by, bh));

  m._bbox = { x: bx, y: by, w: bw, h: bh };
  return m._bbox;
}

export function computeEffectiveBBox(idx) {
  const bbox = computeMaskBBox(idx);
  const m = idx >= 0 ? board.masks[idx] : null;
  if (!m) return bbox;
  if (!bbox && m.points.length === 0) return null;

  let minX = bbox ? bbox.x : Infinity;
  let minY = bbox ? bbox.y : Infinity;
  let maxX = bbox ? bbox.x + bbox.w : -Infinity;
  let maxY = bbox ? bbox.y + bbox.h : -Infinity;

  for (const p of m.points) {
    if (!p.isPositive) continue;
    if (p.x - POINT_BBOX_PAD < minX) minX = p.x - POINT_BBOX_PAD;
    if (p.y - POINT_BBOX_PAD < minY) minY = p.y - POINT_BBOX_PAD;
    if (p.x + POINT_BBOX_PAD > maxX) maxX = p.x + POINT_BBOX_PAD;
    if (p.y + POINT_BBOX_PAD > maxY) maxY = p.y + POINT_BBOX_PAD;
  }

  if (!isFinite(minX) || !isFinite(minY)) return null;

  minX = Math.max(0, minX);
  minY = Math.max(0, minY);
  maxX = Math.min(board.imageWidth, maxX);
  maxY = Math.min(board.imageHeight, maxY);

  return {
    x: Math.round(minX),
    y: Math.round(minY),
    w: Math.round(maxX - minX),
    h: Math.round(maxY - minY),
  };
}

/* ============================================================
   Op log
   ============================================================ */

export function pushOp(op) {
  board.opLog.push(op);
  if (board.opLog.length > OP_LOG_MAX) board.opLog.shift();
}

export function popOp() {
  return board.opLog.pop() || null;
}

export function snapshotRefine(maskIdx) {
  const m = board.masks[maskIdx];
  if (!m) return null;
  return { type: 'refine', maskIdx, prevB64: m.b64 };
}

export function snapshotSegmentAll() {
  return {
    type: 'segmentAll',
    prevMasks: board.masks.map(m => ({
      b64: m.b64,
      points: m.points.map(p => ({ x: p.x, y: p.y, isPositive: p.isPositive })),
      textPrompt: m.textPrompt || "",
    })),
    prevSelection: board.selectedMaskIndices.slice(),
    prevStagingPoints: board.stagingPoints.map(p =>
      ({ x: p.x, y: p.y, isPositive: p.isPositive })),
  };
}

/* ============================================================
   Region composite
   ============================================================ */

function _loadImageB64(b64) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = "data:image/png;base64," + b64;
  });
}

export async function compositeRefineWithRegion(oldB64, newB64, regionCanvas) {
  if (!regionCanvas) return newB64;
  const W = regionCanvas.width, H = regionCanvas.height;
  if (W !== board.imageWidth || H !== board.imageHeight) return newB64;

  const [oldImg, newImg] = await Promise.all([
    _loadImageB64(oldB64), _loadImageB64(newB64),
  ]);

  const oldCv = document.createElement("canvas");
  oldCv.width = W; oldCv.height = H;
  const oldCx = oldCv.getContext("2d");
  oldCx.drawImage(oldImg, 0, 0, W, H);
  const oldPx = oldCx.getImageData(0, 0, W, H).data;

  const newCv = document.createElement("canvas");
  newCv.width = W; newCv.height = H;
  const newCx = newCv.getContext("2d");
  newCx.drawImage(newImg, 0, 0, W, H);
  const newPx = newCx.getImageData(0, 0, W, H).data;

  const regionCv = document.createElement("canvas");
  regionCv.width = W; regionCv.height = H;
  const regionCx = regionCv.getContext("2d");
  regionCx.drawImage(regionCanvas, 0, 0, W, H);
  const regionPx = regionCx.getImageData(0, 0, W, H).data;

  let anyInRegion = false;
  const out = new Uint8ClampedArray(oldPx.length);
  for (let i = 0; i < out.length; i += 4) {
    if (regionPx[i + 3] > 128) {
      out[i]     = newPx[i];
      out[i + 1] = newPx[i + 1];
      out[i + 2] = newPx[i + 2];
      out[i + 3] = newPx[i + 3];
      anyInRegion = true;
    } else {
      out[i]     = oldPx[i];
      out[i + 1] = oldPx[i + 1];
      out[i + 2] = oldPx[i + 2];
      out[i + 3] = oldPx[i + 3];
    }
  }

  if (!anyInRegion) return oldB64;

  const comp = document.createElement("canvas");
  comp.width = W; comp.height = H;
  const compCx = comp.getContext("2d");
  const imgData = compCx.createImageData(W, H);
  imgData.data.set(out);
  compCx.putImageData(imgData, 0, 0);
  return comp.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
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
