"""Mask pixel access, hit test, bbox computation.

Pure queries over the mask image already loaded in the browser.  No
user-visible state is mutated, so no redraw hook is scheduled — the
callers (`pg_comfy_client`, `pg_dispatch`) already run inside a
`requestAnimationFrame` when they care about the visual."""

PIXELS_JS = r"""
import { board } from "./pg_state.js";
import { BBOX_PAD, BBOX_MIN_SIZE, POINT_BBOX_PAD } from "./pg_tuning.js";

/* ============================================================
   Pixel data
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

/* ============================================================
   Hit test
   ============================================================ */

/* Hit test a mask at a point given in image coordinates. Scales the
   point into the mask's own pixel grid so imported masks whose
   dimensions differ from the playground image still hit-test
   correctly. When m.img dimensions match board.image dimensions
   (the common case for Segment All masks) the scale factor is 1.0
   and the arithmetic reduces to the direct lookup it always was. */
export function hitTestMaskAtPoint(ix, iy) {
  if (ix < 0 || iy < 0) return -1;
  const imgW = board.imageWidth  || 1;
  const imgH = board.imageHeight || 1;
  for (let i = board.masks.length - 1; i >= 0; i--) {
    const m = board.masks[i];
    if (!m || !m.loaded) continue;
    const mx = Math.floor(ix * m.img.width  / imgW);
    const my = Math.floor(iy * m.img.height / imgH);
    if (mx < 0 || my < 0 || mx >= m.img.width || my >= m.img.height) continue;
    const px = _maskPixelData(m);
    if (!px) continue;
    const off = (my * m.img.width + mx) * 4;
    if (px[off] >= 128) return i;
  }
  return -1;
}

/* ============================================================
   Bounding boxes
   ============================================================ */

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
"""
