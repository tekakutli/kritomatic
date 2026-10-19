"""
tb_view.py — draw the board.

ES module.  Exports `drawBoardView`, which paints the canvas from the
current board state.  Everything it needs — `board`, `view`, `ctx`,
`_docLayout`, `_zSortedIndices` — is imported from `tb_core.js`; the
only state this file owns is a per-item label-width cache stored on
each item as `_labelText` / `_labelWidth`, which is transient and
never written to disk.

LAYERS, BACK TO FRONT
=====================
    1.  Background fill
    2.  One dashed rectangle per document (the document slot)
    3.  One dot grid, batched into a single path
    4.  Cards, in ascending z-order
    5.  Empty-board caption, if `board.items` is empty

PERFORMANCE NOTES
=================
    VIEWPORT CULLING
        `_drawItemCard` early-returns if the card's screen rect is
        fully outside the viewport (plus a margin for its label).

    SHADOW-ONLY-WHEN-SELECTED
        `shadowBlur` is applied only to the selected card; every
        other card gets a cheap flat offset rect.

    BATCHED GRID
        The dot grid is one `ctx.beginPath()` / many `ctx.rect()` /
        one `ctx.fill()`, not N `fillRect` calls.

    LABEL-WIDTH CACHE
        `ctx.measureText` is only called when the label text has
        changed for that item.

    HOISTED DOC-TO-SCREEN
        Word-box quads are drawn with three precomputed arithmetic
        expressions (`ox`, `oy`, `s`), not a per-corner closure or
        `b2s()` call.  Saves four array allocations per word.
"""

VIEW_JS = r"""
import {
  b2s, view, ctx, board, _zSortedIndices, _rgba, _rgbaLight,
  _docLayout,
} from "./tb_core.js";

/* ==========================================================================
   DOCUMENT BOUNDS
   ========================================================================== */

function _drawDocumentBounds() {
  const layout = _docLayout();
  if (layout.length === 0) return;

  for (const entry of layout) {
    const [x0, y0] = b2s(entry.bx, entry.by);
    const x1 = x0 + entry.bw * view.zoom;
    const y1 = y0 + entry.bh * view.zoom;

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
      ctx.fillText(entry.docW + " \u00d7 " + entry.docH, x0 + 2, y0 - 4);
    }
    ctx.restore();
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

  const z  = view.zoom;
  const px = view.panX;
  const py = view.panY;

  ctx.save();
  ctx.fillStyle = "rgba(0, 229, 255, 0.10)";
  ctx.beginPath();
  for (let bx = startBx; bx < endBx; bx += step) {
    const sx = bx * z + px;
    for (let by = startBy; by < endBy; by += step) {
      ctx.rect(sx, by * z + py, 1.1, 1.1);
    }
  }
  ctx.fill();
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

const _CARD_LABEL_FONT = "700 11px 'JetBrains Mono', 'Fira Code', monospace";

function _cardLabelWidth(it, label) {
  if (it._labelText === label && typeof it._labelWidth === "number") {
    return it._labelWidth;
  }
  const w = ctx.measureText(label).width;
  it._labelText  = label;
  it._labelWidth = w;
  return w;
}

function _drawItemCard(it, isSelected) {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  const [csx, csy] = b2s(it.bx, it.by);
  const sw = it.bw * view.zoom;
  const sh = it.bh * view.zoom;

  const CULL_LABEL_MARGIN = 26;
  if (csx + sw < 0 || csy + sh + CULL_LABEL_MARGIN < 0 ||
      csx > cw || csy > ch) {
    return;
  }

  const hue = it.colorRGB || board.palette[0];
  const RADIUS = 3;

  if (isSelected) {
    ctx.save();
    ctx.shadowColor   = "rgba(0, 0, 0, 0.55)";
    ctx.shadowBlur    = 16;
    ctx.shadowOffsetY = 4;
    ctx.fillStyle     = "#0e1622";
    ctx.beginPath();
    _roundRect(csx, csy, sw, sh, RADIUS);
    ctx.fill();
    ctx.restore();
  } else {
    ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
    ctx.beginPath();
    _roundRect(csx + 2, csy + 3, sw, sh, RADIUS);
    ctx.fill();
    ctx.fillStyle = "#0e1622";
    ctx.beginPath();
    _roundRect(csx, csy, sw, sh, RADIUS);
    ctx.fill();
  }

  const borderAlpha = isSelected ? 0.95 : 0.30;
  const borderW     = Math.max(1.0, isSelected ? 2.4 : 1.1);
  ctx.strokeStyle = _rgba(hue, borderAlpha);
  ctx.lineWidth   = borderW;
  ctx.beginPath();
  _roundRect(csx + borderW / 2, csy + borderW / 2,
             sw - borderW, sh - borderW, RADIUS);
  ctx.stroke();

  if (isSelected) {
    ctx.save();
    ctx.shadowColor = _rgba(hue, 0.75);
    ctx.shadowBlur  = 12;
    ctx.strokeStyle = _rgba(hue, 0.90);
    ctx.lineWidth   = 1.2;
    ctx.beginPath();
    _roundRect(csx + borderW + 0.5, csy + borderW + 0.5,
               sw - 2 * borderW - 1, sh - 2 * borderW - 1, RADIUS);
    ctx.stroke();
    ctx.restore();
  }

  if (board.showWordBoxes && it.wordBoxes && it.wordBoxes.length) {
    const sb = it.shapeBounds;
    const s  = board.scale * view.zoom;
    const ox = (it.bx - sb.x * board.scale) * view.zoom + view.panX;
    const oy = (it.by - sb.y * board.scale) * view.zoom + view.panY;

    ctx.save();
    ctx.strokeStyle = "rgba(0, 229, 255, 0.85)";
    ctx.lineWidth   = 1.4;
    for (const wb of it.wordBoxes) {
      const corners = wb.corners;
      if (!corners || corners.length < 4) continue;
      ctx.beginPath();
      for (let k = 0; k < 4; k++) {
        const dx = ox + corners[k][0] * s;
        const dy = oy + corners[k][1] * s;
        if (k === 0) ctx.moveTo(dx, dy);
        else         ctx.lineTo(dx, dy);
      }
      ctx.closePath();
      ctx.stroke();
    }
    ctx.restore();
  }

  if (sw > 40) {
    ctx.font = _CARD_LABEL_FONT;
    ctx.textAlign    = "left";
    ctx.textBaseline = "top";
    const label = (it.layerName || it.documentName || "?") + " \u00b7 " +
                  (it.text || "").slice(0, 40);
    const labelW = _cardLabelWidth(it, label);
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

export function drawBoardView() {
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
