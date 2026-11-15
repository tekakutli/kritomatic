"""The loaded image and the bbox overlays."""

VIEW_IMAGE_JS = r"""
import { ctx, board } from "./pg_state.js";
import {
  bboxScreenRect, bboxHandles,
} from "./pg_dispatch.js";

const COLORS = {
  text: {
    normal:  "rgba(120, 220, 255, 0.70)",
    hover:   "rgba(120, 220, 255, 0.90)",
    selected: "rgba(120, 220, 255, 1.00)",
    fill:    "rgba(120, 220, 255, 0.06)",
  },
  character: {
    normal:  "rgba(255, 200, 90, 0.70)",
    hover:   "rgba(255, 200, 90, 0.90)",
    selected: "rgba(255, 200, 90, 1.00)",
    fill:    "rgba(255, 200, 90, 0.06)",
  },
};

function _drawOneBox(kind, idx, bbox, isSelected, isHovered) {
  const r = bboxScreenRect(bbox);
  if (!r) return;
  const pal = COLORS[kind];
  const stroke = isSelected ? pal.selected
              : isHovered  ? pal.hover
              : pal.normal;

  ctx.save();
  ctx.fillStyle = pal.fill;
  ctx.fillRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);

  ctx.strokeStyle = "rgba(8, 10, 14, 0.85)";
  ctx.lineWidth = isSelected ? 6.0 : 3.5;
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  ctx.stroke();

  ctx.strokeStyle = stroke;
  ctx.lineWidth = isSelected ? 2.8 : 2.0;
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  ctx.stroke();

  // Label
  const label = (kind === 'text' ? 'TEXT ' : 'CHAR ') + (idx + 1);
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = stroke;
  ctx.textBaseline = "bottom";
  ctx.textAlign = "left";
  ctx.fillText(label, r.x0 + 3, r.y0 - 3);

  if (isSelected) {
    const hs = bboxHandles(bbox);
    if (hs) {
      for (const h of hs) {
        const HS = 7;
        ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
        ctx.fillRect(h.x - HS - 2, h.y - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
        ctx.fillStyle = stroke;
        ctx.fillRect(h.x - HS, h.y - HS, HS * 2, HS * 2);
        ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
        ctx.lineWidth = 1.5;
        ctx.strokeRect(h.x - HS, h.y - HS, HS * 2, HS * 2);
      }
    }
  }

  ctx.restore();
}

export function drawImageLayer(r) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  for (let i = 0; i < board.textBboxes.length; i++) {
    _drawOneBox('text', i, board.textBboxes[i],
                board.selectedKind === 'text' && board.selectedIdx === i,
                board.hoveredKind === 'text' && board.hoveredIdx === i);
  }
  for (let i = 0; i < board.characterBboxes.length; i++) {
    _drawOneBox('character', i, board.characterBboxes[i],
                board.selectedKind === 'character' && board.selectedIdx === i,
                board.hoveredKind === 'character' && board.hoveredIdx === i);
  }
}
"""