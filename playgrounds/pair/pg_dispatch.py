"""
pg_dispatch.py — draw() and the canvas event listeners.

Two bands, one canvas:

    top        click a pair's anchor to start a pair drag; click a
               segment's trapezoid (any of its wrapped copies) or
               either of the pair's lines to select that pair (and,
               when the click landed on a trapezoid, that segment
               too) and start the same pair drag; drag moves the
               whole pair — both lines and every segment — together;
               wheel zooms the view.

    bottom     drag a segment's left/right edge to widen / shrink;
               drag a segment's BODY to translate the pair's
               segments; click a segment's body to select it; click
               a row to select its pair; wheel scrolls through rows.

Draw-Segment mode (panel button) diverts top-band interactions too:
a click in the segment view creates a segment, a click in the canvas
band does nothing.  Escape or a second press of the button cancels.

PAIR DRAG — ONE BEHAVIOUR, THREE ENTRY POINTS
=============================================
A pair is a single abstract object: two parallel lines plus the 1D
layout of segments between them.  Every drag that moves a pair moves
the WHOLE pair, because the underlying state (`p.cx`, `p.cy`) is a
single anchor point that the lines, the trapezoids, and the segment
view all derive from.

Three different clicks start a pair drag:

    1. the pair's anchor marker, which is only drawn for the
       selected pair;

    2. one of the pair's two lines, within a few pixels of it;

    3. a segment's trapezoid — the filled (or dashed) quadrilateral
       on the canvas, at any wrapped copy.

(2) and (3) also SELECT the pair they landed on, and (3) selects the
segment as well.

SEGMENT-BODY DRAG — TWO MODES
=============================
A press inside a segment's body in the SEGMENT VIEW (not on its
edges) starts a dragSegmentBody.  The state captures, at mousedown
time,

    the pair index
    the index of the grabbed segment
    the pixel x of the press
    the abstract segment-x under the press
    a snapshot of every segment's (a, b) in that pair
    the drag mode (whole-row or set-drag)

On each mousemove the delta in abstract segment-x is computed from
the press's own starting segment-x, and the pair's segments are
written back from the snapshot.  Which segments get the delta is
decided by the drag mode captured at mousedown:

    whole-row (no Shift at mousedown)   every segment in the pair
                                        is written as
                                        `snapshot + delta`.

    set-drag (Shift at mousedown)       only the segments that are
                                        members of the SHIFT-DRAG
                                        SET at mousedown get the
                                        delta; the rest are reset
                                        to their snapshot.

A tiny pixel threshold (SEGMENT_BODY_DRAG_THRESHOLD) keeps a plain
click from registering as a zero-distance drag.

PER-ROW VISUAL SCALE
====================
The segment view maps a row's pixels to abstract units over that
row's OWN abstract span — one period of the pair the row belongs
to.  A pair with a nonzero loopbackGap therefore has a wider
abstract range in the same pixel width than a pair without one, and
the same pixel column means a different abstract coordinate on
each row.  Every conversion between screen x and abstract segment-x
in this file must therefore be told WHICH pair's row it is talking
about; there is no single global scale.  The two that matter are:

    · the cursor readout in updateStatus(), which reports the
      coordinate on the SELECTED pair's row;

    · the drag handlers, which capture `startSegX` from the
      grabbed row's pair at mousedown and use the same pair on
      every mousemove.  Because the delta is computed against the
      same pair's scale at both ends, the drag is stable even
      though the scale differs from row to row.

See pg_view_segments.py, THE AXIS IS PERIODIC, for the mapping and
`segXToScreen` / `segScreenToX` for the two functions that own it.

SHIFT-DRAG SET — ACCUMULATING MEMBERS
=====================================
While the user holds Shift, successive shift-clicks on segment
bodies accumulate into a persistent set.  The set lives in
`state.shiftDragSet` and remembers one pair at a time:

    { pairIdx: number, members: Set<number> }

The rules:

    · Shift + click on segment A   adds A to the set.  If no set
                                   existed yet AND a segment was
                                   already selected on the same
                                   pair, that selected segment is
                                   seeded into the new set as
                                   well — so the very first shift-
                                   click on a fresh set includes
                                   the segment you had already
                                   selected.

    · Shift + click on segment B   adds B; the set grows.

    · Shift + drag any member      moves every member of the set
                                   by the same delta, along the
                                   pair's own abstract axis.

    · Release Shift                nothing happens — the set
                                   persists.  The whole point of
                                   the accumulation is that the
                                   set outlives the modifier.

    · Non-shift click anywhere     clears the set.
      on the canvas

    · Switch to another pair and   the set is replaced when a
      shift-click on it            shift-click targets a different
                                   pair.  A set is meaningful only
                                   on one pair.

    · Delete a member segment      the set is cleared.

The set is a pure UI affordance — it is not persisted in the scene
file, and it is reset on scene load.

EDGE DRAG — DELTA-BASED, WRAP-AWARE
===================================
An edge drag captures the drag-start cursor position, the segment's
start a and start b, and the side ("a" or "b").  Each mousemove
computes the delta in abstract coordinates from the drag-start
cursor position and applies it to the correct base endpoint.  The
delta is measured with the row's own pair as the scale, so it is
unaffected by any other row's loopbackGap.

PAIR BODY HIT TEST — SEGMENT OR LINE, ONE CALL
==============================================
`pairBodyHitTest` returns

    { pairIdx, segIdx }    when the cursor is inside any wrapped
                           copy of a segment's trapezoid
    { pairIdx, segIdx:-1 } when the cursor is within a few pixels of
                           one of the pair's two lines
    null                   otherwise.

LOOPBACK GAP
============
`_pairPeriod(p)` returns the pair's own period of the abstract
axis: `SEG_X_SPAN + max(0, p.loopbackGap || 0)`.  Every place this
file wraps a segment — `pairBodyHitTest`, the segment-view drag
handlers, the pair-drag hit tests — passes that period, not the
raw span, so the wrapped copies land where the renderers draw them.

See pg_core.py, LOOPBACK GAP, for the field's meaning.

OVERLAP MODE
============
`state.overlapHighlight` is a global viewing flag toggled by the
"Overlap" checkbox in the Segments section.  When true, both view
renderers draw a red dashed overlay on any segment that overlaps a
segment on another pair, and the panel's lists mark overlapping
rows.  See pg_core.py, `_segmentOverlapsOtherPair`.
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

  /* Shift-drag set: { pairIdx, members: Set<number> } | null.  See
     the module docstring, SHIFT-DRAG SET. */
  shiftDragSet: null,
};

/* True iff the segment at (pairIdx, segIdx) is currently a member
   of the shift-drag set.  Called by the two view renderers and by
   the panel's list builder to decide whether to draw the member
   marker. */
function _isShiftSetMember(pairIdx, segIdx) {
  const s = state.shiftDragSet;
  return !!(s && s.pairIdx === pairIdx && s.members.has(segIdx));
}

/* Drop the shift-drag set, if any, and re-render so its member
   markers disappear.  No-op when the set is already null. */
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
    /* The abstract segment-x readout is reported in the SELECTED
       pair's own scale, since the status line names that pair.
       Rows with a different loopbackGap have a different abstract
       span, so the same pixel x is a different abstract x on each
       row; passing `p` is what makes the number meaningful. */
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

/* Every wrapped copy of every segment is considered.  The period
   passed to forEachSegmentCopy is the pair's own, so a nonzero
   loopbackGap shifts the wrapped copies exactly as far as the
   renderers draw them. */
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
      /* Edge drag is inherently a one-segment operation; the set
         is preserved across an edge drag so a user who is mid-set
         can still tweak an edge without losing the accumulation. */
      selectedPair    = hit.pairIdx;
      selectedSegment = { pairIdx: hit.pairIdx, segIdx: hit.segIdx };
      const p = pairs[hit.pairIdx];
      const seg = p.segments[hit.segIdx];
      state.dragSegmentEdge = {
        pairIdx: hit.pairIdx,
        segIdx:  hit.segIdx,
        side:    hit.side,
        /* Both startX and every mousemove's x must be converted
           with the SAME row's pair — otherwise the delta would
           carry a scale factor of the wrong row.  See the module
           docstring, PER-ROW VISUAL SCALE. */
        startX:  segScreenToX(sx, p),
        startA:  seg.a,
        startB:  seg.b,
      };
      syncPairList();
      draw();
      return;
    }

    if (hit.kind === "body") {
      /* Capture the PREVIOUS selection BEFORE overwriting it — the
         shift-drag set seeding needs to know which segment the
         user had selected before this click, and the assignment
         below obliterates that information. */
      const prevSel = selectedSegment;

      selectedPair    = hit.pairIdx;
      selectedSegment = { pairIdx: hit.pairIdx, segIdx: hit.segIdx };
      const p = pairs[hit.pairIdx];

      let setDrag = false;
      let dragMembers = null;

      if (e.shiftKey) {
        setDrag = true;

        /* A set is meaningful only on one pair.  If no set exists,
           or the existing one belongs to a different pair, start a
           fresh set for this pair and seed it with the segment
           that was selected BEFORE this shift-click — but only
           when that segment lived on the same pair. */
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
        /* Non-shift body press: a fresh whole-row drag begins and
           the set is dropped. */
        state.shiftDragSet = null;
      }

      state.dragSegmentBody = {
        pairIdx:     hit.pairIdx,
        segIdx:      hit.segIdx,
        startX:      sx,
        /* Captured with the grabbed row's pair — see the module
           docstring, PER-ROW VISUAL SCALE. */
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
        /* Same pair as startX — see the module docstring,
           PER-ROW VISUAL SCALE. */
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
        /* still inside the click tolerance; nothing has moved yet */
      } else {
        ds.moved = true;
        /* Both endpoints of the subtraction use the same pair, so
           the delta is scale-free with respect to the row's own
           span.  See the module docstring, PER-ROW VISUAL SCALE. */
        const delta = segScreenToX(sx, p) - ds.startSegX;

        /* The drag mode was captured at mousedown: a set-drag
           moves only the members of the shift-set; a whole-row
           drag moves every segment.  The branch is not re-
           evaluated from the current Shift state. */
        let moveSet = null;  /* null means "all" */
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

  draw();
});

window.addEventListener("mouseup", () => {
  if (state.dragPairAnchor ||
      state.dragSegmentEdge ||
      state.dragSegmentBody) {
    state.dragPairAnchor  = null;
    state.dragSegmentEdge = null;
    state.dragSegmentBody = null;
    canvas.style.cursor = "crosshair";
  }

  /* The shift-drag set is intentionally NOT cleared here.  It
     survives both mouseup and Shift keyup, and only goes away on a
     non-shift canvas click, on segment deletion, or on scene load. */
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
