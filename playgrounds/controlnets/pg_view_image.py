"""The loaded image, or the currently-viewed preprocessed image.

When `board.viewingControlnetTag` is set, the matching controlnet's
preprocessed image is drawn letterboxed into the fit rect (its aspect
may differ from `@thecontrolnet`).  Otherwise `@thecontrolnet` fills
the rect.  No bboxes, no interaction — this skeleton has no
draggable overlays.
"""

VIEW_IMAGE_JS = r"""
import { ctx, board } from "./pg_state.js";

function _drawLetterboxed(img, r) {
  const cw = img.naturalWidth  || img.width;
  const ch = img.naturalHeight || img.height;
  if (!cw || !ch) return;

  // Dark backdrop — preprocessed images may not match the source
  // aspect, so their letterbox region is visibly distinct.
  ctx.fillStyle = "#050810";
  ctx.fillRect(r.x0, r.y0, r.w, r.h);

  const s = Math.min(r.w / cw, r.h / ch);
  const dw = cw * s;
  const dh = ch * s;
  const dx = r.x0 + (r.w - dw) / 2;
  const dy = r.y0 + (r.h - dh) / 2;

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, dx, dy, dw, dh);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(dx + 0.5, dy + 0.5, dw - 1, dh - 1);
}

function _drawFill(img, r) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
}

export function drawImageLayer(r) {
  const tag = board.viewingControlnetTag;
  if (tag) {
    const entry = board.controlnets.find(c => c.title === tag);
    if (entry && entry.status === "done" && entry.el) {
      _drawLetterboxed(entry.el, r);
      return;
    }
  }

  if (board.imageEl) {
    _drawFill(board.imageEl, r);
  }
}
"""