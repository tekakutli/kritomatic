VIEW_JS = r"""
import {
  ctx, board, view, fittedImageRect, imageToScreen,
  maskColor, maskPixelData, selectedMask, selectedMaskIdx,
  contextRectScreen, contextHandles,
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

/* ============================================================
   Live region preview — inverted image under the region mask
   ============================================================ */

let _invertedImageCache    = null;
let _invertedImageKey      = null;
let _strokeCompositeCanvas = null;

function _getInvertedImage() {
  if (!board.imageEl) return null;
  const key = board.imageHash || "none";
  if (_invertedImageCache && _invertedImageKey === key) {
    return _invertedImageCache;
  }

  const W = board.imageWidth, H = board.imageHeight;
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  const cx = cv.getContext("2d");

  if (typeof cx.filter === "string") {
    cx.filter = "invert(1)";
    cx.drawImage(board.imageEl, 0, 0, W, H);
    cx.filter = "none";
  } else {
    cx.drawImage(board.imageEl, 0, 0, W, H);
    const id = cx.getImageData(0, 0, W, H);
    const d  = id.data;
    for (let i = 0; i < d.length; i += 4) {
      d[i]     = 255 - d[i];
      d[i + 1] = 255 - d[i + 1];
      d[i + 2] = 255 - d[i + 2];
    }
    cx.putImageData(id, 0, 0);
  }

  _invertedImageCache = cv;
  _invertedImageKey   = key;
  return cv;
}

function _getStrokeCompositeCanvas(W, H) {
  if (_strokeCompositeCanvas &&
      _strokeCompositeCanvas.width  === W &&
      _strokeCompositeCanvas.height === H) {
    return _strokeCompositeCanvas;
  }
  const cv = document.createElement("canvas");
  cv.width = W; cv.height = H;
  _strokeCompositeCanvas = cv;
  return cv;
}

function _drawLiveRegionStroke(r, sm) {
  const W = sm.regionCanvas.width;
  const H = sm.regionCanvas.height;

  const inverted = _getInvertedImage();
  if (!inverted) {
    ctx.save();
    ctx.globalAlpha = 0.45;
    ctx.globalCompositeOperation = "screen";
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(sm.regionCanvas, r.x0, r.y0, r.w, r.h);
    ctx.restore();
    return;
  }

  const comp = _getStrokeCompositeCanvas(W, H);
  const cctx = comp.getContext("2d");

  cctx.globalCompositeOperation = "source-over";
  cctx.clearRect(0, 0, W, H);
  cctx.drawImage(inverted, 0, 0);

  cctx.globalCompositeOperation = "destination-in";
  cctx.drawImage(sm.regionCanvas, 0, 0);

  if (board.regionStrokeIsErase) {
    cctx.globalCompositeOperation = "source-atop";
    cctx.fillStyle = "rgba(255, 60, 90, 0.30)";
    cctx.fillRect(0, 0, W, H);
  }

  cctx.globalCompositeOperation = "source-over";
  ctx.save();
  ctx.imageSmoothingEnabled = false;
  ctx.drawImage(comp, r.x0, r.y0, r.w, r.h);
  ctx.restore();
}

/* ============================================================
   Region edge (cached per mask, invalidated on stroke end)
   ============================================================ */

function _computeRegionEdgePoints(mask) {
  if (!mask.regionCanvas) return null;
  if (mask._regionEdgePoints !== null && mask._regionEdgePoints !== undefined) {
    return mask._regionEdgePoints;
  }

  const W = mask.regionCanvas.width;
  const H = mask.regionCanvas.height;
  const rctx = mask.regionCanvas.getContext("2d");
  let regionPx;
  try { regionPx = rctx.getImageData(0, 0, W, H).data; }
  catch (e) { return null; }

  const pts = [];
  for (let y = 0; y < H; y++) {
    const rowBase = y * W * 4;
    for (let x = 0; x < W; x++) {
      const iA = rowBase + x * 4 + 3;
      if (regionPx[iA] < 128) continue;

      const leftA  = x > 0     ? regionPx[iA - 4]     : 0;
      const rightA = x < W - 1 ? regionPx[iA + 4]     : 0;
      const upA    = y > 0     ? regionPx[iA - W * 4] : 0;
      const downA  = y < H - 1 ? regionPx[iA + W * 4] : 0;
      if (leftA >= 128 && rightA >= 128 && upA >= 128 && downA >= 128) continue;

      pts.push(x, y);
    }
  }

  mask._regionEdgePoints = pts;
  return pts;
}

/* ============================================================
   Boundary tracing (even-spacing support)
   ============================================================ */

function _traceBoundaryChains(edgeSet, W) {
  const visited = new Set();
  const chains = [];

  const nbrs8 = [
    [ 1, 0], [ 1, 1], [ 0, 1], [-1, 1],
    [-1, 0], [-1,-1], [ 0,-1], [ 1,-1],
  ];

  const getNeighbors = (key) => {
    const x = key % W, y = (key - x) / W;
    const out = [];
    for (const [dx, dy] of nbrs8) {
      const nx = x + dx, ny = y + dy;
      if (nx < 0 || ny < 0) continue;
      const nk = ny * W + nx;
      if (edgeSet.has(nk)) out.push({ key: nk, dx, dy });
    }
    return out;
  };

  const deg = (key) => getNeighbors(key).length;

  const starts = [];
  const seen = new Set();
  for (const key of edgeSet) {
    if (deg(key) <= 1) { starts.push(key); seen.add(key); }
  }
  for (const key of edgeSet) {
    if (!seen.has(key)) { starts.push(key); seen.add(key); }
  }

  for (const startKey of starts) {
    if (visited.has(startKey)) continue;

    const chain = [];
    let key = startKey;
    let prevDX = 0, prevDY = 0;

    while (key !== undefined && !visited.has(key)) {
      visited.add(key);
      const x = key % W, y = (key - x) / W;
      chain.push(x, y);

      const cands = [];
      for (const n of getNeighbors(key)) {
        if (visited.has(n.key)) continue;
        const dot = n.dx * prevDX + n.dy * prevDY;
        const cross = Math.abs(n.dx * prevDY - n.dy * prevDX);
        cands.push({ ...n, dot, cross });
      }
      if (cands.length === 0) break;

      cands.sort((a, b) => {
        if (a.dot !== b.dot) return b.dot - a.dot;
        return a.cross - b.cross;
      });

      const next = cands[0];
      key = next.key;
      prevDX = next.dx;
      prevDY = next.dy;
    }

    if (chain.length >= 4) {
      const fx = chain[0], fy = chain[1];
      const lx = chain[chain.length - 2], ly = chain[chain.length - 1];
      const dx = fx - lx, dy = fy - ly;
      if (dx * dx + dy * dy <= 2) {
        chain.push(fx, fy);
      }
      chains.push(chain);
    }
  }

  return chains;
}

function _resamplePolyline(chain, spacing) {
  if (chain.length < 4) return [];

  const out = [];
  out.push([chain[0], chain[1]]);

  let toNext = spacing;
  let ax = chain[0], ay = chain[1];

  for (let i = 2; i < chain.length; i += 2) {
    const bx = chain[i], by = chain[i + 1];
    let segLen = Math.hypot(bx - ax, by - ay);

    while (segLen >= toNext) {
      const t = toNext / segLen;
      const nx = ax + (bx - ax) * t;
      const ny = ay + (by - ay) * t;
      out.push([nx, ny]);
      ax = nx; ay = ny;
      segLen -= toNext;
      toNext = spacing;
    }
    toNext -= segLen;
    ax = bx; ay = by;
  }

  return out;
}

/* ============================================================
   Region edge — evenly spaced dots
   ============================================================ */

function _drawRegionEdge(r) {
  const sm = selectedMask();
  if (!sm || !sm.regionCanvas) return;

  if (!sm._regionChains) {
    const edgePts = _computeRegionEdgePoints(sm);
    if (!edgePts) return;
    const W = sm.regionCanvas.width;
    const edgeSet = new Set();
    for (let i = 0; i < edgePts.length; i += 2) {
      edgeSet.add(edgePts[i + 1] * W + edgePts[i]);
    }
    sm._regionChains = edgeSet.size > 0
      ? _traceBoundaryChains(edgeSet, W)
      : [];
  }

  const chains = sm._regionChains;
  if (!chains || chains.length === 0) return;

  const SPACING_SCREEN = 8;
  const spacingImg = SPACING_SCREEN / r.s;

  const dots = [];
  for (const chain of chains) {
    const resampled = _resamplePolyline(chain, spacingImg);
    for (const [dx, dy] of resampled) dots.push(dx, dy);
  }
  if (dots.length === 0) return;

  const halfB = 2, halfD = 2.5, offD = 1;

  ctx.save();
  ctx.fillStyle = "rgba(10, 10, 10, 0.75)";
  for (let i = 0; i < dots.length; i += 2) {
    const sx = Math.round(r.x0 + dots[i] * r.s);
    const sy = Math.round(r.y0 + dots[i + 1] * r.s);
    ctx.fillRect(sx - halfD + offD, sy - halfD + offD,
                 halfD * 2, halfD * 2);
  }
  ctx.restore();

  ctx.save();
  ctx.fillStyle = "rgba(255, 235, 130, 1.0)";
  for (let i = 0; i < dots.length; i += 2) {
    const sx = Math.round(r.x0 + dots[i] * r.s);
    const sy = Math.round(r.y0 + dots[i + 1] * r.s);
    ctx.fillRect(sx - halfB, sy - halfB, halfB * 2, halfB * 2);
  }
  ctx.restore();
}

/* ============================================================
   Brush cursor — anchored during resize, halo + core ring
   ============================================================ */

function _drawBrushCursor(r) {
  if (!board.regionMode) return;
  if (board.regionTool !== 'brush') return;
  if (!selectedMask()) return;

  const anchor = board.resizeAnchor;
  const cx = anchor ? anchor.x : board.mouse.x;
  const cy = anchor ? anchor.y : board.mouse.y;

  if (!anchor && !board.mouse.inside) return;

  const [sx, sy] = imageToScreen(cx, cy);
  const radiusScreen = Math.max(2, (board.regionSize / 2) * r.s);

  const fillColor = board.regionSubtract
    ? "rgba(255, 130, 150, 0.10)"
    : "rgba(255, 200, 80, 0.10)";
  const ringColor = board.regionSubtract
    ? "rgba(255, 130, 150, 1.0)"
    : "rgba(255, 200, 80, 1.0)";
  const anchorColor = board.regionSubtract
    ? "rgba(255, 160, 180, 1.0)"
    : "rgba(255, 240, 120, 1.0)";

  const coreWidth  = anchor ? 2.2 : 1.8;
  const haloWidth  = coreWidth + 3.2;

  ctx.save();

  ctx.beginPath();
  ctx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  ctx.fillStyle = fillColor;
  ctx.fill();

  ctx.beginPath();
  ctx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(8, 10, 14, 0.82)";
  ctx.lineWidth = haloWidth;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  ctx.strokeStyle = anchor ? anchorColor : ringColor;
  ctx.lineWidth = coreWidth;
  ctx.stroke();

  ctx.beginPath();
  ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(sx, sy, 1.5, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(255, 255, 255, 0.95)";
  ctx.fill();

  ctx.restore();
}

/* ============================================================
   Lasso draft
   ============================================================ */

function _haloStroke(drawPath, brightColor, width, dash) {
  const dashes = dash || [];

  ctx.beginPath();
  drawPath();
  ctx.strokeStyle = "rgba(8, 10, 14, 0.82)";
  ctx.lineWidth = width + 3.2;
  ctx.setLineDash(dashes);
  ctx.stroke();

  ctx.beginPath();
  drawPath();
  ctx.strokeStyle = brightColor;
  ctx.lineWidth = width;
  ctx.setLineDash(dashes);
  ctx.stroke();
  ctx.setLineDash([]);
}

function _drawLassoDraft() {
  if (board.lassoPoints.length === 0) return;

  const screenPts = board.lassoPoints.map(p => imageToScreen(p.x, p.y));

  const bright      = board.regionSubtract
    ? "rgba(255, 130, 150, 1.0)"
    : "rgba(255, 220, 100, 1.0)";
  const brightSoft  = board.regionSubtract
    ? "rgba(255, 130, 150, 0.90)"
    : "rgba(255, 220, 100, 0.90)";
  const brightFaint = board.regionSubtract
    ? "rgba(255, 130, 150, 0.55)"
    : "rgba(255, 220, 100, 0.55)";
  const vertexColor = board.regionSubtract
    ? "rgba(255, 130, 150, 0.95)"
    : "rgba(255, 220, 100, 0.95)";

  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  if (screenPts.length > 1) {
    _haloStroke(() => {
      ctx.moveTo(screenPts[0][0], screenPts[0][1]);
      for (let i = 1; i < screenPts.length; i++) {
        ctx.lineTo(screenPts[i][0], screenPts[i][1]);
      }
    }, bright, 2.8, null);
  }

  {
    const [sx, sy] = imageToScreen(board.mouse.x, board.mouse.y);
    const last = screenPts[screenPts.length - 1];

    _haloStroke(() => {
      ctx.moveTo(last[0], last[1]);
      ctx.lineTo(sx, sy);
    }, brightSoft, 2.4, [7, 5]);

    if (screenPts.length >= 3) {
      const first = screenPts[0];
      _haloStroke(() => {
        ctx.moveTo(sx, sy);
        ctx.lineTo(first[0], first[1]);
      }, brightFaint, 1.8, [6, 6]);
    }
  }

  for (let i = 0; i < screenPts.length; i++) {
    const [px, py] = screenPts[i];
    const isFirst = (i === 0);
    const isLast  = (i === screenPts.length - 1);

    if (isFirst && screenPts.length >= 3) {
      ctx.beginPath();
      ctx.arc(px, py, 11, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(px, py, 8.5, 0, Math.PI * 2);
      ctx.fillStyle = vertexColor;
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 1.0)";
      ctx.lineWidth = 2.2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(px, py, 3, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(255, 255, 255, 1.0)";
      ctx.fill();
    } else if (isLast) {
      ctx.beginPath();
      ctx.arc(px, py, 8, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(8, 10, 14, 0.80)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(px, py, 5.5, 0, Math.PI * 2);
      ctx.fillStyle = vertexColor;
      ctx.fill();
      ctx.strokeStyle = "rgba(255, 255, 255, 1.0)";
      ctx.lineWidth = 2;
      ctx.stroke();
    } else {
      ctx.beginPath();
      ctx.arc(px, py, 7, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(8, 10, 14, 0.75)";
      ctx.fill();
      ctx.beginPath();
      ctx.arc(px, py, 4.5, 0, Math.PI * 2);
      ctx.fillStyle = vertexColor;
      ctx.fill();
    }
  }

  ctx.restore();
}

/* ============================================================
   Freehand draft
   ============================================================ */

function _drawFreehandDraft() {
  if (!board.freehandActive) return;
  if (!board.freehandPoints || board.freehandPoints.length === 0) return;

  const screenPts = board.freehandPoints.map(p => imageToScreen(p.x, p.y));
  const isErase = !!board.freehandIsErase;

  const strokeBright = isErase
    ? "rgba(255, 130, 150, 1.0)"
    : "rgba(255, 220, 100, 1.0)";
  const strokeSoft = isErase
    ? "rgba(255, 130, 150, 0.55)"
    : "rgba(255, 220, 100, 0.55)";
  const fillSoft = isErase
    ? "rgba(255, 130, 150, 0.18)"
    : "rgba(255, 220, 100, 0.18)";

  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  if (screenPts.length > 1) {
    _haloStroke(() => {
      ctx.moveTo(screenPts[0][0], screenPts[0][1]);
      for (let i = 1; i < screenPts.length; i++) {
        ctx.lineTo(screenPts[i][0], screenPts[i][1]);
      }
    }, strokeBright, 2.8, null);
  }

  if (screenPts.length >= 3) {
    ctx.beginPath();
    ctx.moveTo(screenPts[0][0], screenPts[0][1]);
    for (let i = 1; i < screenPts.length; i++) {
      ctx.lineTo(screenPts[i][0], screenPts[i][1]);
    }
    ctx.closePath();
    ctx.fillStyle = fillSoft;
    ctx.fill();

    const first = screenPts[0];
    const last  = screenPts[screenPts.length - 1];
    _haloStroke(() => {
      ctx.moveTo(last[0], last[1]);
      ctx.lineTo(first[0], first[1]);
    }, strokeSoft, 1.8, [6, 6]);
  }

  const [fx, fy] = screenPts[0];
  ctx.beginPath();
  ctx.arc(fx, fy, 6.5, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
  ctx.fill();
  ctx.beginPath();
  ctx.arc(fx, fy, 4.5, 0, Math.PI * 2);
  ctx.fillStyle = strokeBright;
  ctx.fill();
  ctx.strokeStyle = "rgba(255, 255, 255, 1.0)";
  ctx.lineWidth = 1.8;
  ctx.stroke();

  ctx.restore();
}

/* ============================================================
   Inpaint context rectangle — outline + corner/edge handles
   ============================================================ */

function _drawInpaintContext(r) {
  if (!board.inpaintEnabled) return;
  if (!board.imageEl) return;

  const c = board.inpaintContext;
  // Lazy-init: default to a centered 50%-of-image rect the first
  // time the section is enabled against a loaded image.
  if ((c.w === 0 || c.h === 0)
      && board.imageWidth > 0 && board.imageHeight > 0) {
    c.x = Math.round(board.imageWidth  * 0.25);
    c.y = Math.round(board.imageHeight * 0.25);
    c.w = Math.round(board.imageWidth  * 0.50);
    c.h = Math.round(board.imageHeight * 0.50);
  }

  const rect = contextRectScreen(r);
  if (!rect) return;
  const { x0, y0, x1, y1 } = rect;
  const w = x1 - x0, h = y1 - y0;

  const bright = "rgba(180, 155, 255, 1.0)";
  const soft   = "rgba(180, 155, 255, 0.08)";
  const halo   = "rgba(8, 10, 14, 0.82)";

  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap  = "round";

  // Faint interior fill — signals the rect is live without
  // competing with the mask overlays.
  ctx.fillStyle = soft;
  ctx.fillRect(x0, y0, w, h);

  // Dashed dark halo, then dashed bright line — same visual
  // language as the lasso draft, different hue so the two are
  // never confused.
  ctx.beginPath();
  ctx.rect(x0, y0, w, h);
  ctx.strokeStyle = halo;
  ctx.lineWidth   = 6.0;
  ctx.setLineDash([7, 5]);
  ctx.stroke();

  ctx.beginPath();
  ctx.rect(x0, y0, w, h);
  ctx.strokeStyle = bright;
  ctx.lineWidth   = 2.8;
  ctx.setLineDash([7, 5]);
  ctx.stroke();
  ctx.setLineDash([]);

  // Corner + edge handles.
  const handles = contextHandles(r);
  if (handles) {
    const HS = 7;
    const active = c.activeHandle;
    for (const hd of handles) {
      ctx.fillStyle = "rgba(8, 10, 14, 0.85)";
      ctx.fillRect(hd.x - HS - 2, hd.y - HS - 2, (HS + 2) * 2, (HS + 2) * 2);
      ctx.fillStyle = (active === hd.name)
        ? "rgba(255, 255, 255, 1.0)"
        : bright;
      ctx.fillRect(hd.x - HS, hd.y - HS, HS * 2, HS * 2);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.85)";
      ctx.lineWidth = 1.5;
      ctx.strokeRect(hd.x - HS, hd.y - HS, HS * 2, HS * 2);
    }
  }

  // Label — always drawn inside the top-left of the rect so it
  // stays visible regardless of zoom or pan.
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = bright;
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText("CONTEXT", x0 + 4, y0 + 4);

  ctx.restore();
}

/* ============================================================
   Main draw
   ============================================================ */

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

  const sm = selectedMask();
  if (sm && sm.regionCanvas) {
    if (board.regionStrokeActive) {
      _drawLiveRegionStroke(r, sm);
    } else {
      _drawRegionEdge(r);
    }
  }

  _drawInpaintContext(r);

  for (const p of board.stagingPoints) {
    _drawPoint(p.x, p.y, p.isPositive, false);
  }

  if (sm) {
    for (const p of sm.points) {
      _drawPoint(p.x, p.y, p.isPositive, true);
    }
  }

  _drawBrushCursor(r);
  _drawLassoDraft();
  _drawFreehandDraft();

  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "REFINING MASK " + (selIdx + 1)
             + " via " + board.refineModel.toUpperCase();
    if (sm && sm.regionCanvas) {
      ctxLabel += " \u00b7 REGION (" + board.regionTool
               + (board.regionSubtract ? " \u00b7 subtract" : "") + ")";
    }
    if (board.lassoPoints.length > 0) {
      ctxLabel += " \u00b7 LASSO " + board.lassoPoints.length + " pt";
    }
    if (board.freehandActive) {
      ctxLabel += " \u00b7 FREEHAND " + board.freehandPoints.length + " pt";
    }
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "STAGING MODE \u2014 place points then Segment All";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  if (board.inpaintEnabled) {
    ctxLabel += " \u00b7 INPAINT";
  }

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
    + "   \u00b7   " + ctxLabel
    + "   \u00b7   STAGING " + board.stagingPoints.length
    + "   \u00b7   MASKS " + board.masks.length,
    14, 14,
  );
  ctx.restore();
}
"""
