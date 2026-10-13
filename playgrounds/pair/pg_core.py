"""
pg_core.py — canvas, layout, view, pair model, geometry.

Two coordinate systems:

    world     the abstract 2D space the pairs live in
    screen    pixels, y flipped, origin top-left

A PAIR is:

    { id, name, cx, cy, angle, metricA, metricB, offset, gap,
      loopbackGap, scale, segments: [ { id, name, a, b, filled }, ... ] }

    cx, cy        anchor point of line A, in world coords
    angle         direction of the lines, radians
    metricA       world units per abstract unit along line A
    metricB       world units per abstract unit along line B
    offset        shift of line B along the direction of line A
    gap           perpendicular distance between the lines
    loopbackGap   extra abstract distance before the axis wraps
    scale         whole-pair zoom factor about (cx, cy); 1 = identity
    segments      abstract 1D intervals along the pair's axis

The unit direction u = (cos angle, sin angle).  The perpendicular is
v = (-sin angle, cos angle).

    Line A passes through (cx, cy) + u · (t · metricA · scale)
    Line B passes through (cx, cy) + u · (offset + t · metricB) · scale
                            + v · gap · scale

A segment [a, b] becomes the trapezoid with world corners

    A(a)  A(b)  B(b)  B(a)

which is what drawSegmentTrapezoid renders, clipped to the viewport.

PER-PAIR SCALE
==============
Each pair carries a `scale` field.  It scales the ENTIRE pair about
its anchor point (cx, cy): both lines, the gap, the offset, and the
two metrics — and therefore every trapezoid — grow or shrink by
that factor, as if the canvas zoom applied to that one pair alone.
The anchor point itself does not move; the pair's position on
screen is unchanged.  Abstract segment intervals [a, b] never change
with scale: a segment that spans one abstract unit spans one
abstract unit, whether the pair is scaled to 0.5, 1, or 3.

The field is a visual transform only.  It does not affect the
segment view (which is an editor over abstract coordinates), the
wrap period, the KRA export's trapezoid layout on the .kra page
(which reproduces the screen footprint at the moment of export), or
the overlap-resolution search: the latter compares screen-space
polygons, so a scaled pair simply presents a different footprint to
the test, and the abstract-unit shift it applies to the lower pair
is still found by the same coarse-then-binary search.

A missing or invalid scale reads as 1.  This is what every pair
starts with, and what every scene written before the field existed
loads as; see pg_scene.py.

THE SEGMENT AXIS IS PERIODIC
============================
Each pair has its own abstract period:

    period(p) = SEG_X_SPAN + max(0, p.loopbackGap || 0)

A segment [a, b] and every [a + k·period(p), b + k·period(p)] for
integer k describe the same physical segment; only the choice of
"which lap" differs.  Both the segment view and the canvas view
draw every copy that intersects their own visible region, so a
segment dragged past the right edge of the segment view reappears
on the left, and the corresponding trapezoid on the pair of lines
likewise reappears at the opposite end.

`forEachSegmentCopy(period, aBase, bBase, fn)` iterates every k in
[−SEGMENT_WRAP_K, +SEGMENT_WRAP_K] and calls fn with the shifted
a and b.  The period is passed in (not read from a global) so that
each pair's own loopback gap is honoured.  SEGMENT_WRAP_K is
generous enough for every realistic metric / view-scale combination
in this playground.

`segmentCornersWorldAt(p, a, b)` returns the four world corners of
a trapezoid whose abstract endpoints are explicitly (a, b); the
`segmentCornersWorld(p, seg)` form is a convenience for the k = 0
copy.  Drags and copies alike call the explicit form so the wrapped
geometry is always computed from the same three lines of math.

LOOPBACK GAP
============
The `loopbackGap` field extends the pair's period from SEG_X_SPAN
to SEG_X_SPAN + loopbackGap.  At 0 the historic behaviour applies:
copies are spaced one visible-span apart, so a segment dragged past
one end of the visible window immediately has a wrapped copy at the
other end.  As the value rises, the copies spread further apart, and
a segment dragged past the right edge of the segment view has to
travel the extra distance before its k = −1 copy emerges from the
left edge.  There is a stretch of that length where neither the
segment nor its copy is on screen — the "imaginary" gap the field
names.

The value is per-pair and expressed in abstract units.  It affects:

    * the segment view (drawing and hit testing of every copy);
    * the canvas (drawing and hit testing of every trapezoid copy);
    * the overlap detection and resolution, since those compare the
      screen-space positions of the wrapped copies;
    * the panel's saved scene, which round-trips the value.

It does NOT affect the KRA export, which writes the k = 0 copy of
each segment as it always did, nor the pair's geometry (its lines,
its metrics, its offset, its gap).

SEGMENT LIFECYCLE AND THE PANEL LISTS
=====================================
`addSegmentToPair` and `deleteSegment` are the only two operations
that change the segments array's length.  Both end with a call to
`syncPairList()`, which rebuilds the pair list, the segment list,
and the scrub fields in one go.

PAIR ORDER
==========
The order of the `pairs` array is the pair hierarchy.  Every view
that renders the pairs iterates the array in index order, so
rearranging the array rearranges every view at once.  A pair's hue
is derived from `p.id`, not from its array position.

OVERLAP BETWEEN SEGMENTS ON DIFFERENT PAIRS
===========================================
`_segmentOverlapsOtherPair(pairIdx, segIdx)` is a screen-space
polygon-polygon test over the visible wrapped copies.  See the
implementation comments for the SAT math and the cost-bounding
tricks.  Two segments on the same pair are never compared.

OVERLAP RESOLUTION — "MAKE ROOM"
================================
`_makeRoomInferior(pairIdx, segIdx)` backs the panel's "make room"
button.  See the implementation comments for the algorithm.
"""

