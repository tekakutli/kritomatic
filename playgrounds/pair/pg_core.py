"""
pg_core.py — model, geometry, and session state.

Everything mutable that other modules reassign lives on one of two
objects:

    pairState   model cursor: selectedPair, selectedSegment,
                nextPairId, nextSegId
    state       UI session: drag state, mouse, segScroll,
                drawSegmentMode, overlapHighlight, shiftDragSet

Both are `const` objects exported here.  ES module `let` imports are
read-only, so a top-level `let` cannot be reassigned from another
module; a property of a `const` object can.

`pairs` stays as an exported `const` array — push/splice mutate it
in place and every importer sees the same reference.

CHANGE HOOK
===========
Lifecycle functions (addPair, deletePair, addSegmentToPair, etc.)
call `notifyChanged()` after any model mutation.  `notifyChanged`
runs whatever handler the boot module injected via
`setAfterChange()`.  That handler is responsible for syncing the
panel, drawing, and notifying the remote client.  This keeps
pg_core free of imports from pg_panel_lists / pg_dispatch / pgkit,
breaking the cycles those would otherwise create.

DRAW REFERENCE
==============
resize() and fitView() call a `_draw` injected at boot via
`_setDraw(draw)`.  Same reason as above: a static import from
pg_dispatch.js would create a cycle (pg_dispatch imports from
pg_core).
"""

