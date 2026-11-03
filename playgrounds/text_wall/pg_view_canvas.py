CANVAS_VIEW_JS = r"""
import {
  w2s, ctx, view, layout, pairs, pairState, state, OVERLAP_HUE,
  pairHue, _huergb, _huergbLight, _pairPeriod, _pairScale,
  _kRangeForSegmentInBand, forEachSegmentCopyInRange,
  segmentCornersWorldAt, lineEndpointsWorld, pointOnPairLine,
  _cachedSegmentOverlaps,
} from "./pg_core.js";

/* ==========================================================================
   BACKGROUND GRID
   ========================================================================== */

function _drawBoardGrid() {
  const cw = window.innerWidth;
  const ch = layout.topH;

  let step = 5;
  const spx = step * view.scale;
  if (spx > 6) {
    const [sx0, sy0] = w2s(0, 0);
    const offX = ((sx0 % spx) + spx) % spx;
    const offY = ((sy0 % spx) + spx) % spx;
    ctx.fillStyle = "rgba(30, 40, 54, 0.5)";
    ctx.beginPath();
    for (let x = offX; x < cw; x += spx) {
      for (let y = offY; y < ch; y += spx) {
        ctx.rect(x, y, 1, 1);
      }
    }
    ctx.fill();
  }
}

/* ==========================================================================
   TRAPEZOID DRAWING
   ========================================================================== */

const TRAPEZOID_MARGIN_PX = 24;

function _screenBBox(screen) {
  let minX =  Infinity, maxX = -Infinity;
  let minY =  Infinity, maxY = -Infinity;
  for (const [sx, sy] of screen) {
    if (sx < minX) minX = sx;
    if (sx > maxX) maxX = sx;
    if (sy < minY) minY = sy;
    if (sy > maxY) maxY = sy;
  }
  return { minX, maxX, minY, maxY };
}

function _bboxIntersectsTopBand(bb, cw, ch) {
  if (bb.maxX < -TRAPEZOID_MARGIN_PX) return false;
  if (bb.minX > cw + TRAPEZOID_MARGIN_PX) return false;
  if (bb.maxY < -TRAPEZOID_MARGIN_PX) return false;
  if (bb.minY > ch + TRAPEZOID_MARGIN_PX) return false;
  return true;
}

function drawSegmentTrapezoid(p, pairIdx, seg, segIdx, hue,
                              pairSelected, segSelected) {
  const cw = window.innerWidth;
  const ch = layout.topH;
  const filled = seg.filled !== false;
  const overlapOn = state.overlapHighlight &&
                    _cachedSegmentOverlaps(pairIdx, segIdx);

  const kRange = _kRangeForSegmentInBand(p, seg, cw, ch);
  if (kRange.min > kRange.max) return;

  forEachSegmentCopyInRange(_pairPeriod(p), seg.a, seg.b,
                            kRange.min, kRange.max, (a, b, k) => {
    const w = segmentCornersWorldAt(p, a, b);
    const screen = w.map(([wx, wy]) => w2s(wx, wy));
    const bb = _screenBBox(screen);
    if (!_bboxIntersectsTopBand(bb, cw, ch)) return;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(screen[0][0], screen[0][1]);
    for (let q = 1; q < 4; q++) ctx.lineTo(screen[q][0], screen[q][1]);
    ctx.closePath();

    if (filled) {
      const alpha = segSelected ? 0.32 : (pairSelected ? 0.20 : 0.10);
      ctx.fillStyle = _huergb(hue, alpha);
      ctx.fill();
      ctx.strokeStyle = _huergb(hue,
        segSelected ? 1.00 : (pairSelected ? 0.85 : 0.65));
      ctx.lineWidth = segSelected ? 2.0 : 1.3;
      ctx.stroke();
    } else {
      ctx.setLineDash([5, 4]);
      ctx.strokeStyle = _huergb(hue,
        segSelected ? 0.90 : (pairSelected ? 0.60 : 0.40));
      ctx.lineWidth = segSelected ? 1.8 : 1.1;
      ctx.stroke();
      ctx.setLineDash([]);
    }

    if (overlapOn) {
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = _huergb(OVERLAP_HUE, 0.95);
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.moveTo(screen[0][0], screen[0][1]);
      for (let q = 1; q < 4; q++) ctx.lineTo(screen[q][0], screen[q][1]);
      ctx.closePath();
      ctx.stroke();
      ctx.restore();
    }

    if (segSelected) {
      for (const [sx, sy] of screen) {
        ctx.beginPath();
        ctx.arc(sx, sy, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = _huergbLight(hue);
        ctx.fill();
        ctx.strokeStyle = "#0a0e14";
        ctx.lineWidth = 1.2;
        ctx.stroke();
      }
    }

    ctx.restore();
  });
}

function drawPairLine(p, which, hue, selected) {
  const [[ax, ay], [bx, by]] = lineEndpointsWorld(p, which);
  const [sax, say] = w2s(ax, ay);
  const [sbx, sby] = w2s(bx, by);

  ctx.save();
  ctx.strokeStyle = _huergb(hue, selected ? 0.95 : 0.55);
  ctx.lineWidth   = selected ? 2.0 : 1.2;
  ctx.beginPath();
  ctx.moveTo(sax, say);
  ctx.lineTo(sbx, sby);
  ctx.stroke();
  ctx.restore();
}

function drawPair(p, pairIdx, selected) {
  const hue = pairHue(p);

  drawPairLine(p, "A", hue, selected);
  drawPairLine(p, "B", hue, selected);

  for (let j = 0; j < p.segments.length; j++) {
    const seg = p.segments[j];
    const isSelSeg = pairState.selectedSegment &&
                     pairState.selectedSegment.pairIdx === pairIdx &&
                     pairState.selectedSegment.segIdx === j;
    drawSegmentTrapezoid(p, pairIdx, seg, j, hue, selected, isSelSeg);
  }

  if (selected) {
    const [sx, sy] = w2s(p.cx, p.cy);
    ctx.save();
    ctx.beginPath();
    ctx.arc(sx, sy, 5.5, 0, Math.PI * 2);
    ctx.fillStyle = _huergbLight(hue);
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }
}

function drawPairLabel(p, selected) {
  const hue = pairHue(p);
  const A0 = pointOnPairLine(p, "A", 0);
  const B0 = pointOnPairLine(p, "B", 0);
  const mx = (A0[0] + B0[0]) / 2;
  const my = (A0[1] + B0[1]) / 2;
  const [sx, sy] = w2s(mx, my);

  const text = p.name;
  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const w = ctx.measureText(text).width + 12;
  const h = 14;
  ctx.fillStyle = "rgba(10, 14, 20, 0.86)";
  ctx.fillRect(sx - w / 2, sy - h / 2, w, h);
  ctx.strokeStyle = _huergb(hue, selected ? 0.95 : 0.7);
  ctx.lineWidth = 0.8;
  ctx.strokeRect(sx - w / 2 + 0.4, sy - h / 2 + 0.4, w - 0.8, h - 0.8);
  ctx.fillStyle = _huergb(hue, 0.95);
  ctx.fillText(text, sx, sy + 0.5);
  ctx.restore();
}

export function drawCanvasView() {
  const cw = window.innerWidth;
  const ch = layout.topH;

  _drawBoardGrid();

  for (let i = 0; i < pairs.length; i++) {
    if (i === pairState.selectedPair) continue;
    drawPair(pairs[i], i, false);
  }
  if (pairState.selectedPair >= 0 && pairState.selectedPair < pairs.length) {
    drawPair(pairs[pairState.selectedPair], pairState.selectedPair, true);
  }

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(
    "PAIRS " + pairs.length +
    "   SELECTED " + (pairState.selectedPair >= 0
                       ? pairs[pairState.selectedPair].name : "—"),
    14, 14);
  if (state.overlapHighlight) {
    ctx.fillStyle = _huergb(OVERLAP_HUE, 0.95);
    ctx.fillText("\u25CF OVERLAP MODE", 14, 28);
  }
  ctx.restore();

  for (let i = 0; i < pairs.length; i++) {
    drawPairLabel(pairs[i], i === pairState.selectedPair);
  }
}
"""
