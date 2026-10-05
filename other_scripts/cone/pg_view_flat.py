"""
pg_view_flat.py — the parameter-space view of the cone's lateral
surface.

The cone's lateral surface is parameterised by (phi, s).  Drawing the
parameter rectangle flat gives the "2D view".  A patch — being
[phi0, phi1] × [s0, s1] in the model — shows up here as an actual
rectangle, and back on the cone as a curved trapezoid narrowing
toward the apex.

SQUARES IN THE FLAT VIEW
========================
A square lives on the patch's plane, not on the cone surface, so it
has no intrinsic (phi, s) footprint.  When a patch is active — its
row is selected in the panel, or one of its squares is — every
square of that patch is projected into the flat view by the linear
correspondence (u, v) → (phi, s) that keeps the patch's own corners
at the patch's own phi/s bounds:

    phi = phiC + (u / uLen) · (phi1 − phi0)
    s   = sC   + (v / vLen) · (s1   − s0)

Under this mapping the horizon falls at s = 1 — the flat view's
existing apex line — so a square shrinks to zero height exactly
where its cone-view twin shrinks to zero size.  The two views
agree about where "the horizon" is.

Squares are editable from this band: the selected one carries a
brighter stroke and four corner handles, and pg_dispatch routes a
click on a handle or the interior into flatResizeSquareFromCorner /
flatMoveSquareBody.

Interaction on this band:

    corner drag     edits the two bounds that corner touches
    edge drag       edits the one bound that edge is on
    body drag       translates all four bounds together

    plus, for a floating square:
    corner drag     resizes that square
    body drag       moves that square
"""


FLAT_VIEW_JS = r"""
/* ==========================================================================
   FLAT VIEW
   ========================================================================== */

const FLAT_PHI_MIN = 0;
const FLAT_PHI_MAX = 2 * Math.PI;
const FLAT_S_MIN   = 0;
const FLAT_S_MAX   = 1;

const QUAD_MIN_SIZE_PHI = 0.06;
const QUAD_MIN_SIZE_S   = 0.04;

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

function drawFlatView() {
  const cw = window.innerWidth;
  ctx.fillStyle = "#060a10";
  ctx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = flatRect();

  ctx.save();

  /* reference grid — rings */
  const N_RINGS = 6;
  for (let k = 1; k < N_RINGS; k++) {
    const s = k / N_RINGS;
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, s);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, s);
    ctx.strokeStyle = "rgba(0, 229, 255, " + (0.03 + s * 0.09) + ")";
    ctx.lineWidth = 0.7;
    ctx.beginPath(); ctx.moveTo(sx0, sy); ctx.lineTo(sx1, sy); ctx.stroke();
  }

  /* reference grid — meridians */
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

  /* frame */
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  /* apex line at s = 1 */
  {
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, 1);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, 1);
    ctx.strokeStyle = "rgba(255, 43, 214, 0.55)";
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(sx0, sy); ctx.lineTo(sx1, sy); ctx.stroke();
  }

  /* seam markers at phi = 0 and phi = 2π */
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

  /* captions */
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

  /* patches, unselected first, selected on top */
  for (let i = 0; i < quads.length; i++) {
    if (i === selectedQuad) continue;
    drawFlatQuad(quads[i], false);
  }
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    drawFlatQuad(quads[selectedQuad], true);
  }

  /* squares of the active patch, projected into parameter space
     ============================================================
     squareFlatRect maps a square's physical size and position on
     the patch's plane into the flat view's (phi, s).  Clipped to
     the parameter rectangle so a square near the phi seam does not
     draw off the sheet.

     Squares are editable from this band: the selected one carries
     a brighter stroke and four corner handles. */

  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    const activeQ = quads[selectedQuad];

    ctx.save();
    ctx.beginPath();
    ctx.rect(r.x0, r.y0, r.w, r.h);
    ctx.clip();

    for (let i = 0; i < floatSquares.length; i++) {
      const sq = floatSquares[i];
      if (sq.quadId !== activeQ.id) continue;
      const fr = squareFlatRect(sq);
      if (!fr) continue;

      const [sx0, syTop] = flatToScreen(fr.phi0, fr.s1);
      const [sx1, syBot] = flatToScreen(fr.phi1, fr.s0);
      const w = sx1 - sx0;
      const hgt = syBot - syTop;
      if (w <= 0.4 || hgt <= 0.4) continue;

      const isSel = (i === selectedSquare);

      ctx.fillStyle = isSel
        ? "rgba(120, 220, 255, 0.30)"
        : "rgba(120, 220, 255, 0.16)";
      ctx.fillRect(sx0, syTop, w, hgt);

      ctx.strokeStyle = isSel
        ? "rgba(180, 240, 255, 1.00)"
        : "rgba(140, 225, 255, 0.78)";
      ctx.lineWidth = isSel ? 1.8 : 1.2;
      ctx.strokeRect(sx0 + 0.5, syTop + 0.5, w - 1, hgt - 1);

      if (isSel) {
        const handles = [
          [sx0, syTop],
          [sx1, syTop],
          [sx1, syBot],
          [sx0, syBot],
        ];
        for (const [hx, hy] of handles) {
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
    ctx.restore();
  }
}

function drawFlatQuad(q, selected) {
  const pts = quadCorners(q).map(c => flatToScreen(c.phi, c.s));

  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = selected
    ? "rgba(255, 200, 90, 0.18)"
    : "rgba(255, 200, 90, 0.07)";
  ctx.fill();

  ctx.lineJoin = "round";
  ctx.strokeStyle = selected
    ? "rgba(255, 220, 130, 1.00)"
    : "rgba(255, 200, 90, 0.55)";
  ctx.lineWidth = selected ? 2.0 : 1.3;
  ctx.stroke();

  const rDot = selected ? 5.0 : 2.5;
  for (const [sx, sy] of pts) {
    ctx.beginPath(); ctx.arc(sx, sy, rDot, 0, Math.PI * 2);
    ctx.fillStyle = selected ? "#ffe680" : "rgba(255, 210, 100, 0.75)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.5; ctx.stroke();
  }
}

/* ---- Hit testing ------------------------------------------------ */

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
  const order = [];
  if (selectedQuad >= 0 && selectedQuad < quads.length) {
    order.push(selectedQuad);
  }
  for (let i = 0; i < quads.length; i++) {
    if (i !== selectedQuad) order.push(i);
  }

  for (const qi of order) {
    const corners = quadCorners(quads[qi]).map(c =>
      flatToScreen(c.phi, c.s));

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
  return null;
}

/* ---- Drag application ------------------------------------------- */

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
  let np0 = q.phi0 + dPhi, np1 = q.phi1 + dPhi;
  if (np0 < FLAT_PHI_MIN) { np1 += FLAT_PHI_MIN - np0; np0 = FLAT_PHI_MIN; }
  if (np1 > FLAT_PHI_MAX) { np0 -= np1 - FLAT_PHI_MAX; np1 = FLAT_PHI_MAX; }
  if (np0 < FLAT_PHI_MIN) np0 = FLAT_PHI_MIN;

  let ns0 = q.s0 + dS, ns1 = q.s1 + dS;
  if (ns0 < FLAT_S_MIN) { ns1 += FLAT_S_MIN - ns0; ns0 = FLAT_S_MIN; }
  if (ns1 > FLAT_S_MAX) { ns0 -= ns1 - FLAT_S_MAX; ns1 = FLAT_S_MAX; }
  if (ns0 < FLAT_S_MIN) ns0 = FLAT_S_MIN;

  q.phi0 = np0; q.phi1 = np1;
  q.s0 = ns0;   q.s1 = ns1;
}
"""
