"""The loaded image.  Nothing on top — this skeleton has no overlays."""

VIEW_IMAGE_JS = r"""
import { ctx, board } from "./pg_state.js";

export function drawImageLayer(r) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
}
"""