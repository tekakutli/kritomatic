VIEW_JS = r"""
import {
  ctx, board, fittedImageRect, imageToScreen,
  maskColor, maskPixelData, selectedMask, selectedMaskIdx,
} from "./pg_core.js";

function _tintedMask(mask, color) {
  const key = color.r + "," + color.g + "," + color.b;
  if (mask._tinted && mask._tintedKey === key) return mask._tinted;
  const px = maskPixelData(mask);
  if (!px) return null;

  const W = mask.img.width, H = mask.img.height;
  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  const imgData = octx.createImageData(W, H);
  const out = imgData.data;

  for (let i = 0; i < px.length; i += 4) {
    const m = px[i] / 255;
    const a = px[i + 3] / 255;
    out[i]     = color.r;
    out[i + 1] = color.g;
    out[i + 2] = color.b;
    out[i + 3] = Math.round(255 * m * a);
  }
  octx.putImageData(imgData, 0, 0);

  mask._tinted = off;
  mask._tintedKey = key;
  return off;
}

function _drawPoint(x, y, isPositive, filled) {
  const [sx, sy] = imageToScreen(x, y);
  ctx.beginPath();
  ctx.arc(sx, sy, 6, 0, Math.PI * 2);
  if (filled) {
    ctx.fillStyle = isPositive
      ? "rgba(0, 229, 255, 0.92)"
      : "rgba(255, 90, 105, 0.92)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  } else {
    ctx.strokeStyle = isPositive
      ? "rgba(0, 229, 255, 0.70)"
      : "rgba(255, 90, 105, 0.70)";
    ctx.lineWidth = 1.6;
    ctx.setLineDash([3, 2]);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

/* Brush cursor: yellow filled circle + outline + center dot. */
function _drawBrushCursor(r) {
  if (!board.regionMode) return;
  if (board.regionTool !== 'brush') return;
  if (!board.mouse.inside) return;
  if (!selectedMask()) return;

  const [sx, sy] = imageToScreen(board.mouse.x, board.mouse.y);
  const radiusScreen = Math.max(2, (board.regionSize / 2) * r.s);

  ctx.save();
  ctx.beginPath();
  ctx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255, 200, 80, 0.10)";
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 200, 80, 0.92)";
  ctx.lineWidth = 1.5;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(sx, sy, 1.5, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
  ctx.fill();
  ctx.restore();
}

/* Lasso draft: polyline of placed vertices + pending edge to cursor
   + closing preview if >= 3 vertices. First vertex is drawn larger to
   signal "click to close". */
function _drawLassoDraft(r) {
  if (board.lassoPoints.length === 0) return;

  const screenPts = board.lassoPoints.map(p => imageToScreen(p.x, p.y));

  ctx.save();

  // Polyline of placed vertices
  if (screenPts.length > 1) {
    ctx.beginPath();
    ctx.moveTo(screenPts[0][0], screenPts[0][1]);
    for (let i = 1; i < screenPts.length; i++) {
      ctx.lineTo(screenPts[i][0], screenPts[i][1]);
    }
    ctx.strokeStyle = "rgba(255, 200, 80, 0.95)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // Pending edge to cursor
  if (board.mouse.inside) {
    const [sx, sy] = imageToScreen(board.mouse.x, board.mouse.y);
    const last = screenPts[screenPts.length - 1];
    ctx.beginPath();
    ctx.moveTo(last[0], last[1]);
    ctx.lineTo(sx, sy);
    ctx.strokeStyle = "rgba(255, 200, 80, 0.60)";
    ctx.lineWidth = 1.5;
    ctx.setLineDash([4, 3]);
    ctx.stroke();
    ctx.setLineDash([]);

    // Closing preview back to first vertex
    if (screenPts.length >= 3) {
      const first = screenPts[0];
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(first[0], first[1]);
      ctx.strokeStyle = "rgba(255, 200, 80, 0.35)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 4]);
      ctx.stroke();
      ctx.setLineDash([]);
    }
  }

  // Vertex handles
  for (let i = 0; i < screenPts.length; i++) {
    const [px, py] = screenPts[i];
    const isFirst = (i === 0);
    const isLast  = (i === screenPts.length - 1);

    if (isFirst && screenPts.length >= 3) {
      ctx.beginPath();
      ctx.arc(px, py, 8, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 200, 80, 0.85)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 0.95)";
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 3, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
      ctx.fill();
    } else if (isLast) {
      ctx.beginPath();
      ctx.arc(px, py, 5, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 200, 80, 0.90)";
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 0.90)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(px, py, 4, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 200, 80, 0.70)";
      ctx.fill();
    }
  }

  ctx.restore();
}

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;

  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("drop an image to begin", cw / 2, ch / 2);
    return;
  }

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(board.imageEl, r.x0, r.y0, r.w, r.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  // Selected masks (tinted overlay)
  const selectedSet = new Set(board.selectedMaskIndices);
  for (let i = 0; i < board.masks.length; i++) {
    if (!selectedSet.has(i)) continue;
    const m = board.masks[i];
    if (!m || !m.loaded) continue;
    const tinted = _tintedMask(m, maskColor(i));
    if (!tinted) continue;
    const isHovered = (i === board.hoveredMaskIdx);
    ctx.save();
    ctx.globalAlpha = isHovered ? 0.55 : 0.40;
    ctx.drawImage(tinted, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }

  // Region overlay
  const sm = selectedMask();
  if (sm && sm.regionCanvas) {
    ctx.save();
    ctx.globalAlpha = 0.32;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(sm.regionCanvas, r.x0, r.y0, r.w, r.h);
    ctx.restore();
  }

  // Staging points — hollow
  for (const p of board.stagingPoints) {
    _drawPoint(p.x, p.y, p.isPositive, false);
  }

  // Selected-mask points — filled
  if (sm) {
    for (const p of sm.points) {
      _drawPoint(p.x, p.y, p.isPositive, true);
    }
  }

  // Cursor overlays
  _drawBrushCursor(r);
  _drawLassoDraft(r);

  // Header
  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "REFINING MASK " + (selIdx + 1)
             + " via " + board.refineModel.toUpperCase();
    if (sm && sm.regionCanvas) {
      ctxLabel += " \u00b7 REGION (" + board.regionTool + ")";
    }
    if (board.lassoPoints.length > 0) {
      ctxLabel += " \u00b7 LASSO " + board.lassoPoints.length + " pt";
    }
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "STAGING MODE \u2014 place points then Segment All";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   " + ctxLabel
    + "   \u00b7   STAGING " + board.stagingPoints.length
    + "   \u00b7   MASKS " + board.masks.length,
    14, 14,
  );
  ctx.restore();
}
"""
