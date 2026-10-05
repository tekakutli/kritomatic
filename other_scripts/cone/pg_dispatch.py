"""
pg_dispatch.py — draw() and the canvas event listeners.

[unchanged docstring...]

ALT PASS-THROUGH
================
Holding Alt while clicking on a square targets the PATCH THAT SQUARE
BELONGS TO, rather than the square itself.  The patch is identified
by the hit square's quadId — not by the cursor position — so the
pass-through works even when the square overhangs its patch (which
happens easily: a square can be dragged past the patch's u / v
bounds).  Without that indirection, an alt+click on an overhanging
part of a square would miss every patch and fall through to the
apex drag, changing the point of view instead of moving the patch.

When Alt is NOT held, square interaction is unchanged.

Alt also suppresses the Shift+click "drop a square here" action, so
Alt+Shift+drag on a square behaves as a patch drag constrained to
the patch's centre→apex axis (Shift's usual meaning for a patch
drag).

Ctrl is deliberately NOT used for this: on macOS the browser
translates Ctrl+click into a right-click, so the mouse event arrives
with e.button !== 0 and the handler bails at the top.
"""

DISPATCH_JS = r"""
/* ==========================================================================
   DRAG DELTA CAPS
   ========================================================================== */

const BODY_DRAG_MAX_DU = 4.0;
const BODY_DRAG_MAX_DV = 4.0;

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

  /* Panel refresh: the two patch-position scrub fields follow the
     selected patch's centre.  Called every frame so live drags,
     apex tilts, and depth changes are all reflected.  A focused
     field is left alone by the sync function. */
  _syncPatchCoordInputs();
}

/* ==========================================================================
   STATUS
   ========================================================================== */

function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;

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
      const slp  = Math.round((sq.slope || 0) * 180 / Math.PI);
      drag = "   \u00B7  shape #" + sq.id +
             "  W " + dims.w.toFixed(2) +
             "  H " + dims.h.toFixed(2) +
             "  \u2220 " + deg + "\u00B0" +
             "  \u2197 " + slp + "\u00B0";
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
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    order.push(selectedQuad);
  }
  for (let i = 0; i < quads.length; i++) {
    if (i !== selectedQuad) order.push(i);
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
   EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  /* Alt is the "grab the patch under the square" modifier.  When
     held, a click on a square targets the patch that square belongs
     to (identified by the square's quadId, so the pass-through works
     even when the square overhangs its patch).  See the module
     docstring, ALT PASS-THROUGH. */
  const passThrough = e.altKey;

  /* ---- cone band ------------------------------------------------ */
  if (sy < layout.dividerY) {

    /* The square hit test always runs, so we know what is under the
       cursor even when Alt is held.  It is the only reliable way to
       name the patch that a clicked square belongs to. */
    const sqHit = squareHitTest(sx, sy);

    if (passThrough && sqHit) {
      /* Alt + square: target the patch that square lives on. */
      const sq = floatSquares[sqHit.squareIdx];
      const qi = sq ? quadIdxById(sq.quadId) : -1;
      if (qi >= 0) {
        const q = quads[qi];
        if (qi !== selectedQuad) {
          selectedQuad = qi;
          syncQuadList();
        }
        selectedSquare = -1;

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
        canvas.style.cursor = "grabbing";
        draw();
        return;
      }
    }

    if (!passThrough && e.shiftKey) {
      const patchIdx = findPatchAtScreen(sx, sy);
      if (patchIdx >= 0) {
        const f = patchFrame(patchIdx);
        if (f) {
          const lc = screenToFrame(f, sx, sy);
          addSquareAt(patchIdx, lc.u, lc.v);
          if (patchIdx !== selectedQuad) {
            selectedQuad = patchIdx;
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
      if (qi >= 0 && qi !== selectedQuad) {
        selectedQuad = qi;
        syncQuadList();
      }
      selectedSquare = sqHit.squareIdx;

      if (sqHit.kind === "rotate") {
        state.dragSquare = {
          mode: "rotate",
          squareIdx: sqHit.squareIdx,
          startTheta: sq.theta || 0,
        };
      } else if (sqHit.kind === "corner") {
        state.dragSquare = {
          mode: "corner",
          squareIdx: sqHit.squareIdx,
          cornerIdx: sqHit.cornerIdx,
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
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }

    if (!passThrough && PATCH_SHAPE_EDIT_ENABLED) {
      const vHit = findQuadVertexAt(sx, sy);
      if (vHit) {
        if (vHit.quadIdx !== selectedQuad) {
          selectedQuad = vHit.quadIdx;
          syncQuadList();
        }
        selectedSquare = -1;
        state.dragQuadVertex = vHit;
        canvas.style.cursor = "grabbing";
        draw();
        return;
      }
    }

    const patchHit = findPatchAtScreen(sx, sy);
    if (patchHit >= 0) {
      const q = quads[patchHit];
      if (patchHit !== selectedQuad) {
        selectedQuad = patchHit;
        syncQuadList();
      }
      selectedSquare = -1;

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
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }

    selectedSquare = -1;
    state.dragApex = {
      startSx: sx, startSy: sy,
      startAx: cone.ax, startAy: cone.ay,
    };
    canvas.style.cursor = "grabbing";
    return;
  }

  /* ---- flat band ------------------------------------------------ */

  /* Same shape: run the flat square hit test first, so an alt+click
     can name the patch by the square's quadId rather than relying
     on the pointer being over the patch's own quad. */
  const sqFlatHit = squareFlatHitTest(sx, sy);

  if (passThrough && sqFlatHit) {
    const sq = floatSquares[sqFlatHit.squareIdx];
    const qi = sq ? quadIdxById(sq.quadId) : -1;
    if (qi >= 0) {
      const q = quads[qi];
      if (qi !== selectedQuad) {
        selectedQuad = qi;
        syncQuadList();
      }
      selectedSquare = -1;

      const p = screenToFlat(sx, sy);
      state.flatDrag = {
        mode: "body",
        quadIdx:   qi,
        startPhi:  p.phi,
        startS:    p.s,
        startPhi0: q.phi0,
        startPhi1: q.phi1,
        startS0:   q.s0,
        startS1:   q.s1,
      };
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }
  }

  if (!passThrough && sqFlatHit) {
    const sq = floatSquares[sqFlatHit.squareIdx];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0 && qi !== selectedQuad) {
      selectedQuad = qi;
      syncQuadList();
    }
    selectedSquare = sqFlatHit.squareIdx;

    if (sqFlatHit.kind === "rotate") {
      state.flatSquareDrag = {
        mode: "rotate",
        squareIdx: sqFlatHit.squareIdx,
        startTheta: sq.theta || 0,
      };
    } else if (sqFlatHit.kind === "corner") {
      state.flatSquareDrag = {
        mode: "corner",
        squareIdx: sqFlatHit.squareIdx,
        cornerIdx: sqFlatHit.cornerIdx,
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
    canvas.style.cursor = "grabbing";
    draw();
    return;
  }

  const hHit = flatHitTest(sx, sy);
  if (!hHit) {
    selectedQuad = -1;
    selectedSquare = -1;
    syncQuadList();
    draw();
    return;
  }
  if (hHit.quadIdx !== selectedQuad) {
    selectedQuad = hHit.quadIdx;
    syncQuadList();
  }
  selectedSquare = -1;

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
      flatDrag = {
        mode: "corner",
        quadIdx: hHit.quadIdx,
        cornerIdx: hHit.cornerIdx,
      };
    } else if (hHit.kind === "edge") {
      flatDrag = {
        mode: "edge",
        quadIdx: hHit.quadIdx,
        edge: hHit.edge,
      };
    }
  }

  state.flatDrag = flatDrag;
  canvas.style.cursor = "grabbing";
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

  if (state.dragQuadVertex) {
    const { quadIdx, cornerIdx } = state.dragQuadVertex;
    const q = quads[quadIdx];
    const corner = quadCorners(q)[cornerIdx];
    const [wx, wy] = s2w(sx, sy);
    const np = projectToConeSurface(wx, wy, corner.s);
    flatMoveCorner(q, cornerIdx, np.phi, np.s);
  } else if (state.dragSquare) {
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

          /* Shift held: constrain to the patch's V axis, which
             points from the patch's base midpoint toward the apex.
             Both directions along that axis remain free. */
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
          resizeSquareFromCorner(sq, ds.cornerIdx, wx, wy);
        } else if (ds.mode === "rotate") {
          const [wx, wy] = s2w(sx, sy);
          rotateSquareToCursor(sq, wx, wy, e.shiftKey, ds.startTheta);
        }
      }
    }
  } else if (state.dragApex) {
    const dSx = sx - state.dragApex.startSx;
    const dSy = sy - state.dragApex.startSy;
    cone.ax = state.dragApex.startAx + dSx / view.scale;
    cone.ay = state.dragApex.startAy - dSy / view.scale;
    clampApex();
  } else if (state.dragPatchBody) {
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

            /* Shift held: constrain to the patch's V axis, which
               points from the patch's base midpoint toward the
               apex.  Both directions along that axis remain free. */
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
        flatResizeSquareFromCornerIdx(sq, sx, sy, fsd.cornerIdx);
      } else if (fsd.mode === "rotate") {
        flatRotateSquareToCursor(sq, sx, sy, e.shiftKey, fsd.startTheta);
      }
    }
  } else if (state.flatDrag) {
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
  draw();
});

window.addEventListener("mouseup", () => {
  if (state.dragQuadVertex || state.dragApex ||
      state.flatDrag || state.dragSquare || state.flatSquareDrag ||
      state.dragPatchBody) {
    state.dragQuadVertex = null;
    state.dragApex       = null;
    state.flatDrag       = null;
    state.dragSquare     = null;
    state.flatSquareDrag = null;
    state.dragPatchBody  = null;
    canvas.style.cursor  = "crosshair";
  }
});

canvas.addEventListener("wheel", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sy = e.clientY - rect.top;
  if (sy >= layout.dividerY) return;
  e.preventDefault();
  const factor = e.deltaY < 0 ? 1.08 : 1 / 1.08;
  cone.depth = Math.max(3, Math.min(40, cone.depth * factor));
  clampApex();
  syncPanelSliders();
  draw();
}, { passive: false });

canvas.style.cursor = "crosshair";
"""
