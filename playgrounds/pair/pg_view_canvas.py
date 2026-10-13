"""
pg_view_canvas.py — the upper band.

Draws each pair's two lines from one edge of the viewport to the
other, then the trapezoid of every filled segment.  A pair that is
selected is drawn slightly brighter; its anchor point carries a small
marker that can be dragged.

THE TRAPEZOIDS WRAP
===================
A segment lives on the pair's periodic abstract axis.  On the canvas
its trapezoid is drawn once per lap of the axis whose screen
bounding box intersects the top band: the k = 0 copy at the segment's
own abstract position, and every wrapped copy at a + k·span, b +
k·span.  A segment dragged off one end of the visible portion of the
pair therefore reappears at the other end, in lock-step with the
segment view.

The trapezoid is a quadrilateral in world coords; the drawing code
projects its four corners with w2s and fills.  Empty segments are
drawn as a dashed outline only, so the user can see where they are
without them reading as solid geometry.

PER-PAIR SCALE
==============
Each pair carries a `scale` field (see pg_core.py, PER-PAIR SCALE).
Because every world position on a pair's lines goes through
`pointOnPairLine`, this file inherits the scale for free: the two
lines, the trapezoids, and the anchor marker's neighbours all
reflect it without an explicit multiply here.  The one place that
must be told explicitly is the pair's name label, which is placed at
the visual midpoint of the pair's "patch": it computes that midpoint
via `pointOnPairLine` so it follows the scale.

OVERLAP MODE
============
When the "Overlap" checkbox is on, every trapezoid whose segment
shares abstract interval space with a segment on a different pair
is drawn a second time as a red dashed outline over its usual
stroke.  See pg_core.py, OVERLAP BETWEEN SEGMENTS ON DIFFERENT
PAIRS.  The overlay is drawn per wrapped copy, so a wrapping
segment that overlaps on one lap but not another shows the red
outline only on the laps where the overlap actually happens.

SEGMENT DRAG IN THE CANVAS BAND
===============================
A segment's trapezoid (any of its wrapped copies) is clickable-and-
draggable, and dragging it moves the whole pair — both lines and
every segment — by the same world translation.  This is the same
pair drag the anchor marker and the two lines start; see
pg_dispatch.py, PAIR DRAG.  The segment's trapezoid being a
first-class target means the user does not have to find a thin line
to grab the pair; clicking the filled (or dashed) shape on the
canvas is enough.
"""

