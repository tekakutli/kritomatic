"""The loaded image and the character bboxes.

No viewer, no dimming, no rotation.  Draws image + character boxes
+ a faint border.  Overlays are drawn on a single canvas (`#c`)
because the magi playground has no separate UI layer."""

VIEW_IMAGE_JS = r"""
import { ctx, board } from "./pg_state.js";
import { bboxScreenRect, bboxHandles } from "./pg_dispatch.js";

const COLORS = {
  normal:  "rgba(255, 200, 90, 0.70)",
  hover:   "rgba(255, 200, 90, 0.90)",
  selected: "rgba(255, 200, 90, 1.00)",
  fill:    "rgba(255, 200, 90, 0.06)",
  analyzed: "rgba(0, 229, 255, 0.85)",
};

function _drawOneBbox(idx, bbox, isSelected, isHovered, hasEmbedding) {
  const r = bboxScreenRect(bbox);
  if (!r) return;

  // Analyzed bboxes get a cyan tint on the halo; unanalyzed are pure
  // yellow.  The distinction is a useful "have I analyzed this one?"
  // indicator that doesn't require reading the row in the panel.
  const baseStroke = hasEmbedding ? COLORS.analyzed
                    : isSelected ? COLORS.selected
                    : isHovered  ? COLORS.hover
                    : COLORS.normal;

  ctx.save();
  ctx.fillStyle = COLORS.fill;
  ctx.fillRect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);

  ctx.strokeStyle = "rgba(8, 10, 14, 0.85)";
  ctx.lineWidth = isSelected ? 6.0 : 3.5;
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  ctx.stroke();

  ctx.strokeStyle = baseStroke;
  ctx.lineWidth = isSelected ? 2.8 : 2.0;
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.x1 - r.x0, r.y1 - r.y0);
  ctx.stroke();

  const label = "CHAR " + (idx + 1);
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = baseStroke;
  ctx.textBaseline = "bottom";
  ctx.textAlign = "left";
  ctx.fillText(label, r.x0 + 3, r.y0 - 3);

  if (hasEmbedding) {
    // Small "✓" after the label so analyzed boxes read at a glance.
    const labelW = ctx.measureText(label).width;
    ctx.fillStyle = COLORS.analyzed;
    ctx.fillText("\u2713", r.x0 + 3 + labelW + 4, r.y0 - 3);
  }

  if (isSelected) {
    const hs = bboxHandles(bbox);
    if (hs) {
      for (const h of hs) {
        const HS = 7;
        ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
        ctx.fillRect(h.x - HS - 2, h.y - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
        ctx.fillStyle = baseStroke;
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

  for (let i = 0; i < board.characterBboxes.length; i++) {
    _drawOneBbox(
      i,
      board.characterBboxes[i],
      board.selectedIdx === i,
      board.hoveredIdx === i,
      board.referenceEmbeddings[i] !== null,
    );
  }

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
}
"""