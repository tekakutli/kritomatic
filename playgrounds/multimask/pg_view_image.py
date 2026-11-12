"""The loaded image and the tinted overlay for each area's mask."""

VIEW_IMAGE_JS = r"""
import { ctx, board, areaColorById } from "./pg_state.js";
import { maskPixelData } from "./pg_pixels.js";

function _tintedMask(mask, color) {
  const key = color.r + "," + color.g + "," + color.b;
  if (mask._tinted && mask._tintedKey === key) return mask._tinted;

  const px = maskPixelData(mask);
  if (!px) return null;
  const W = mask.w, H = mask.h;
  if (!W || !H) return null;

  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  const imgData = octx.createImageData(W, H);
  const out = imgData.data;

  for (let i = 0; i < px.length; i += 4) {
    const m = px[i] / 255;
    const a = px[i + 3] / 255;
    out[i]     = color.r;
    out[i + 1] = color.g;
    out[i + 2] = color.b;
    out[i + 3] = Math.round(255 * m * a);
  }
  octx.putImageData(imgData, 0, 0);

  mask._tinted = off;
  mask._tintedKey = key;
  return off;
}

export function drawImageLayer(r) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  for (const area of board.areas) {
    const m = area.mask;
    if (!m || !m.loaded) continue;
    const tinted = _tintedMask(m, areaColorById(area.id));
    if (!tinted) continue;
    const isSel   = (area.id === board.selectedAreaId);
    const isHover = (area.id === board.hoveredAreaId);
    let alpha = 0.35;
    if (isSel)   alpha = 0.55;
    if (isHover) alpha = Math.min(0.70, alpha + 0.10);
    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.drawImage(tinted, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }
}
"""