CANVAS_VIEW_JS = r"""
function drawCanvasView() {
  const cw = window.innerWidth;
  const ch = layout.topH;

  /* background */
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  /* world grid — a light dot pattern every ~5 world units, so the
     canvas has a sense of scale without competing with the pairs. */
  {
    const step = 5;
    const [sx0, sy0] = w2s(0, 0);
    const spx = step * view.scale;
    if (spx > 6) {
      const offX = ((sx0 % spx) + spx) % spx;
      const offY = ((sy0 % spx) + spx) % spx;
      ctx.fillStyle = "rgba(30, 40, 54, 0.5)";
      for (let x = offX; x < cw; x += spx) {
        for (let y = offY; y < ch; y += spx) {
          ctx.fillRect(x, y, 1, 1);
        }
      }
    }
  }

  /* pairs: unselected first, selected on top */
  for (let i = 0; i < pairs.length; i++) {
    if (i === selectedPair) continue;
    drawPair(pairs[i], i, false);
  }
  if (selectedPair >= 0 && selectedPair < pairs.length) {
    drawPair(pairs[selectedPair], selectedPair, true);
  }

  /* caption */
  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText(
    "PAIRS " + pairs.length +
    "   SELECTED " + (selectedPair >= 0
                       ? pairs[selectedPair].name : "—"),
    14, 14);

  if (state.overlapHighlight) {
    ctx.fillStyle = _huergb(OVERLAP_HUE, 0.95);
    ctx.fillText("\u25CF OVERLAP MODE", 14, 28);
  }
  ctx.restore();

  /* per-pair name labels, drawn last */
  for (let i = 0; i < pairs.length; i++) {
    drawPairLabel(pairs[i], i === selectedPair);
  }
}

/* `pairIdx` is passed so the overlap test can identify the pair
   without an O(n) lookup per segment; the caller already iterates
   the pairs array with the index in hand. */
function drawPair(p, pairIdx, selected) {
  const hue = pairHue(p);

  /* the two lines, clipped to the viewport */
  drawPairLine(p, "A", hue, selected);
  drawPairLine(p, "B", hue, selected);

  /* trapezoids for the segments, one per wrapped copy */
  for (let j = 0; j < p.segments.length; j++) {
    const seg = p.segments[j];
    const isSelSeg = selectedSegment &&
                     selectedSegment.pairIdx === pairIdx &&
                     selectedSegment.segIdx === j;
    drawSegmentTrapezoid(p, pairIdx, seg, j, hue, selected, isSelSeg);
  }

  /* anchor marker, only when the pair is selected */
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

/* ==========================================================================
   TRAPEZOID DRAWING — WITH WRAPPING
   ==========================================================================
   For each integer k in [-SEGMENT_WRAP_K, +SEGMENT_WRAP_K], compute
   the trapezoid whose abstract endpoints are seg.a + k·SEG_X_SPAN and
   seg.b + k·SEG_X_SPAN.  Skip it when its screen bounding box does
   not intersect the top band.  Draw the rest.  A segment dragged off
   the visible end of the pair reappears at the other end because some
   of its wrapped copies land back inside the viewport.

   The overlap overlay, when the mode is on and the segment overlaps
   a segment on another pair, is drawn per wrapped copy as well: the
   red outline appears only on the laps whose abstract range actually
   contains the overlap.  This is why the overlay is inside the
   per-copy loop rather than after it. */

const TRAPEZOID_MARGIN_PX = 24;

function _trapezoidScreenCornersForK(p, seg, k) {
  const period = _pairPeriod(p);
  const a = seg.a + k * period;
  const b = seg.b + k * period;
  const corners = segmentCornersWorldAt(p, a, b);
  return corners.map(([wx, wy]) => w2s(wx, wy));
}

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
                    _segmentOverlapsOtherPair(pairIdx, segIdx);

  forEachSegmentCopy(_pairPeriod(p), seg.a, seg.b, (a, b, k) => {
    const w = segmentCornersWorldAt(p, a, b);
    const screen = w.map(([wx, wy]) => w2s(wx, wy));
    const bb = _screenBBox(screen);
    if (!_bboxIntersectsTopBand(bb, cw, ch)) return;

    ctx.save();
    ctx.beginPath();
    ctx.moveTo(screen[0][0], screen[0][1]);
    for (let q = 1; q < 4; q++) {
      ctx.lineTo(screen[q][0], screen[q][1]);
    }
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

    /* Overlap overlay: red dashed outline drawn over the segment's
       own stroke.  Drawn per copy, so only the laps that contain
       the actual overlap are marked. */
    if (overlapOn) {
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = _huergb(OVERLAP_HUE, 0.95);
      ctx.lineWidth = 2.2;
      ctx.beginPath();
      ctx.moveTo(screen[0][0], screen[0][1]);
      for (let q = 1; q < 4; q++) {
        ctx.lineTo(screen[q][0], screen[q][1]);
      }
      ctx.closePath();
      ctx.stroke();
      ctx.restore();
    }

    /* corner tick marks when the segment is selected */
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

function drawPairLabel(p, selected) {
  const hue = pairHue(p);

  /* place the label at the midpoint of the patch between the two
     anchor points.  Both anchors come from pointOnPairLine, so a
     nonzero pair scale moves the midpoint along with the geometry
     — a shrunk pair's label sits closer to the shared anchor than
     an unscaled pair's, and an expanded pair's label is pushed
     further out. */
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
"""
