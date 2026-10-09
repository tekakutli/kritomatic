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

Alt also works on MIRROR squares.  A mirror is drawn by mutating
the square into its mirrored state and running the ordinary draw,
so the mirror's on-screen geometry is a second position for the
same square — one that no ordinary hit test can see.  When Alt is
held, a click that misses every editable square falls back to
squareMirrorHitTest / squareFlatMirrorHitTest, which run the same
per-square hit test inside _withSquareMirror.  A hit there resolves
to the same patch the original square would, and the drag proceeds
identically.

The order matters: the ordinary hit test runs first, so an Alt+click
that lands on an editable square always wins over a mirror square
that happens to overlap it.  Mirrors are only considered when the
cursor is over nothing editable.

When Alt is NOT held, square interaction is unchanged — mirror
squares remain non-editable, as before.

Alt also suppresses the Shift+click "drop a square here" action, so
Alt+Shift+drag on a square behaves as a patch drag constrained to
the patch's centre→apex axis (Shift's usual meaning for a patch
drag).

Ctrl is deliberately NOT used for this: on macOS the browser
translates Ctrl+click into a right-click, so the mouse event arrives
with e.button !== 0 and the handler bails at the top.

SHIFT-HELD CORNER RESIZE
========================
A corner drag resizes a shape.  By default the drag ANCHORS the
DIAGONALLY OPPOSITE corner: that corner stays fixed, and only the
two edges that meet at the dragged corner move.  The shape's centre
shifts to the midpoint of the fixed anchor and the cursor.

Holding Shift switches to a CENTRED resize — the shape resizes
about its own centre.  All four sides move symmetrically and the
stored (u, v) does not change.

The anchor is captured at mousedown time (from the shape's pre-drag
state) and stored on the drag state as anchorU / anchorV.  It is
consumed in setShapeFromCornerLocal's anchored branch, in
pg_view_squares.py.  Both the cone band and the flat band use the
same mechanism; the only difference is which depth multiplier the
anchor's v-extent is computed with (effectiveConeDepth() vs
SHAPE_DEPTH_FLAT).

Shift's existing meanings for the other drags are unchanged:
constrain-to-V-axis for square body drags, 15° snap for rotate
drags, centre→apex axis constraint for patch drags.  The role swap
only applies when the drag mode is "corner".

DRAW TOOL INTERCEPTION
======================
While the Draw Quad tool is active, every canvas interaction is
routed to the draw tool first:

    mousedown   accumulates a corner point (button 0 only),
    mousemove   updates the live preview,
    mouseup     swallowed,
    wheel       swallowed.

Pressing Escape cancels the tool (the keydown handler lives in
pg_panel.py).  On the fourth click, finishDrawTool fits the patch
and rectangle, clears the transient points, and returns the canvas
to its ordinary behaviour.
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

  /* Draw tool preview: the click chain so far, plus a rubber-band
     segment to the current hover position when the tool is active
     and fewer than four corners have been committed. */
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
   ALT PASS-THROUGH HELPERS
   ==========================================================================
   Both bands' Alt+square paths end in the same action: select the
   patch that the square belongs to, clear the square selection, and
   seed the appropriate patch-body drag state.  Extracting the two
   bodies into named helpers lets the ordinary hit test and the
   mirror fallback share exactly one code path, so their behaviour
   cannot drift apart. */

function _beginConePatchDragFromSquareIdx(squareIdx, sx, sy) {
  const sq = floatSquares[squareIdx];
  const qi = sq ? quadIdxById(sq.quadId) : -1;
  if (qi < 0) return false;

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
  return true;
}

function _beginFlatPatchDragFromSquareIdx(squareIdx, sx, sy) {
  const sq = floatSquares[squareIdx];
  const qi = sq ? quadIdxById(sq.quadId) : -1;
  if (qi < 0) return false;

  const q = quads[qi];
  if (qi !== selectedQuad) {
    selectedQuad = qi;
    syncQuadList();
  }
  selectedSquare = -1;

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
  canvas.style.cursor = "grabbing";
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

  /* ---- draw tool intercept ------------------------------------- */
  if (drawTool.active) {
    e.preventDefault();
    addDrawPoint(sx, sy);
    return;
  }

  /* Alt is the "grab the patch under the square" modifier.  When
     held, a click on a square targets the patch that square belongs
     to (identified by the square's quadId, so the pass-through works
     even when the square overhangs its patch).  A click that misses
     every editable square but lands on a MIRROR square does the
     same, via the mirror hit test.  See the module docstring,
     ALT PASS-THROUGH. */
  const passThrough = e.altKey;

  /* ---- cone band ------------------------------------------------ */
  if (sy < layout.dividerY) {

    /* The square hit test always runs, so we know what is under the
       cursor even when Alt is held.  It is the only reliable way to
       name the patch that a clicked square belongs to.  It only
       sees editable squares; the mirror fallback below covers the
       visual clones. */
    const sqHit = squareHitTest(sx, sy);

    if (passThrough) {
      let hit = sqHit;
      if (!hit) {
        /* Ordinary hit missed.  The cursor might be over a mirror
           square — one that the ordinary hit test can't see because
           it exists only inside the mirror state.  Try again with
           the mirror hit test; a hit there resolves to the same
           patch the original square would. */
        hit = squareMirrorHitTest(sx, sy);
      }
      if (hit && _beginConePatchDragFromSquareIdx(hit.squareIdx,
                                                  sx, sy)) {
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
        /* Capture the anchor (the diagonally-opposite corner's
           local position) at drag start, so the anchored (default)
           resize has it available without recomputing from a
           shape the drag has already reshaped.  See the module
           docstring, SHIFT-HELD CORNER RESIZE. */
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

  /* Same shape as the cone band: run the flat square hit test first,
     then fall back to the mirror hit test when Alt is held.  Both
     bands route the pass-through into the same helper, so an alt+click
     on a mirror grabs the same patch the original square would. */
  const sqFlatHit = squareFlatHitTest(sx, sy);

  if (passThrough) {
    let hit = sqFlatHit;
    if (!hit) hit = squareFlatMirrorHitTest(sx, sy);
    if (hit && _beginFlatPatchDragFromSquareIdx(hit.squareIdx,
                                                sx, sy)) {
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
      /* Same anchor capture as the cone band, but with the flat
         band's depth multiplier.  See the module docstring,
         SHIFT-HELD CORNER RESIZE. */
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

  /* Draw tool: just track the hover position for the preview. */
  if (drawTool.active) {
    drawTool.hover = [sx, sy];
    draw();
    return;
  }

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
          /* Default: anchor the diagonally-opposite corner.
             Shift held: resize about the shape's centre.  The
             anchor was captured at mousedown; see the module
             docstring, SHIFT-HELD CORNER RESIZE. */
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
        /* Same anchored / centred split as the cone band, with the
           default roles flipped: no Shift anchors, Shift centres.
           See the module docstring, SHIFT-HELD CORNER RESIZE. */
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
  if (drawTool.active) return;
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
  if (drawTool.active) return;
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
