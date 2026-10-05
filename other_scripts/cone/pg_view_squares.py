"""
pg_view_squares.py — rectangles on each patch's plane.

A patch defines a local 2D frame (origin C, axes U, V).  U and V are
unit length but generally not perpendicular.

THE MODEL
=========
A shape is a rectangle of independent width and height, rotated by an
angle within the patch's own (u, v) plane:

    { id, quadId, u, v, scaleU, scaleV, theta }

    u, v      centre in normalized patch coordinates
    scaleU    width,  as a fraction of the patch's U-midline length
    scaleV    height, as a fraction of the same U-midline length
    theta     rotation angle, radians, in the (u, v) plane

World-local dimensions:

    w = scaleU · uLen
    h = scaleV · uLen · SHAPE_DEPTH

SHAPE_DEPTH defaults to 1.0.  A shape with scaleU == scaleV is
therefore a true square in (u, v).

THE PERSPECTIVE — SHAPE-RELATIVE TAPER
======================================
The cone band is a perspective projection of the patch's plane.  A
point's apparent shrink toward the apex depends on its v̂ through the
horizon factor

    persp(v̂) = (v̂_A − v̂) / v̂_A

The FULLY PER-VERTEX model evaluates each corner's factor at that
corner's own v̂.  That is physically correct, and it produced the taper
a rectangle on a tilted plane would have — but it also meant that
rotating the shape in place changed the four factors, because
rotation moves corners to different world v̂.  A square rotated 90°
rendered as a different quadrilateral, not as a rotated square.

This module keeps the taper but makes it SHAPE-RELATIVE.  For a
shape whose centre is at local v_C and whose centre's factor is

    p_C = persp(v̂_C)

a corner at local v = v_C + dv gets the factor

    p(dv) = p_C − dv / vMax

with vMax = v̂_A · vLen.  This is the first-order Taylor expansion of
the true horizon factor around the shape's own centre, evaluated in
the shape's OWN local V — not in world v̂.  Consequences:

    • A shape still tapers: corners with positive dv (nearer the
      apex, in the shape's own frame) get a slightly smaller p,
      corners with negative dv slightly larger.  On a tilted plane
      this is what makes a rectangle look like it lies on the
      surface.

    • The taper is anchored to the shape and its own axes.
      Rotating the shape rotates the taper with it, so its screen
      footprint is a rigid rotation of its unrotated footprint, up
      to the second-order terms the linearization drops.

    • For a true square (w == h), the linearization is exact under
      rotation: the corner dv's are ±h/2 in the shape's own frame
      and they stay ±h/2 for every θ.

    • Two shapes at different v̂ still get different p_C, so the
      plane's perspective gradient across the patch is preserved — a
      shape nearer the base is drawn larger.

The projection is, for a point at local (u, v):

    screen = apex_screen + p(dv) · (w2s(world) − apex_screen)

INVERTING THE PROJECTION
========================
The map is nonlinear in (u, v) because p depends on v, but linear in
the shape's own frame once p is fixed.  The inverse used by the drag
handlers iterates:

    1.  start with v_guess = v_C (the shape's own centre's v);
    2.  p = p_C − (v_guess − v_C)/vMax;
    3.  unproject the cursor's screen point linearly with that p:
            unS = apex_screen + (screen − apex_screen) / p
    4.  v_new = screenToFrame(f, unS).v;
    5.  if |v_new − v_guess| is small, stop; otherwise v_guess = v_new
        and repeat from (2).

Convergence is fast: the linearization error is second-order in
dv/vMax, and dv is at most the shape's half-extent, so three or four
iterations put the residual under a pixel at any reasonable shape
size.

The full per-vertex quadratic solver used by earlier revisions is
kept as `cursorToLocalFullPersp` below.  It is not on the drag path,
but it is preserved because it implements a different and
independently useful inversion.

PHI WRAP
========
The flat-view editing functions read the cursor's phi, which is
unclamped in the flat view's coordinate system.  flatCursorToShapeLocal
projects the cursor's phi into the patch's reference frame via
_phiShiftForPatch (defined in pg_view_flat.py).
"""


