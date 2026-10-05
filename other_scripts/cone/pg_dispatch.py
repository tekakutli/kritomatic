"""
pg_dispatch.py — draw() and the canvas event listeners.

Priority on mousedown in the cone band:

    1.  shift + click on a patch          → drop a square there
    2.  hit on a square's corner / body   → resize / move that square
    3.  hit on a patch corner             → shape the patch
    4.  anywhere else                     → tilt the apex

In the flat band:

    1.  corner / edge / body of a square  → resize / move that square
    2.  corner / edge / body of a patch   → shape / slide that patch
    3.  empty space                       → deselect

Grab-and-drag
=============
A body drag records the cursor's local position and the square's
centre at mousedown, then applies the cursor's DELTA to the centre on
every mousemove.  The square therefore stays exactly where it was
when grabbed — no jump to the click — and follows the cursor
faithfully from there.  The same scheme is used on both bands; the
only difference is that the cone band's cursor-to-local conversion
inverts the per-vertex perspective projection, while the flat band's
is a plain linear map.

Squares are real squares in the patch's plane: sides along U and
along V of the patch's local frame.  Side length is scale × uLen,
constant in world-local coordinates; perspective is applied per
vertex at draw time, so a square shrinks to zero on the horizon and
stretches away from it below the patch.
"""

