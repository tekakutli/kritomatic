"""The loaded image, the viewer mask, bboxes, and the viewer frame.

Render order:
  1. Image
  2. Dim outside viewer (4 rects in canvas margin + viewer exterior)
  3. Bboxes (inside viewer, so never dimmed)
  4. Viewer outline + corner handles
  5. Image border
"""

VIEW_IMAGE_JS = r"""
import { ctx, board, viewerAspect } from "./pg_state.js";
import { bboxScreenRect, bboxHandles } from "./pg_dispatch.js";

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

function _drawOneBbox(kind, idx, bbox, isSelected, isHovered) {
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

function _dimOutsideViewer(r) {
  const v = board.viewer;
  const vx0 = r.x0 + v.x * r.w;
  const vy0 = r.y0 + v.y * r.h;
  const vx1 = r.x0 + (v.x + v.w) * r.w;
  const vy1 = r.y0 + (v.y + v.h) * r.h;

  ctx.save();
  ctx.fillStyle = "rgba(6, 8, 12, 0.55)";

  // Top strip (canvas area above viewer)
  if (vy0 > r.y0) {
    ctx.fillRect(r.x0, r.y0, r.w, vy0 - r.y0);
  }
  // Bottom strip
  if (vy1 < r.y0 + r.h) {
    ctx.fillRect(r.x0, vy1, r.w, r.y0 + r.h - vy1);
  }
  // Left strip (between viewer top and bottom)
  if (vx0 > r.x0) {
    ctx.fillRect(r.x0, vy0, vx0 - r.x0, vy1 - vy0);
  }
  // Right strip
  if (vx1 < r.x0 + r.w) {
    ctx.fillRect(vx1, vy0, r.x0 + r.w - vx1, vy1 - vy0);
  }
  ctx.restore();
}

function _drawViewerFrame(r) {
  const v = board.viewer;
  const vx0 = r.x0 + v.x * r.w;
  const vy0 = r.y0 + v.y * r.h;
  const vx1 = r.x0 + (v.x + v.w) * r.w;
  const vy1 = r.y0 + (v.y + v.h) * r.h;

  ctx.save();

  // Halo stroke
  ctx.strokeStyle = "rgba(8, 10, 14, 0.85)";
  ctx.lineWidth = 5.5;
  ctx.beginPath();
  ctx.rect(vx0, vy0, vx1 - vx0, vy1 - vy0);
  ctx.stroke();

  // Bright stroke
  ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
  ctx.lineWidth = 2.0;
  ctx.setLineDash([10, 5]);
  ctx.beginPath();
  ctx.rect(vx0, vy0, vx1 - vx0, vy1 - vy0);
  ctx.stroke();
  ctx.setLineDash([]);

  // Corner handles
  const HS = 8;
  const corners = [
    [vx0, vy0], [vx1, vy0],
    [vx0, vy1], [vx1, vy1],
  ];
  for (const [hx, hy] of corners) {
    ctx.fillStyle = "rgba(8, 10, 14, 0.9)";
    ctx.fillRect(hx - HS - 2, hy - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
    ctx.fillStyle = "rgba(255, 255, 255, 1.0)";
    ctx.fillRect(hx - HS, hy - HS, HS * 2, HS * 2);
    ctx.strokeStyle = "rgba(0, 0, 0, 0.9)";
    ctx.lineWidth = 1.2;
    ctx.strokeRect(hx - HS, hy - HS, HS * 2, HS * 2);
  }

  // Frame label
  ctx.font = "700 11px 'JetBrains Mono', monospace";
  ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
  ctx.textBaseline = "top";
  ctx.textAlign = "left";
  const ratio = v.ratio;
  ctx.fillText("FRAME " + ratio + ":1", vx0 + 4, vy0 - 16);

  ctx.restore();
}

export function drawImageLayer(r) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  _dimOutsideViewer(r);

  for (let i = 0; i < board.textBboxes.length; i++) {
    _drawOneBbox('text', i, board.textBboxes[i],
                 board.selectedKind === 'text' && board.selectedIdx === i,
                 board.hoveredKind === 'text' && board.hoveredIdx === i);
  }
  for (let i = 0; i < board.characterBboxes.length; i++) {
    _drawOneBbox('character', i, board.characterBboxes[i],
                 board.selectedKind === 'character' && board.selectedIdx === i,
                 board.hoveredKind === 'character' && board.hoveredIdx === i);
  }

  _drawViewerFrame(r);

  // Faint canvas border on top
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
}
"""