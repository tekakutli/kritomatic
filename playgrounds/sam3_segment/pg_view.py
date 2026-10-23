VIEW_JS = r"""
import {
  ctx, board, fittedImageRect, imageToScreen,
  maskColor, maskPixelData,
} from "./pg_core.js";

/* ============================================================
   Tinted mask overlay — cached per mask per color
   ============================================================ */

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
    const m = px[i] / 255;              // R channel is the mask value
    const a = px[i + 3] / 255;          // preserve original alpha if any
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

/* ============================================================
   Legend
   ============================================================ */

function _drawSelectionLegend(cw) {
  if (board.selectedMaskIndices.length === 0) return;

  const pad = 8;
  const boxW = 92;
  const lineH = 18;
  const boxH = pad * 2 + lineH * board.selectedMaskIndices.length;
  const bx = cw - boxW - 14;
  const by = 14;

  ctx.save();
  ctx.fillStyle = "rgba(10, 14, 20, 0.86)";
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.fillRect(bx, by, boxW, boxH);
  ctx.strokeRect(bx + 0.5, by + 0.5, boxW - 1, boxH - 1);

  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.textBaseline = "middle";
  ctx.textAlign = "left";

  for (let n = 0; n < board.selectedMaskIndices.length; n++) {
    const i = board.selectedMaskIndices[n];
    const c = maskColor(i);
    const y = by + pad + n * lineH + lineH / 2;

    ctx.fillStyle = "rgb(" + c.r + "," + c.g + "," + c.b + ")";
    ctx.fillRect(bx + pad, y - 5, 10, 10);

    ctx.fillStyle = "#a8b5c4";
    ctx.fillText("Mask " + (i + 1), bx + pad + 16, y);
  }
  ctx.restore();
}

/* ============================================================
   View
   ============================================================ */

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
    const m = board.masks[i];
    if (!m || !m.loaded) continue;
    const isSelected = selectedSet.has(i);
    if (!isSelected) continue;

    const tinted = _tintedMask(m, maskColor(i));
    if (!tinted) continue;

    const isHovered = (i === board.hoveredMaskIdx);
    const alpha = isHovered ? 0.55 : 0.40;

    ctx.save();
    ctx.globalAlpha = alpha;
    ctx.drawImage(tinted, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }

  // Points
  for (const p of board.points) {
    const [sx, sy] = imageToScreen(p.x, p.y);
    ctx.beginPath();
    ctx.arc(sx, sy, 6, 0, Math.PI * 2);
    ctx.fillStyle = p.isPositive ? "rgba(0, 229, 255, 0.90)"
                                 : "rgba(255, 90, 105, 0.90)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  _drawSelectionLegend(cw);

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   POINTS " + board.points.length
    + "   \u00b7   MASKS " + board.selectedMaskIndices.length
    + " / " + board.masks.length,
    14, 14,
  );
  ctx.restore();
}
"""