CORE_JS = r"""
/* ==========================================================================
   CANVAS, LAYOUT, VIEW
   ========================================================================== */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export let   dpr    = window.devicePixelRatio || 1;

export const layout = {
  topH:      0,
  dividerY:  0,
  reservedY: 0,
  reservedH: 0,
};

export const view = {
  scale: 3,
  tx: 0, ty: 0,
  zoom: 1, panX: 0, panY: 0,
};

export function w2s(x, y) { return [x * view.scale + view.tx,
                                    -y * view.scale + view.ty]; }
export function s2w(sx, sy) { return [(sx - view.tx) / view.scale,
                                      -(sy - view.ty) / view.scale]; }

/* ==========================================================================
   SEGMENT AXIS — PERIOD AND WRAP HELPERS
   ========================================================================== */

export const SEG_X_MIN  = -6;
export const SEG_X_MAX  =  6;
export const SEG_X_SPAN = SEG_X_MAX - SEG_X_MIN;

export const SEGMENT_WRAP_K = 20;

export function _pairPeriod(p) {
  if (!p) return SEG_X_SPAN;
  const g = (typeof p.loopbackGap === "number" && p.loopbackGap > 0)
    ? p.loopbackGap : 0;
  return SEG_X_SPAN + g;
}

export function _pairScale(p) {
  if (!p) return 1;
  const s = p.scale;
  return (typeof s === "number" && isFinite(s) && s > 0) ? s : 1;
}

export function forEachSegmentCopy(period, aBase, bBase, fn) {
  for (let k = -SEGMENT_WRAP_K; k <= SEGMENT_WRAP_K; k++) {
    fn(aBase + k * period, bBase + k * period, k);
  }
}

export function forEachSegmentCopyInRange(period, aBase, bBase, kMin, kMax, fn) {
  for (let k = kMin; k <= kMax; k++) {
    fn(aBase + k * period, bBase + k * period, k);
  }
}

/* ==========================================================================
   SESSION STATE
   ========================================================================== */

export const pairState = {
  selectedPair:    -1,
  selectedSegment: null,
  nextPairId:      1,
  nextSegId:       1,
};

export const state = {
  dragPairAnchor:  null,
  dragSegmentEdge: null,
  dragSegmentBody: null,
  mouse: { sx: 0, sy: 0, inside: false },
  segScroll: 0,
  drawSegmentMode: false,
  overlapHighlight: false,
  shiftDragSet: null,
};

export const OVERLAP_HUE = { r: 255, g: 90, b: 105 };

/* ==========================================================================
   CHANGE HOOK
   ========================================================================== */

let _afterChange = null;
export function setAfterChange(fn) { _afterChange = fn; }

export function notifyChanged() { if (_afterChange) _afterChange(); }

/* ==========================================================================
   DRAW REFERENCE (breaks cycle with pg_dispatch.js)
   ========================================================================== */

let _drawRef = null;
export function _setDraw(fn) { _drawRef = fn; }
function _draw() { if (_drawRef) _drawRef(); }

/* ==========================================================================
   OVERLAP QUERY — CANVAS-SPACE POLYGON INTERSECTION
   ========================================================================== */

function _segPolygonAtK(p, seg, k) {
  const period = _pairPeriod(p);
  const a = seg.a + k * period;
  const b = seg.b + k * period;
  const corners = segmentCornersWorldAt(p, a, b);
  return corners.map(([wx, wy]) => w2s(wx, wy));
}

function _segAABB(poly) {
  let minX =  Infinity, maxX = -Infinity;
  let minY =  Infinity, maxY = -Infinity;
  for (const [x, y] of poly) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  return { minX, maxX, minY, maxY };
}

function _wrapRangeForCanvas(p, seg, cw, ch) {
  const poly0 = _segPolygonAtK(p, seg, 0);
  const bb0 = _segAABB(poly0);
  const cx0 = (bb0.minX + bb0.maxX) / 2;
  const cy0 = (bb0.minY + bb0.maxY) / 2;

  const period = _pairPeriod(p);
  const s      = _pairScale(p);
  const dxPerK =  period * p.metricA * s * Math.cos(p.angle) * view.scale;
  const dyPerK = -period * p.metricA * s * Math.sin(p.angle) * view.scale;
  const mag = Math.hypot(dxPerK, dyPerK);
  if (mag < 1e-6) return { min: 0, max: 0 };

  const farX = Math.max(cx0, cw - cx0, 0);
  const farY = Math.max(cy0, ch - cy0, 0);
  const reach = Math.hypot(farX, farY) + 128;
  const kSpan = Math.ceil(reach / mag) + 1;
  return { min: -kSpan, max: kSpan };
}

function _convexQuadsIntersect(A, B) {
  for (const poly of [A, B]) {
    const n = poly.length;
    for (let i = 0; i < n; i++) {
      const ax = poly[i][0], ay = poly[i][1];
      const bx = poly[(i + 1) % n][0], by = poly[(i + 1) % n][1];
      const nx = -(by - ay);
      const ny =  (bx - ax);

      let minA =  Infinity, maxA = -Infinity;
      let minB =  Infinity, maxB = -Infinity;
      for (const [x, y] of A) {
        const d = nx * x + ny * y;
        if (d < minA) minA = d;
        if (d > maxA) maxA = d;
      }
      for (const [x, y] of B) {
        const d = nx * x + ny * y;
        if (d < minB) minB = d;
        if (d > maxB) maxB = d;
      }
      if (maxA <= minB || maxB <= minA) return false;
    }
  }
  return true;
}

function _kRangeForTargetX(bb, dxPerK, targetMin, targetMax) {
  if (Math.abs(dxPerK) < 1e-9) {
    if (bb.maxX < targetMin || bb.minX > targetMax) return null;
    return [-1e9, 1e9];
  }
  const t1 = (targetMax - bb.minX) / dxPerK;
  const t2 = (targetMin - bb.maxX) / dxPerK;
  return [Math.min(t1, t2), Math.max(t1, t2)];
}

function _kRangeForTargetY(bb, dyPerK, targetMin, targetMax) {
  if (Math.abs(dyPerK) < 1e-9) {
    if (bb.maxY < targetMin || bb.minY > targetMax) return null;
    return [-1e9, 1e9];
  }
  const t1 = (targetMax - bb.minY) / dyPerK;
  const t2 = (targetMin - bb.maxY) / dyPerK;
  return [Math.min(t1, t2), Math.max(t1, t2)];
}

export function _segmentTrapezoidsOverlap(p1, seg1, p2, seg2) {
  const cw = window.innerWidth;
  const ch = layout.topH;
  const MARGIN = 4;

  const r1 = _wrapRangeForCanvas(p1, seg1, cw, ch);
  const r2 = _wrapRangeForCanvas(p2, seg2, cw, ch);

  const bb2Base = _segAABB(_segPolygonAtK(p2, seg2, 0));
  const period2 = _pairPeriod(p2);
  const s2  = _pairScale(p2);
  const dx2 =  period2 * p2.metricA * s2 * Math.cos(p2.angle) * view.scale;
  const dy2 = -period2 * p2.metricA * s2 * Math.sin(p2.angle) * view.scale;

  for (let k1 = r1.min; k1 <= r1.max; k1++) {
    const poly1 = _segPolygonAtK(p1, seg1, k1);
    const bb1 = _segAABB(poly1);

    if (bb1.maxX < -MARGIN || bb1.minX > cw + MARGIN) continue;
    if (bb1.maxY < -MARGIN || bb1.minY > ch + MARGIN) continue;

    const kx = _kRangeForTargetX(bb2Base, dx2, bb1.minX, bb1.maxX);
    if (!kx) continue;
    const ky = _kRangeForTargetY(bb2Base, dy2, bb1.minY, bb1.maxY);
    if (!ky) continue;

    const lo = Math.max(kx[0], ky[0], r2.min);
    const hi = Math.min(kx[1], ky[1], r2.max);
    const k2First = Math.ceil(lo);
    const k2Last  = Math.floor(hi);

    for (let k2 = k2First; k2 <= k2Last; k2++) {
      const poly2 = _segPolygonAtK(p2, seg2, k2);
      if (_convexQuadsIntersect(poly1, poly2)) return true;
    }
  }
  return false;
}

export function _segmentOverlapsOtherPair(pairIdx, segIdx) {
  if (pairIdx < 0 || pairIdx >= pairs.length) return false;
  const p = pairs[pairIdx];
  if (segIdx < 0 || segIdx >= p.segments.length) return false;
  const seg = p.segments[segIdx];

  for (let j = 0; j < pairs.length; j++) {
    if (j === pairIdx) continue;
    const q = pairs[j];
    for (let k = 0; k < q.segments.length; k++) {
      if (_segmentTrapezoidsOverlap(p, seg, q, q.segments[k])) return true;
    }
  }
  return false;
}

export function _pairHasAnyOverlap(pairIdx) {
  if (pairIdx < 0 || pairIdx >= pairs.length) return false;
  const p = pairs[pairIdx];
  for (let j = 0; j < p.segments.length; j++) {
    if (_segmentOverlapsOtherPair(pairIdx, j)) return true;
  }
  return false;
}

/* ==========================================================================
   OVERLAP RESOLUTION — "MAKE ROOM"
   ========================================================================== */

const OVERLAP_RESOLVE_MAX_SHIFT   = SEG_X_SPAN;
const OVERLAP_RESOLVE_COARSE_STEP = 0.1;
const OVERLAP_RESOLVE_FINE_ITERS  = 14;
const OVERLAP_RESOLVE_MAX_ITERS   = 50;

export function _segmentOverlapsPairBelow(pairIdx, segIdx) {
  if (pairIdx < 0 || pairIdx + 1 >= pairs.length) return false;
  const pUpper = pairs[pairIdx];
  if (segIdx < 0 || segIdx >= pUpper.segments.length) return false;
  const segUpper = pUpper.segments[segIdx];
  const pLower = pairs[pairIdx + 1];
  for (let j = 0; j < pLower.segments.length; j++) {
    if (_segmentTrapezoidsOverlap(pUpper, segUpper, pLower, pLower.segments[j])) {
      return true;
    }
  }
  return false;
}

function _segmentsOverlapWithShift(pUpper, segUpper, pLower, segLower, shift) {
  const a0 = segLower.a, b0 = segLower.b;
  segLower.a = a0 + shift;
  segLower.b = b0 + shift;
  let overlap;
  try {
    overlap = _segmentTrapezoidsOverlap(pUpper, segUpper, pLower, segLower);
  } finally {
    segLower.a = a0;
    segLower.b = b0;
  }
  return overlap;
}

function _resolveOverlapDeltaDirection(pUpper, segUpper, pLower, segLower, sign) {
  let prevD = 0;
  for (let d = OVERLAP_RESOLVE_COARSE_STEP;
       d <= OVERLAP_RESOLVE_MAX_SHIFT + 1e-9;
       d += OVERLAP_RESOLVE_COARSE_STEP) {
    const stillOverlapping = _segmentsOverlapWithShift(
      pUpper, segUpper, pLower, segLower, sign * d);
    if (!stillOverlapping) {
      let lo = prevD, hi = d;
      for (let i = 0; i < OVERLAP_RESOLVE_FINE_ITERS; i++) {
        const mid = (lo + hi) / 2;
        const o = _segmentsOverlapWithShift(
          pUpper, segUpper, pLower, segLower, sign * mid);
        if (o) lo = mid; else hi = mid;
      }
      return hi;
    }
    prevD = d;
  }
  return null;
}

function _resolveOverlapDelta(pUpper, segUpper, pLower, segLower) {
  const dPos = _resolveOverlapDeltaDirection(pUpper, segUpper, pLower, segLower, +1);
  const dNeg = _resolveOverlapDeltaDirection(pUpper, segUpper, pLower, segLower, -1);
  if (dPos === null && dNeg === null) return null;
  if (dPos === null) return { direction: -1, delta: dNeg };
  if (dNeg === null) return { direction: +1, delta: dPos };
  if (dPos <= dNeg) return { direction: +1, delta: dPos };
  return { direction: -1, delta: dNeg };
}

function _findOverlapWithPairBelow(pairIdx, segIdx) {
  if (pairIdx < 0 || pairIdx + 1 >= pairs.length) return null;
  const pUpper = pairs[pairIdx];
  if (segIdx < 0 || segIdx >= pUpper.segments.length) return null;
  const segUpper = pUpper.segments[segIdx];
  const pLower = pairs[pairIdx + 1];

  let best = null;
  for (let j = 0; j < pLower.segments.length; j++) {
    const segLower = pLower.segments[j];
    if (!_segmentTrapezoidsOverlap(pUpper, segUpper, pLower, segLower)) continue;
    const sol = _resolveOverlapDelta(pUpper, segUpper, pLower, segLower);
    if (!sol) continue;
    if (!best || sol.delta < best.delta) {
      best = { lowerPairIdx: pairIdx + 1, lowerSegIdx: j,
               direction: sol.direction, delta: sol.delta };
    }
  }
  return best;
}

export function _makeRoomInferior(pairIdx, segIdx) {
  let resolved = 0;
  for (let iter = 0; iter < OVERLAP_RESOLVE_MAX_ITERS; iter++) {
    const found = _findOverlapWithPairBelow(pairIdx, segIdx);
    if (!found) break;

    const pLower = pairs[found.lowerPairIdx];
    const pivot = pLower.segments[found.lowerSegIdx];
    if (!pivot) break;

    const dir = found.direction;
    const delta = dir * found.delta;
    const pivotCenter = (pivot.a + pivot.b) / 2;

    for (const s of pLower.segments) {
      const c = (s.a + s.b) / 2;
      const follows = (dir > 0) ? (c >= pivotCenter) : (c <= pivotCenter);
      if (follows) { s.a += delta; s.b += delta; }
    }
    resolved++;
  }
  return resolved;
}

/* ==========================================================================
   PAIR MODEL
   ========================================================================== */

export const pairs = [];

export function pairFrame(p) {
  const ux = Math.cos(p.angle);
  const uy = Math.sin(p.angle);
  return { ux, uy, vx: -uy, vy: ux };
}

export function pointOnPairLine(p, which, abstractT) {
  const f = pairFrame(p);
  const s = _pairScale(p);
  if (which === "A") {
    return [p.cx + abstractT * p.metricA * s * f.ux,
            p.cy + abstractT * p.metricA * s * f.uy];
  }
  return [p.cx + (p.offset + abstractT * p.metricB) * s * f.ux
              +  p.gap * s * f.vx,
          p.cy + (p.offset + abstractT * p.metricB) * s * f.uy
              +  p.gap * s * f.vy];
}

export function segmentCornersWorldAt(p, a, b) {
  return [
    pointOnPairLine(p, "A", a),
    pointOnPairLine(p, "A", b),
    pointOnPairLine(p, "B", b),
    pointOnPairLine(p, "B", a),
  ];
}

export function segmentCornersWorld(p, seg) {
  return segmentCornersWorldAt(p, seg.a, seg.b);
}

const LINE_REACH = 1e5;

export function lineEndpointsWorld(p, which) {
  const f = pairFrame(p);
  const [ax, ay] = pointOnPairLine(p, which, 0);
  return [
    [ax - LINE_REACH * f.ux, ay - LINE_REACH * f.uy],
    [ax + LINE_REACH * f.ux, ay + LINE_REACH * f.uy],
  ];
}

/* ==========================================================================
   RENDER HELPERS
   ========================================================================== */

let _overlapMemo = null;

export function _resetOverlapMemo() { _overlapMemo = new Map(); }

export function _cachedSegmentOverlaps(pairIdx, segIdx) {
  if (!_overlapMemo) _overlapMemo = new Map();
  const key = pairIdx + ":" + segIdx;
  if (_overlapMemo.has(key)) return _overlapMemo.get(key);
  const r = _segmentOverlapsOtherPair(pairIdx, segIdx);
  _overlapMemo.set(key, r);
  return r;
}

export function _isShiftSetMember(pairIdx, segIdx) {
  const s = state.shiftDragSet;
  return !!(s && s.pairIdx === pairIdx && s.members.has(segIdx));
}

export function _kRangeForSegmentInBand(p, seg, cw, bandTop) {
  const f = pairFrame(p);
  const period = _pairPeriod(p);
  const m = p.metricA * _pairScale(p);
  const sc = view.scale;

  const x0 = p.cx * sc + view.tx;
  const y0 = -p.cy * sc + view.ty;
  const slopeX =  m * f.ux * sc;
  const slopeY = -m * f.uy * sc;

  let txMin = -Infinity, txMax = Infinity;
  if (Math.abs(slopeX) > 1e-9) {
    const tA = (0  - x0) / slopeX;
    const tB = (cw - x0) / slopeX;
    txMin = Math.min(tA, tB);
    txMax = Math.max(tA, tB);
  } else if (x0 < 0 || x0 > cw) {
    return { min: 0, max: -1 };
  }

  let tyMin = -Infinity, tyMax = Infinity;
  if (Math.abs(slopeY) > 1e-9) {
    const tA = (0       - y0) / slopeY;
    const tB = (bandTop - y0) / slopeY;
    tyMin = Math.min(tA, tB);
    tyMax = Math.max(tA, tB);
  } else if (y0 < 0 || y0 > bandTop) {
    return { min: 0, max: -1 };
  }

  const tMin = Math.max(txMin, tyMin) - period;
  const tMax = Math.min(txMax, tyMax) + period;
  if (tMin > tMax) return { min: 0, max: -1 };

  const kMin = Math.ceil((tMin - seg.b) / period);
  const kMax = Math.floor((tMax - seg.a) / period);

  return {
    min: Math.max(-SEGMENT_WRAP_K, kMin),
    max: Math.min( SEGMENT_WRAP_K, kMax),
  };
}

export function _kRangeForSegmentInSegmentView(p, seg) {
  const period = _pairPeriod(p);
  const tMin = -period / 2 - period;
  const tMax =  period / 2 + period;

  const kMin = Math.ceil((tMin - seg.b) / period);
  const kMax = Math.floor((tMax - seg.a) / period);

  return {
    min: Math.max(-SEGMENT_WRAP_K, kMin),
    max: Math.min( SEGMENT_WRAP_K, kMax),
  };
}

/* ==========================================================================
   LIFECYCLE
   ========================================================================== */

export function pairHue(p) {
  const PALETTE = [
    { r: 255, g: 200, b:  90 },
    { r: 120, g: 220, b: 255 },
    { r: 255, g: 130, b: 210 },
    { r: 140, g: 225, b: 160 },
    { r: 180, g: 155, b: 255 },
    { r: 255, g: 165, b: 105 },
    { r: 110, g: 220, b: 210 },
    { r: 220, g: 230, b: 120 },
  ];
  if (!p) return PALETTE[0];
  const n = PALETTE.length;
  const idx = ((p.id - 1) % n + n) % n;
  return PALETTE[idx];
}

export function _huergb(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}

export function _huergbLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

export function addPair() {
  const id  = pairState.nextPairId++;
  const idx = pairs.length;
  pairs.push({
    id, name: "P" + id,
    cx: 0, cy: -idx * 2.5,
    angle: 0,
    metricA: 1.0,
    metricB: 2.0,
    offset: 0.0,
    gap: 3.0,
    loopbackGap: 0,
    scale: 1,
    segments: [],
  });
  pairState.selectedPair = pairs.length - 1;
  pairState.selectedSegment = null;
  notifyChanged();
}

export function clonePair(idx) {
  if (idx < 0 || idx >= pairs.length) return;
  const src = pairs[idx];
  const id  = pairState.nextPairId++;
  const copy = {
    id, name: "P" + id,
    cx: src.cx, cy: src.cy - 2.5,
    angle: src.angle,
    metricA: src.metricA,
    metricB: src.metricB,
    offset: src.offset,
    gap: src.gap,
    loopbackGap: (typeof src.loopbackGap === "number") ? src.loopbackGap : 0,
    scale: (typeof src.scale === "number" && isFinite(src.scale) && src.scale > 0)
             ? src.scale : 1,
    segments: [],
  };
  for (const s of src.segments) {
    copy.segments.push({
      id: pairState.nextSegId++,
      name: s.name,
      a: s.a, b: s.b,
      filled: s.filled !== false,
    });
  }
  pairs.push(copy);
  pairState.selectedPair = pairs.length - 1;
  pairState.selectedSegment = null;
  notifyChanged();
}

export function deletePair(idx) {
  if (idx < 0 || idx >= pairs.length) return;
  pairs.splice(idx, 1);
  if (pairState.selectedPair >= pairs.length) {
    pairState.selectedPair = pairs.length - 1;
  }
  pairState.selectedSegment = null;
  notifyChanged();
}

export function movePair(idx, delta) {
  if (idx < 0 || idx >= pairs.length) return;
  const j = idx + delta;
  if (j < 0 || j >= pairs.length) return;

  const tmp = pairs[idx];
  pairs[idx] = pairs[j];
  pairs[j] = tmp;

  if (pairState.selectedPair === idx) pairState.selectedPair = j;
  else if (pairState.selectedPair === j) pairState.selectedPair = idx;

  if (pairState.selectedSegment) {
    if (pairState.selectedSegment.pairIdx === idx) pairState.selectedSegment.pairIdx = j;
    else if (pairState.selectedSegment.pairIdx === j) pairState.selectedSegment.pairIdx = idx;
  }

  state.shiftDragSet = null;
  notifyChanged();
}

export function addSegmentToPair(pairIdx, a, b) {
  if (pairIdx < 0 || pairIdx >= pairs.length) return;
  const p = pairs[pairIdx];
  const id = pairState.nextSegId++;
  p.segments.push({ id, name: "S" + id, a, b, filled: true });
  pairState.selectedPair = pairIdx;
  pairState.selectedSegment = { pairIdx, segIdx: p.segments.length - 1 };
  notifyChanged();
}

export function deleteSegment(pairIdx, segIdx) {
  if (pairIdx < 0 || pairIdx >= pairs.length) return;
  const p = pairs[pairIdx];
  if (segIdx < 0 || segIdx >= p.segments.length) return;
  p.segments.splice(segIdx, 1);

  const sel = pairState.selectedSegment;
  if (sel && sel.pairIdx === pairIdx) {
    if (sel.segIdx === segIdx) {
      pairState.selectedSegment = null;
    } else if (sel.segIdx > segIdx) {
      pairState.selectedSegment = { pairIdx, segIdx: sel.segIdx - 1 };
    }
  }

  state.shiftDragSet = null;
  notifyChanged();
}

/* ==========================================================================
   RESIZE / FIT
   ========================================================================== */

export function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  layout.topH      = Math.round(ch * 0.58);
  layout.dividerY  = layout.topH;
  layout.reservedY = layout.topH + 4;
  layout.reservedH = ch - layout.reservedY;

  fitView();
  _draw();
}
window.addEventListener("resize", resize);

export function fitView() {
  view.scale = 30 * view.zoom;
  view.tx = window.innerWidth / 2 + view.panX;
  view.ty = layout.topH / 2 + view.panY;
}
"""