CORE_JS = r"""
/* ==========================================================================
   CANVAS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx    = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

/* ==========================================================================
   LAYOUT
   ========================================================================== */

const layout = {
  topH:      0,
  dividerY:  0,
  reservedY: 0,
  reservedH: 0,
};

/* ==========================================================================
   VIEW
   ========================================================================== */

const view = {
  scale: 3,
  tx: 0, ty: 0,
  zoom: 1, panX: 0, panY: 0,
};

function w2s(x, y) { return [x * view.scale + view.tx,
                             -y * view.scale + view.ty]; }
function s2w(sx, sy) { return [(sx - view.tx) / view.scale,
                               -(sy - view.ty) / view.scale]; }

/* ==========================================================================
   SEGMENT AXIS — PERIOD AND WRAP HELPERS
   ==========================================================================
   See the module docstring, THE SEGMENT AXIS IS PERIODIC, and
   LOOPBACK GAP. */

const SEG_X_MIN  = -6;
const SEG_X_MAX  =  6;
const SEG_X_SPAN = SEG_X_MAX - SEG_X_MIN;

const SEGMENT_WRAP_K = 20;

/* Per-pair period of the segment axis.  See the module docstring,
   LOOPBACK GAP.  Falls back to the raw span when the pair or its
   gap is missing, so callers that pass null get the historic
   behaviour. */
function _pairPeriod(p) {
  if (!p) return SEG_X_SPAN;
  const g = (typeof p.loopbackGap === "number" && p.loopbackGap > 0)
    ? p.loopbackGap : 0;
  return SEG_X_SPAN + g;
}

/* Per-pair whole-pair zoom factor.  See the module docstring,
   PER-PAIR SCALE.  Missing or invalid values read as 1, so pairs
   that predate the field — and every fresh pair — behave as if the
   field were absent. */
function _pairScale(p) {
  if (!p) return 1;
  const s = p.scale;
  return (typeof s === "number" && isFinite(s) && s > 0) ? s : 1;
}

/* Iterate every integer k in [−SEGMENT_WRAP_K, +SEGMENT_WRAP_K],
   calling fn(aBase + k·period, bBase + k·period, k).  The period
   comes from the caller so each pair uses its own loopback gap. */
function forEachSegmentCopy(period, aBase, bBase, fn) {
  for (let k = -SEGMENT_WRAP_K; k <= SEGMENT_WRAP_K; k++) {
    fn(aBase + k * period, bBase + k * period, k);
  }
}

/* ==========================================================================
   OVERLAP QUERY — CANVAS-SPACE POLYGON INTERSECTION
   ========================================================================== */

const OVERLAP_HUE = { r: 255, g: 90, b: 105 };

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
  /* The world displacement of one k-step is period · metricA · scale;
     the world-to-screen factor is view.scale.  The pair scale is
     folded in here so the on-screen step matches what
     pointOnPairLine produces for the wrapped copies. */
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

function _segmentTrapezoidsOverlap(p1, seg1, p2, seg2) {
  const cw = window.innerWidth;
  const ch = layout.topH;
  const MARGIN = 4;

  const r1 = _wrapRangeForCanvas(p1, seg1, cw, ch);
  const r2 = _wrapRangeForCanvas(p2, seg2, cw, ch);

  const bb2Base = _segAABB(_segPolygonAtK(p2, seg2, 0));
  const period2 = _pairPeriod(p2);
  /* Same fold-in of the pair scale as in _wrapRangeForCanvas: the
     screen displacement of one k-step of p2 must account for p2's
     own scale. */
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

function _segmentOverlapsOtherPair(pairIdx, segIdx) {
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

function _pairHasAnyOverlap(pairIdx) {
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

function _segmentOverlapsPairBelow(pairIdx, segIdx) {
  if (pairIdx < 0 || pairIdx + 1 >= pairs.length) return false;
  const pUpper = pairs[pairIdx];
  if (segIdx < 0 || segIdx >= pUpper.segments.length) return false;
  const segUpper = pUpper.segments[segIdx];
  const pLower = pairs[pairIdx + 1];
  for (let j = 0; j < pLower.segments.length; j++) {
    if (_segmentTrapezoidsOverlap(pUpper, segUpper, pLower,
                                  pLower.segments[j])) {
      return true;
    }
  }
  return false;
}

function _segmentsOverlapWithShift(pUpper, segUpper, pLower, segLower,
                                   shift) {
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

function _resolveOverlapDeltaDirection(pUpper, segUpper, pLower, segLower,
                                       sign) {
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
  const dPos = _resolveOverlapDeltaDirection(pUpper, segUpper, pLower,
                                             segLower, +1);
  const dNeg = _resolveOverlapDeltaDirection(pUpper, segUpper, pLower,
                                             segLower, -1);
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
    if (!_segmentTrapezoidsOverlap(pUpper, segUpper, pLower, segLower)) {
      continue;
    }
    const sol = _resolveOverlapDelta(pUpper, segUpper, pLower, segLower);
    if (!sol) continue;
    if (!best || sol.delta < best.delta) {
      best = {
        lowerPairIdx: pairIdx + 1,
        lowerSegIdx:  j,
        direction:    sol.direction,
        delta:        sol.delta,
      };
    }
  }
  return best;
}

function _makeRoomInferior(pairIdx, segIdx) {
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
      const follows = (dir > 0) ? (c >= pivotCenter)
                                : (c <= pivotCenter);
      if (follows) {
        s.a += delta;
        s.b += delta;
      }
    }
    resolved++;
  }
  return resolved;
}

/* ==========================================================================
   PAIR MODEL
   ========================================================================== */

const pairs = [];
let nextPairId   = 1;
let nextSegId    = 1;
let selectedPair    = -1;
let selectedSegment = null;   /* { pairIdx, segIdx } */

function pairFrame(p) {
  const ux = Math.cos(p.angle);
  const uy = Math.sin(p.angle);
  return {
    ux, uy,
    vx: -uy,
    vy:  ux,
  };
}

/* Every world position on either line goes through this function,
   so it is the single place where the pair's own `scale` is folded
   into the geometry.  See the module docstring, PER-PAIR SCALE.
   The anchor point (cx, cy) is the fixed point of the scale: at
   abstractT = 0 on line A, every factor of scale multiplies zero,
   so A(0) stays exactly at (cx, cy) for every scale. */
function pointOnPairLine(p, which, abstractT) {
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

function segmentCornersWorldAt(p, a, b) {
  return [
    pointOnPairLine(p, "A", a),
    pointOnPairLine(p, "A", b),
    pointOnPairLine(p, "B", b),
    pointOnPairLine(p, "B", a),
  ];
}

function segmentCornersWorld(p, seg) {
  return segmentCornersWorldAt(p, seg.a, seg.b);
}

const LINE_REACH = 1e5;

function lineEndpointsWorld(p, which) {
  const f = pairFrame(p);
  const [ax, ay] = pointOnPairLine(p, which, 0);
  return [
    [ax - LINE_REACH * f.ux, ay - LINE_REACH * f.uy],
    [ax + LINE_REACH * f.ux, ay + LINE_REACH * f.uy],
  ];
}

/* ==========================================================================
   LIFECYCLE
   ========================================================================== */

function pairHue(p) {
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

function _huergb(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}

function _huergbLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

function addPair() {
  const id  = nextPairId++;
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
  selectedPair    = pairs.length - 1;
  selectedSegment = null;
  syncPairList();
  draw();
}

function clonePair(idx) {
  if (idx < 0 || idx >= pairs.length) return;
  const src = pairs[idx];
  const id  = nextPairId++;
  const copy = {
    id, name: "P" + id,
    cx: src.cx, cy: src.cy - 2.5,
    angle: src.angle,
    metricA: src.metricA,
    metricB: src.metricB,
    offset: src.offset,
    gap: src.gap,
    loopbackGap: (typeof src.loopbackGap === "number")
                   ? src.loopbackGap : 0,
    scale: (typeof src.scale === "number" && isFinite(src.scale)
              && src.scale > 0)
             ? src.scale : 1,
    segments: [],
  };
  for (const s of src.segments) {
    copy.segments.push({
      id: nextSegId++,
      name: s.name,
      a: s.a, b: s.b,
      filled: s.filled !== false,
    });
  }
  pairs.push(copy);
  selectedPair    = pairs.length - 1;
  selectedSegment = null;
  syncPairList();
  draw();
}

function deletePair(idx) {
  if (idx < 0 || idx >= pairs.length) return;
  pairs.splice(idx, 1);
  if (selectedPair >= pairs.length) selectedPair = pairs.length - 1;
  selectedSegment = null;
  syncPairList();
  draw();
}

function movePair(idx, delta) {
  if (idx < 0 || idx >= pairs.length) return;
  const j = idx + delta;
  if (j < 0 || j >= pairs.length) return;

  const tmp = pairs[idx];
  pairs[idx] = pairs[j];
  pairs[j] = tmp;

  if (selectedPair === idx) selectedPair = j;
  else if (selectedPair === j) selectedPair = idx;

  if (selectedSegment) {
    if (selectedSegment.pairIdx === idx) selectedSegment.pairIdx = j;
    else if (selectedSegment.pairIdx === j) selectedSegment.pairIdx = idx;
  }

  /* Reordering can invalidate a shift-drag set's membership. */
  if (typeof _clearShiftDragSet === "function") _clearShiftDragSet();

  syncPairList();
  draw();
}

function addSegmentToPair(pairIdx, a, b) {
  if (pairIdx < 0 || pairIdx >= pairs.length) return;
  const p = pairs[pairIdx];
  const id = nextSegId++;
  p.segments.push({
    id, name: "S" + id,
    a, b,
    filled: true,
  });
  selectedPair    = pairIdx;
  selectedSegment = { pairIdx, segIdx: p.segments.length - 1 };
  syncPairList();
  draw();
}

function deleteSegment(pairIdx, segIdx) {
  if (pairIdx < 0 || pairIdx >= pairs.length) return;
  const p = pairs[pairIdx];
  if (segIdx < 0 || segIdx >= p.segments.length) return;
  p.segments.splice(segIdx, 1);

  if (selectedSegment &&
      selectedSegment.pairIdx === pairIdx) {
    if (selectedSegment.segIdx === segIdx) {
      selectedSegment = null;
    } else if (selectedSegment.segIdx > segIdx) {
      selectedSegment = {
        pairIdx,
        segIdx: selectedSegment.segIdx - 1,
      };
    }
  }

  /* The segment that was in the shift-drag set is gone (or the
     indices in the set are stale); drop the set. */
  if (typeof _clearShiftDragSet === "function") _clearShiftDragSet();

  syncPairList();
  draw();
}

/* ==========================================================================
   RESIZE / FIT
   ========================================================================== */

function resize() {
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
  draw();
}
window.addEventListener("resize", resize);

function fitView() {
  view.scale = 30 * view.zoom;
  view.tx = window.innerWidth / 2 + view.panX;
  view.ty = layout.topH / 2 + view.panY;
}
"""
