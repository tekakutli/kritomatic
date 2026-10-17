"""
pg_dispatch.py — draw() and the canvas event listeners.

Two bands, one canvas:

    top        click a pair's anchor to start a pair drag; click a
               segment's trapezoid or either of the pair's lines to
               select that pair and start the same pair drag.

    bottom     drag a segment's left/right edge to widen / shrink;
               drag a segment's BODY to translate the pair's
               segments; click a segment's body to select it; click
               a row to select its pair; wheel scrolls through rows.

PAIR DRAG — ONE BEHAVIOUR, THREE ENTRY POINTS
=============================================
A pair is a single abstract object: two parallel lines plus the 1D
layout of segments between them.  Every drag that moves a pair moves
the WHOLE pair.

Three different clicks start a pair drag: the pair's anchor marker,
either of its two lines, or a segment's trapezoid.  (2) and (3) also
SELECT the pair they landed on.

SEGMENT-BODY DRAG — TWO MODES
=============================
A press inside a segment's body in the SEGMENT VIEW starts a
dragSegmentBody.  The mousedown captures a snapshot of every
segment's (a, b); each mousemove writes back from that snapshot.
The drag mode is decided at mousedown: whole-row (no Shift) moves
every segment; set-drag (Shift held) moves only the members of the
shift-drag set.

SHIFT-DRAG SET — ACCUMULATING MEMBERS
=====================================
While Shift is held, successive shift-clicks on segment bodies
accumulate into a persistent set.  A shift-drag on any member moves
the whole set.  Releasing Shift keeps the set; a plain (non-shift)
canvas click clears it.

EDGE DRAG — DELTA-BASED, WRAP-AWARE
===================================
An edge drag captures the drag-start cursor position and the
segment's start a and b.  Each mousemove applies a delta from
drag-start, so the drag is correct when the user grabbed a wrapped
copy of the edge.

PAIR BODY HIT TEST — SEGMENT OR LINE, ONE CALL
==============================================
`pairBodyHitTest` returns
    { pairIdx, segIdx }    for a segment trapezoid hit
    { pairIdx, segIdx:-1 } for a line hit
    null                   otherwise.

LOOPBACK GAP
============
`_pairPeriod(p)` returns the pair's own period of the abstract axis.

OVERLAP MODE
============
`state.overlapHighlight` is a global viewing flag toggled by the
"Overlap" checkbox in the Segments section.

MOUSEMOVE FAST PATH
===================
The `mousemove` handler only calls `draw()` when a drag is active or
the draw-segment tool is on.  When nothing is dragging and no tool
is active, only `state.mouse` has changed — the canvas is
pixel-identical to the previous frame — so we update the status
readout and return, without touching the renderers.  The per-frame
overlap memo is also reset only inside `draw()`, so it survives
across the fast path without staleness.
"""

