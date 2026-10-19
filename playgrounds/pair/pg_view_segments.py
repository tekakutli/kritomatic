SEGMENTS_VIEW_JS = r"""
import {
  ctx, view, layout, pairs, pairState, state, OVERLAP_HUE,
  pairHue, _huergb, _huergbLight, _pairPeriod, _pairScale,
  _kRangeForSegmentInSegmentView, forEachSegmentCopy,
  forEachSegmentCopyInRange, _cachedSegmentOverlaps, _isShiftSetMember,
  addSegmentToPair, SEG_X_MIN, SEG_X_MAX,
} from "./pg_core.js";
import { syncPairList } from "./pg_panel_lists.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   SEGMENT VIEW — mapping
   ========================================================================== */

export const ROW_HEIGHT = 28;
export const ROW_GAP    = 4;
export const ROW_STRIDE = ROW_HEIGHT + ROW_GAP;

const SEG_EDGE_HIT_PX = 8;
const DEFAULT_SEGMENT_HALF_WIDTH = 0.8;

export function _pairSpan(p) {
  if (!p) return SEG_X_MAX - SEG_X_MIN;
  return _pairPeriod(p);
}

export function _pairViewMin(p) { return -_pairSpan(p) / 2; }
export function _pairViewMax(p) { return  _pairSpan(p) / 2; }

export function segRect() {
  const W = window.innerWidth;
  const MARGIN_X = 40;
  const x0 = MARGIN_X;
  const w  = W - 2 * MARGIN_X;
  const y0 = layout.reservedY + 4;
  const h  = layout.reservedH - 8;
  return { x0, y0, w, h };
}

export function segXToScreen(x, p) {
  const r = segRect();
  const span = _pairSpan(p);
  const u = (x - _pairViewMin(p)) / span;
  return r.x0 + u * r.w;
}

export function segScreenToX(sx, p) {
  const r = segRect();
  const span = _pairSpan(p);
  return _pairViewMin(p) + (sx - r.x0) / r.w * span;
}

export function rowTopY(idx) {
  const r = segRect();
  return r.y0 + idx * ROW_STRIDE - state.segScroll;
}

function _segEdgeHitAbstract(p) {
  const r = segRect();
  if (r.w < 1) return 0.5;
  return SEG_EDGE_HIT_PX / r.w * _pairSpan(p);
}

/* ==========================================================================
   SEGMENT VIEW — drawing
   ========================================================================== */

function drawSegmentBar(p, seg, hue, top, pairIdx, segIdx, isSelPair) {
  const r = segRect();
  const isSel = pairState.selectedSegment &&
                pairState.selectedSegment.pairIdx === pairIdx &&
                pairState.selectedSegment.segIdx === segIdx;
  const y0 = top + 4;
  const y1 = top + ROW_HEIGHT - 4;
  const filled = seg.filled !== false;
  const vMin = _pairViewMin(p);
  const vMax = _pairViewMax(p);
  const overlapOn = state.overlapHighlight &&
                    _cachedSegmentOverlaps(pairIdx, segIdx);
  const isSetMember = _isShiftSetMember(pairIdx, segIdx);

  const kRange = _kRangeForSegmentInSegmentView(p, seg);
  if (kRange.min > kRange.max) return;

  forEachSegmentCopyInRange(_pairPeriod(p), seg.a, seg.b,
                            kRange.min, kRange.max, (a, b, k) => {
    if (b < vMin || a > vMax) return;
    const aCl = Math.max(a, vMin);
    const bCl = Math.min(b, vMax);
    if (bCl <= aCl) return;

    const sxa = segXToScreen(aCl, p);
    const sxb = segXToScreen(bCl, p);
    if (sxb <= sxa) return;

    ctx.save();
    if (filled) {
      ctx.fillStyle = _huergb(hue, isSel ? 0.55 : 0.30);
      ctx.fillRect(sxa, y0, sxb - sxa, y1 - y0);
      ctx.strokeStyle = _huergb(hue, isSel ? 1.0 : 0.7);
      ctx.lineWidth = 1.2;
      ctx.strokeRect(sxa + 0.5, y0 + 0.5, sxb - sxa - 1, y1 - y0 - 1);
    } else {
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = _huergb(hue, isSel ? 0.9 : 0.5);
      ctx.lineWidth = 1.2;
      ctx.strokeRect(sxa + 0.5, y0 + 0.5, sxb - sxa - 1, y1 - y0 - 1);
      ctx.setLineDash([]);
    }
    if (overlapOn) {
      ctx.save();
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = _huergb(OVERLAP_HUE, 0.95);
      ctx.lineWidth = 2.2;
      ctx.strokeRect(sxa + 0.5, y0 + 0.5, sxb - sxa - 1, y1 - y0 - 1);
      ctx.restore();
    }
    if (isSetMember) {
      ctx.save();
      ctx.setLineDash([6, 3]);
      ctx.strokeStyle = "rgba(255, 255, 255, 0.88)";
      ctx.lineWidth = 2.0;
      ctx.strokeRect(sxa + 2.5, y0 + 2.5,
                     Math.max(0, sxb - sxa - 5),
                     Math.max(0, y1 - y0 - 5));
      ctx.restore();
    }
    if (isSel) {
      if (a >= vMin && a <= vMax) {
        const ex = segXToScreen(a, p);
        ctx.beginPath();
        ctx.arc(ex, (y0 + y1) / 2, 4, 0, Math.PI * 2);
        ctx.fillStyle = _huergbLight(hue);
        ctx.fill();
        ctx.strokeStyle = "#0a0e14";
        ctx.lineWidth = 1.3;
        ctx.stroke();
      }
      if (b >= vMin && b <= vMax) {
        const ex = segXToScreen(b, p);
        ctx.beginPath();
        ctx.arc(ex, (y0 + y1) / 2, 4, 0, Math.PI * 2);
        ctx.fillStyle = _huergbLight(hue);
        ctx.fill();
        ctx.strokeStyle = "#0a0e14";
        ctx.lineWidth = 1.3;
        ctx.stroke();
      }
    }
    ctx.restore();
  });
}

function drawSegmentRow(p, pairIdx, top, r) {
  const hue = pairHue(p);
  const isSelPair = (pairIdx === pairState.selectedPair);

  ctx.fillStyle = isSelPair
    ? "rgba(0,229,255,0.03)"
    : "rgba(30,40,54,0.12)";
  ctx.fillRect(r.x0, top, r.w, ROW_HEIGHT);

  ctx.strokeStyle = _huergb(hue, isSelPair ? 0.7 : 0.4);
  ctx.lineWidth = 1;
  const axisY = top + ROW_HEIGHT / 2;
  ctx.beginPath();
  ctx.moveTo(r.x0, axisY);
  ctx.lineTo(r.x0 + r.w, axisY);
  ctx.stroke();

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "left"; ctx.textBaseline = "middle";
  ctx.fillStyle = _huergb(hue, isSelPair ? 1.0 : 0.7);
  ctx.fillText(p.name, r.x0 - 30, top + ROW_HEIGHT / 2);
  ctx.restore();

  for (let j = 0; j < p.segments.length; j++) {
    drawSegmentBar(p, p.segments[j], hue, top, pairIdx, j, isSelPair);
  }
}

export function drawSegmentsView() {
  const cw = window.innerWidth;
  ctx.fillStyle = "#060a10";
  ctx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = segRect();

  ctx.save();
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  ctx.font = "700 9px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "center"; ctx.textBaseline = "bottom";
  ctx.fillStyle = "#5a6774";
  for (let x = Math.ceil(SEG_X_MIN); x <= SEG_X_MAX; x += 1) {
    const sx = segXToScreen(x);
    if (sx < r.x0 || sx > r.x0 + r.w) continue;
    const major = (x % 5 === 0);
    ctx.strokeStyle = major ? "#223040" : "#1a2433";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sx, r.y0 - 2);
    ctx.lineTo(sx, r.y0 - 6);
    ctx.stroke();
    if (major) ctx.fillText(String(x), sx, r.y0 - 6);
  }
  ctx.restore();

  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.w, r.h);
  ctx.clip();

  if (pairs.length === 0) {
    ctx.fillStyle = "#3d4756";
    ctx.font = "italic 11px 'JetBrains Mono', 'Fira Code', monospace";
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.fillText("no pairs yet — press + Pair",
                 r.x0 + r.w / 2, r.y0 + r.h / 2);
    ctx.restore();
    return;
  }

  for (let i = 0; i < pairs.length; i++) {
    const top = rowTopY(i);
    if (top + ROW_HEIGHT < r.y0) continue;
    if (top > r.y0 + r.h) continue;
    drawSegmentRow(pairs[i], i, top, r);
  }

  ctx.restore();

  if (state.drawSegmentMode && state.mouse.inside &&
      state.mouse.sy >= layout.reservedY) {
    const sx = state.mouse.sx;
    if (sx >= r.x0 && sx <= r.x0 + r.w) {
      let hintPair = null;
      for (let i = 0; i < pairs.length; i++) {
        const top = rowTopY(i);
        if (state.mouse.sy >= top && state.mouse.sy <= top + ROW_HEIGHT) {
          hintPair = pairs[i]; break;
        }
      }
      const hintPeriod = hintPair ? _pairPeriod(hintPair) : (SEG_X_MAX - SEG_X_MIN);
      const hMin = hintPair ? _pairViewMin(hintPair) : SEG_X_MIN;
      const hMax = hintPair ? _pairViewMax(hintPair) : SEG_X_MAX;

      const cx = segScreenToX(sx, hintPair);
      const halfW = DEFAULT_SEGMENT_HALF_WIDTH;
      ctx.save();
      ctx.strokeStyle = "rgba(0,229,255,0.55)";
      ctx.lineWidth = 1.2;
      ctx.setLineDash([4, 3]);
      forEachSegmentCopy(hintPeriod, cx - halfW, cx + halfW, (a, b) => {
        if (b < hMin || a > hMax) return;
        const aCl = Math.max(a, hMin);
        const bCl = Math.min(b, hMax);
        if (bCl <= aCl) return;
        const ax = segXToScreen(aCl, hintPair);
        const bx = segXToScreen(bCl, hintPair);
        ctx.strokeRect(ax + 0.5, r.y0 + 4.5, (bx - ax) - 1, r.h - 9);
      });
      ctx.setLineDash([]);
      ctx.restore();
    }
  }
}

/* ==========================================================================
   SEGMENT VIEW — hit testing
   ========================================================================== */

export function segmentHitTest(sx, sy) {
  const r = segRect();
  if (sy < r.y0 || sy > r.y0 + r.h) return null;
  if (sx < r.x0 || sx > r.x0 + r.w) return null;

  let rowIdx = -1;
  for (let i = 0; i < pairs.length; i++) {
    const top = rowTopY(i);
    if (sy >= top && sy <= top + ROW_HEIGHT) { rowIdx = i; break; }
  }
  if (rowIdx < 0) return null;

  const p = pairs[rowIdx];
  const period = _pairPeriod(p);
  const vMin = _pairViewMin(p);
  const vMax = _pairViewMax(p);
  const xc = segScreenToX(sx, p);
  const edgeThresh = _segEdgeHitAbstract(p);

  let bestEdgeSeg = -1, bestEdgeSide = null, bestEdgeDist = edgeThresh;
  let bodySeg = -1;

  for (let j = 0; j < p.segments.length; j++) {
    const seg = p.segments[j];
    let segBody = false;

    forEachSegmentCopy(period, seg.a, seg.b, (a, b) => {
      if (b < vMin || a > vMax) return;
      if (a >= vMin && a <= vMax) {
        const d = Math.abs(xc - a);
        if (d < bestEdgeDist) { bestEdgeDist = d; bestEdgeSeg = j; bestEdgeSide = "a"; }
      }
      if (b >= vMin && b <= vMax) {
        const d = Math.abs(xc - b);
        if (d < bestEdgeDist) { bestEdgeDist = d; bestEdgeSeg = j; bestEdgeSide = "b"; }
      }
      if (bodySeg < 0) {
        const aCl = Math.max(a, vMin), bCl = Math.min(b, vMax);
        if (xc > aCl && xc < bCl) segBody = true;
      }
    });
    if (segBody && bodySeg < 0) bodySeg = j;
  }

  if (bestEdgeSeg >= 0) {
    return { kind: "edge", pairIdx: rowIdx, segIdx: bestEdgeSeg, side: bestEdgeSide };
  }
  if (bodySeg >= 0) {
    return { kind: "body", pairIdx: rowIdx, segIdx: bodySeg };
  }
  return { kind: "row", pairIdx: rowIdx };
}

export function createSegmentAt(sx, sy) {
  const r = segRect();
  if (sy < r.y0 || sy > r.y0 + r.h) return null;
  if (sx < r.x0 || sx > r.x0 + r.w) return null;

  for (let i = 0; i < pairs.length; i++) {
    const top = rowTopY(i);
    if (sy < top || sy > top + ROW_HEIGHT) continue;
    const p = pairs[i];
    const cx = segScreenToX(sx, p);
    const a = cx - DEFAULT_SEGMENT_HALF_WIDTH;
    const b = cx + DEFAULT_SEGMENT_HALF_WIDTH;
    addSegmentToPair(i, a, b);
    if (i !== pairState.selectedPair) {
      pairState.selectedPair = i;
      syncPairList();
    }
    return { pairIdx: i };
  }
  return null;
}
"""
