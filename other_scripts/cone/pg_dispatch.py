"""
pg_dispatch.py — draw() and the canvas event listeners.

Priority on mousedown in the cone band:

    1.  shift + click on a patch          → drop a shape there
    2.  rotate handle of a shape          → rotate it
    3.  corner handle of a shape          → resize it
    4.  body of a shape                   → translate it
    5.  patch body                        → translate the patch
    6.  anywhere else                     → tilt the apex

In the flat band:

    1.  rotate handle of a shape          → rotate it
    2.  corner handle of a shape          → resize it
    3.  body of a shape                   → translate it
    4.  patch body / edge / corner        → translate the patch
    5.  empty space                       → deselect

Shapes are rectangles with independent width and height, rotated by
an angle within the patch's own (u, v) plane.  Corner drags change
the two dimensions independently; the rotate handle changes the
angle.  Hold Shift while rotating to snap to 15° increments.

PERSPECTIVE FREEZE
==================
A shape's screen position on the cone band is scaled about the apex
by one factor — the shape's own centre v̂ — so the whole shape is
scaled uniformly and rotation in (u, v) is a pure rotation on
screen.  During a drag the factor is frozen at mousedown: the drag's
cursor-to-local conversion and its corner / rotate math all use that
one value, so nothing about the shape shifts between scalings
mid-gesture.

Each band renders the shape's V extent through its own multiplier —
SHAPE_DEPTH_CONE in the cone band, SHAPE_DEPTH_FLAT in the flat
band — so a corner drag in one band un-scales by that band's
multiplier when writing back to the shape's stored scaleV.
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
      "shift+click a patch to drop a shape \u00B7 " +
      "drag corners, rotate handle, or interior to edit";
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
      /* Show the dimensions as the ACTIVE band renders them.
         state.dragSquare is a cone-band drag, so it uses the cone
         depth; state.flatSquareDrag is a flat-band drag, so it
         uses the flat depth. */
      const inFlat = !!state.flatSquareDrag;
      const depth  = inFlat ? SHAPE_DEPTH_FLAT : SHAPE_DEPTH_CONE;
      const dims   = squareDims(sq, depth);
      const deg    = Math.round((sq.theta || 0) * 180 / Math.PI);
      drag = "   \u00B7  shape #" + sq.id +
             "  W " + dims.w.toFixed(2) +
             "  H " + dims.h.toFixed(2) +
             "  \u2220 " + deg + "\u00B0";
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

  /* ---- cone band ------------------------------------------------ */
  if (sy < layout.dividerY) {

    /* 1. shift+click drops a shape on the patch under the cursor */
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

    /* 2. shape hit — rotate / corner / body */
    const sqHit = squareHitTest(sx, sy);
    if (sqHit) {
      const sq = floatSquares[sqHit.squareIdx];
      const qi = quadIdxById(sq.quadId);
      if (qi >= 0 && qi !== selectedQuad) {
        selectedQuad = qi;
        syncQuadList();
      }
      selectedSquare = sqHit.squareIdx;

      /* Persp is captured once here and reused for every frame of
         the gesture.  Without the freeze, the shape's own persp
         would change mid-drag and the deltas would come out of two
         different scalings. */
      const frozenPersp = shapePersp(sq);

      if (sqHit.kind === "rotate") {
        state.dragSquare = {
          mode: "rotate",
          squareIdx: sqHit.squareIdx,
          persp: frozenPersp,
        };
      } else if (sqHit.kind === "corner") {
        state.dragSquare = {
          mode: "corner",
          squareIdx: sqHit.squareIdx,
          cornerIdx: sqHit.cornerIdx,
          persp: frozenPersp,
        };
      } else {
        const f = qi >= 0 ? patchFrame(qi) : null;
        const c = squareCenterLocal(sq);
        if (f && c) {
          const lc = cursorToLocalWithShapePersp(
            f, qi, sx, sy, frozenPersp);
          state.dragSquare = {
            mode: "body",
            squareIdx: sqHit.squareIdx,
            startCursorU: lc.u,
            startCursorV: lc.v,
            startCenterU: c[0],
            startCenterV: c[1],
            persp: frozenPersp,
          };
        }
      }
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }

    /* 3. patch corner — shape the patch (gated) */
    if (PATCH_SHAPE_EDIT_ENABLED) {
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

    /* 4. patch body — translate the patch */
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
        quadIdx: patchHit,
        lastPhi: p0.phi,
        lastS:   p0.s,
      };
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }

    /* 5. else — tilt the apex */
    selectedSquare = -1;
    state.dragApex = {
      startSx: sx, startSy: sy,
      startAx: cone.ax, startAy: cone.ay,
    };
    canvas.style.cursor = "grabbing";
    return;
  }

  /* ---- flat band ------------------------------------------------ */

  /* 1. shape first */
  const sqFlatHit = squareFlatHitTest(sx, sy);
  if (sqFlatHit) {
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

  /* 2. patch */
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

  let flatDrag = {
    mode: "body",
    quadIdx: hHit.quadIdx,
    lastPhi: p.phi,
    lastS: p.s,
  };

  if (PATCH_SHAPE_EDIT_ENABLED) {
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
          const lc = cursorToLocalWithShapePersp(f, qi, sx, sy, ds.persp);
          const newU = ds.startCenterU + (lc.u - ds.startCursorU);
          const newV = ds.startCenterV + (lc.v - ds.startCursorV);
          setClonePosition(sq, newU, newV);
        } else if (ds.mode === "corner") {
          const [wx, wy] = s2w(sx, sy);
          resizeSquareFromCorner(sq, ds.cornerIdx, wx, wy, ds.persp);
        } else if (ds.mode === "rotate") {
          const [wx, wy] = s2w(sx, sy);
          rotateSquareToCursor(sq, wx, wy, e.shiftKey, ds.persp);
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
      const p = projectToConeSurface(wx, wy, dp.lastS);

      let dPhi = p.phi - dp.lastPhi;
      if (dPhi >  Math.PI) dPhi -= 2 * Math.PI;
      if (dPhi < -Math.PI) dPhi += 2 * Math.PI;

      const dS = p.s - dp.lastS;

      flatMoveBody(q, dPhi, dS);
      dp.lastPhi = p.phi;
      dp.lastS   = p.s;
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
            const uWorld =
              fsd.startCenterU * f.uLen + (lc.u - fsd.startCursorU);
            const vWorld =
              fsd.startCenterV * f.vLen + (lc.v - fsd.startCursorV);
            setClonePosition(sq, uWorld, vWorld);
          }
        }
      } else if (fsd.mode === "corner") {
        flatResizeSquareFromCornerIdx(sq, sx, sy, fsd.cornerIdx);
      } else if (fsd.mode === "rotate") {
        flatRotateSquareToCursor(sq, sx, sy, e.shiftKey);
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