DISPATCH_JS = r"""
/* ==========================================================================
   TRANSIENT STATE
   ========================================================================== */

const SEGMENT_BODY_DRAG_THRESHOLD = 3;

const state = {
  dragPairAnchor:  null,
  dragSegmentEdge: null,
  dragSegmentBody: null,
  mouse: { sx: 0, sy: 0, inside: false },
  segScroll: 0,
  drawSegmentMode: false,
  overlapHighlight: false,
  shiftDragSet: null,
};

function _isShiftSetMember(pairIdx, segIdx) {
  const s = state.shiftDragSet;
  return !!(s && s.pairIdx === pairIdx && s.members.has(segIdx));
}

function _clearShiftDragSet() {
  if (state.shiftDragSet) {
    state.shiftDragSet = null;
    draw();
  }
}

/* ==========================================================================
   DRAW
   ========================================================================== */

function draw() {
  /* Fresh per-frame memo for the overlap test.  Both renderers
     consult _cachedSegmentOverlaps; the memo makes each distinct
     (pair, segment) query happen once per frame instead of twice. */
  _overlapMemo = new Map();

  const cw = window.innerWidth;
  const ch = window.innerHeight;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, cw, layout.topH); ctx.clip();
  drawCanvasView();
  ctx.restore();

  ctx.fillStyle = "#dce6f2";
  ctx.fillRect(0, layout.dividerY,     cw, 1);
  ctx.fillStyle = "#1e2836";
  ctx.fillRect(0, layout.dividerY + 2, cw, 1);

  drawSegmentsView();

  const M = 6;
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 0.8;
  ctx.strokeRect(M + 0.5, M + 0.5, cw - 2 * M - 1, ch - 2 * M - 1);

  updateStatus();

  _syncPairFieldInputs();
}

/* ==========================================================================
   STATUS
   ========================================================================== */

function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;

  if (state.drawSegmentMode) {
    el.textContent = "DRAW SEGMENT — click a row to place a segment "
                   + "(Esc to cancel)";
    el.className = "ok";
    return;
  }

  if (selectedPair < 0 || selectedPair >= pairs.length) {
    el.textContent = "drag pairs · drag segment edges below";
    el.className = "";
    return;
  }

  const p = pairs[selectedPair];

  if (state.dragSegmentBody) {
    const ds = state.dragSegmentBody;
    if (ds.setDrag && ds.dragMembers) {
      el.textContent = p.name + "  ·  dragging "
                     + ds.dragMembers.length
                     + " segment(s) in the shift-set";
    } else {
      el.textContent = p.name + "  ·  dragging "
                     + p.segments.length + " segment(s) together  "
                     + "(hold Shift to build a set)";
    }
    el.className = "ok";
    return;
  }

  if (state.dragPairAnchor) {
    el.textContent = p.name + "  ·  dragging the whole pair";
    el.className = "ok";
    return;
  }

  if (state.shiftDragSet &&
      state.shiftDragSet.pairIdx === selectedPair &&
      state.shiftDragSet.members.size > 0) {
    el.textContent = p.name + "  ·  shift-set: "
                   + state.shiftDragSet.members.size
                   + " segment(s)  ·  shift-drag any member to move"
                   + " them all  ·  plain click clears";
    el.className = "ok";
    return;
  }

  if (state.mouse.inside && state.mouse.sy >= layout.reservedY) {
    const x = segScreenToX(state.mouse.sx, p);
    el.textContent = p.name + "  ·  segment-x = " + x.toFixed(3)
                   + "  ·  segments: " + p.segments.length;
    el.className = "ok";
    return;
  }

  if (state.mouse.inside) {
    const [wx, wy] = s2w(state.mouse.sx, state.mouse.sy);
    el.textContent = p.name
      + "  incl " + (p.angle * 180 / Math.PI).toFixed(1) + "°"
      + "  mA " + p.metricA.toFixed(2)
      + "  mB " + p.metricB.toFixed(2)
      + "  off " + p.offset.toFixed(2)
      + "  gap " + p.gap.toFixed(2)
      + "  loop " + ((typeof p.loopbackGap === "number")
                       ? p.loopbackGap : 0).toFixed(2)
      + "   cursor (" + wx.toFixed(2) + ", " + wy.toFixed(2) + ")";
    el.className = "ok";
    return;
  }

  el.textContent = p.name + "  ·  " + p.segments.length + " segment(s)";
  el.className = "";
}

/* ==========================================================================
   HIT TESTS — top band
   ========================================================================== */

const ANCHOR_HIT_R = 14;

function pairAnchorHitTest(sx, sy) {
  for (let i = pairs.length - 1; i >= 0; i--) {
    const p = pairs[i];
    if (i !== selectedPair) continue;
    const [asx, asy] = w2s(p.cx, p.cy);
    if (Math.hypot(sx - asx, sy - asy) < ANCHOR_HIT_R) {
      return i;
    }
  }
  return -1;
}

function _ptInPolygon(px, py, poly) {
  let inside = false;
  const n = poly.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function pairBodyHitTest(sx, sy) {
  const cw = window.innerWidth;
  const ch = layout.topH;

  for (let i = pairs.length - 1; i >= 0; i--) {
    const p = pairs[i];
    for (let j = 0; j < p.segments.length; j++) {
      const seg = p.segments[j];
      let hit = false;
      forEachSegmentCopy(_pairPeriod(p), seg.a, seg.b, (a, b) => {
        if (hit) return;
        const corners = segmentCornersWorldAt(p, a, b)
          .map(([wx, wy]) => w2s(wx, wy));
        const bb = _screenBBox(corners);
        if (!_bboxIntersectsTopBand(bb, cw, ch)) return;
        if (_ptInPolygon(sx, sy, corners)) hit = true;
      });
      if (hit) {
        return { pairIdx: i, segIdx: j };
      }
    }
  }
  for (let i = pairs.length - 1; i >= 0; i--) {
    const p = pairs[i];
    for (const which of ["A", "B"]) {
      const [[ax, ay], [bx, by]] = lineEndpointsWorld(p, which);
      const [sax, say] = w2s(ax, ay);
      const [sbx, sby] = w2s(bx, by);
      if (_ptSegDist(sx, sy, sax, say, sbx, sby) < 6) {
        return { pairIdx: i, segIdx: -1 };
      }
    }
  }
  return null;
}

function _ptSegDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const l2 = dx * dx + dy * dy;
  if (l2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/* ==========================================================================
   EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  /* ---- segment view ---- */
  if (sy >= layout.reservedY) {
    if (state.drawSegmentMode) {
      const created = createSegmentAt(sx, sy);
      if (created) {
        flashStatus("Segment added on "
                    + pairs[created.pairIdx].name, "ok");
      }
      return;
    }

    const hit = segmentHitTest(sx, sy);
    if (!hit) {
      _clearShiftDragSet();
      selectedSegment = null;
      draw();
      return;
    }

    if (hit.kind === "edge") {
      selectedPair    = hit.pairIdx;
      selectedSegment = { pairIdx: hit.pairIdx, segIdx: hit.segIdx };
      const p = pairs[hit.pairIdx];
      const seg = p.segments[hit.segIdx];
      state.dragSegmentEdge = {
        pairIdx: hit.pairIdx,
        segIdx:  hit.segIdx,
        side:    hit.side,
        startX:  segScreenToX(sx, p),
        startA:  seg.a,
        startB:  seg.b,
      };
      syncPairList();
      draw();
      return;
    }

    if (hit.kind === "body") {
      const prevSel = selectedSegment;

      selectedPair    = hit.pairIdx;
      selectedSegment = { pairIdx: hit.pairIdx, segIdx: hit.segIdx };
      const p = pairs[hit.pairIdx];

      let setDrag = false;
      let dragMembers = null;

      if (e.shiftKey) {
        setDrag = true;
        if (!state.shiftDragSet ||
            state.shiftDragSet.pairIdx !== hit.pairIdx) {
          state.shiftDragSet = {
            pairIdx: hit.pairIdx,
            members: new Set(),
          };
          if (prevSel && prevSel.pairIdx === hit.pairIdx) {
            state.shiftDragSet.members.add(prevSel.segIdx);
          }
        }
        state.shiftDragSet.members.add(hit.segIdx);
        dragMembers = Array.from(state.shiftDragSet.members);
      } else {
        state.shiftDragSet = null;
      }

      state.dragSegmentBody = {
        pairIdx:     hit.pairIdx,
        segIdx:      hit.segIdx,
        startX:      sx,
        startSegX:   segScreenToX(sx, p),
        moved:       false,
        setDrag:     setDrag,
        dragMembers: dragMembers,
        startPositions: p.segments.map(s => ({ a: s.a, b: s.b })),
      };
      canvas.style.cursor = "grabbing";
      syncPairList();
      draw();
      return;
    }

    if (hit.kind === "row") {
      _clearShiftDragSet();
      selectedPair    = hit.pairIdx;
      selectedSegment = null;
      syncPairList();
      draw();
      return;
    }
    return;
  }

  /* ---- top band ---- */
  if (state.drawSegmentMode) return;

  const anchorHit = pairAnchorHitTest(sx, sy);
  if (anchorHit >= 0) {
    selectedPair    = anchorHit;
    selectedSegment = null;
    const p = pairs[anchorHit];
    const [wx, wy] = s2w(sx, sy);
    state.dragPairAnchor = {
      pairIdx: anchorHit,
      startWx: wx, startWy: wy,
      startCx: p.cx, startCy: p.cy,
    };
    canvas.style.cursor = "grabbing";
    syncPairList();
    draw();
    return;
  }

  const bodyHit = pairBodyHitTest(sx, sy);
  if (bodyHit) {
    selectedPair = bodyHit.pairIdx;
    selectedSegment = (bodyHit.segIdx >= 0)
      ? { pairIdx: bodyHit.pairIdx, segIdx: bodyHit.segIdx }
      : null;
    const p = pairs[bodyHit.pairIdx];
    const [wx, wy] = s2w(sx, sy);
    state.dragPairAnchor = {
      pairIdx: bodyHit.pairIdx,
      startWx: wx, startWy: wy,
      startCx: p.cx, startCy: p.cy,
    };
    canvas.style.cursor = "grabbing";
    syncPairList();
    draw();
    return;
  }

  _clearShiftDragSet();
  selectedPair    = -1;
  selectedSegment = null;
  syncPairList();
  draw();
});

/* ==========================================================================
   MOUSEMOVE — fast path
   ==========================================================================
   Only a drag or the draw-segment tool changes the canvas.  When
   neither is active, the renderers are skipped and only the status
   readout is refreshed.  See the module docstring, MOUSEMOVE FAST
   PATH. */

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  state.mouse.sx = sx; state.mouse.sy = sy;
  state.mouse.inside = (sx >= 0 && sy >= 0 &&
                        sx < window.innerWidth &&
                        sy < window.innerHeight);

  if (state.drawSegmentMode) {
    draw();
    return;
  }

  if (state.dragSegmentEdge) {
    const ds = state.dragSegmentEdge;
    const p  = pairs[ds.pairIdx];
    if (!p) {
      state.dragSegmentEdge = null;
    } else {
      const seg = p.segments[ds.segIdx];
      if (!seg) {
        state.dragSegmentEdge = null;
      } else {
        const x = segScreenToX(sx, p);
        const d = x - ds.startX;
        if (ds.side === "a") {
          seg.a = Math.min(ds.startA + d, ds.startB - 0.05);
        } else {
          seg.b = Math.max(ds.startB + d, ds.startA + 0.05);
        }
      }
    }
    draw();
    return;
  }

  if (state.dragSegmentBody) {
    const ds = state.dragSegmentBody;
    const p  = pairs[ds.pairIdx];
    if (!p) {
      state.dragSegmentBody = null;
    } else {
      const dxPix = sx - ds.startX;
      if (!ds.moved &&
          Math.abs(dxPix) < SEGMENT_BODY_DRAG_THRESHOLD) {
        /* click tolerance */
      } else {
        ds.moved = true;
        const delta = segScreenToX(sx, p) - ds.startSegX;

        let moveSet = null;
        if (ds.setDrag && ds.dragMembers) {
          moveSet = new Set(ds.dragMembers);
        }

        for (let k = 0; k < p.segments.length; k++) {
          const start = ds.startPositions[k];
          if (!start) continue;
          const shouldMove = (moveSet === null) || moveSet.has(k);
          if (shouldMove) {
            p.segments[k].a = start.a + delta;
            p.segments[k].b = start.b + delta;
          } else {
            p.segments[k].a = start.a;
            p.segments[k].b = start.b;
          }
        }
      }
    }
    draw();
    return;
  }

  if (state.dragPairAnchor) {
    const dp = state.dragPairAnchor;
    const p  = pairs[dp.pairIdx];
    if (!p) {
      state.dragPairAnchor = null;
    } else {
      const [wx, wy] = s2w(sx, sy);
      p.cx = dp.startCx + (wx - dp.startWx);
      p.cy = dp.startCy + (wy - dp.startWy);
    }
    draw();
    return;
  }

  /* Nothing is dragging and no tool is active: only state.mouse
     changed.  Update the status readout and return — the canvas is
     pixel-identical to the previous frame. */
  updateStatus();
});

window.addEventListener("mouseup", () => {
  const hadDrag = !!(state.dragPairAnchor ||
                     state.dragSegmentEdge ||
                     state.dragSegmentBody);
  if (hadDrag) {
    state.dragPairAnchor  = null;
    state.dragSegmentEdge = null;
    state.dragSegmentBody = null;
    canvas.style.cursor = "crosshair";
    /* Segment or pair positions moved → the row-scope actions'
       appliesTo predicates may now differ. */
    pgkitNotifyEntities();
  }
});

canvas.addEventListener("wheel", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sy = e.clientY - rect.top;

  if (sy >= layout.reservedY) {
    e.preventDefault();
    state.segScroll = Math.max(
      0,
      state.segScroll + e.deltaY * 0.6
    );
    draw();
    return;
  }

  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
  view.zoom = Math.max(0.2, Math.min(8, view.zoom * factor));
  fitView();
  draw();
}, { passive: false });

canvas.style.cursor = "crosshair";
"""
