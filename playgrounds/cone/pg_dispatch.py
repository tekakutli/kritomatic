"""
pg_dispatch.py — draw() and the canvas event listeners.

ES module.  Exports `draw` and `updateStatus`.  Owns the drag `state`
(from `pg_state`) and every mouse/wheel/key handler.

STAGE 1 OPTIMIZATIONS (preserved)
================================
    MOUSEMOVE FAST PATH       pure hover updates status only, no draw
    CURSOR SET ONLY ON CHANGE `_setCursor` compares first
    REDUNDANT FILL REMOVED    outer full-canvas clear gone

STAGE 3 CHANGES (preserved)
===========================
    wheel      → pgkitNotifyLocal for cone.depth / cone.apexX / cone.apexY
    mouseup    → notifyChanged() after any drag released

SHIFT+CLICK COORDINATE FIX
==========================
`addSquareAt` from pg_state takes NORMALIZED (uHat, vHat) where 0 is
the patch centre and the patch spans roughly [-1, 1].  This file
converts from local units via `f.uLen` / `f.vLen` before calling it.
Before the fix, raw local coords were passed and the shim divided by
`dPhi * 4` / `dS * 4`, which produced a wrong u/v and placed new
squares off-centre — visually, shift+clicking a patch dropped a
square at the wrong position (sometimes outside the patch).

APEX DRAG / WHEEL NOW WRITE / RE-DERIVE VIA RAW
===============================================
The apex drag in mousemove writes `cone.axRaw` / `cone.ayRaw` — the
intent — and calls `clampApex()` so the display reflects the current
depth.  The wheel handler changes `cone.depth`, then calls
`clampApex()`, which re-derives display from the *unchanged* intent
against the new depth.  This is what makes zoom-out-then-in
reversible for the shapes' perspective: the clamp is no longer
destructive, so a smaller base at lower depth cannot irreversibly
shrink the apex.  See pg_state.py's DUAL APEX section.
"""

