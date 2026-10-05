"""pg_dispatch.py — draw() and the canvas event listeners."""

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

  /* cone band */
  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, cw, layout.coneH); ctx.clip();
  drawConeView();
  ctx.restore();

  /* divider */
  ctx.fillStyle = "#dce6f2";
  ctx.fillRect(0, layout.dividerY,     cw, 1);
  ctx.fillStyle = "#1e2836";
  ctx.fillRect(0, layout.dividerY + 2, cw, 1);

  /* flat (parameter-space) view */
  drawFlatView();

  /* sheet frame */
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
      "drag patches in either view";
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
  const drag = state.dragQuadVertex
    ? "   \u00B7  shaping " + quads[state.dragQuadVertex.quadIdx].name
    : "";
  el.textContent =
    "cursor (" + wx.toFixed(1) + ", " + wy.toFixed(1) + ")" +
    "   apex (" + cone.ax.toFixed(1) + ", " + cone.ay.toFixed(1) + ")" +
    "   tilt " + tiltDeg.toFixed(1) + "\u00B0" +
    "   depth " + cone.depth.toFixed(1) +
    drag;
  el.className = "ok";
}

/* ==========================================================================
   HIT TEST — 3D corner
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
   ==========================================================================
   Priority on mousedown:

     1. a patch corner within 12 px              → drag that corner
     2. the cone band, anywhere else             → drag the apex
     3. flat band, corner handle                 → drag that corner
     4. flat band, edge                          → drag that edge
     5. flat band, patch interior                → move the whole patch
     6. flat band, empty                         → deselect */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  if (sy < layout.dividerY) {
    const vHit = findQuadVertexAt(sx, sy);
    if (vHit) {
      if (vHit.quadIdx !== selectedQuad) {
        selectedQuad = vHit.quadIdx;
        syncQuadList();
      }
      state.dragQuadVertex = vHit;
      canvas.style.cursor = "grabbing";
      draw();
      return;
    }
    state.dragApex = {
      startSx: sx, startSy: sy,
      startAx: cone.ax, startAy: cone.ay,
    };
    canvas.style.cursor = "grabbing";
    return;
  }

  const hHit = flatHitTest(sx, sy);
  if (!hHit) {
    selectedQuad = -1;
    syncQuadList();
    draw();
    return;
  }
  if (hHit.quadIdx !== selectedQuad) {
    selectedQuad = hHit.quadIdx;
    syncQuadList();
  }
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
    /* 3D corner drag.  Cursor → world → nearest (phi, s).  The
       result feeds flatMoveCorner, so the same clamping that
       guards a flat-view drag also guards a cone-view drag. */
    const { quadIdx, cornerIdx } = state.dragQuadVertex;
    const q = quads[quadIdx];
    const corner = quadCorners(q)[cornerIdx];
    const [wx, wy] = s2w(sx, sy);
    const np = projectToConeSurface(wx, wy, corner.s);
    flatMoveCorner(q, cornerIdx, np.phi, np.s);
  } else if (state.dragApex) {
    const dSx = sx - state.dragApex.startSx;
    const dSy = sy - state.dragApex.startSy;
    cone.ax = state.dragApex.startAx + dSx / view.scale;
    cone.ay = state.dragApex.startAy - dSy / view.scale;
    clampApex();
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
  if (state.dragQuadVertex || state.dragApex || state.flatDrag) {
    state.dragQuadVertex = null;
    state.dragApex       = null;
    state.flatDrag       = null;
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