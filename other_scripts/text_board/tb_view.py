"""
tb_view.py — draw the board.

Cards are drawn in `_zSortedIndices()` order.  The card rectangle is
the axis-aligned document-space AABB of the text shape, exactly as the
daemon reports it.  The text label is drawn on top at its own anchor,
rotated to its own angle, as an indication — it is not clipped to the
card.  Word boxes are drawn from their document-space corners.

Before the cards, one dashed rectangle per document is drawn at that
document's canvas extent in board-space coordinates.
"""

VIEW_JS = r"""
/* ==========================================================================
   DOCUMENT BOUNDS
   ========================================================================== */

function _drawDocumentBounds() {
  if (_documentSlots.size === 0) return;

  const sorted = Array.from(_documentSlots.values())
    .sort((a, b) => a.index - b.index);

  const rowW = _slotRowWidth();
  const rowLeft = -rowW / 2;

  let x = rowLeft;
  for (const s of sorted) {
    const sx = x;
    const sy = -(s.docH * _boardScale) / 2;
    const sw = s.docW * _boardScale;
    const sh = s.docH * _boardScale;

    const [x0, y0] = b2s(sx, sy);
    const x1 = x0 + sw * view.zoom;
    const y1 = y0 + sh * view.zoom;

    ctx.save();

    ctx.fillStyle = "rgba(0, 229, 255, 0.025)";
    ctx.fillRect(x0, y0, x1 - x0, y1 - y0);

    ctx.strokeStyle = "rgba(0, 229, 255, 0.35)";
    ctx.lineWidth = 1.2;
    ctx.setLineDash([6, 6]);
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, (x1 - x0) - 1, (y1 - y0) - 1);
    ctx.setLineDash([]);

    if (x1 - x0 > 120) {
      ctx.font = "700 11px 'JetBrains Mono', monospace";
      ctx.textAlign    = "left";
      ctx.textBaseline = "bottom";
      ctx.fillStyle = "rgba(0, 229, 255, 0.55)";
      ctx.fillText(s.docW + " \u00d7 " + s.docH, x0 + 2, y0 - 4);
    }

    ctx.restore();

    x += s.docW * _boardScale + DOCUMENT_GAP;
  }
}

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

/* ==========================================================================
   COLOUR SANITISATION
   ==========================================================================
   The colour Krita reports for a text shape is whatever the SVG carries.
   That is frequently "#000000", occasionally "none", and sometimes a
   named colour.  Canvas silently ignores any value it does not
   understand, which produces a fill or stroke in whatever the previous
   colour was — very often the page background.  Normalise everything
   here so the drawing code never has to think about it. */

function _parseHexColor(c) {
  if (!c || c[0] !== "#") return null;
  const hex = c.slice(1);
  if (hex.length === 3) {
    return {
      r: parseInt(hex[0] + hex[0], 16),
      g: parseInt(hex[1] + hex[1], 16),
      b: parseInt(hex[2] + hex[2], 16),
    };
  }
  if (hex.length === 6) {
    return {
      r: parseInt(hex.slice(0, 2), 16),
      g: parseInt(hex.slice(2, 4), 16),
      b: parseInt(hex.slice(4, 6), 16),
    };
  }
  return null;
}

function _sanitizeTextColor(c, fallback) {
  if (!c || typeof c !== "string") return fallback;
  if (c === "none" || c === "transparent") return fallback;
  if (/^#[0-9a-fA-F]{3}([0-9a-fA-F]{3})?$/.test(c)) return c;
  if (/^rgba?\(/.test(c)) return c;
  return fallback;
}

function _lightenForDarkCard(c, fallback, minLum) {
  const safe = _sanitizeTextColor(c, fallback);
  const rgb = _parseHexColor(safe);
  if (!rgb) return safe;
  const lum = (0.2126 * rgb.r + 0.7152 * rgb.g + 0.0722 * rgb.b) / 255;
  if (lum >= minLum) return safe;
  const k = Math.min(255 / Math.max(rgb.r, 1),
                     Math.min(255 / Math.max(rgb.g, 1),
                              255 / Math.max(rgb.b, 1))) * 0.85 + 0.5;
  const r = Math.min(255, Math.round(rgb.r * k + 40));
  const g = Math.min(255, Math.round(rgb.g * k + 40));
  const b = Math.min(255, Math.round(rgb.b * k + 40));
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

function _drawItemCard(it, isSelected) {
  const hue = it.colorRGB || board.palette[0];

  const [csx, csy] = b2s(it.bx, it.by);
  const sw = it.bw * view.zoom;
  const sh = it.bh * view.zoom;
  const RADIUS = 3;

  // Card body.
  ctx.save();
  ctx.shadowColor   = "rgba(0, 0, 0, 0.55)";
  ctx.shadowBlur    = 16;
  ctx.shadowOffsetY = 4;
  ctx.fillStyle     = "#0e1622";
  ctx.beginPath();
  _roundRect(csx, csy, sw, sh, RADIUS);
  ctx.fill();
  ctx.restore();

  // Border.
  const borderAlpha = isSelected ? 1.00 : 0.90;
  const borderW     = Math.max(1.5, isSelected ? 2.8 : 2.2);
  ctx.strokeStyle = _rgba(hue, borderAlpha);
  ctx.lineWidth   = borderW;
  ctx.beginPath();
  _roundRect(csx + borderW / 2, csy + borderW / 2,
             sw - borderW, sh - borderW, RADIUS);
  ctx.stroke();

  if (isSelected) {
    ctx.save();
    ctx.shadowColor = _rgba(hue, 0.85);
    ctx.shadowBlur  = 14;
    ctx.strokeStyle = _rgba(hue, 0.95);
    ctx.lineWidth   = 1.4;
    ctx.beginPath();
    _roundRect(csx + borderW + 0.5, csy + borderW + 0.5,
               sw - 2 * borderW - 1, sh - 2 * borderW - 1, RADIUS);
    ctx.stroke();
    ctx.restore();
  }

  // Helper: convert a point from the shape's DOCUMENT space to SCREEN.
  // The card is at (it.bx, it.by) in board space; a document point p
  // sits at (it.bx + (p.x - shapeBounds.x) * scale,
  //          it.by + (p.y - shapeBounds.y) * scale).
  const sb = it.shapeBounds;
  const docToScreen = (px, py) => {
    const bx = it.bx + (px - sb.x) * _boardScale;
    const by = it.by + (py - sb.y) * _boardScale;
    return b2s(bx, by);
  };

  // Text label at its document-space anchor, rotated.
  const [ax, ay] = docToScreen(it.x, it.y);
  const fontPx = Math.max(6, (it.fontSize || 12) * _boardScale * view.zoom);

  ctx.save();
  ctx.translate(ax, ay);
  ctx.rotate((it.rotationDeg || 0) * Math.PI / 180);
  ctx.font = fontPx + "px " + (it.fontFamily || "sans-serif");
  ctx.textBaseline = "alphabetic";
  if (it.alignment === "center")      ctx.textAlign = "center";
  else if (it.alignment === "right")  ctx.textAlign = "right";
  else                                ctx.textAlign = "left";
  ctx.fillStyle = _lightenForDarkCard(it.color, "#dce6f2", 0.55);
  ctx.fillText(it.text || "", 0, 0);
  ctx.restore();

  // Word boxes: doc-space quads, mapped through the same helper.
  if (board.showWordBoxes && it.wordBoxes && it.wordBoxes.length) {
    ctx.save();
    ctx.strokeStyle = "rgba(0, 229, 255, 0.85)";
    ctx.lineWidth   = 1.4;
    for (const wb of it.wordBoxes) {
      const corners = wb.corners;
      if (!corners || corners.length < 4) continue;
      ctx.beginPath();
      for (let k = 0; k < 4; k++) {
        const [dx, dy] = docToScreen(corners[k][0], corners[k][1]);
        if (k === 0) ctx.moveTo(dx, dy);
        else         ctx.lineTo(dx, dy);
      }
      ctx.closePath();
      ctx.stroke();
    }
    ctx.restore();
  }

  // Label under the card.
  if (sw > 40) {
    ctx.font = "700 11px 'JetBrains Mono', 'Fira Code', monospace";
    ctx.textAlign    = "left";
    ctx.textBaseline = "top";
    const label = (it.layerName || it.documentName || "?") + " \u00b7 " +
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

  _drawDocumentBounds();
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