FLOAT_SQUARES_JS = r"""
/* ==========================================================================
   STATE
   ========================================================================== */

const floatSquares = [];
let nextSquareId = 1;
let selectedSquare = -1;

const SHAPE_DEFAULT_SCALE = 0.75;
const SHAPE_MIN_SCALE     = 0.03;
const SHAPE_MAX_SCALE     = 3.00;

/* One depth multiplier, both views.  Default 1.0 makes a shape with
   scaleU == scaleV a true square in (u, v). */
let SHAPE_DEPTH = 1.00;

const WEDGE_BASE_REACH = 2.50;

const ROTATE_HANDLE_OFFSET = 0.25;
const ROTATE_HANDLE_R      = 10;

/* ==========================================================================
   PATCH FRAME
   ========================================================================== */

function patchFrame(quadIdx) {
  if (quadIdx < 0 || quadIdx >= quads.length) return null;
  const q = quads[quadIdx];
  const c = quadCorners(q).map(v => surfacePoint(v.phi, v.s));
  const C0 = c[0], C1 = c[1], C2 = c[2], C3 = c[3];

  const Cx = (C0[0] + C1[0] + C2[0] + C3[0]) / 4;
  const Cy = (C0[1] + C1[1] + C2[1] + C3[1]) / 4;

  let Ux = (C1[0] + C2[0]) / 2 - (C0[0] + C3[0]) / 2;
  let Uy = (C1[1] + C2[1]) / 2 - (C0[1] + C3[1]) / 2;

  let Vx = (C0[0] + C1[0]) / 2 - (C3[0] + C2[0]) / 2;
  let Vy = (C0[1] + C1[1]) / 2 - (C3[1] + C2[1]) / 2;

  const uLen = Math.hypot(Ux, Uy) || 1;
  const vLen = Math.hypot(Vx, Vy) || 1;

  Ux /= uLen; Uy /= uLen;
  Vx /= vLen; Vy /= vLen;

  const det = Ux * Vy - Uy * Vx;
  if (Math.abs(det) < 1e-9) return null;

  return { Cx, Cy, Ux, Uy, Vx, Vy, det, uLen, vLen };
}

function frameToWorld(f, u, v) {
  return [f.Cx + u * f.Ux + v * f.Vx,
          f.Cy + u * f.Uy + v * f.Vy];
}

function frameToScreen(f, u, v) {
  const [wx, wy] = frameToWorld(f, u, v);
  return w2s(wx, wy);
}

function screenToFrame(f, sx, sy) {
  const [wx, wy] = s2w(sx, sy);
  const dx = wx - f.Cx;
  const dy = wy - f.Cy;
  return {
    u: (dx * f.Vy - dy * f.Vx) / f.det,
    v: (f.Ux * dy - f.Uy * dx) / f.det,
  };
}

function quadIdxById(id) {
  for (let i = 0; i < quads.length; i++) {
    if (quads[i].id === id) return i;
  }
  return -1;
}

/* ==========================================================================
   PATCH CORNERS IN LOCAL FRAME — [TL, TR, BR, BL]
   ========================================================================== */

function patchCornersLocal(quadIdx) {
  if (quadIdx < 0 || quadIdx >= quads.length) return null;
  const f = patchFrame(quadIdx);
  if (!f) return null;
  const q = quads[quadIdx];
  const world = quadCorners(q).map(c => surfacePoint(c.phi, c.s));
  return world.map(([wx, wy]) => {
    const dx = wx - f.Cx;
    const dy = wy - f.Cy;
    return [
      (dx * f.Vy - dy * f.Vx) / f.det,
      (f.Ux * dy - f.Uy * dx) / f.det,
    ];
  });
}

/* ==========================================================================
   HORIZON
   ========================================================================== */

function horizonLocal(qi) {
  if (qi < 0 || qi >= quads.length) return null;
  const q = quads[qi];
  const ds = q.s1 - q.s0;
  if (Math.abs(ds) < 1e-9) return null;
  const sAvg = (q.s0 + q.s1) / 2;
  return { vHat: (1 - sAvg) / ds };
}

function perspAt(qi, vHat) {
  const h = horizonLocal(qi);
  if (!h || h.vHat <= 1e-6) return 1;
  const p = (h.vHat - vHat) / h.vHat;
  return Math.max(0, p);
}

/* ==========================================================================
   SHAPE PERSPECTIVE PARAMETERS
   ==========================================================================
   pC   the horizon factor at the shape's own centre
   vMax v̂_A · vLen — the scaling constant that converts a local dv
        into a change in perspective factor */

function shapePerspCentre(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return 1;
  return perspAt(qi, sq.v);
}

function shapeVMax(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return 1;
  const f = patchFrame(qi);
  if (!f) return 1;
  const h = horizonLocal(qi);
  if (!h) return 1;
  return h.vHat * f.vLen;
}

/* ==========================================================================
   CLAMP — v only
   ========================================================================== */

function clampCloneV(qi, vHat) {
  const h = horizonLocal(qi);
  if (!h) return vHat;
  const vMin = -WEDGE_BASE_REACH;
  const vMax = h.vHat;
  if (vMax <= vMin) return vMin;
  return Math.max(vMin, Math.min(vMax, vHat));
}

function setClonePosition(sq, uLocal, vLocal) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  sq.u = uLocal / f.uLen;
  sq.v = clampCloneV(qi, vLocal / f.vLen);
}

/* ==========================================================================
   LIFECYCLE
   ========================================================================== */

function addSquareAt(quadIdx, uLocal, vLocal) {
  if (quadIdx < 0 || quadIdx >= quads.length) return;
  const q = quads[quadIdx];
  const f = patchFrame(quadIdx);
  if (!f) return;
  const uHat = uLocal / f.uLen;
  const vHat = clampCloneV(quadIdx, vLocal / f.vLen);
  floatSquares.push({
    id: nextSquareId++,
    quadId: q.id,
    u: uHat, v: vHat,
    scaleU: SHAPE_DEFAULT_SCALE,
    scaleV: SHAPE_DEFAULT_SCALE,
    theta: 0,
  });
  selectedSquare = floatSquares.length - 1;
  syncQuadList();
  draw();
}

function addSquareAtCenter(quadIdx) { addSquareAt(quadIdx, 0, 0); }

function deleteSquare(idx) {
  if (idx < 0 || idx >= floatSquares.length) return;
  floatSquares.splice(idx, 1);
  if (selectedSquare === idx) selectedSquare = -1;
  else if (selectedSquare > idx) selectedSquare -= 1;
  syncQuadList();
  draw();
}

function deleteSquaresForQuad(quadId) {
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    if (floatSquares[i].quadId === quadId) {
      floatSquares.splice(i, 1);
      if (selectedSquare === i) selectedSquare = -1;
      else if (selectedSquare > i) selectedSquare -= 1;
    }
  }
}

function clampShapeScales(sq) {
  sq.scaleU = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sq.scaleU));
  sq.scaleV = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sq.scaleV));
}

/* ==========================================================================
   GEOMETRY
   ========================================================================== */

function squareDims(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return { w: 0, h: 0 };
  const f = patchFrame(qi);
  if (!f) return { w: 0, h: 0 };
  return { w: sq.scaleU * f.uLen,
           h: sq.scaleV * f.uLen * SHAPE_DEPTH };
}

function squareCenterLocal(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  return [sq.u * f.uLen, sq.v * f.vLen];
}

/* Rotated corners, in world-local (u, v). */
function squareCornersLocal(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;

  const uC = sq.u * f.uLen;
  const vC = sq.v * f.vLen;
  const dims = squareDims(sq);
  const hw = dims.w / 2;
  const hh = dims.h / 2;

  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);

  const raw = [
    [-hw, +hh], [+hw, +hh], [+hw, -hh], [-hw, -hh],
  ];
  return raw.map(([dx, dy]) => {
    const rx = dx * cosT - dy * sinT;
    const ry = dx * sinT + dy * cosT;
    return [uC + rx, vC + ry];
  });
}

/* Unrotated corners, in world-local (u, v).  Kept for reference and
   for callers that want the shape's base configuration. */
function squareCornersLocalUnrotated(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const uC = sq.u * f.uLen;
  const vC = sq.v * f.vLen;
  const dims = squareDims(sq);
  const hw = dims.w / 2;
  const hh = dims.h / 2;
  return [
    [uC - hw, vC + hh], [uC + hw, vC + hh],
    [uC + hw, vC - hh], [uC - hw, vC - hh],
  ];
}

function squareRotateHandleLocal(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const uC = sq.u * f.uLen;
  const vC = sq.v * f.vLen;
  const dims = squareDims(sq);
  const k = dims.h / 2 + ROTATE_HANDLE_OFFSET * f.uLen;
  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);
  return [uC + (-k * sinT), vC + (+k * cosT)];
}

/* ==========================================================================
   PROJECTION — shape-relative taper
   ========================================================================== */

function projectShapePoint(f, qi, uLocal, vLocal, vC, pC, vMax) {
  const [wx, wy] = frameToWorld(f, uLocal, vLocal);
  const [sx, sy] = w2s(wx, wy);
  const [ax, ay] = w2s(cone.ax, cone.ay);
  const dv = vLocal - vC;
  const p = pC - dv / vMax;
  return [ax + p * (sx - ax), ay + p * (sy - ay)];
}

function squareCornersScreen(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const local = squareCornersLocal(sq);
  if (!local) return null;
  const vC = sq.v * f.vLen;
  const pC = shapePerspCentre(sq);
  const vMax = shapeVMax(sq);
  return local.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax));
}

function squareRotateHandleScreen(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const hl = squareRotateHandleLocal(sq);
  if (!hl) return null;
  const vC = sq.v * f.vLen;
  const pC = shapePerspCentre(sq);
  const vMax = shapeVMax(sq);
  return projectShapePoint(f, qi, hl[0], hl[1], vC, pC, vMax);
}

/* ==========================================================================
   RENDERING
   ========================================================================== */

const HORIZON_REACH = 4000;

function drawHorizon(qi) {
  const f = patchFrame(qi);
  if (!f) return;
  const [ax, ay] = [cone.ax, cone.ay];
  const p1 = w2s(ax - HORIZON_REACH * f.Ux, ay - HORIZON_REACH * f.Uy);
  const p2 = w2s(ax + HORIZON_REACH * f.Ux, ay + HORIZON_REACH * f.Uy);
  ctx.save();
  ctx.strokeStyle = "rgba(120, 220, 255, 0.42)";
  ctx.lineWidth = 1.3;
  ctx.setLineDash([7, 6]);
  ctx.beginPath();
  ctx.moveTo(p1[0], p1[1]);
  ctx.lineTo(p2[0], p2[1]);
  ctx.stroke();
  ctx.setLineDash([]);
  const [axs, ays] = w2s(ax, ay);
  ctx.beginPath();
  ctx.arc(axs, ays, 7.5, 0, Math.PI * 2);
  ctx.strokeStyle = "rgba(120, 220, 255, 0.85)";
  ctx.lineWidth = 1.6;
  ctx.stroke();
  ctx.restore();
}

function drawBaseReach(qi) {
  const f = patchFrame(qi);
  if (!f) return;
  const vLocal = -WEDGE_BASE_REACH * f.vLen;
  const [px, py] = frameToWorld(f, 0, vLocal);
  const p1 = w2s(px - HORIZON_REACH * f.Ux, py - HORIZON_REACH * f.Uy);
  const p2 = w2s(px + HORIZON_REACH * f.Ux, py + HORIZON_REACH * f.Uy);
  ctx.save();
  ctx.strokeStyle = "rgba(120, 220, 255, 0.22)";
  ctx.lineWidth = 1.0;
  ctx.setLineDash([4, 6]);
  ctx.beginPath();
  ctx.moveTo(p1[0], p1[1]);
  ctx.lineTo(p2[0], p2[1]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

function drawFloatSquares() {
  for (let i = 0; i < floatSquares.length; i++) {
    if (i === selectedSquare) continue;
    drawFloatSquare(floatSquares[i], false);
  }
  if (selectedSquare >= 0 && selectedSquare < floatSquares.length) {
    drawFloatSquare(floatSquares[selectedSquare], true);
  }
}

function _drawRotateHandle(sx, sy) {
  ctx.beginPath();
  ctx.arc(sx, sy, 6.0, 0, Math.PI * 2);
  ctx.fillStyle = "#b8ecff";
  ctx.fill();
  ctx.strokeStyle = "#0a0e14";
  ctx.lineWidth = 1.6;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(sx, sy, 3.0, Math.PI * 0.15, Math.PI * 1.85);
  ctx.strokeStyle = "#0a0e14";
  ctx.lineWidth = 1.2;
  ctx.stroke();
}

function drawFloatSquare(sq, selected) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  if (selected) {
    drawHorizon(qi);
    drawBaseReach(qi);
  }
  const pts = squareCornersScreen(sq);
  if (!pts) return;
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < 4; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = selected
    ? "rgba(120, 220, 255, 0.30)"
    : "rgba(120, 220, 255, 0.12)";
  ctx.fill();
  ctx.lineJoin = "round";
  ctx.strokeStyle = selected
    ? "rgba(180, 240, 255, 1.00)"
    : "rgba(140, 225, 255, 0.72)";
  ctx.lineWidth = selected ? 2.0 : 1.3;
  ctx.stroke();
  if (!selected) return;
  for (const [sx, sy] of pts) {
    ctx.beginPath();
    ctx.arc(sx, sy, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = "#b8ecff";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }
  const rh = squareRotateHandleScreen(sq);
  if (rh) _drawRotateHandle(rh[0], rh[1]);
}

/* ==========================================================================
   HIT TESTING — cone band
   ========================================================================== */

const SQUARE_HANDLE_R   = 10;
const SQUARE_BODY_MIN_R = 8;

function _ptInQuadPx(px, py, poly) {
  let inside = false;
  for (let i = 0, j = 3; i < 4; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function squareHitTest(sx, sy) {
  const order = [];
  if (selectedSquare >= 0 && selectedSquare < floatSquares.length) {
    order.push(selectedSquare);
  }
  for (let i = 0; i < floatSquares.length; i++) {
    if (i !== selectedSquare) order.push(i);
  }
  for (const si of order) {
    const sq = floatSquares[si];
    const pts = squareCornersScreen(sq);
    if (!pts) continue;
    const rh = squareRotateHandleScreen(sq);
    if (rh && Math.hypot(sx - rh[0], sy - rh[1]) < ROTATE_HANDLE_R) {
      return { kind: "rotate", squareIdx: si };
    }
    for (let ci = 0; ci < 4; ci++) {
      if (Math.hypot(sx - pts[ci][0], sy - pts[ci][1]) < SQUARE_HANDLE_R) {
        return { kind: "corner", squareIdx: si, cornerIdx: ci };
      }
    }
    if (_ptInQuadPx(sx, sy, pts)) return { kind: "body", squareIdx: si };
    const mx = (pts[0][0] + pts[1][0] + pts[2][0] + pts[3][0]) / 4;
    const my = (pts[0][1] + pts[1][1] + pts[2][1] + pts[3][1]) / 4;
    if (Math.hypot(sx - mx, sy - my) < SQUARE_BODY_MIN_R) {
      return { kind: "body", squareIdx: si };
    }
  }
  return null;
}

function findPatchAtScreen(sx, sy) {
  for (let qi = quads.length - 1; qi >= 0; qi--) {
    const q = quads[qi];
    const corners = quadCorners(q).map(c => {
      const [wx, wy] = surfacePoint(c.phi, c.s);
      return w2s(wx, wy);
    });
    if (_ptInQuadPx(sx, sy, corners)) return qi;
  }
  return -1;
}

/* ==========================================================================
   INVERSE — iterative, shape-relative
   ========================================================================== */

function cursorToLocalWithPersp(f, qi, sq, sx, sy) {
  const pC = shapePerspCentre(sq);
  const vMax = shapeVMax(sq);
  const vC = sq.v * f.vLen;
  const [ax, ay] = w2s(cone.ax, cone.ay);

  let uCur = sq.u * f.uLen;
  let vCur = vC;

  for (let iter = 0; iter < 10; iter++) {
    const dv = vCur - vC;
    const p = pC - dv / vMax;
    const pAbs = Math.max(Math.abs(p), 1e-9);
    const invP = (p >= 0 ? 1 : -1) / pAbs;
    const unSx = ax + (sx - ax) * invP;
    const unSy = ay + (sy - ay) * invP;
    const lc = screenToFrame(f, unSx, unSy);
    const du = Math.abs(lc.u - uCur);
    const dvv = Math.abs(lc.v - vCur);
    uCur = lc.u;
    vCur = lc.v;
    if (du < 1e-4 && dvv < 1e-4) break;
  }
  return { u: uCur, v: vCur };
}

/* ==========================================================================
   INVERSE — full per-vertex quadratic (preserved)
   ==========================================================================
   Inverts the PER-VERTEX projection, where each corner's factor is
   evaluated at that corner's own world v̂.  Not on the drag path any
   more; kept because it implements a different and independently
   useful inversion. */

function cursorToLocalFullPersp(f, qi, sx, sy) {
  const h = horizonLocal(qi);
  if (!h) return screenToFrame(f, sx, sy);
  const vMax = h.vHat * f.vLen;
  if (vMax < 1e-6) return screenToFrame(f, sx, sy);

  const [ax, ay] = w2s(cone.ax, cone.ay);
  const SX = sx - ax;
  const SY = sy - ay;

  const sc = view.scale;
  const A =  sc * (f.Cx - cone.ax);
  const B =  sc * f.Ux;
  const C =  sc * f.Vx;
  const D = -sc * (f.Cy - cone.ay);
  const E = -sc * f.Uy;
  const F = -sc * f.Vy;

  const P = E * SX - B * SY;
  const Q = E * A  - B * D;
  const R = E * C  - B * F;

  const a = -R / vMax;
  const b = R - Q / vMax;
  const c = Q - P;

  let v = null;
  if (Math.abs(a) < 1e-12) {
    if (Math.abs(b) > 1e-12) v = -c / b;
  } else {
    const disc = b * b - 4 * a * c;
    if (disc >= 0) {
      const sq = Math.sqrt(disc);
      const r1 = (-b - sq) / (2 * a);
      const r2 = (-b + sq) / (2 * a);
      const ok = (r) => isFinite(r)
                     && r < vMax - 1e-3
                     && r > -50 * f.vLen;
      const r1ok = ok(r1), r2ok = ok(r2);
      if (r1ok && r2ok) v = Math.abs(r1) < Math.abs(r2) ? r1 : r2;
      else if (r1ok) v = r1;
      else if (r2ok) v = r2;
    }
  }

  if (v === null || !isFinite(v)) return screenToFrame(f, sx, sy);

  const pEff = 1 - v / vMax;
  if (Math.abs(pEff) < 1e-9) return { u: 0, v };

  let u;
  if (Math.abs(B) >= Math.abs(E)) {
    if (Math.abs(B) < 1e-9) return { u: 0, v };
    u = (SX / pEff - A - C * v) / B;
  } else {
    u = (SY / pEff - D - F * v) / E;
  }
  return { u, v };
}

/* ==========================================================================
   SHARED EDIT OPERATIONS
   ========================================================================== */

function setShapeFromCornerLocal(sq, cornerIdx, cu, cv) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  const c = squareCenterLocal(sq);
  if (!c) return;
  const [uC, vC] = c;
  const ox = cu - uC;
  const oy = cv - vC;
  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);
  const ux = ox * cosT + oy * sinT;
  const uy = -ox * sinT + oy * cosT;
  const signs = [[-1, +1], [+1, +1], [+1, -1], [-1, -1]][cornerIdx];
  const sx = signs[0], sy = signs[1];
  const wMin = SHAPE_MIN_SCALE * f.uLen;
  const hMin = SHAPE_MIN_SCALE * f.uLen * SHAPE_DEPTH;
  const w = Math.max(2 * sx * ux, wMin);
  const h = Math.max(2 * sy * uy, hMin);
  sq.scaleU = w / f.uLen;
  sq.scaleV = h / (f.uLen * SHAPE_DEPTH);
  clampShapeScales(sq);
}

function setShapeRotationFromCursorLocal(sq, cu, cv, snap) {
  const c = squareCenterLocal(sq);
  if (!c) return;
  const ox = cu - c[0];
  const oy = cv - c[1];
  if (Math.abs(ox) < 1e-6 && Math.abs(oy) < 1e-6) return;
  let theta = Math.atan2(-ox, oy);
  if (snap) {
    const step = Math.PI / 12;
    theta = Math.round(theta / step) * step;
  }
  sq.theta = theta;
}

/* ==========================================================================
   CONE-BAND EDIT WRAPPERS
   ========================================================================== */

function resizeSquareFromCorner(sq, cornerIdx, cursorWX, cursorWY) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  const [sx, sy] = w2s(cursorWX, cursorWY);
  const lc = cursorToLocalWithPersp(f, qi, sq, sx, sy);
  setShapeFromCornerLocal(sq, cornerIdx, lc.u, lc.v);
}

function rotateSquareToCursor(sq, cursorWX, cursorWY, snap) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  const [sx, sy] = w2s(cursorWX, cursorWY);
  const lc = cursorToLocalWithPersp(f, qi, sq, sx, sy);
  setShapeRotationFromCursorLocal(sq, lc.u, lc.v, snap);
}

/* ==========================================================================
   FLAT-VIEW PROJECTION
   ==========================================================================
   Same squareCornersLocal array, mapped through the linear
   (u, v) → (phi, s) correspondence.  The flat band has no
   perspective; the mapping is the same linear map the patch
   rectangle uses. */

function squareFlatCorners(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return null;
  const locals = squareCornersLocal(sq);
  if (!locals) return null;
  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;
  return locals.map(([u, v]) => [
    phiC + (u / f.uLen) * dPhi,
    sC   + (v / f.vLen) * dS,
  ]);
}

function squareFlatCornersScreen(sq, shift) {
  const corners = squareFlatCorners(sq);
  if (!corners) return null;
  return corners.map(([phi, s]) => flatToScreen(phi + shift, s));
}

function squareFlatRotateHandleScreen(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return null;
  const hl = squareRotateHandleLocal(sq);
  if (!hl) return null;
  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;
  const phi = phiC + (hl[0] / f.uLen) * dPhi;
  const s   = sC   + (hl[1] / f.vLen) * dS;
  return flatToScreen(phi, s);
}

/* ==========================================================================
   FLAT-VIEW EDIT WRAPPERS
   ========================================================================== */

function flatCursorToShapeLocal(sq, sx, sy) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return null;
  const { phi, s } = screenToFlat(sx, sy);
  const phiAdj = phi + _phiShiftForPatch(phi, q.phi0, q.phi1);
  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;
  if (Math.abs(dPhi) < 1e-9 || Math.abs(dS) < 1e-9) return null;
  const uHat = (phiAdj - phiC) / dPhi;
  const vHat = (s      - sC)   / dS;
  return { u: uHat * f.uLen, v: vHat * f.vLen };
}

function flatResizeSquareFromCornerIdx(sq, sx, sy, cornerIdx) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  setShapeFromCornerLocal(sq, cornerIdx, lc.u, lc.v);
}

function flatRotateSquareToCursor(sq, sx, sy, snap) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  setShapeRotationFromCursorLocal(sq, lc.u, lc.v, snap);
}

function flatMoveSquareBody(sq, sx, sy) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  setClonePosition(sq, lc.u, lc.v);
}

/* ==========================================================================
   FLAT-VIEW HIT TEST
   ========================================================================== */

function squareFlatHitTest(sx, sy) {
  if (selectedQuad < 0 || selectedQuad >= quads.length) return null;
  const activeQ = quads[selectedQuad];
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    const sq = floatSquares[i];
    if (sq.quadId !== activeQ.id) continue;
    const rh = squareFlatRotateHandleScreen(sq);
    if (rh && Math.hypot(sx - rh[0], sy - rh[1]) < ROTATE_HANDLE_R) {
      return { kind: "rotate", squareIdx: i };
    }
    const corners = squareFlatCorners(sq);
    if (!corners) continue;
    const phis = corners.map(c => c[0]);
    const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));
    for (const shift of shifts) {
      const screen = corners.map(([phi, s]) =>
        flatToScreen(phi + shift, s));
      for (let ci = 0; ci < 4; ci++) {
        if (Math.hypot(sx - screen[ci][0], sy - screen[ci][1])
            < FLAT_HANDLE_R) {
          return { kind: "corner", squareIdx: i, cornerIdx: ci };
        }
      }
      if (_ptInQuadPx(sx, sy, screen)) {
        return { kind: "body", squareIdx: i };
      }
    }
  }
  return null;
}
"""
