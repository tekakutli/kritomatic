"""The loaded image (or character crop) and the scratch rectangle.

Two rendering modes driven by `board.viewingCharacterIdx`:

  · canvas view (-1) — draw the @thecharacter image, then the scratch
    rect on top.  Stored character bboxes are NOT rendered — the
    scratch rect is the only draggable/visible overlay.
  · character view  — draw only the character's stored crop,
    letterboxed into the fitted rect.  No overlays.
"""

VIEW_IMAGE_JS = r"""
import { ctx, board } from "./pg_state.js";
import { scratchRectScreen, scratchRectHandles } from "./pg_dispatch.js";

const COLORS = {
  scratchBorder: "rgba(0, 229, 255, 0.90)",
  scratchFill:   "rgba(0, 229, 255, 0.06)",
};

/* ============================================================
   Scratch rect
   ============================================================ */

function _drawScratchRect() {
  const r = scratchRectScreen();
  if (!r) return;
  const w = r.x1 - r.x0, h = r.y1 - r.y0;

  ctx.save();
  ctx.fillStyle = COLORS.scratchFill;
  ctx.fillRect(r.x0, r.y0, w, h);

  // Halo
  ctx.strokeStyle = "rgba(8, 10, 14, 0.85)";
  ctx.lineWidth = 6.0;
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, w, h);
  ctx.stroke();

  // Bright dashed
  ctx.strokeStyle = COLORS.scratchBorder;
  ctx.lineWidth = 2.8;
  ctx.setLineDash([6, 4]);
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, w, h);
  ctx.stroke();
  ctx.setLineDash([]);

  // Handles
  const hs = scratchRectHandles();
  if (hs) {
    for (const hd of hs) {
      const HS = 7;
      ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
      ctx.fillRect(hd.x - HS - 2, hd.y - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
      ctx.fillStyle = COLORS.scratchBorder;
      ctx.fillRect(hd.x - HS, hd.y - HS, HS * 2, HS * 2);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(hd.x - HS, hd.y - HS, HS * 2, HS * 2);
    }
  }

  // Label
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = COLORS.scratchBorder;
  ctx.textBaseline = "bottom";
  ctx.textAlign = "left";
  ctx.fillText("NEW CROP", r.x0 + 3, r.y0 - 3);

  ctx.restore();
}

/* ============================================================
   Canvas view
   ============================================================ */

function _drawCanvasView(r) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  // Scratch rect only — stored character bboxes are not rendered.
  _drawScratchRect();

  // Faint image border
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
}

/* ============================================================
   Character crop view
   ============================================================ */

function _drawCharacterCropView(r, idx) {
  const bbox = board.characterBboxes[idx];
  if (!bbox) return;

  // Dark backdrop
  ctx.fillStyle = "#050810";
  ctx.fillRect(r.x0, r.y0, r.w, r.h);

  const cropEl = bbox.cropEl;
  if (!cropEl || !cropEl.complete || cropEl.naturalWidth === 0) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(
      "(crop not available)",
      r.x0 + r.w / 2, r.y0 + r.h / 2,
    );
    return;
  }

  // Letterbox the crop into the fitted rect while preserving aspect
  const cw = cropEl.naturalWidth;
  const ch = cropEl.naturalHeight;
  const s  = Math.min(r.w / cw, r.h / ch);
  const dw = cw * s;
  const dh = ch * s;
  const dx = r.x0 + (r.w - dw) / 2;
  const dy = r.y0 + (r.h - dh) / 2;

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(cropEl, dx, dy, dw, dh);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(dx + 0.5, dy + 0.5, dw - 1, dh - 1);
}

/* ============================================================
   Public entry point
   ============================================================ */

export function drawImageLayer(r) {
  if (board.viewingCharacterIdx >= 0) {
    _drawCharacterCropView(r, board.viewingCharacterIdx);
  } else {
    _drawCanvasView(r);
  }
}
"""
