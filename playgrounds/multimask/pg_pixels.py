"""Mask pixel access and hit test.  Only the queries multimask
needs — no bbox computation, no rotation caches."""

PIXELS_JS = r"""
import { board } from "./pg_state.js";

function _maskPixelData(mask) {
  if (mask._pixels) return mask._pixels;
  if (!mask.img) return null;
  const w = mask.w, h = mask.h;
  if (!w || !h) return null;
  const off = document.createElement("canvas");
  off.width = w; off.height = h;
  const octx = off.getContext("2d");
  octx.drawImage(mask.img, 0, 0);
  try {
    mask._pixels = octx.getImageData(0, 0, w, h).data;
    return mask._pixels;
  } catch (e) {
    return null;
  }
}

export function maskPixelData(mask) {
  return _maskPixelData(mask);
}

/* Returns the id of the topmost area whose mask has an opaque pixel
   under (ix, iy), or null.  Masks are in their own pixel grid; the
   point is scaled into it, so a mask whose dimensions differ from
   the playground image still hit-tests correctly. */
export function hitTestAreaAtPoint(ix, iy) {
  if (ix < 0 || iy < 0) return null;
  const imgW = board.imageWidth  || 1;
  const imgH = board.imageHeight || 1;
  for (let i = board.areas.length - 1; i >= 0; i--) {
    const a = board.areas[i];
    const m = a.mask;
    if (!m || !m.loaded) continue;
    const mx = Math.floor(ix * m.w / imgW);
    const my = Math.floor(iy * m.h / imgH);
    if (mx < 0 || my < 0 || mx >= m.w || my >= m.h) continue;
    const px = _maskPixelData(m);
    if (!px) continue;
    const off = (my * m.w + mx) * 4;
    if (px[off] >= 128) return a.id;
  }
  return null;
}
"""