DISPATCH_JS = r"""
import {
  canvas, ctx, env, view, layout, cone, quads, floatSquares,
  coneState, state, drawTool, PATCH_SHAPE_EDIT_ENABLED,
  w2s, s2w, surfacePoint, clampApex,
  projectToConeSurface, syncPanelSliders,
  addSquareAt, quadIdxById, quadCorners,
  notifyChanged,
} from "./pg_state.js";
import {
  patchFrame, screenToFrame,
  shapeVMax, shapePerspCentre,
  squareAnchorCornerLocal, squareCenterLocal, setClonePosition,
  squareDims, SHAPE_DEPTH_FLAT, effectiveConeDepth,
  applyPatchDragFromStart, flatMoveCorner, flatMoveEdge,
} from "./pg_view_squares_core.js";
import {
  squareHitTest, squareMirrorHitTest, findPatchAtScreen,
  cursorToLocalShapeRelativeExactWith,
  resizeSquareFromCorner, rotateSquareToCursor,
} from "./pg_view_squares_edit.js";
import {
  flatCursorToShapeLocal, flatResizeSquareFromCornerIdx,
  flatRotateSquareToCursor, squareFlatHitTest, squareFlatMirrorHitTest,
} from "./pg_view_squares_flat.js";
import { squareCornersScreen } from "./pg_view_squares_proj.js";
import { drawConeView } from "./pg_view_cone.js";
import { drawFlatView, flatHitTest } from "./pg_view_flat.js";
import { screenToFlat } from "./pg_flat_math.js";
import { syncQuadList } from "./pg_panel_lists.js";
import { _syncPatchCoordInputs } from "./pg_panel_fields.js";
import { addDrawPoint } from "./pg_core.js";
import { pgkitNotifyLocal } from "./pgkit/remote_client.js";

const BODY_DRAG_MAX_DU = 4.0;
const BODY_DRAG_MAX_DV = 4.0;

let _lastCursor = "crosshair";
function _setCursor(c) {
  if (c === _lastCursor) return;
  canvas.style.cursor = c;
  _lastCursor = c;
}

/* ==========================================================================
   DRAW
   ========================================================================== */

export function draw() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  ctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);

  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, cw, layout.coneH); ctx.clip();
  drawConeView();
  ctx.restore();

  ctx.fillStyle = "#dce6f2";
  ctx.fillRect(0, layout.dividerY,     cw, 1);
  ctx.fillStyle = "#1e2836";
  ctx.fillRect(0, layout.dividerY + 2, cw, 1);

  drawFlatView();

  const M = 6;
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 0.8;
  ctx.strokeRect(M + 0.5, M + 0.5, cw - 2 * M - 1, ch - 2 * M - 1);

  updateStatus();

  if (drawTool.active) {
    ctx.save();
    ctx.strokeStyle = "#00e5ff";
    ctx.lineWidth = 2;
    ctx.setLineDash([5, 3]);
    const pts = drawTool.points;
    if (pts.length > 0) {
      ctx.beginPath();
      ctx.moveTo(pts[0][0], pts[0][1]);
      for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
      if (drawTool.hover && pts.length < 4) {
        ctx.lineTo(drawTool.hover[0], drawTool.hover[1]);
      }
      ctx.stroke();

      for (const [x, y] of pts) {
        ctx.beginPath();
        ctx.arc(x, y, 4, 0, Math.PI * 2);
        ctx.fillStyle = "#00e5ff";
        ctx.fill();
      }
      if (drawTool.hover && pts.length < 4) {
        ctx.beginPath();
        ctx.arc(drawTool.hover[0], drawTool.hover[1], 3, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(0,229,255,0.5)";
        ctx.fill();
      }
    }
    ctx.restore();
  }

  _syncPatchCoordInputs();
}

/* ==========================================================================
   STATUS
   ========================================================================== */

export function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;

  if (drawTool.active) {
    const n = drawTool.points.length;
    el.textContent = "DRAW QUAD \u2014 click corner " + (n + 1)
                   + " of 4   (Esc to cancel)";
    el.className = "ok";
    return;
  }

  if (!state.mouse.inside) {
    el.textContent =
      "drag apex to tilt \u00B7 scroll to change depth \u00B7 " +
      "shift+click a patch to drop a shape \u00B7 " +
      "drag corners, rotate handle, or interior to edit \u00B7 " +
      "alt+click a square to grab its patch";
    el.className = "";
    return;
  }

  if (state.mouse.sy >= layout.dividerY) {
    const p = screenToFlat(state.mouse.sx, state.mouse.sy);
    el.textContent =
      "flat   \u03C6 = " + p.phi.toFixed(2) +
      "   s = " + p.s.toFixed(3);
    el.className = "ok";
    return;
  }

  const [wx, wy] = s2w(state.mouse.sx, state.mouse.sy);
  const tiltDeg = Math.atan2(Math.hypot(cone.ax, cone.ay), cone.depth)
                  * 180 / Math.PI;

  let drag = "";
  const ds = state.dragSquare || state.flatSquareDrag;
  if (state.dragQuadVertex) {
    drag = "   \u00B7  shaping " + quads[state.dragQuadVertex.quadIdx].name;
  } else if (ds) {
    const sq = floatSquares[ds.squareIdx];
    if (sq) {
      const dims = squareDims(sq);
      const deg  = Math.round((sq.theta || 0) * 180 / Math.PI);
      const slp  = (sq.slope || 0).toFixed(2);
      drag = "   \u00B7  shape #" + sq.id +
             "  W " + dims.w.toFixed(2) +
             "  H " + dims.h.toFixed(2) +
             "  \u2220 " + deg + "\u00B0" +
             "  \u2197 " + slp;
    }
  } else if (state.dragPatchBody) {
    drag = "   \u00B7  moving " +
           quads[state.dragPatchBody.quadIdx].name;
  } else if (state.flatDrag &&
             state.flatDrag.mode === "body") {
    drag = "   \u00B7  moving " +
           quads[state.flatDrag.quadIdx].name;
  }

  el.textContent =
    "cursor (" + wx.toFixed(1) + ", " + wy.toFixed(1) + ")" +
    "   apex (" + cone.ax.toFixed(1) + ", " + cone.ay.toFixed(1) + ")" +
    "   tilt " + tiltDeg.toFixed(1) + "\u00B0" +
    "   depth " + cone.depth.toFixed(1) +
    drag;
  el.className = "ok";
}

/* ==========================================================================
   HIT TEST — 3D patch corner
   ========================================================================== */

function findQuadVertexAt(sx, sy) {
  const R_HIT = 12;
  let best = null, bestD = R_HIT;

  const order = [];
  if (coneState.selectedQuad >= 0 &&
      coneState.selectedQuad < quads.length) {
    order.push(coneState.selectedQuad);
  }
  for (let i = 0; i < quads.length; i++) {
    if (i !== coneState.selectedQuad) order.push(i);
  }

  for (const qi of order) {
    const corners = quadCorners(quads[qi]);
    for (let ci = 0; ci < 4; ci++) {
      const c = corners[ci];
      const [wx, wy]   = surfacePoint(c.phi, c.s);
      const [vsx, vsy] = w2s(wx, wy);
      const d = Math.hypot(sx - vsx, sy - vsy);
      if (d < bestD) {
        bestD = d;
        best = { quadIdx: qi, cornerIdx: ci };
      }
    }
  }
  return best;
}

/* ==========================================================================
   ALT PASS-THROUGH HELPERS
   ========================================================================== */

function _beginConePatchDragFromSquareIdx(squareIdx, sx, sy) {
  const sq = floatSquares[squareIdx];
  const qi = sq ? quadIdxById(sq.quadId) : -1;
  if (qi < 0) return false;

  const q = quads[qi];
  if (qi !== coneState.selectedQuad) {
    coneState.selectedQuad = qi;
    syncQuadList();
  }
  coneState.selectedSquare = -1;

  const [wx, wy] = s2w(sx, sy);
  const p0 = projectToConeSurface(wx, wy, (q.s0 + q.s1) / 2);
  state.dragPatchBody = {
    quadIdx:   qi,
    startPhi:  p0.phi,
    startS:    p0.s,
    startPhi0: q.phi0,
    startPhi1: q.phi1,
    startS0:   q.s0,
    startS1:   q.s1,
  };
  _setCursor("grabbing");
  draw();
  return true;
}

function _beginFlatPatchDragFromSquareIdx(squareIdx, sx, sy) {
  const sq = floatSquares[squareIdx];
  const qi = sq ? quadIdxById(sq.quadId) : -1;
  if (qi < 0) return false;

  const q = quads[qi];
  if (qi !== coneState.selectedQuad) {
    coneState.selectedQuad = qi;
    syncQuadList();
  }
  coneState.selectedSquare = -1;

  const p = screenToFlat(sx, sy);
  state.flatDrag = {
    mode:      "body",
    quadIdx:   qi,
    startPhi:  p.phi,
    startS:    p.s,
    startPhi0: q.phi0,
    startPhi1: q.phi1,
    startS0:   q.s0,
    startS1:   q.s1,
  };
  _setCursor("grabbing");
  draw();
  return true;
}

/* ==========================================================================
   EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  if (drawTool.active) {
    e.preventDefault();
    addDrawPoint(sx, sy);
    return;
  }

  const passThrough = e.altKey;

  if (sy < layout.dividerY) {

    const sqHit = squareHitTest(sx, sy);

    if (passThrough) {
      let hit = sqHit;
      if (!hit) hit = squareMirrorHitTest(sx, sy);
      if (hit && _beginConePatchDragFromSquareIdx(hit.squareIdx, sx, sy)) {
        return;
      }
    }

    if (!passThrough && e.shiftKey) {
      const patchIdx = findPatchAtScreen(sx, sy);
      if (patchIdx >= 0) {
        const f = patchFrame(patchIdx);
        if (f) {
          const lc = screenToFrame(f, sx, sy);
          // Local units → normalized units (patch spans roughly [-1, 1]).
          addSquareAt(patchIdx, lc.u / f.uLen, lc.v / f.vLen);
          if (patchIdx !== coneState.selectedQuad) {
            coneState.selectedQuad = patchIdx;
            syncQuadList();
          }
          draw();
          return;
        }
      }
    }

    if (!passThrough && sqHit) {
      const sq = floatSquares[sqHit.squareIdx];
      const qi = quadIdxById(sq.quadId);
      if (qi >= 0 && qi !== coneState.selectedQuad) {
        coneState.selectedQuad = qi;
        syncQuadList();
      }
      coneState.selectedSquare = sqHit.squareIdx;

      if (sqHit.kind === "rotate") {
        state.dragSquare = {
          mode: "rotate",
          squareIdx: sqHit.squareIdx,
          startTheta: sq.theta || 0,
        };
      } else if (sqHit.kind === "corner") {
        const anchor = squareAnchorCornerLocal(
          sq, sqHit.cornerIdx, effectiveConeDepth());
        state.dragSquare = {
          mode: "corner",
          squareIdx: sqHit.squareIdx,
          cornerIdx: sqHit.cornerIdx,
          anchorU: anchor ? anchor[0] : undefined,
          anchorV: anchor ? anchor[1] : undefined,
        };
      } else {
        const f = qi >= 0 ? patchFrame(qi) : null;
        const c = squareCenterLocal(sq);
        if (f && c) {
          const vMax = shapeVMax(sq);
          const vC   = sq.v * f.vLen;
          const pC   = shapePerspCentre(sq);
          const lc   = cursorToLocalShapeRelativeExactWith(
                           f, qi, vC, pC, vMax, sx, sy);
          state.dragSquare = {
            mode: "body",
            squareIdx: sqHit.squareIdx,
            startCursorU: lc.u,
            startCursorV: lc.v,
            startCenterU: c[0],
            startCenterV: c[1],
            startVc:      vC,
            startPc:      pC,
            startVMax:    vMax,
          };
        }
      }
      _setCursor("grabbing");
      draw();
      return;
    }

    if (!passThrough && PATCH_SHAPE_EDIT_ENABLED) {
      const vHit = findQuadVertexAt(sx, sy);
      if (vHit) {
        if (vHit.quadIdx !== coneState.selectedQuad) {
          coneState.selectedQuad = vHit.quadIdx;
          syncQuadList();
        }
        coneState.selectedSquare = -1;
        state.dragQuadVertex = vHit;
        _setCursor("grabbing");
        draw();
        return;
      }
    }

    const patchHit = findPatchAtScreen(sx, sy);
    if (patchHit >= 0) {
      const q = quads[patchHit];
      if (patchHit !== coneState.selectedQuad) {
        coneState.selectedQuad = patchHit;
        syncQuadList();
      }
      coneState.selectedSquare = -1;

      const [wx, wy] = s2w(sx, sy);
      const p0 = projectToConeSurface(wx, wy, (q.s0 + q.s1) / 2);
      state.dragPatchBody = {
        quadIdx:   patchHit,
        startPhi:  p0.phi,
        startS:    p0.s,
        startPhi0: q.phi0,
        startPhi1: q.phi1,
        startS0:   q.s0,
        startS1:   q.s1,
      };
      _setCursor("grabbing");
      draw();
      return;
    }

    coneState.selectedSquare = -1;
    state.dragApex = {
      startSx: sx, startSy: sy,
      startAx: cone.ax, startAy: cone.ay,
    };
    _setCursor("grabbing");
    return;
  }

  const sqFlatHit = squareFlatHitTest(sx, sy);

  if (passThrough) {
    let hit = sqFlatHit;
    if (!hit) hit = squareFlatMirrorHitTest(sx, sy);
    if (hit && _beginFlatPatchDragFromSquareIdx(hit.squareIdx, sx, sy)) {
      return;
    }
  }

  if (!passThrough && sqFlatHit) {
    const sq = floatSquares[sqFlatHit.squareIdx];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0 && qi !== coneState.selectedQuad) {
      coneState.selectedQuad = qi;
      syncQuadList();
    }
    coneState.selectedSquare = sqFlatHit.squareIdx;

    if (sqFlatHit.kind === "rotate") {
      state.flatSquareDrag = {
        mode: "rotate",
        squareIdx: sqFlatHit.squareIdx,
        startTheta: sq.theta || 0,
      };
    } else if (sqFlatHit.kind === "corner") {
      const anchor = squareAnchorCornerLocal(
        sq, sqFlatHit.cornerIdx, SHAPE_DEPTH_FLAT);
      state.flatSquareDrag = {
        mode: "corner",
        squareIdx: sqFlatHit.squareIdx,
        cornerIdx: sqFlatHit.cornerIdx,
        anchorU: anchor ? anchor[0] : undefined,
        anchorV: anchor ? anchor[1] : undefined,
      };
    } else {
      const lc = flatCursorToShapeLocal(sq, sx, sy);
      if (lc) {
        state.flatSquareDrag = {
          mode: "body",
          squareIdx: sqFlatHit.squareIdx,
          startCursorU: lc.u,
          startCursorV: lc.v,
          startCenterU: sq.u,
          startCenterV: sq.v,
        };
      }
    }
    _setCursor("grabbing");
    draw();
    return;
  }

  const hHit = flatHitTest(sx, sy);
  if (!hHit) {
    coneState.selectedQuad = -1;
    coneState.selectedSquare = -1;
    syncQuadList();
    draw();
    return;
  }
  if (hHit.quadIdx !== coneState.selectedQuad) {
    coneState.selectedQuad = hHit.quadIdx;
    syncQuadList();
  }
  coneState.selectedSquare = -1;

  const p = screenToFlat(sx, sy);
  const q = quads[hHit.quadIdx];

  let flatDrag = {
    mode: "body",
    quadIdx:   hHit.quadIdx,
    startPhi:  p.phi,
    startS:    p.s,
    startPhi0: q.phi0,
    startPhi1: q.phi1,
    startS0:   q.s0,
    startS1:   q.s1,
  };

  if (!passThrough && PATCH_SHAPE_EDIT_ENABLED) {
    if (hHit.kind === "corner") {
      flatDrag = { mode: "corner", quadIdx: hHit.quadIdx, cornerIdx: hHit.cornerIdx };
    } else if (hHit.kind === "edge") {
      flatDrag = { mode: "edge", quadIdx: hHit.quadIdx, edge: hHit.edge };
    }
  }

  state.flatDrag = flatDrag;
  _setCursor("grabbing");
  draw();
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  state.mouse.sx = sx; state.mouse.sy = sy;
  state.mouse.inside = (sx >= 0 && sy >= 0 &&
                        sx < window.innerWidth && sy < window.innerHeight);

  if (drawTool.active) {
    drawTool.hover = [sx, sy];
    draw();
    return;
  }

  let dragging = false;

  if (state.dragQuadVertex) {
    dragging = true;
    const { quadIdx, cornerIdx } = state.dragQuadVertex;
    const q = quads[quadIdx];
    const corner = quadCorners(q)[cornerIdx];
    const [wx, wy] = s2w(sx, sy);
    const np = projectToConeSurface(wx, wy, corner.s);
    flatMoveCorner(q, cornerIdx, np.phi, np.s);
  } else if (state.dragSquare) {
    dragging = true;
    const ds = state.dragSquare;
    const sq = floatSquares[ds.squareIdx];
    if (!sq) {
      state.dragSquare = null;
    } else {
      const qi = quadIdxById(sq.quadId);
      const f = qi >= 0 ? patchFrame(qi) : null;
      if (f) {
        if (ds.mode === "body") {
          const lc = cursorToLocalShapeRelativeExactWith(
                       f, qi, ds.startVc, ds.startPc, ds.startVMax,
                       sx, sy);
          let dU = lc.u - ds.startCursorU;
          let dV = lc.v - ds.startCursorV;
          if (e.shiftKey) dU = 0;
          const maxDU = BODY_DRAG_MAX_DU * f.uLen;
          const maxDV = BODY_DRAG_MAX_DV * f.vLen;
          if (dU >  maxDU) dU =  maxDU;
          if (dU < -maxDU) dU = -maxDU;
          if (dV >  maxDV) dV =  maxDV;
          if (dV < -maxDV) dV = -maxDV;
          const newU = ds.startCenterU + dU;
          const newV = ds.startCenterV + dV;
          setClonePosition(sq, newU, newV);
        } else if (ds.mode === "corner") {
          const [wx, wy] = s2w(sx, sy);
          const useAnchor = !e.shiftKey && ds.anchorU !== undefined &&
                                           ds.anchorV !== undefined;
          resizeSquareFromCorner(
            sq, ds.cornerIdx, wx, wy,
            useAnchor ? ds.anchorU : undefined,
            useAnchor ? ds.anchorV : undefined);
        } else if (ds.mode === "rotate") {
          const [wx, wy] = s2w(sx, sy);
          rotateSquareToCursor(sq, wx, wy, e.shiftKey, ds.startTheta);
        }
      }
    }
  } else if (state.dragApex) {
    dragging = true;
    const dSx = sx - state.dragApex.startSx;
    const dSy = sy - state.dragApex.startSy;
    // Write the INTENT, not the displayed value. clampApex derives the
    // displayed apex from the intent against the current depth; the
    // drag sees the result immediately through cone.ax/ay on the next
    // draw. This matches the pre-fix feel during drag while keeping
    // the original intent intact for a later zoom-in to restore.
    cone.axRaw = state.dragApex.startAx + dSx / view.scale;
    cone.ayRaw = state.dragApex.startAy - dSy / view.scale;
    clampApex();
  } else if (state.dragPatchBody) {
    dragging = true;
    const dp = state.dragPatchBody;
    const q = quads[dp.quadIdx];
    if (!q) {
      state.dragPatchBody = null;
    } else {
      const [wx, wy] = s2w(sx, sy);
      const p = projectToConeSurface(wx, wy, dp.startS);
      applyPatchDragFromStart(q, dp, p.phi, p.s, e.shiftKey);
    }
  } else if (state.flatSquareDrag) {
    dragging = true;
    const fsd = state.flatSquareDrag;
    const sq = floatSquares[fsd.squareIdx];
    if (!sq) {
      state.flatSquareDrag = null;
    } else {
      if (fsd.mode === "body") {
        const lc = flatCursorToShapeLocal(sq, sx, sy);
        if (lc) {
          const qi = quadIdxById(sq.quadId);
          const f  = qi >= 0 ? patchFrame(qi) : null;
          if (f) {
            let dU = lc.u - fsd.startCursorU;
            let dV = lc.v - fsd.startCursorV;
            if (e.shiftKey) dU = 0;
            const maxDU = BODY_DRAG_MAX_DU * f.uLen;
            const maxDV = BODY_DRAG_MAX_DV * f.vLen;
            if (dU >  maxDU) dU =  maxDU;
            if (dU < -maxDU) dU = -maxDU;
            if (dV >  maxDV) dV =  maxDV;
            if (dV < -maxDV) dV = -maxDV;
            const uWorld = fsd.startCenterU * f.uLen + dU;
            const vWorld = fsd.startCenterV * f.vLen + dV;
            setClonePosition(sq, uWorld, vWorld);
          }
        }
      } else if (fsd.mode === "corner") {
        const useAnchor = !e.shiftKey && fsd.anchorU !== undefined &&
                                         fsd.anchorV !== undefined;
        flatResizeSquareFromCornerIdx(
          sq, sx, sy, fsd.cornerIdx,
          useAnchor ? fsd.anchorU : undefined,
          useAnchor ? fsd.anchorV : undefined);
      } else if (fsd.mode === "rotate") {
        flatRotateSquareToCursor(sq, sx, sy, e.shiftKey, fsd.startTheta);
      }
    }
  } else if (state.flatDrag) {
    dragging = true;
    const fd = state.flatDrag;
    const q  = quads[fd.quadIdx];
    const p  = screenToFlat(sx, sy);
    if (fd.mode === "corner") {
      flatMoveCorner(q, fd.cornerIdx, p.phi, p.s);
    } else if (fd.mode === "edge") {
      flatMoveEdge(q, fd.edge, p.phi, p.s);
    } else if (fd.mode === "body") {
      applyPatchDragFromStart(q, fd, p.phi, p.s, e.shiftKey);
    }
  }

  if (dragging) {
    draw();
    return;
  }
  updateStatus();
});

window.addEventListener("mouseup", () => {
  if (drawTool.active) return;
  const hadDrag = !!(state.dragQuadVertex || state.dragApex ||
                     state.flatDrag || state.dragSquare ||
                     state.flatSquareDrag || state.dragPatchBody);
  if (hadDrag) {
    state.dragQuadVertex = null;
    state.dragApex       = null;
    state.flatDrag       = null;
    state.dragSquare     = null;
    state.flatSquareDrag = null;
    state.dragPatchBody  = null;
    _setCursor("crosshair");
    notifyChanged();
  }
});

canvas.addEventListener("wheel", (e) => {
  if (drawTool.active) return;
  const rect = canvas.getBoundingClientRect();
  const sy = e.clientY - rect.top;
  if (sy >= layout.dividerY) return;
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
  cone.depth = Math.max(3, Math.min(40, cone.depth * factor));
  // Re-derive the DISPLAYED apex from the INTENT (cone.axRaw/ayRaw)
  // against the new depth. This is what makes the clamp reversible:
  // a zoom-out shrinks the base radius and may push the displayed
  // apex inward, but the intent is untouched, so a zoom-in restores
  // the displayed apex — and with it, the shapes' projection from it.
  clampApex();
  syncPanelSliders();
  pgkitNotifyLocal("cone.depth");
  pgkitNotifyLocal("cone.apexX");
  pgkitNotifyLocal("cone.apexY");
  draw();
}, { passive: false });

_setCursor("crosshair");
"""
