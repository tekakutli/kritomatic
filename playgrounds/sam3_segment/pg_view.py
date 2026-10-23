VIEW_JS = r"""
import {
  ctx, board, fittedImageRect, imageToScreen,
  maskColor, maskPixelData, selectedMask,
} from "./pg_core.js";

function _tintedMask(mask, color) {
  const key = color.r + "," + color.g + "," + color.b;
  if (mask._tinted && mask._tintedKey === key) return mask._tinted;
  const px = maskPixelData(mask);
  if (!px) return null;

  const W = mask.img.width, H = mask.img.height;
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

function _drawPoint(x, y, isPositive, filled) {
  const [sx, sy] = imageToScreen(x, y);
  ctx.beginPath();
  ctx.arc(sx, sy, 6, 0, Math.PI * 2);
  if (filled) {
    ctx.fillStyle = isPositive
      ? "rgba(0, 229, 255, 0.92)"
      : "rgba(255, 90, 105, 0.92)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  } else {
    ctx.strokeStyle = isPositive
      ? "rgba(0, 229, 255, 0.70)"
      : "rgba(255, 90, 105, 0.70)";
    ctx.lineWidth = 1.6;
    ctx.setLineDash([3, 2]);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;

  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("drop an image to begin", cw / 2, ch / 2);
    return;
  }

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  // Selected masks
  const selectedSet = new Set(board.selectedMaskIndices);
  for (let i = 0; i < board.masks.length; i++) {
    if (!selectedSet.has(i)) continue;
    const m = board.masks[i];
    if (!m || !m.loaded) continue;
    const tinted = _tintedMask(m, maskColor(i));
    if (!tinted) continue;
    const isHovered = (i === board.hoveredMaskIdx);
    ctx.save();
    ctx.globalAlpha = isHovered ? 0.55 : 0.40;
    ctx.drawImage(tinted, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }

  // Staging points — hollow rings, always visible when present
  for (const p of board.stagingPoints) {
    _drawPoint(p.x, p.y, p.isPositive, false);
  }

  // Selected mask points — filled circles
  const sm = selectedMask();
  if (sm) {
    for (const p of sm.points) {
      _drawPoint(p.x, p.y, p.isPositive, true);
    }
  }

  // Header
  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "REFINING MASK " + (selIdx + 1)
             + " via " + board.refineModel.toUpperCase();
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "STAGING MODE \u2014 place points then Segment All";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   " + ctxLabel
    + "   \u00b7   STAGING " + board.stagingPoints.length
    + "   \u00b7   MASKS " + board.masks.length,
    14, 14,
  );
  ctx.restore();
}
"""
