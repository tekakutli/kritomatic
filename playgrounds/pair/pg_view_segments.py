"""
pg_view_segments.py — the lower band (segment view).

One row per pair.  Each row is an axis: a pair's abstract coordinate
mapped linearly to the row's pixel width.  Each segment is a bar
along that axis.  Filled segments are drawn solid; empty ones are
drawn as an outline only.

THE AXIS IS PERIODIC — AND EACH ROW SCALES TO ITS OWN PERIOD
============================================================
See pg_core.py, THE SEGMENT AXIS IS PERIODIC, and LOOPBACK GAP.  A
segment whose abstract [a, b] crosses either end of the row's range
is drawn at both ends: its k = 0 copy is clipped at the boundary,
and its k = -1 or k = +1 copy appears on the opposite side.  The
step between copies is the pair's own period, `_pairPeriod(p)`,
which is `SEG_X_SPAN + max(0, p.loopbackGap || 0)`.

    loopbackGap = 0   the historic behaviour: copies are one
                      SEG_X_SPAN apart, and the row spans exactly
                      SEG_X_SPAN, so a segment dragged past one
                      edge immediately has its wrapped copy at
                      the other.

    loopbackGap > 0   the copies are further apart.  If the row
                      kept the historic SEG_X_SPAN span, there
                      would be a stretch of the abstract axis where
                      neither the segment nor its wrapped copy lay
                      inside the visible range — the "hidden gap"
                      the field names.

To keep every segment on a pair visible, EACH ROW scales
independently to span exactly one period of ITS OWN pair:

    row abstract range  =  [-_pairPeriod(p)/2, +_pairPeriod(p)/2]

At loopbackGap = 0 on a pair, this collapses to the historic
[-SEG_X_SPAN/2, +SEG_X_SPAN/2] = [SEG_X_MIN, SEG_X_MAX], so a pair
without a gap looks exactly as it did before.  A pair with a gap
gets a proportionally wider abstract range in the same pixel
width, so its wrapped copies sit exactly one row-width apart and
no segment can fall into a stretch where neither it nor its
wrapped copy is on screen.

Rows therefore do NOT share a horizontal scale: a segment at
abstract coordinate x sits at a different pixel column on a pair
with a gap than it does on a pair without one.  That is the point
of the field — the gap is a property of the pair's own axis, not
of the view.  A pair with a gap and a pair without one keep their
own zoom side by side.

`segXToScreen(x, p)` and `segScreenToX(sx, p)` are the only two
functions that know about the per-pair span.  When `p` is null
they fall back to the base SEG_X_SPAN — which is what the segment
view's top ruler uses, so that ruler still reads as the historic
[-6, +6] reference.  Rows with a gap will not line up with that
ruler; their own axis line at the row's vertical centre is what
tells the user where they are on that pair's scale.

EDGE-HIT THRESHOLD IS IN PIXELS, NOT ABSTRACT UNITS
===================================================
`SEG_EDGE_HIT_PX` is the pixel distance from a segment's edge within
which a click counts as an edge grab rather than a body grab.  The
hit test converts it to abstract units on the fly, using the row's
current width and the row's own pair, so the threshold stays a
constant number of pixels regardless of the row's width or its
abstract span.

HIT PRIORITY WITHIN A ROW
=========================
The hit test iterates every segment in the clicked row, not just
until it finds a match.  Among all edges within the pixel threshold,
the closest one wins; the closest edge, if any, beats every body
hit.  Only when no edge is within range does the segment whose body
contains the cursor get selected.

OVERLAP MODE
============
When the "Overlap" checkbox is on, every segment that shares
screen-space territory with a segment on a different pair gets a
red dashed outline over its usual stroke — drawn per wrapped copy.
See pg_core.py, OVERLAP BETWEEN SEGMENTS ON DIFFERENT PAIRS.

SHIFT-DRAG SET MARKER
=====================
When a segment is a member of the current shift-drag set
(`state.shiftDragSet`), its bar is drawn a second time with a
bright white dashed outline.  See pg_dispatch.py, SHIFT-DRAG SET.

Interaction:

    click on a segment's left or right edge   drag to widen / shrink
    click on a segment's body                 select it, then drag
                                              the whole pair's segment
                                              row together.  With
                                              Shift held, the press
                                              adds the segment to
                                              the shift-drag set and
                                              the drag moves only the
                                              set's members.
    click on empty space in a row             select the pair
    Draw Segment mode                         click anywhere in a
                                              row to drop a segment
                                              of default width there

The band is scrollable with the wheel; state.segScroll holds the
vertical offset in pixels.
"""

