DISPATCH_JS = r"""
import {
  canvas, ctx, dpr, view, layout, pairs, pairState, state,
  w2s, s2w, fitView,
  _pairPeriod, _kRangeForSegmentInBand, forEachSegmentCopyInRange,
  segmentCornersWorldAt, lineEndpointsWorld,
  _resetOverlapMemo,
} from "./pg_core.js";
import { drawCanvasView } from "./pg_view_canvas.js";
import {
  drawSegmentsView, segScreenToX, segmentHitTest, createSegmentAt,
} from "./pg_view_segments.js";
import { syncPairList } from "./pg_panel_lists.js";
import { _syncPairFieldInputs } from "./pg_panel_fields.js";
import {
  pgkitNotifyEntities, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

let _statusTimer = null;

const SEGMENT_BODY_DRAG_THRESHOLD = 3;
const ANCHOR_HIT_R = 14;

/* ==========================================================================
   HIT TESTS
   ========================================================================== */

function pairAnchorHitTest(sx, sy) {
  for (let i = pairs.length - 1; i >= 0; i--) {
    const p = pairs[i];
    if (i !== pairState.selectedPair) continue;
    const [asx, asy] = w2s(p.cx, p.cy);
    if (Math.hypot(sx - asx, sy - asy) < ANCHOR_HIT_R) return i;
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
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

function _ptSegDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const l2 = dx * dx + dy * dy;
  if (l2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function pairBodyHitTest(sx, sy) {
  const cw = window.innerWidth;
  const ch = layout.topH;

  for (let i = pairs.length - 1; i >= 0; i--) {
    const p = pairs[i];
    for (let j = 0; j < p.segments.length; j++) {
      const seg = p.segments[j];
      let hit = false;
      const kRange = _kRangeForSegmentInBand(p, seg, cw, ch);
      if (kRange.min > kRange.max) continue;
      forEachSegmentCopyInRange(_pairPeriod(p), seg.a, seg.b,
        kRange.min, kRange.max, (a, b) => {
        if (hit) return;
        const corners = segmentCornersWorldAt(p, a, b)
          .map(([wx, wy]) => w2s(wx, wy));
        if (_ptInPolygon(sx, sy, corners)) hit = true;
      });
      if (hit) return { pairIdx: i, segIdx: j };
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

/* ==========================================================================
   DRAW
   ========================================================================== */

export function draw() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  _resetOverlapMemo();

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

export function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;

  if (state.drawSegmentMode) {
    el.textContent = "DRAW SEGMENT — click a row to place a segment (Esc to cancel)";
    el.className = "ok";
    return;
  }

  if (pairState.selectedPair < 0 || pairState.selectedPair >= pairs.length) {
    el.textContent = "drag pairs · drag segment edges below";
    el.className = "";
    return;
  }

  const p = pairs[pairState.selectedPair];

  if (state.dragSegmentBody) {
    const ds = state.dragSegmentBody;
    if (ds.setDrag && ds.dragMembers) {
      el.textContent = p.name + "  ·  dragging " + ds.dragMembers.length +
                       " segment(s) in the shift-set";
    } else {
      el.textContent = p.name + "  ·  dragging " + p.segments.length +
                       " segment(s) together  (hold Shift to build a set)";
    }
    el.className = "ok"; return;
  }

  if (state.dragPairAnchor) {
    el.textContent = p.name + "  ·  dragging the whole pair";
    el.className = "ok"; return;
  }

  if (state.shiftDragSet &&
      state.shiftDragSet.pairIdx === pairState.selectedPair &&
      state.shiftDragSet.members.size > 0) {
    el.textContent = p.name + "  ·  shift-set: " +
                     state.shiftDragSet.members.size +
                     " segment(s)  ·  shift-drag any member to move" +
                     " them all  ·  plain click clears";
    el.className = "ok"; return;
  }

  if (state.mouse.inside && state.mouse.sy >= layout.reservedY) {
    const x = segScreenToX(state.mouse.sx, p);
    el.textContent = p.name + "  ·  segment-x = " + x.toFixed(3) +
                     "  ·  segments: " + p.segments.length;
    el.className = "ok"; return;
  }

  if (state.mouse.inside) {
    const [wx, wy] = s2w(state.mouse.sx, state.mouse.sy);
    el.textContent = p.name +
      "  incl " + (p.angle * 180 / Math.PI).toFixed(1) + "°" +
      "  mA " + p.metricA.toFixed(2) +
      "  mB " + p.metricB.toFixed(2) +
      "  off " + p.offset.toFixed(2) +
      "  gap " + p.gap.toFixed(2) +
      "  loop " + ((typeof p.loopbackGap === "number") ? p.loopbackGap : 0).toFixed(2) +
      "   cursor (" + wx.toFixed(2) + ", " + wy.toFixed(2) + ")";
    el.className = "ok"; return;
  }

  el.textContent = p.name + "  ·  " + p.segments.length + " segment(s)";
  el.className = "";
}

/* ==========================================================================
   EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  if (sy >= layout.reservedY) {
    if (state.drawSegmentMode) {
      const created = createSegmentAt(sx, sy);
      if (created) flashStatus("Segment added on " + pairs[created.pairIdx].name, "ok");
      return;
    }

    const hit = segmentHitTest(sx, sy);
    if (!hit) {
      state.shiftDragSet = null;
      pairState.selectedSegment = null;
      draw(); return;
    }

    if (hit.kind === "edge") {
      pairState.selectedPair = hit.pairIdx;
      pairState.selectedSegment = { pairIdx: hit.pairIdx, segIdx: hit.segIdx };
      const p = pairs[hit.pairIdx];
      const seg = p.segments[hit.segIdx];
      state.dragSegmentEdge = {
        pairIdx: hit.pairIdx, segIdx: hit.segIdx, side: hit.side,
        startX: segScreenToX(sx, p),
        startA: seg.a, startB: seg.b,
      };
      syncPairList(); draw(); return;
    }

    if (hit.kind === "body") {
      const prevSel = pairState.selectedSegment;
      pairState.selectedPair = hit.pairIdx;
      pairState.selectedSegment = { pairIdx: hit.pairIdx, segIdx: hit.segIdx };
      const p = pairs[hit.pairIdx];

      let setDrag = false, dragMembers = null;
      if (e.shiftKey) {
        setDrag = true;
        if (!state.shiftDragSet || state.shiftDragSet.pairIdx !== hit.pairIdx) {
          state.shiftDragSet = { pairIdx: hit.pairIdx, members: new Set() };
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
        pairIdx: hit.pairIdx, segIdx: hit.segIdx,
        startX: sx, startSegX: segScreenToX(sx, p), moved: false,
        setDrag, dragMembers,
        startPositions: p.segments.map(s => ({ a: s.a, b: s.b })),
      };
      canvas.style.cursor = "grabbing";
      syncPairList(); draw(); return;
    }

    if (hit.kind === "row") {
      state.shiftDragSet = null;
      pairState.selectedPair = hit.pairIdx;
      pairState.selectedSegment = null;
      syncPairList(); draw(); return;
    }
    return;
  }

  if (state.drawSegmentMode) return;

  const anchorHit = pairAnchorHitTest(sx, sy);
  if (anchorHit >= 0) {
    pairState.selectedPair = anchorHit;
    pairState.selectedSegment = null;
    const p = pairs[anchorHit];
    const [wx, wy] = s2w(sx, sy);
    state.dragPairAnchor = {
      pairIdx: anchorHit,
      startWx: wx, startWy: wy,
      startCx: p.cx, startCy: p.cy,
    };
    canvas.style.cursor = "grabbing";
    syncPairList(); draw(); return;
  }

  const bodyHit = pairBodyHitTest(sx, sy);
  if (bodyHit) {
    pairState.selectedPair = bodyHit.pairIdx;
    pairState.selectedSegment = (bodyHit.segIdx >= 0)
      ? { pairIdx: bodyHit.pairIdx, segIdx: bodyHit.segIdx } : null;
    const p = pairs[bodyHit.pairIdx];
    const [wx, wy] = s2w(sx, sy);
    state.dragPairAnchor = {
      pairIdx: bodyHit.pairIdx,
      startWx: wx, startWy: wy,
      startCx: p.cx, startCy: p.cy,
    };
    canvas.style.cursor = "grabbing";
    syncPairList(); draw(); return;
  }

  state.shiftDragSet = null;
  pairState.selectedPair = -1;
  pairState.selectedSegment = null;
  syncPairList(); draw();
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  state.mouse.sx = sx; state.mouse.sy = sy;
  state.mouse.inside = (sx >= 0 && sy >= 0 &&
                        sx < window.innerWidth && sy < window.innerHeight);

  if (state.drawSegmentMode) { draw(); return; }

  if (state.dragSegmentEdge) {
    const ds = state.dragSegmentEdge;
    const p = pairs[ds.pairIdx];
    if (!p) state.dragSegmentEdge = null;
    else {
      const seg = p.segments[ds.segIdx];
      if (!seg) state.dragSegmentEdge = null;
      else {
        const x = segScreenToX(sx, p);
        const d = x - ds.startX;
        if (ds.side === "a") seg.a = Math.min(ds.startA + d, ds.startB - 0.05);
        else seg.b = Math.max(ds.startB + d, ds.startA + 0.05);
      }
    }
    draw(); return;
  }

  if (state.dragSegmentBody) {
    const ds = state.dragSegmentBody;
    const p = pairs[ds.pairIdx];
    if (!p) state.dragSegmentBody = null;
    else {
      const dxPix = sx - ds.startX;
      if (!ds.moved && Math.abs(dxPix) < SEGMENT_BODY_DRAG_THRESHOLD) {
        /* click tolerance */
      } else {
        ds.moved = true;
        const delta = segScreenToX(sx, p) - ds.startSegX;
        let moveSet = null;
        if (ds.setDrag && ds.dragMembers) moveSet = new Set(ds.dragMembers);
        for (let k = 0; k < p.segments.length; k++) {
          const start = ds.startPositions[k];
          if (!start) continue;
          const shouldMove = (moveSet === null) || moveSet.has(k);
          if (shouldMove) { p.segments[k].a = start.a + delta; p.segments[k].b = start.b + delta; }
          else { p.segments[k].a = start.a; p.segments[k].b = start.b; }
        }
      }
    }
    draw(); return;
  }

  if (state.dragPairAnchor) {
    const dp = state.dragPairAnchor;
    const p = pairs[dp.pairIdx];
    if (!p) state.dragPairAnchor = null;
    else {
      const [wx, wy] = s2w(sx, sy);
      p.cx = dp.startCx + (wx - dp.startWx);
      p.cy = dp.startCy + (wy - dp.startWy);
    }
    draw(); return;
  }

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
    pgkitNotifyEntities();
    draw();
  }
});

canvas.addEventListener("wheel", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sy = e.clientY - rect.top;

  if (sy >= layout.reservedY) {
    e.preventDefault();
    state.segScroll = Math.max(0, state.segScroll + e.deltaY * 0.6);
    draw(); return;
  }

  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
  view.zoom = Math.max(0.2, Math.min(8, view.zoom * factor));
  fitView();
  pgkitNotifyLocal("view.zoom");
  draw();
}, { passive: false });

canvas.style.cursor = "crosshair";
"""
