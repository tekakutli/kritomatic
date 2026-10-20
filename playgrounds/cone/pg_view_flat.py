"""
pg_view_flat.py — the parameter-space view of the cone's lateral
surface.

ES module.  Renders the unfolded-cone sheet: static chrome (grid,
frame, captions, cardinal markers) blitted from a cache, plus the
dynamic content (patches, floating squares, labels).

STAGE 1 OPTIMIZATION (preserved)
================================
The static chrome is drawn once per key change (resize, half-angle,
depth) into an offscreen canvas and blitted per frame.

IMPORT SOURCE NOTES
===================
`squareFlatCorners` is a geometry primitive from `pg_view_squares_core`,
not from `pg_view_squares_flat` (the latter is the label / hit-test
renderer).  `PATCH_SHAPE_EDIT_ENABLED` is a feature flag from
`pg_state`, not from `pg_view_squares_core`.  Both were miscategorised
in an earlier revision and blocked module load until fixed.
"""

FLAT_VIEW_JS = r"""
import {
  ctx, env, view, layout, cone, quads, floatSquares, coneState,
  coneR, quadCorners, quadIdxById, PATCH_SHAPE_EDIT_ENABLED,
} from "./pg_state.js";
import {
  flatRect, flatToScreen, _flatInsideRect,
  _phiCopies, FLAT_PHI_MIN, FLAT_PHI_MAX, FLAT_S_MIN, FLAT_S_MAX,
} from "./pg_flat_math.js";
import {
  patchHue, _huergb, _huergbLight, _withSquareMirror,
  squareFlatCorners,
  FLAT_HANDLE_R, FLAT_EDGE_R,
} from "./pg_view_squares_core.js";
import {
  squareFlatRotateHandleScreen, drawShapeLabelsFlat,
} from "./pg_view_squares_flat.js";
import { squarePlaneIntersectionFlat } from "./pg_view_squares_proj.js";

/* ==========================================================================
   CHROME CACHE
   ========================================================================== */

let _flatChromeCache = null;

function _flatChromeKey(cw, ch) {
  const r = flatRect();
  return [
    cw, ch, env.dpr,
    layout.reservedY, layout.reservedH,
    r.x0, r.y0, r.w, r.h,
    coneR(),
  ].join("|");
}

function _buildFlatChrome(cw, ch) {
  const off = document.createElement("canvas");
  off.width  = Math.round(cw * env.dpr);
  off.height = Math.round(ch * env.dpr);
  const octx = off.getContext("2d");
  octx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);

  octx.fillStyle = "#060a10";
  octx.fillRect(0, layout.reservedY, cw, layout.reservedH);

  const r = flatRect();

  octx.save();
  const N_RINGS = 6;
  for (let k = 1; k < N_RINGS; k++) {
    const s = k / N_RINGS;
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, s);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, s);
    octx.strokeStyle = "rgba(0, 229, 255, " + (0.03 + s * 0.09) + ")";
    octx.lineWidth = 0.7;
    octx.beginPath(); octx.moveTo(sx0, sy); octx.lineTo(sx1, sy); octx.stroke();
  }

  const N_MERID = 12;
  for (let k = 0; k <= N_MERID; k++) {
    const phi = FLAT_PHI_MIN +
                (FLAT_PHI_MAX - FLAT_PHI_MIN) * k / N_MERID;
    const [sx, sy0] = flatToScreen(phi, FLAT_S_MAX);
    const [,   sy1] = flatToScreen(phi, FLAT_S_MIN);
    octx.strokeStyle = "rgba(0, 229, 255, 0.05)";
    octx.lineWidth = 0.7;
    octx.beginPath(); octx.moveTo(sx, sy0); octx.lineTo(sx, sy1); octx.stroke();
  }

  octx.strokeStyle = "#1e2836";
  octx.lineWidth = 1;
  octx.strokeRect(r.x0 + 0.5, r.y0 + 0.5, r.w - 1, r.h - 1);

  {
    const [sx0, sy] = flatToScreen(FLAT_PHI_MIN, 1);
    const [sx1]     = flatToScreen(FLAT_PHI_MAX, 1);
    octx.strokeStyle = "rgba(255, 43, 214, 0.55)";
    octx.lineWidth = 2;
    octx.beginPath(); octx.moveTo(sx0, sy); octx.lineTo(sx1, sy); octx.stroke();
  }

  {
    const [sxL, sy0] = flatToScreen(FLAT_PHI_MIN, FLAT_S_MAX);
    const [,   sy1]  = flatToScreen(FLAT_PHI_MIN, FLAT_S_MIN);
    const [sxR]      = flatToScreen(FLAT_PHI_MAX, 0);
    octx.setLineDash([4, 4]);
    octx.strokeStyle = "rgba(255, 43, 214, 0.30)";
    octx.lineWidth = 1;
    octx.beginPath();
    octx.moveTo(sxL, sy0); octx.lineTo(sxL, sy1);
    octx.moveTo(sxR, sy0); octx.lineTo(sxR, sy1);
    octx.stroke();
    octx.setLineDash([]);
  }
  octx.restore();

  octx.save();
  octx.font = "700 9px 'JetBrains Mono', 'Fira Code', monospace";

  octx.fillStyle = "#3d4756";
  octx.textAlign = "left"; octx.textBaseline = "top";
  octx.fillText("UNFOLDED CONE  \u00B7  PARAMETER SPACE",
               r.x0, r.y0 - 16);

  octx.textAlign = "right"; octx.fillStyle = "#5a6774";
  octx.fillText("\u03C6 = 0", r.x0 - 6, r.y0);
  octx.textAlign = "left";
  octx.fillText("\u03C6 = 2\u03C0", r.x0 + r.w + 6, r.y0);

  octx.textAlign = "left"; octx.textBaseline = "bottom";
  octx.fillStyle = "#3d4756";
  octx.fillText("s = 0  \u00B7  base", r.x0, r.y0 + r.h + 14);

  octx.textBaseline = "top";
  octx.fillStyle = "rgba(255, 43, 214, 0.75)";
  octx.fillText("s = 1  \u00B7  apex", r.x0, r.y0 + 4);
  octx.restore();

  const yBase = r.y0 + r.h;
  const R_PIN = 9;
  const yPin  = yBase - R_PIN - 3;
  const cards = [
    { key: "E", phi: 0               },
    { key: "N", phi: Math.PI / 2     },
    { key: "W", phi: Math.PI         },
    { key: "S", phi: 3 * Math.PI / 2 },
  ];
  octx.save();
  octx.font = "700 11px 'JetBrains Mono', 'Fira Code', monospace";
  octx.textAlign = "center";
  octx.textBaseline = "middle";
  for (const c of cards) {
    const [sx] = flatToScreen(c.phi, 0);

    octx.beginPath();
    octx.moveTo(sx, yBase);
    octx.lineTo(sx, yBase - 4);
    octx.strokeStyle = "rgba(255, 200, 90, 0.35)";
    octx.lineWidth = 1.0;
    octx.stroke();

    octx.beginPath();
    octx.arc(sx, yPin, R_PIN, 0, Math.PI * 2);
    octx.fillStyle = "rgba(11, 16, 24, 0.88)";
    octx.fill();
    octx.strokeStyle = "rgba(255, 200, 90, 0.85)";
    octx.lineWidth = 1.3;
    octx.stroke();

    octx.fillStyle = "rgba(255, 200, 90, 0.95)";
    octx.fillText(c.key, sx, yPin + 0.5);
  }
  octx.restore();

  return off;
}

function _flatChrome(cw, ch) {
  const key = _flatChromeKey(cw, ch);
  if (_flatChromeCache && _flatChromeCache.key === key) {
    return _flatChromeCache.canvas;
  }
  const canvas = _buildFlatChrome(cw, ch);
  _flatChromeCache = { key, canvas };
  return canvas;
}

/* ==========================================================================
   FLAT VIEW — DRAW
   ========================================================================== */

export function drawFlatView() {
  const cw = window.innerWidth;
  ctx.drawImage(_flatChrome(cw, window.innerHeight), 0, 0, cw, window.innerHeight);

  const r = flatRect();

  ctx.save();
  ctx.beginPath();
  ctx.rect(r.x0, r.y0, r.w, r.h);
  ctx.clip();

  for (let i = 0; i < quads.length; i++) {
    if (i === coneState.selectedQuad) continue;
    drawFlatQuad(quads[i], false);
  }
  if (coneState.selectedQuad >= 0 &&
      coneState.selectedQuad < quads.length) {
    drawFlatQuad(quads[coneState.selectedQuad], true);
  }

  if (coneState.selectedQuad >= 0 &&
      coneState.selectedQuad < quads.length) {
    const activeQ = quads[coneState.selectedQuad];
    for (let i = 0; i < floatSquares.length; i++) {
      const sq = floatSquares[i];
      if (sq.quadId !== activeQ.id) continue;
      const isSel = (i === coneState.selectedSquare);
      _drawFlatSquareOne(sq, isSel, false);
      if (activeQ.mirror) {
        _withSquareMirror(sq, () => _drawFlatSquareOne(sq, false, true));
      }
    }
  }

  drawShapeLabelsFlat();

  ctx.restore();
}

function drawFlatQuad(q, selected) {
  const shifts = _phiCopies(q.phi0, q.phi1);
  for (const shift of shifts) {
    drawFlatQuadShifted(q, selected, shift);
  }
}

function drawFlatQuadShifted(q, selected, shift) {
  const hue = patchHue(q);
  const pts = [
    { phi: q.phi0, s: q.s1 }, { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 }, { phi: q.phi0, s: q.s0 },
  ].map(c => flatToScreen(c.phi + shift, c.s));

  ctx.save();

  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = _huergb(hue, selected ? 0.07 : 0.03);
  ctx.fill();

  ctx.lineJoin = "round";
  ctx.setLineDash([6, 4]);
  ctx.strokeStyle = _huergb(hue, selected ? 0.75 : 0.42);
  ctx.lineWidth = selected ? 1.6 : 1.1;
  ctx.stroke();
  ctx.setLineDash([]);

  if (PATCH_SHAPE_EDIT_ENABLED) {
    const rDot = selected ? 5.0 : 2.8;
    for (const [sx, sy] of pts) {
      ctx.beginPath(); ctx.arc(sx, sy, rDot, 0, Math.PI * 2);
      ctx.fillStyle = selected ? _huergbLight(hue) : _huergb(hue, 0.75);
      ctx.fill();
      ctx.strokeStyle = "#0a0e14"; ctx.lineWidth = 1.4; ctx.stroke();
    }
  }

  ctx.restore();
}

function _drawFlatSquareOne(sq, isSel, isMirror) {
  const corners = squareFlatCorners(sq);
  if (!corners) return;

  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q) return;
  const hue = patchHue(q);

  const phis = corners.map(c => c[0]);
  const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));

  const fillAlpha   = isMirror ? 0.06 : (isSel ? 0.30 : 0.16);
  const strokeAlpha = isMirror ? 0.55 : (isSel ? 1.00 : 0.78);
  const lineW       = isMirror ? 1.0  : (isSel ? 1.8  : 1.2);

  for (const shift of shifts) {
    const screen = corners.map(([phi, s]) =>
      flatToScreen(phi + shift, s));

    ctx.beginPath();
    ctx.moveTo(screen[0][0], screen[0][1]);
    for (let k = 1; k < 4; k++) {
      ctx.lineTo(screen[k][0], screen[k][1]);
    }
    ctx.closePath();
    ctx.fillStyle = _huergb(hue, fillAlpha);
    ctx.fill();

    ctx.lineJoin = "round";
    ctx.strokeStyle = _huergb(hue, strokeAlpha);
    ctx.lineWidth = lineW;
    ctx.stroke();

    if (sq.slope && Math.abs(sq.slope) > 1e-6) {
      const isect = squarePlaneIntersectionFlat(sq);
      if (isect) {
        const [p0, p1] = isect.map(([phi, s]) =>
          flatToScreen(phi + shift, s));
        ctx.save();
        ctx.setLineDash([4, 3]);
        ctx.strokeStyle = _huergb(hue,
          isMirror ? 0.32 : (isSel ? 0.65 : 0.42));
        ctx.lineWidth   = 1.0;
        ctx.beginPath();
        ctx.moveTo(p0[0], p0[1]);
        ctx.lineTo(p1[0], p1[1]);
        ctx.stroke();
        ctx.restore();
      }
    }

    if (!isSel || isMirror) continue;

    for (const [hx, hy] of screen) {
      ctx.beginPath();
      ctx.arc(hx, hy, 4.2, 0, Math.PI * 2);
      ctx.fillStyle = _huergbLight(hue);
      ctx.fill();
      ctx.strokeStyle = "#0a0e14";
      ctx.lineWidth = 1.4;
      ctx.stroke();
    }

    const rh = squareFlatRotateHandleScreen(sq, shift);
    if (rh) {
      let topIdx = 0;
      for (let k = 1; k < screen.length; k++) {
        if (screen[k][1] < screen[topIdx][1]) topIdx = k;
      }
      ctx.beginPath();
      ctx.moveTo(screen[topIdx][0], screen[topIdx][1]);
      ctx.lineTo(rh[0], rh[1]);
      ctx.strokeStyle = _huergb(hue, 0.45);
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.beginPath();
      ctx.arc(rh[0], rh[1], 6.0, 0, Math.PI * 2);
      ctx.fillStyle = _huergbLight(hue);
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

/* ==========================================================================
   HIT TESTING
   ========================================================================== */

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
  const n = c.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = c[i][0], yi = c[i][1];
    const xj = c[j][0], yj = c[j][1];
    if (((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

export function flatHitTest(sx, sy) {
  if (!_flatInsideRect(sx, sy)) return null;

  const order = [];
  if (coneState.selectedQuad >= 0 &&
      coneState.selectedQuad < quads.length) {
    order.push(coneState.selectedQuad);
  }
  for (let i = 0; i < quads.length; i++) {
    if (i !== coneState.selectedQuad) order.push(i);
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
"""
