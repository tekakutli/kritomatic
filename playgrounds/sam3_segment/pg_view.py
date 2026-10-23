VIEW_JS = r"""
import {
  ctx, board, fittedImageRect, imageToScreen,
} from "./pg_core.js";

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

  // image
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  // dim border around image
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  // selected mask overlay
  const mask = board.selectedMaskIdx >= 0
    ? board.masks[board.selectedMaskIdx]
    : null;
  if (mask && mask.loaded) {
    ctx.save();
    ctx.globalAlpha = 0.45;
    ctx.globalCompositeOperation = "source-over";
    // Masks returned by SAM3 are "white = selected" over black.
    // Composite them via a color tint: draw as-is, but tint red.
    // A simpler approach is to draw at low alpha; we tint via
    // a colored overlay drawn from the mask's alpha channel.
    // Draw directly; visual result is grayscale overlay, which is
    // legible enough at this scale.
    ctx.drawImage(mask.img, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }

  // points
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

  // header
  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   POINTS " + board.points.length
    + "   \u00b7   MASKS " + board.masks.length,
    14, 14,
  );
  ctx.restore();
}
"""