SEGMENTS_VIEW_JS = r"""
/* ==========================================================================
   SEGMENT VIEW — mapping
   ==========================================================================
   SEG_X_MIN, SEG_X_MAX and SEG_X_SPAN are defined in pg_core.py.
   The per-pair period is `_pairPeriod(p)`.  Each row maps one
   period of ITS OWN pair to the row's pixel width, so pairs with
   different loopback gaps scale independently of one another.  See
   the module docstring, THE AXIS IS PERIODIC. */

const ROW_HEIGHT = 28;
const ROW_GAP    = 4;
const ROW_STRIDE = ROW_HEIGHT + ROW_GAP;

/* Edge-hit radius, in PIXELS.  The hit test converts this to
   abstract units using the row's current width and the row's own
   pair. */
const SEG_EDGE_HIT_PX = 8;

const DEFAULT_SEGMENT_HALF_WIDTH = 0.8;

/* Abstract span of the row for pair `p`.  At loopbackGap = 0 this
   is SEG_X_SPAN; a nonzero loopbackGap widens it by exactly the
   gap, so the pair's wrapped copies land one row-width apart and
   no segment enters a hidden stretch of the axis.  When `p` is
   null — the top ruler, and any caller without a pair in hand —
   the base span SEG_X_SPAN is used. */
function _pairSpan(p) {
  if (!p) return SEG_X_SPAN;
  return _pairPeriod(p);
}

function _pairViewMin(p) { return -_pairSpan(p) / 2; }
function _pairViewMax(p) { return  _pairSpan(p) / 2; }

function segRect() {
  const W = window.innerWidth;
  const MARGIN_X = 40;
  const x0 = MARGIN_X;
  const w  = W - 2 * MARGIN_X;
  const y0 = layout.reservedY + 4;
  const h  = layout.reservedH - 8;
  return { x0, y0, w, h };
}

/* Both directions need the same pair context.  Passing the row's
   own pair keeps the abstract-to-pixel mapping consistent between
   the drawing pass, the hit test, and the drag arithmetic — all
   three call these two functions with the same `p`, so a drag
   delta is the same number at mousedown and at every mousemove. */
function segXToScreen(x, p) {
  const r = segRect();
  const span = _pairSpan(p);
  const u = (x - _pairViewMin(p)) / span;
  return r.x0 + u * r.w;
}

function segScreenToX(sx, p) {
  const r = segRect();
  const span = _pairSpan(p);
  return _pairViewMin(p) + (sx - r.x0) / r.w * span;
}

function rowTopY(idx) {
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

function drawSegmentsView() {
  const cw = window.innerWidth;
  ctx.fillStyle = "#060a10";
  ctx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = segRect();

  ctx.save();
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  /* Top ruler.  This is the historic, base-scale reference at
     SEG_X_SPAN; it is NOT the scale of every row.  Rows carrying a
     nonzero loopbackGap have their own wider abstract range and do
     not line up with these ticks — the row's own axis line is what
     tells the user where they are on that pair's scale.  The
     ruler exists so that a workspace with no gaps anywhere still
     reads exactly as it always did. */
  ctx.font = "700 9px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "bottom";
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
    if (major) {
      ctx.fillText(String(x), sx, r.y0 - 6);
    }
  }
  ctx.restore();

  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.w, r.h);
  ctx.clip();

  if (pairs.length === 0) {
    ctx.fillStyle = "#3d4756";
    ctx.font = "italic 11px 'JetBrains Mono', 'Fira Code', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
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

  /* Draw-mode cursor hint.  The preview copies must use the period
     AND the abstract-to-pixel scale of the row the cursor is over.
     Between rows, fall back to the base span so the hint still
     reads as a plausible placement on the historic scale. */
  if (state.drawSegmentMode &&
      state.mouse.inside && state.mouse.sy >= layout.reservedY) {
    const sx = state.mouse.sx;
    if (sx >= r.x0 && sx <= r.x0 + r.w) {
      let hintPair = null;
      for (let i = 0; i < pairs.length; i++) {
        const top = rowTopY(i);
        if (state.mouse.sy >= top && state.mouse.sy <= top + ROW_HEIGHT) {
          hintPair = pairs[i];
          break;
        }
      }
      const hintPeriod = hintPair ? _pairPeriod(hintPair) : SEG_X_SPAN;
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
        ctx.strokeRect(ax + 0.5, r.y0 + 4.5,
                       (bx - ax) - 1, r.h - 9);
      });
      ctx.setLineDash([]);
      ctx.restore();
    }
  }
}

function drawSegmentRow(p, pairIdx, top, r) {
  const hue = pairHue(p);
  const isSelPair = (pairIdx === selectedPair);

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
  ctx.textAlign = "left";
  ctx.textBaseline = "middle";
  ctx.fillStyle = _huergb(hue, isSelPair ? 1.0 : 0.7);
  ctx.fillText(p.name, r.x0 - 30, top + ROW_HEIGHT / 2);
  ctx.restore();

  for (let j = 0; j < p.segments.length; j++) {
    const seg = p.segments[j];
    drawSegmentBar(p, seg, hue, top, pairIdx, j, isSelPair);
  }
}

function drawSegmentBar(p, seg, hue, top, pairIdx, segIdx, isSelPair) {
  const r = segRect();

  const isSel = selectedSegment &&
                selectedSegment.pairIdx === pairIdx &&
                selectedSegment.segIdx  === segIdx;

  const y0 = top + 4;
  const y1 = top + ROW_HEIGHT - 4;
  const filled = seg.filled !== false;

  const period = _pairPeriod(p);
  const vMin   = _pairViewMin(p);
  const vMax   = _pairViewMax(p);

  const overlapOn = state.overlapHighlight &&
                    _segmentOverlapsOtherPair(pairIdx, segIdx);

  /* Set-member marker, orthogonal to the selected and overlap
     markers.  See the module docstring, SHIFT-DRAG SET MARKER. */
  const isSetMember = _isShiftSetMember(pairIdx, segIdx);

  /* Every wrapped copy of the segment that intersects this row's
     own visible abstract range.  Because the range is exactly one
     period of `p`, the pair's wrapped copies sit one row-width
     apart: for any position on the pair's axis, at least one copy
     is on screen, so no segment can be lost inside a hidden gap.
     The same `forEachSegmentCopy` and the same `_pairPeriod(p)` are
     used here as on the canvas — this file simply maps the copies
     with the pair's own span instead of a global one. */
  forEachSegmentCopy(period, seg.a, seg.b, (a, b, k) => {
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
      ctx.strokeRect(sxa + 0.5, y0 + 0.5,
                     sxb - sxa - 1, y1 - y0 - 1);
    } else {
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = _huergb(hue, isSel ? 0.9 : 0.5);
      ctx.lineWidth = 1.2;
      ctx.strokeRect(sxa + 0.5, y0 + 0.5,
                     sxb - sxa - 1, y1 - y0 - 1);
      ctx.setLineDash([]);
    }

    /* Overlap overlay: red dashed outline. */
    if (overlapOn) {
      ctx.save();
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = _huergb(OVERLAP_HUE, 0.95);
      ctx.lineWidth = 2.2;
      ctx.strokeRect(sxa + 0.5, y0 + 0.5,
                     sxb - sxa - 1, y1 - y0 - 1);
      ctx.restore();
    }

    /* Set-member overlay: bright white dashed outline, inset. */
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

    /* Edge handles, drawn when the segment is selected.  Handles
       appear on the actual abstract endpoints (a and b of this
       copy) whenever those endpoints lie inside this row's own
       visible range.  A clipped edge (an end outside the range)
       is not drawn. */
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

/* ==========================================================================
   SEGMENT VIEW — hit testing
   ==========================================================================
   A cursor near any wrapped copy of a segment hits that segment.  An
   edge hit reports which abstract endpoint (a or b) of the base
   segment is being grabbed; the caller applies the drag as a delta,
   so it does not matter which lap the cursor was on.

   Wrapped copies are spaced by the pair's own period; the row's
   visible abstract range is exactly that period, so for every pair
   the copies are one row-width apart and at least one copy is
   always on screen.  Only endpoints that lie inside the visible
   range are grabbable; an endpoint clipped at a row boundary is
   not. */

function segmentHitTest(sx, sy) {
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

  let bestEdgeSeg  = -1;
  let bestEdgeSide = null;
  let bestEdgeDist = edgeThresh;

  let bodySeg = -1;

  for (let j = 0; j < p.segments.length; j++) {
    const seg = p.segments[j];
    let segBody = false;

    forEachSegmentCopy(period, seg.a, seg.b, (a, b) => {
      if (b < vMin || a > vMax) return;

      /* Edge candidates: only abstract endpoints that lie inside
         this row's own visible range.  A clipped endpoint (outside
         the range) is not directly grabbable. */
      if (a >= vMin && a <= vMax) {
        const d = Math.abs(xc - a);
        if (d < bestEdgeDist) {
          bestEdgeDist = d;
          bestEdgeSeg  = j;
          bestEdgeSide = "a";
        }
      }
      if (b >= vMin && b <= vMax) {
        const d = Math.abs(xc - b);
        if (d < bestEdgeDist) {
          bestEdgeDist = d;
          bestEdgeSeg  = j;
          bestEdgeSide = "b";
        }
      }

      /* Body candidates: the cursor inside the visible portion of
         this copy.  The first segment whose body contains the
         cursor claims the body slot. */
      if (bodySeg < 0) {
        const aCl = Math.max(a, vMin);
        const bCl = Math.min(b, vMax);
        if (xc > aCl && xc < bCl) segBody = true;
      }
    });

    if (segBody && bodySeg < 0) bodySeg = j;
  }

  if (bestEdgeSeg >= 0) {
    return { kind: "edge", pairIdx: rowIdx,
             segIdx: bestEdgeSeg, side: bestEdgeSide };
  }
  if (bodySeg >= 0) {
    return { kind: "body", pairIdx: rowIdx, segIdx: bodySeg };
  }
  return { kind: "row", pairIdx: rowIdx };
}

function createSegmentAt(sx, sy) {
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
    if (i !== selectedPair) {
      selectedPair = i;
      syncPairList();
    }
    return { pairIdx: i };
  }
  return null;
}
"""
