"""
pg_view_canvas.py — the upper band.

Draws each pair's two lines from one edge of the viewport to the
other, then the trapezoid of every filled segment.  A pair that is
selected is drawn slightly brighter; its anchor point carries a small
marker that can be dragged.

THE TRAPEZOIDS WRAP
===================
A segment lives on the pair's periodic abstract axis.  Its wrapped
copies are drawn once per k whose copy could intersect the top band.
The k-range is computed by `_kRangeForSegmentInBand` (in pg_core.py)
rather than iterating the full [-20, +20]; for typical metrics and
zoom levels only 2–8 of those copies are on screen.

PER-FRAME OVERLAP MEMO
======================
`_cachedSegmentOverlaps` (in pg_core.py) memoizes the SAT overlap
test within one frame.  Both this file and pg_view_segments.py query
the same (pairIdx, segIdx) pairs, so the memo halves the total cost
without any risk of staleness — the memo is reset at the top of
every draw() in pg_dispatch.py.

GRID BATCHING
=============
The background grid is drawn as a single path of small 1×1 rects
filled once, rather than as thousands of per-rect fillRect calls.
The result is pixel-identical and much faster.
"""

CANVAS_VIEW_JS = r"""
function drawCanvasView() {
  const cw = window.innerWidth;
  const ch = layout.topH;

  /* Background is cleared by the caller (pg_dispatch.py's draw()).
     No second clear here. */

  /* World grid — one path of 1×1 rects, filled once.  See the
     module docstring, GRID BATCHING. */
  {
    const step = 5;
    const [sx0, sy0] = w2s(0, 0);
    const spx = step * view.scale;
    if (spx > 6) {
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

/* `pairIdx` is passed so the overlap memo can identify the pair
   without an O(n) lookup per segment; the caller already iterates
   the pairs array with the index in hand. */
function drawPair(p, pairIdx, selected) {
  const hue = pairHue(p);

  /* the two lines, clipped to the viewport */
  drawPairLine(p, "A", hue, selected);
  drawPairLine(p, "B", hue, selected);

  /* trapezoids for the segments, one per on-screen wrapped copy */
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
   The k-range is computed once per (pair, segment) via
   _kRangeForSegmentInBand; the loop only iterates the copies that
   could be on-screen.  The overlap overlay, when the mode is on and
   the segment overlaps a segment on another pair, is drawn per
   wrapped copy so only the laps that contain the actual overlap are
   marked. */

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
