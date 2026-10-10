"""
tb_view.py — draw the board.

Cards are drawn in `_zSortedIndices()` order.  Inside each card, the
text is rendered at the shape's own font, and each word's four-corner
polygon is stroked as a faint rectangle (togglable).  The border colour
is derived from the item's format signature, so identically-formatted
shapes share a colour.
"""

VIEW_JS = r"""
/* ==========================================================================
   BACKGROUND GRID
   ========================================================================== */

function _drawBoardGrid() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  let step = 64;
  while (step * view.zoom < 16) step *= 2;
  while (step * view.zoom > 160) step /= 2;

  const startBx = Math.floor((-view.panX / view.zoom) / step) * step;
  const startBy = Math.floor((-view.panY / view.zoom) / step) * step;
  const endBx   = (cw - view.panX) / view.zoom + step;
  const endBy   = (ch - view.panY) / view.zoom + step;

  ctx.save();
  ctx.fillStyle = "rgba(0, 229, 255, 0.10)";
  for (let bx = startBx; bx < endBx; bx += step) {
    for (let by = startBy; by < endBy; by += step) {
      const [sx, sy] = b2s(bx, by);
      ctx.fillRect(sx, sy, 1.1, 1.1);
    }
  }
  ctx.restore();
}

/* ==========================================================================
   CARD
   ========================================================================== */

function _roundRect(x, y, w, h, r) {
  if (w <= 2 * r) r = Math.max(0, w / 2 - 0.01);
  if (h <= 2 * r) r = Math.max(0, h / 2 - 0.01);
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y,     x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x,     y + h, r);
  ctx.arcTo(x,     y + h, x,     y,     r);
  ctx.arcTo(x,     y,     x + w, y,     r);
  ctx.closePath();
}

function _drawItemCard(it, isSelected) {
  const hue = it.colorRGB || board.palette[0];

  const [csx, csy] = b2s(it.bx, it.by);
  const sw = it.bw * view.zoom;
  const sh = it.bh * view.zoom;
  const RADIUS = 3;

  // Card background.
  ctx.save();
  ctx.shadowColor   = "rgba(0, 0, 0, 0.55)";
  ctx.shadowBlur    = 16;
  ctx.shadowOffsetY = 4;
  ctx.fillStyle     = "#0e1622";
  ctx.beginPath();
  _roundRect(csx, csy, sw, sh, RADIUS);
  ctx.fill();
  ctx.restore();

  // Clip to the card so text and word boxes stay inside.
  ctx.save();
  ctx.beginPath();
  _roundRect(csx, csy, sw, sh, RADIUS);
  ctx.clip();

  const sb = it.shapeBounds;
  const scale = sb.w > 0 ? it.bw / sb.w : 1;

  // Text rendering.
  const tx = csx + (it.x - sb.x) * scale * view.zoom;
  const ty = csy + (it.y - sb.y) * scale * view.zoom;
  const fontPx = Math.max(4, it.fontSize * scale * view.zoom);

  ctx.font = fontPx + "px " + (it.fontFamily || "sans-serif");
  ctx.textBaseline = "alphabetic";
  if (it.alignment === "center")      ctx.textAlign = "center";
  else if (it.alignment === "right")  ctx.textAlign = "right";
  else                                ctx.textAlign = "left";

  // If a rotation is present, rotate the text about its anchor.
  const rotRad = (it.rotationDeg || 0) * Math.PI / 180;
  if (Math.abs(rotRad) > 1e-4) {
    ctx.save();
    ctx.translate(tx, ty);
    ctx.rotate(rotRad);
    ctx.fillStyle = it.color || "#dce6f2";
    ctx.fillText(it.text || "", 0, 0);
    ctx.restore();
  } else {
    ctx.fillStyle = it.color || "#dce6f2";
    ctx.fillText(it.text || "", tx, ty);
  }

  // Per-word bounding boxes.
  if (board.showWordBoxes && it.wordBoxes && it.wordBoxes.length) {
    ctx.save();
    ctx.strokeStyle = "rgba(0, 229, 255, 0.55)";
    ctx.lineWidth   = 1.0 / Math.max(view.zoom, 0.3);
    for (const wb of it.wordBoxes) {
      const corners = wb.corners;
      if (!corners || corners.length < 4) continue;
      ctx.beginPath();
      for (let k = 0; k < 4; k++) {
        const dx = csx + (corners[k][0] - sb.x) * scale * view.zoom;
        const dy = csy + (corners[k][1] - sb.y) * scale * view.zoom;
        if (k === 0) ctx.moveTo(dx, dy);
        else         ctx.lineTo(dx, dy);
      }
      ctx.closePath();
      ctx.stroke();
    }
    ctx.restore();
  }

  ctx.restore(); // un-clip

  // Border.
  const alpha = isSelected ? 1.0 : 0.65;
  const width = isSelected ? 2.4 : 1.4;
  ctx.strokeStyle = _rgba(hue, alpha);
  ctx.lineWidth   = width;
  ctx.beginPath();
  _roundRect(csx + 0.5, csy + 0.5, sw - 1, sh - 1, RADIUS);
  ctx.stroke();

  if (isSelected) {
    ctx.save();
    ctx.shadowColor = _rgba(hue, 0.85);
    ctx.shadowBlur  = 14;
    ctx.strokeStyle = _rgba(hue, 0.95);
    ctx.lineWidth   = 2.0;
    ctx.beginPath();
    _roundRect(csx + 0.5, csy + 0.5, sw - 1, sh - 1, RADIUS);
    ctx.stroke();
    ctx.restore();
  }

  // Label under the card.
  if (sw > 60) {
    ctx.font = "700 11px 'JetBrains Mono', 'Fira Code', monospace";
    ctx.textAlign    = "left";
    ctx.textBaseline = "top";
    const label = (it.layerName || it.documentName || "?") + " · " +
                  (it.text || "").slice(0, 40);
    const labelW = ctx.measureText(label).width;
    const padX = 6;
    const lh   = 18;
    const lx   = csx;
    const ly   = csy + sh + 6;

    ctx.fillStyle = "rgba(10, 14, 20, 0.86)";
    ctx.fillRect(lx, ly, labelW + 2 * padX, lh);
    ctx.strokeStyle = _rgba(hue, 0.75);
    ctx.lineWidth = 0.8;
    ctx.strokeRect(lx + 0.4, ly + 0.4, labelW + 2 * padX - 0.8, lh - 0.8);
    ctx.fillStyle = _rgba(hue, 0.95);
    ctx.fillText(label, lx + padX, ly + 4);
  }
}

/* ==========================================================================
   VIEW
   ========================================================================== */

function drawBoardView() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  _drawBoardGrid();

  const order = _zSortedIndices();
  for (const i of order) {
    _drawItemCard(board.items[i], i === board.selectedIdx);
  }

  if (board.items.length === 0) {
    ctx.save();
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign    = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("empty board — press R to pull every text shape from Krita",
                 cw / 2, ch / 2);
    ctx.restore();
  }
}
"""
