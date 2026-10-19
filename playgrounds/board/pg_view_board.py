VIEW_BOARD_JS = r"""
import {
  b2s, view, ctx, board, _zSortedIndices, _rgba, _rgbaLight,
  fileState, ensureThumbLoaded,
  CORNER_SIGNS, CARD_HANDLE_R, ROTATE_HANDLE_R, ROTATE_HANDLE_OFFSET,
} from "./pg_core.js";

function _drawBoardGrid() {
  const cw = window.innerWidth, ch = window.innerHeight;
  let step = 64;
  while (step * view.zoom < 16) step *= 2;
  while (step * view.zoom > 160) step /= 2;

  const startBx = Math.floor((-view.panX / view.zoom) / step) * step;
  const startBy = Math.floor((-view.panY / view.zoom) / step) * step;
  const endBx   = (cw - view.panX) / view.zoom + step;
  const endBy   = (ch - view.panY) / view.zoom + step;

  const z = view.zoom, px = view.panX, py = view.panY;
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

function _roundRect(x, y, w, h, r) {
  if (w <= 2 * r) r = Math.max(0, w / 2 - 0.01);
  if (h <= 2 * r) r = Math.max(0, h / 2 - 0.01);
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + w, y, x + w, y + h, r);
  ctx.arcTo(x + w, y + h, x, y + h, r);
  ctx.arcTo(x, y + h, x, y, r);
  ctx.arcTo(x, y, x + w, y, r);
  ctx.closePath();
}

const _CARD_LABEL_FONT = "700 11px 'JetBrains Mono', 'Fira Code', monospace";

function _cardLabelWidth(file, label) {
  if (file._labelText === label && typeof file._labelWidth === "number") {
    return file._labelWidth;
  }
  const w = ctx.measureText(label).width;
  file._labelText = label;
  file._labelWidth = w;
  return w;
}

function _drawFileCard(file, isSelected) {
  const cw = window.innerWidth, ch = window.innerHeight;
  const hue = file.color || board.palette[0];
  const state = fileState(file);

  const cx = file.x + file.w / 2, cy = file.y + file.h / 2;
  const [csx, csy] = b2s(cx, cy);
  const sw = file.w * view.zoom, sh = file.h * view.zoom;
  const RADIUS = 3;

  const halfDiag = Math.hypot(sw, sh) / 2 + 30;
  if (csx + halfDiag < 0 || csx - halfDiag > cw ||
      csy + halfDiag < 0 || csy - halfDiag > ch) return;

  ctx.save();
  ctx.translate(csx, csy);
  ctx.rotate(file.rotation);

  if (isSelected) {
    ctx.save();
    ctx.shadowColor = "rgba(0, 0, 0, 0.55)";
    ctx.shadowBlur = 18; ctx.shadowOffsetY = 4;
    ctx.fillStyle = "#0e1622";
    ctx.beginPath(); _roundRect(-sw/2, -sh/2, sw, sh, RADIUS); ctx.fill();
    ctx.restore();
  } else {
    ctx.fillStyle = "rgba(0, 0, 0, 0.35)";
    ctx.beginPath(); _roundRect(-sw/2 + 2, -sh/2 + 3, sw, sh, RADIUS); ctx.fill();
    ctx.fillStyle = "#0e1622";
    ctx.beginPath(); _roundRect(-sw/2, -sh/2, sw, sh, RADIUS); ctx.fill();
  }

  ctx.save();
  ctx.beginPath(); _roundRect(-sw/2, -sh/2, sw, sh, RADIUS); ctx.clip();
  const img = ensureThumbLoaded(file);
  if (img && img.complete && img.naturalWidth > 0) {
    ctx.globalAlpha = (state === "open") ? 1.00 : (state === "closed") ? 0.55 : 0.20;
    ctx.imageSmoothingEnabled = true; ctx.imageSmoothingQuality = "high";
    ctx.drawImage(img, -sw/2, -sh/2, sw, sh);
    ctx.globalAlpha = 1.0;
  } else {
    ctx.fillStyle = state === "missing" ? "#1a0c10" : "#0e1622";
    ctx.fillRect(-sw/2, -sh/2, sw, sh);
    if (sw > 60 && sh > 20) {
      ctx.fillStyle = "#3d4756"; ctx.font = "700 11px 'JetBrains Mono', monospace";
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(state === "missing" ? "file missing" :
                   file.thumb ? "loading\u2026" : "not loaded", 0, 0);
    }
  }
  ctx.restore();

  let borderColor, borderAlpha, borderW;
  if (state === "missing") {
    borderColor = { r: 212, g: 133, b: 144 };
    borderAlpha = isSelected ? 1.00 : 0.75; borderW = isSelected ? 2.4 : 1.4;
  } else if (state === "closed") {
    borderColor = hue;
    borderAlpha = isSelected ? 0.85 : 0.35; borderW = isSelected ? 2.4 : 1.2;
  } else {
    borderColor = hue;
    borderAlpha = isSelected ? 1.00 : (file.active ? 0.85 : 0.65);
    borderW = isSelected ? 2.4 : 1.4;
  }
  ctx.strokeStyle = _rgba(borderColor, borderAlpha);
  ctx.lineWidth = borderW;
  ctx.beginPath();
  _roundRect(-sw/2 + 0.5, -sh/2 + 0.5, sw - 1, sh - 1, RADIUS); ctx.stroke();

  if (file.active && state === "open") {
    ctx.save();
    ctx.shadowColor = _rgba(hue, 0.85); ctx.shadowBlur = 14;
    ctx.strokeStyle = _rgba(hue, 0.95); ctx.lineWidth = 2.0;
    ctx.beginPath();
    _roundRect(-sw/2 + 0.5, -sh/2 + 0.5, sw - 1, sh - 1, RADIUS); ctx.stroke();
    ctx.restore();
  }

  if (sw > 40 && sh > 24) {
    const ix = sw/2 - 12, iy = -sh/2 + 12;
    if (state === "open") {
      ctx.beginPath(); ctx.arc(ix, iy, 4, 0, Math.PI*2);
      ctx.fillStyle = file.modified ? "#ffc966" : _rgba(hue, 0.95);
      ctx.fill(); ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.2; ctx.stroke();
    } else if (state === "closed") {
      ctx.beginPath(); ctx.arc(ix, iy, 4, 0, Math.PI*2);
      ctx.strokeStyle = _rgba(hue, 0.65); ctx.lineWidth = 1.5; ctx.stroke();
    } else {
      ctx.strokeStyle = "#d48590"; ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(ix-4, iy-4); ctx.lineTo(ix+4, iy+4);
      ctx.moveTo(ix+4, iy-4); ctx.lineTo(ix-4, iy+4); ctx.stroke();
    }
  }

  if (isSelected && sw > 20 && sh > 20) {
    const hw = sw/2, hh = sh/2;
    for (const [sx, sy] of CORNER_SIGNS) {
      ctx.beginPath(); ctx.arc(sx*hw, sy*hh, CARD_HANDLE_R, 0, Math.PI*2);
      ctx.fillStyle = _rgba(hue, 0.95); ctx.fill();
      ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.4; ctx.stroke();
    }
  }

  if (isSelected && sw > 20 && sh > 20) {
    const hy = -sh/2 - ROTATE_HANDLE_OFFSET;
    ctx.beginPath(); ctx.moveTo(0, -sh/2); ctx.lineTo(0, hy + ROTATE_HANDLE_R);
    ctx.strokeStyle = _rgba(hue, 0.55); ctx.lineWidth = 1.2; ctx.stroke();
    ctx.beginPath(); ctx.arc(0, hy, ROTATE_HANDLE_R, 0, Math.PI*2);
    ctx.fillStyle = _rgbaLight(hue); ctx.fill();
    ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.4; ctx.stroke();
    ctx.beginPath();
    ctx.arc(0, hy, ROTATE_HANDLE_R * 0.5, Math.PI * 0.15, Math.PI * 1.85);
    ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.1; ctx.stroke();
  }

  if (sw > 60) {
    ctx.font = _CARD_LABEL_FONT;
    ctx.textAlign = "left"; ctx.textBaseline = "top";
    const label = file.name;
    const labelW = _cardLabelWidth(file, label);
    const padX = 6, lh = 18, lx = -sw/2, ly = sh/2 + 6;
    ctx.fillStyle = "rgba(10, 14, 20, 0.86)";
    ctx.fillRect(lx, ly, labelW + 2*padX, lh);
    ctx.strokeStyle = _rgba(state === "missing" ? {r:212,g:133,b:144} : hue, 0.75);
    ctx.lineWidth = 0.8;
    ctx.strokeRect(lx + 0.4, ly + 0.4, labelW + 2*padX - 0.8, lh - 0.8);
    ctx.fillStyle = state === "missing" ? "rgb(212, 133, 144)" : _rgba(hue, 0.95);
    ctx.fillText(label, lx + padX, ly + 4);
  }

  ctx.restore();
}

export function drawBoardView() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);
  _drawBoardGrid();
  const order = _zSortedIndices();
  for (const i of order) _drawFileCard(board.files[i], i === board.selectedIdx);
  if (board.files.length === 0) {
    ctx.save();
    ctx.fillStyle = "#5a6774"; ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("empty board — click + Add files… or Retrieve open", cw/2, ch/2);
    ctx.restore();
  }
}
"""
