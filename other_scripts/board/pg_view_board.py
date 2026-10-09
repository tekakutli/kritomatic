"""
pg_view_board.py — draw the board.

Everything is drawn in board space and projected to screen through
b2s().  The background grid is drawn at a fixed board-space spacing so
it stays meaningful under zoom, and thinned out when the spacing on
screen becomes too dense to read.

Cards are drawn back-to-front: unselected first, selected last.  Each
card is a filled dark rect, the thumbnail, a hue-coloured border, an
active-glow if the document is active in Krita, a small amber dot if
the document has unsaved changes, and the document name in a small
label just below the card.

A card whose document has no thumbnail (either because the daemon
predates `get_all_document_thumbnails` or because the thumbnail is
still decoding) draws a placeholder label in the card body.  The two
cases are distinguished: "loading…" while a data URL exists and is
being decoded, "no thumbnail" when the daemon returned none at all.
"""

VIEW_BOARD_JS = r"""
/* ==========================================================================
   BACKGROUND GRID
   ========================================================================== */

function _drawBoardGrid() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  // Board-space spacing of the base grid.  When zoomed out far enough
  // that the base spacing would be denser than ~16 screen pixels,
  // step up by powers of two until the visible grid is legible.
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

function _drawDocCard(doc, isSelected) {
  const hue = doc.color || board.palette[0];

  const [sx, sy] = b2s(doc.x, doc.y);
  const sw = doc.w * view.zoom;
  const sh = doc.h * view.zoom;
  const RADIUS = 3;

  // Drop shadow.
  ctx.save();
  ctx.shadowColor = "rgba(0, 0, 0, 0.55)";
  ctx.shadowBlur = 18;
  ctx.shadowOffsetY = 4;
  ctx.fillStyle = "#0e1622";
  ctx.beginPath();
  _roundRect(sx, sy, sw, sh, RADIUS);
  ctx.fill();
  ctx.restore();

  // Thumbnail.  Letterbox if the actual thumbnail image and the card
  // disagree on aspect (only happens if the daemon didn't return
  // dimensions for a document that's still resolving).
  const img = ensureThumbLoaded(doc);
  if (img && img.complete && img.naturalWidth > 0) {
    ctx.save();
    ctx.beginPath();
    _roundRect(sx, sy, sw, sh, RADIUS);
    ctx.clip();
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";

    const imgAR  = img.naturalWidth / img.naturalHeight;
    const cardAR = sw / sh;
    let dx = sx, dy = sy, dw = sw, dh = sh;
    if (imgAR > cardAR) {
      dh = sw / imgAR;
      dy = sy + (sh - dh) / 2;
    } else {
      dw = sh * imgAR;
      dx = sx + (sw - dw) / 2;
    }
    ctx.drawImage(img, dx, dy, dw, dh);
    ctx.restore();
  } else {
    // Placeholder for a doc whose thumbnail is decoding (thumb set,
    // img not yet complete) or was never returned (thumb null).
    ctx.fillStyle = "#0e1622";
    ctx.fillRect(sx, sy, sw, sh);
    if (sw > 60 && sh > 20) {
      ctx.fillStyle = "#3d4756";
      ctx.font = "700 11px 'JetBrains Mono', monospace";
      ctx.textAlign    = "center";
      ctx.textBaseline = "middle";
      const msg = doc.thumb ? "loading\u2026" : "no thumbnail";
      ctx.fillText(msg, sx + sw / 2, sy + sh / 2);
    }
  }

  // Border.
  const strokeAlpha = isSelected ? 1.00 : (doc.active ? 0.85 : 0.55);
  const strokeW     = isSelected ? 2.4  : 1.4;
  ctx.strokeStyle = _rgba(hue, strokeAlpha);
  ctx.lineWidth   = strokeW;
  ctx.beginPath();
  _roundRect(sx + 0.5, sy + 0.5, sw - 1, sh - 1, RADIUS);
  ctx.stroke();

  // Active-document glow.
  if (doc.active) {
    ctx.save();
    ctx.shadowColor = _rgba(hue, 0.85);
    ctx.shadowBlur = 14;
    ctx.strokeStyle = _rgba(hue, 0.95);
    ctx.lineWidth = 2.0;
    ctx.beginPath();
    _roundRect(sx + 0.5, sy + 0.5, sw - 1, sh - 1, RADIUS);
    ctx.stroke();
    ctx.restore();
  }

  // Modified dot.
  if (doc.modified && sw > 30 && sh > 30) {
    ctx.beginPath();
    ctx.arc(sx + sw - 10, sy + 10, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = "#ffc966";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  // Name label below the card.
  if (sw > 60) {
    ctx.save();
    ctx.font = "700 11px 'JetBrains Mono', 'Fira Code', monospace";
    ctx.textAlign    = "left";
    ctx.textBaseline = "top";
    const label = doc.name;
    const labelW = ctx.measureText(label).width;
    const padX = 6;
    const lh   = 18;
    const lx   = sx;
    const ly   = sy + sh + 6;

    ctx.fillStyle = "rgba(10, 14, 20, 0.86)";
    ctx.fillRect(lx, ly, labelW + 2 * padX, lh);
    ctx.strokeStyle = _rgba(hue, 0.75);
    ctx.lineWidth = 0.8;
    ctx.strokeRect(lx + 0.4, ly + 0.4, labelW + 2 * padX - 0.8, lh - 0.8);
    ctx.fillStyle = _rgba(hue, 0.95);
    ctx.fillText(label, lx + padX, ly + 4);
    ctx.restore();
  }
}

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
   VIEW
   ========================================================================== */

function drawBoardView() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  _drawBoardGrid();

  // Non-selected first, selected on top.
  for (let i = 0; i < board.docs.length; i++) {
    if (i === board.selectedIdx) continue;
    _drawDocCard(board.docs[i], false);
  }
  if (board.selectedIdx >= 0 && board.selectedIdx < board.docs.length) {
    _drawDocCard(board.docs[board.selectedIdx], true);
  }

  // Empty-state caption.
  if (board.docs.length === 0) {
    ctx.save();
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign    = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("no documents on the board — press R to pull from Krita",
                 cw / 2, ch / 2);
    ctx.restore();
  }
}
"""
