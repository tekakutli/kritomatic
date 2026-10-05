"""
pg_view_flat.py — the parameter-space view of the cone's lateral
surface.

[...existing documentation, plus:]

SHAPES IN THE FLAT VIEW
=======================
A shape lives on the patch's plane.  It is rendered in this band by:

    1.  Mapping its unrotated (u, v) corners to (phi, s) via the same
        linear correspondence that keeps the patch's own corners at
        the patch's phi/s bounds.

    2.  For each visible wrapped copy (integer-2π shift), projecting
        the (phi, s) corners to screen with flatToScreen.

    3.  Rotating the resulting screen points by θ about the shape's
        own flat-screen centre, as a rigid screen rotation.

Rotation in the flat view is therefore screen-space — the shape's
flat footprint is the unrotated one, turned.  It matches the cone
band's behaviour exactly: both views rotate the projected shape
rigidly on screen, so a square stays a square (up to the flat view's
own fixed anisotropy) and the two views agree.
"""


FLAT_VIEW_JS = r"""
/* ==========================================================================
   FLAT VIEW
   ========================================================================== */

const FLAT_PHI_MIN  = 0;
const FLAT_PHI_MAX  = 2 * Math.PI;
const FLAT_PHI_SPAN = FLAT_PHI_MAX - FLAT_PHI_MIN;
const FLAT_S_MIN    = 0;
const FLAT_S_MAX    = 1;

const QUAD_MIN_SIZE_PHI = 0.06;
const QUAD_MIN_SIZE_S   = 0.04;

/* ==========================================================================
   PHI-WRAP HELPERS
   ========================================================================== */

function _phiCopies(phi0, phi1) {
  const kMin = Math.floor((FLAT_PHI_MIN - phi1) / FLAT_PHI_SPAN);
  const kMax = Math.ceil ((FLAT_PHI_MAX - phi0) / FLAT_PHI_SPAN);
  const out = [];
  for (let k = kMin; k <= kMax; k++) out.push(k * FLAT_PHI_SPAN);
  return out;
}

function _phiShiftForPatch(phiCursor, phi0, phi1) {
  const phiMid = (phi0 + phi1) / 2;
  return Math.round((phiMid - phiCursor) / FLAT_PHI_SPAN) * FLAT_PHI_SPAN;
}

function flatRect() {
  const W = window.innerWidth;
  const H = layout.reservedH;
  const MARGIN_X = 44;
  const TOP_PAD  = 26;
  const BOT_PAD  = 26;
  const maxW = W - 2 * MARGIN_X;
  const maxH = H - TOP_PAD - BOT_PAD - 4;
  const w = Math.min(maxW, maxH * 2.5);
  const h = Math.min(maxH, w / 2.5);
  const x0 = (W - w) / 2;
  const y0 = layout.reservedY + TOP_PAD + (maxH - h) / 2;
  return { x0, y0, w, h };
}

function flatToScreen(phi, s) {
  const r = flatRect();
  const u = (phi - FLAT_PHI_MIN) / (FLAT_PHI_MAX - FLAT_PHI_MIN);
  const v = s;
  return [r.x0 + u * r.w, r.y0 + (1 - v) * r.h];
}

function screenToFlat(sx, sy) {
  const r = flatRect();
  return {
    phi: FLAT_PHI_MIN +
         (sx - r.x0) / r.w * (FLAT_PHI_MAX - FLAT_PHI_MIN),
    s:   1 - (sy - r.y0) / r.h,
  };
}

function _flatInsideRect(sx, sy) {
  const r = flatRect();
  return sx >= r.x0 && sx <= r.x0 + r.w &&
         sy >= r.y0 && sy <= r.y0 + r.h;
}

function drawFlatView() {
  const cw = window.innerWidth;
  ctx.fillStyle = "#060a10";
  ctx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = flatRect();

  ctx.save();
  const N_RINGS = 6;
  for (let k = 1; k < N_RINGS; k++) {
    const s = k / N_RINGS;
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, s);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, s);
    ctx.strokeStyle = "rgba(0, 229, 255, " + (0.03 + s * 0.09) + ")";
    ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(sx0, sy); ctx.lineTo(sx1, sy); ctx.stroke();
  }
  const N_MERID = 12;
  for (let k = 0; k <= N_MERID; k++) {
    const phi = FLAT_PHI_MIN +
                (FLAT_PHI_MAX - FLAT_PHI_MIN) * k / N_MERID;
    const [sx, sy0] = flatToScreen(phi, FLAT_S_MAX);
    const [,   sy1] = flatToScreen(phi, FLAT_S_MIN);
    ctx.strokeStyle = "rgba(0, 229, 255, 0.05)";
    ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(sx, sy0); ctx.lineTo(sx, sy1); ctx.stroke();
  }
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);
  {
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, 1);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, 1);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.55)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(sx0, sy); ctx.lineTo(sx1, sy); ctx.stroke();
  }
  {
    const [sxL, sy0] = flatToScreen(FLAT_PHI_MIN, FLAT_S_MAX);
    const [,   sy1]  = flatToScreen(FLAT_PHI_MIN, FLAT_S_MIN);
    const [sxR]      = flatToScreen(FLAT_PHI_MAX, 0);
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.30)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(sxL, sy0); ctx.lineTo(sxL, sy1);
    ctx.moveTo(sxR, sy0); ctx.lineTo(sxR, sy1);
    ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();

  ctx.save();
  ctx.font = "700 9px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.fillStyle = "#3d4756";
  ctx.textAlign = "left"; ctx.textBaseline = "top";
  ctx.fillText("UNFOLDED CONE  \u00B7  PARAMETER SPACE",
               r.x0, r.y0 - 16);
  ctx.textAlign = "right"; ctx.fillStyle = "#5a6774";
  ctx.fillText("\u03C6 = 0", r.x0 - 6, r.y0);
  ctx.textAlign = "left";
  ctx.fillText("\u03C6 = 2\u03C0", r.x0 + r.w + 6, r.y0);
  ctx.textAlign = "left"; ctx.textBaseline = "bottom";
  ctx.fillStyle = "#3d4756";
  ctx.fillText("s = 0  \u00B7  base", r.x0, r.y0 + r.h + 14);
  ctx.textBaseline = "top";
  ctx.fillStyle = "rgba(255, 43, 214, 0.75)";
  ctx.fillText("s = 1  \u00B7  apex", r.x0, r.y0 + 4);
  ctx.restore();

  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.w, r.h);
  ctx.clip();

  /* patches */
  for (let i = 0; i < quads.length; i++) {
    if (i === selectedQuad) continue;
    drawFlatQuad(quads[i], false);
  }
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    drawFlatQuad(quads[selectedQuad], true);
  }

  /* shapes — screen rotation, both bands agree */
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    const activeQ = quads[selectedQuad];

    for (let i = 0; i < floatSquares.length; i++) {
      const sq = floatSquares[i];
      if (sq.quadId !== activeQ.id) continue;

      const corners = squareFlatCorners(sq);
      if (!corners) continue;
      const phis = corners.map(c => c[0]);
      const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));

      const isSel = (i === selectedSquare);

      for (const shift of shifts) {
        const screen = squareFlatCornersScreen(sq, shift);
        if (!screen) continue;

        ctx.beginPath();
        ctx.moveTo(screen[0][0], screen[0][1]);
        for (let k = 1; k < 4; k++) {
          ctx.lineTo(screen[k][0], screen[k][1]);
        }
        ctx.closePath();
        ctx.fillStyle = isSel
          ? "rgba(120, 220, 255, 0.30)"
          : "rgba(120, 220, 255, 0.16)";
        ctx.fill();

        ctx.lineJoin = "round";
        ctx.strokeStyle = isSel
          ? "rgba(180, 240, 255, 1.00)"
          : "rgba(140, 225, 255, 0.78)";
        ctx.lineWidth = isSel ? 1.8 : 1.2;
        ctx.stroke();

        if (isSel) {
          for (const [hx, hy] of screen) {
            ctx.beginPath();
            ctx.arc(hx, hy, 4.2, 0, Math.PI * 2);
            ctx.fillStyle = "#b8ecff";
            ctx.fill();
            ctx.strokeStyle = "#0a0e14";
            ctx.lineWidth = 1.4;
            ctx.stroke();
          }
        }
      }

      if (isSel) {
        /* Draw the rotate handle on whichever copy the shape's
           primary position lands on. */
        const shift0 = 0;
        const rh = squareFlatRotateHandleScreen(sq, shift0);
        if (rh) {
          ctx.beginPath();
          ctx.arc(rh[0], rh[1], 6.0, 0, Math.PI * 2);
          ctx.fillStyle = "#b8ecff";
          ctx.fill();
          ctx.strokeStyle = "#0a0e14";
          ctx.lineWidth = 1.6;
          ctx.stroke();
          ctx.beginPath();
          ctx.arc(rh[0], rh[1], 3.0, Math.PI * 0.15, Math.PI * 1.85);
          ctx.strokeStyle = "#0a0e14";
          ctx.lineWidth = 1.2;
          ctx.stroke();
        }
      }
    }
  }

  ctx.restore();
}

function drawFlatQuad(q, selected) {
  const shifts = _phiCopies(q.phi0, q.phi1);
  for (const shift of shifts) {
    drawFlatQuadShifted(q, selected, shift);
  }
}

function drawFlatQuadShifted(q, selected, shift) {
  const pts = quadCorners(q).map(c =>
    flatToScreen(c.phi + shift, c.s));
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = selected
    ? "rgba(255, 200, 90, 0.10)"
    : "rgba(255, 200, 90, 0.04)";
  ctx.fill();
  ctx.lineJoin = "round";
  ctx.strokeStyle = selected
    ? "rgba(255, 220, 130, 0.60)"
    : "rgba(255, 200, 90, 0.28)";
  ctx.lineWidth = selected ? 1.5 : 1.0;
  ctx.stroke();
  if (!PATCH_SHAPE_EDIT_ENABLED) return;
  const rDot = selected ? 5.0 : 2.5;
  for (const [sx, sy] of pts) {
    ctx.beginPath(); ctx.arc(sx, sy, rDot, 0, Math.PI * 2);
    ctx.fillStyle = selected ? "#ffe680" : "rgba(255, 210, 100, 0.75)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.5; ctx.stroke();
  }
}

/* ---- Hit testing on the patch rectangle ------------------------ */

const FLAT_HANDLE_R = 10;
const FLAT_EDGE_R   = 6;

function _flatPtSegDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const l2 = dx * dx + dy * dy;
  if (l2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / l2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function _flatPtInQuad(px, py, c) {
  let inside = false;
  for (let i = 0, j = 3; i < 4; j = i++) {
    const xi = c[i][0], yi = c[i][1];
    const xj = c[j][0], yj = c[j][1];
    if (((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function flatHitTest(sx, sy) {
  if (!_flatInsideRect(sx, sy)) return null;
  const order = [];
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    order.push(selectedQuad);
  }
  for (let i = 0; i < quads.length; i++) {
    if (i !== selectedQuad) order.push(i);
  }
  for (const qi of order) {
    const q = quads[qi];
    const shifts = _phiCopies(q.phi0, q.phi1);
    for (const shift of shifts) {
      const corners = quadCorners(q).map(c =>
        flatToScreen(c.phi + shift, c.s));
      for (let ci = 0; ci < 4; ci++) {
        const [csx, csy] = corners[ci];
        if (Math.hypot(sx - csx, sy - csy) < FLAT_HANDLE_R) {
          return { kind: "corner", quadIdx: qi, cornerIdx: ci };
        }
      }
      const edges = [
        { name: "top",    a: corners[0], b: corners[1] },
        { name: "right",  a: corners[1], b: corners[2] },
        { name: "bottom", a: corners[2], b: corners[3] },
        { name: "left",   a: corners[3], b: corners[0] },
      ];
      let bestEdge = null, bestD = FLAT_EDGE_R;
      for (const e of edges) {
        const d = _flatPtSegDist(sx, sy, e.a[0], e.a[1], e.b[0], e.b[1]);
        if (d < bestD) { bestD = d; bestEdge = e.name; }
      }
      if (bestEdge) return { kind: "edge", quadIdx: qi, edge: bestEdge };
      if (_flatPtInQuad(sx, sy, corners)) {
        return { kind: "body", quadIdx: qi };
      }
    }
  }
  return null;
}

/* ---- Drag application -------------------------------------------
   Same shape-editing mutators as before, gated behind
   PATCH_SHAPE_EDIT_ENABLED. */

function flatMoveCorner(q, cornerIdx, phi, s) {
  if (cornerIdx === 0) {
    q.phi0 = Math.max(FLAT_PHI_MIN,
                      Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
    q.s1   = Math.min(FLAT_S_MAX,
                      Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 1) {
    q.phi1 = Math.min(FLAT_PHI_MAX,
                      Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
    q.s1   = Math.min(FLAT_S_MAX,
                      Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 2) {
    q.phi1 = Math.min(FLAT_PHI_MAX,
                      Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
    q.s0   = Math.max(FLAT_S_MIN,
                      Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 3) {
    q.phi0 = Math.max(FLAT_PHI_MIN,
                      Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
    q.s0   = Math.max(FLAT_S_MIN,
                      Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  }
}

function flatMoveEdge(q, edge, phi, s) {
  if (edge === "top") {
    q.s1 = Math.min(FLAT_S_MAX, Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (edge === "bottom") {
    q.s0 = Math.max(FLAT_S_MIN, Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  } else if (edge === "left") {
    q.phi0 = Math.max(FLAT_PHI_MIN,
                      Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
  } else if (edge === "right") {
    q.phi1 = Math.min(FLAT_PHI_MAX,
                      Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
  }
}

function flatMoveBody(q, dPhi, dS) {
  q.phi0 += dPhi;
  q.phi1 += dPhi;
  let ns0 = q.s0 + dS, ns1 = q.s1 + dS;
  if (ns0 < FLAT_S_MIN) { ns1 += FLAT_S_MIN - ns0; ns0 = FLAT_S_MIN; }
  if (ns1 > FLAT_S_MAX) { ns0 -= ns1 - FLAT_S_MAX; ns1 = FLAT_S_MAX; }
  if (ns0 < FLAT_S_MIN) ns0 = FLAT_S_MIN;
  q.s0 = ns0; q.s1 = ns1;
}
"""