DISPATCH_JS = r"""
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
      "drag patches in either view \u00B7 shift+click a patch to " +
      "drop a square";
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
  if (state.dragQuadVertex) {
    drag = "   \u00B7  shaping " + quads[state.dragQuadVertex.quadIdx].name;
  } else if (state.dragSquare) {
    const sq = floatSquares[state.dragSquare.squareIdx];
    if (sq) {
      const side = squareSideLength(sq);
      drag = "   \u00B7  square #" + sq.id +
             "  side " + side.toFixed(1);
    }
  } else if (state.flatSquareDrag) {
    const sq = floatSquares[state.flatSquareDrag.squareIdx];
    if (sq) {
      const side = squareSideLength(sq);
      drag = "   \u00B7  square #" + sq.id + " (flat)" +
             "  side " + side.toFixed(1);
    }
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

  /* ---- cone band ------------------------------------------------ */
  if (sy < layout.dividerY) {

    /* 1. shift+click drops a square on the patch under the cursor */
    if (e.shiftKey) {
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

    /* 2. square hit — corner for resize, body for grab-and-drag */
    const sqHit = squareHitTest(sx, sy);
    if (sqHit) {
      const sq = floatSquares[sqHit.squareIdx];
      const qi = quadIdxById(sq.quadId);
      if (qi >= 0 && qi !== selectedQuad) {
        selectedQuad = qi;
        syncQuadList();
      }
      selectedSquare = sqHit.squareIdx;

      if (sqHit.kind === "corner") {
        state.dragSquare = {
          mode: "corner",
          squareIdx: sqHit.squareIdx,
          cornerIdx: sqHit.cornerIdx,
        };
      } else {
        /* Grab-and-drag: record the cursor's start local position
           and the square's start centre.  Subsequent mousemoves
           apply the cursor's delta to the centre, so the square
           does not jump to the click. */
        const f = qi >= 0 ? patchFrame(qi) : null;
        const c = squareCenterLocal(sq);
        if (f && c) {
          const lc = cursorToLocalWithPersp(f, qi, sx, sy);
          state.dragSquare = {
            mode: "body",
            squareIdx: sqHit.squareIdx,
            startCursorU: lc.u,
            startCursorV: lc.v,
            startCenterU: c[0],
            startCenterV: c[1],
          };
        }
      }
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }

    /* 3. patch corner — shape the patch */
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

    /* 4. else — tilt the apex */
    selectedSquare = -1;
    state.dragApex = {
      startSx: sx, startSy: sy,
      startAx: cone.ax, startAy: cone.ay,
    };
    canvas.style.cursor = "grabbing";
    return;
  }

  /* ---- flat band ------------------------------------------------ */

  /* 1. Squares first.  They live inside the active patch, so they
     win any overlap with the patch's own corner / edge / body. */
  const sqFlatHit = squareFlatHitTest(sx, sy);
  if (sqFlatHit) {
    const sq = floatSquares[sqFlatHit.squareIdx];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0 && qi !== selectedQuad) {
      selectedQuad = qi;
      syncQuadList();
    }
    selectedSquare = sqFlatHit.squareIdx;

    if (sqFlatHit.kind === "corner") {
      state.flatSquareDrag = {
        mode: "corner",
        squareIdx: sqFlatHit.squareIdx,
        cornerIdx: sqFlatHit.cornerIdx,
      };
    } else {
      /* Same grab-and-drag idea as the cone band, in (uHat, vHat)
         coordinates. */
      const p = flatCursorToSquareLocal(sq, sx, sy);
      if (p) {
        state.flatSquareDrag = {
          mode: "body",
          squareIdx: sqFlatHit.squareIdx,
          startCursorU: p.uHat,
          startCursorV: p.vHat,
          startCenterU: sq.u,
          startCenterV: sq.v,
        };
      }
    }
    canvas.style.cursor = "grabbing";
    draw();
    return;
  }

  /* 2. Patch corner / edge / body */
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
  if (hHit.kind === "corner") {
    state.flatDrag = {
      mode: "corner", quadIdx: hHit.quadIdx,
      cornerIdx: hHit.cornerIdx,
    };
  } else if (hHit.kind === "edge") {
    state.flatDrag = {
      mode: "edge", quadIdx: hHit.quadIdx, edge: hHit.edge,
    };
  } else {
    const p = screenToFlat(sx, sy);
    state.flatDrag = {
      mode: "body", quadIdx: hHit.quadIdx,
      lastPhi: p.phi, lastS: p.s,
    };
  }
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
          /* Grab-and-drag: apply the cursor's delta from the
             mousedown position to the mousedown centre.  The
             square does not jump when grabbed and follows the
             cursor faithfully from there. */
          const lc = cursorToLocalWithPersp(f, qi, sx, sy);
          const newU = ds.startCenterU + (lc.u - ds.startCursorU);
          const newV = ds.startCenterV + (lc.v - ds.startCursorV);
          setClonePosition(sq, newU, newV);
        } else {
          const [wx, wy] = s2w(sx, sy);
          resizeSquareFromCorner(sq, ds.cornerIdx, wx, wy);
        }
      }
    }
  } else if (state.dragApex) {
    const dSx = sx - state.dragApex.startSx;
    const dSy = sy - state.dragApex.startSy;
    cone.ax = state.dragApex.startAx + dSx / view.scale;
    cone.ay = state.dragApex.startAy - dSy / view.scale;
    clampApex();
  } else if (state.flatSquareDrag) {
    const fsd = state.flatSquareDrag;
    const sq = floatSquares[fsd.squareIdx];
    if (!sq) {
      state.flatSquareDrag = null;
    } else {
      if (fsd.mode === "body") {
        /* Same grab-and-drag idea, in (uHat, vHat).  The v-bound is
           applied after the delta so a square pushed past the apex
           line stops there, exactly as on the cone band. */
        const p = flatCursorToSquareLocal(sq, sx, sy);
        if (p) {
          const qi = quadIdxById(sq.quadId);
          const h = qi >= 0 ? horizonLocal(qi) : null;
          if (h) {
            sq.u = fsd.startCenterU + (p.uHat - fsd.startCursorU);
            const vMin = -WEDGE_BASE_REACH;
            const vMax = h.vHat;
            sq.v = Math.max(vMin, Math.min(vMax,
              fsd.startCenterV + (p.vHat - fsd.startCursorV)));
          }
        }
      } else {
        flatResizeSquareFromCorner(sq, sx, sy);
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
      flatMoveBody(q, p.phi - fd.lastPhi, p.s - fd.lastS);
      fd.lastPhi = p.phi;
      fd.lastS   = p.s;
    }
  }
  draw();
});

window.addEventListener("mouseup", () => {
  if (state.dragQuadVertex || state.dragApex ||
      state.flatDrag || state.dragSquare || state.flatSquareDrag) {
    state.dragQuadVertex = null;
    state.dragApex       = null;
    state.flatDrag       = null;
    state.dragSquare     = null;
    state.flatSquareDrag = null;
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
