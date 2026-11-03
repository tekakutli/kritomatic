"""Region rendering.

Two modes:

  live stroke    — while `board.regionStrokeActive` is true, show the
                   inverted image under the region mask (the classic
                   "what will be refined" preview).  During an erase
                   stroke, tint the region red.

  idle           — after the stroke, trace the region boundary and
                   place a dot every few screen-pixels along it.  The
                   boundary trace is expensive; the result is cached on
                   the mask entry (`_regionEdgePoints`, `_regionChains`)
                   and invalidated by `pg_region` on every mutation."""

VIEW_REGION_JS = r"""
import { ctx, board } from "./pg_state.js";
import { maskPixelData } from "./pg_pixels.js";
import { selectedMask } from "./pg_masks.js";

/* ============================================================
   Live stroke preview
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
  if (mask._regionEdgePoints != null) return mask._regionEdgePoints;

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
   Public entry — dispatch on the region's runtime state
   ============================================================ */

export function drawRegionLayer(r, sm) {
  if (board.regionStrokeActive) {
    _drawLiveRegionStroke(r, sm);
  } else {
    _drawRegionEdge(r);
  }
}
"""
