"""Render: plain area, viewer (with image contained inside), frame.

Render order:
  1. Area — plain fill + border.
  2. Viewer interior — darker background, then the fetched image
     scaled to *contain* (letterboxed) inside the viewer rect.
  3. Viewer frame — dashed white outline, corner handles, label.
  4. Area corner handles — drawn last so they sit on top of viewer
     handles when they overlap (viewer touching an area edge).

Area handles use a cyan accent to distinguish them from the white
viewer handles at a glance.  They mirror the viewer handle visual
language (dark halo → accent fill → dark stroke) so they read as
"same family, different role".
"""

VIEW_IMAGE_JS = r"""
import { ctx, board } from "./pg_state.js";

function _drawArea(r) {
  ctx.fillStyle = "rgba(36, 44, 56, 1.0)";
  ctx.fillRect(r.x0, r.y0, r.w, r.h);
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
}

function _drawViewerContent(r) {
  const v = board.viewer;
  const vx0 = r.x0 + v.pxX * r.s;
  const vy0 = r.y0 + v.pxY * r.s;
  const vw  = v.pxW * r.s;
  const vh  = v.pxH * r.s;

  // Viewer interior background (a darker shade of the area fill).
  ctx.fillStyle = "rgba(16, 20, 28, 1.0)";
  ctx.fillRect(vx0, vy0, vw, vh);

  // Image — contain-fit inside the viewer, centred.
  const img = board.areaImageEl;
  if (img) {
    const iw = img.naturalWidth  || img.width  || 0;
    const ih = img.naturalHeight || img.height || 0;
    if (iw > 0 && ih > 0 && vw > 0 && vh > 0) {
      const scale = Math.min(vw / iw, vh / ih);
      const dw = iw * scale;
      const dh = ih * scale;
      const dx = vx0 + (vw - dw) / 2;
      const dy = vy0 + (vh - dh) / 2;

      ctx.save();
      ctx.beginPath();
      ctx.rect(vx0, vy0, vw, vh);
      ctx.clip();
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, dx, dy, dw, dh);
      ctx.restore();
    }
  }
}

function _drawViewerFrame(r) {
  const v = board.viewer;
  const vx0 = r.x0 + v.pxX * r.s;
  const vy0 = r.y0 + v.pxY * r.s;
  const vx1 = r.x0 + (v.pxX + v.pxW) * r.s;
  const vy1 = r.y0 + (v.pxY + v.pxH) * r.s;

  ctx.save();

  // Halo stroke
  ctx.strokeStyle = "rgba(8, 10, 14, 0.85)";
  ctx.lineWidth = 5.5;
  ctx.beginPath();
  ctx.rect(vx0, vy0, vx1 - vx0, vy1 - vy0);
  ctx.stroke();

  // Bright dashed stroke
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
  const label = (board.areaImageW > 0 && board.areaImageH > 0)
    ? "IMAGE " + board.areaImageW + "\u00d7" + board.areaImageH
    : "IMAGE";
  ctx.fillText(label, vx0 + 4, vy0 - 16);

  ctx.restore();
}

/* Area corner handles — cyan accent, drawn AFTER viewer content and
 * frame so they sit on top when the viewer touches an area edge.
 * The handle visual language matches the viewer handles (dark halo,
 * coloured fill, dark stroke) so they read as the same family. */
function _drawAreaHandles(r) {
  const HS = 7;
  const corners = [
    [r.x0,       r.y0],
    [r.x0 + r.w, r.y0],
    [r.x0,       r.y0 + r.h],
    [r.x0 + r.w, r.y0 + r.h],
  ];

  ctx.save();
  for (const [hx, hy] of corners) {
    ctx.fillStyle = "rgba(8, 10, 14, 0.9)";
    ctx.fillRect(hx - HS - 2, hy - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
    ctx.fillStyle = "rgba(120, 220, 255, 0.95)";
    ctx.fillRect(hx - HS, hy - HS, HS * 2, HS * 2);
    ctx.strokeStyle = "rgba(0, 0, 0, 0.9)";
    ctx.lineWidth = 1.2;
    ctx.strokeRect(hx - HS, hy - HS, HS * 2, HS * 2);
  }
  ctx.restore();
}

export function drawImageLayer(r) {
  _drawArea(r);
  _drawViewerContent(r);
  _drawViewerFrame(r);
  _drawAreaHandles(r);
}
"""
