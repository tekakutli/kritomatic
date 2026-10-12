"""
pg_view_squares_edit.py — drag solvers and edit operations.

The cone-band hit test, the four cursor→local inverse solvers, the
corner-resize / rotate entry points, and the two quick-rotate
operations.  All the solvers invert the UN-WARPED projection; a
drag on a strongly warped square tracks the cursor approximately,
with a discrepancy that vanishes at warp = 0.

This file is edited when the drag mechanics or the inverse solvers
change.  See the ANCHORED RESIZE and INVERSION sections of
pg_view_squares.py.
"""

SQUARES_EDIT_JS = r"""
/* ==========================================================================
   HIT TESTING — cone band
   ========================================================================== */

const SQUARE_HANDLE_R   = 10;
const SQUARE_BODY_MIN_R = 8;

function _squareHitOrder() {
  const order = [];
  if (selectedSquare >= 0 && selectedSquare < floatSquares.length) {
    order.push(selectedSquare);
  }
  for (let i = 0; i < floatSquares.length; i++) {
    if (i !== selectedSquare) order.push(i);
  }
  return order;
}

function _squareHitTestOne(si, sx, sy) {
  const sq = floatSquares[si];
  if (!sq) return null;

  const pts = squareCornersScreen(sq);
  if (!pts || pts.length < 3) return null;

  const rh = squareRotateHandleScreen(sq);
  if (rh && Math.hypot(sx - rh[0], sy - rh[1]) < ROTATE_HANDLE_R) {
    return { kind: "rotate", squareIdx: si };
  }

  for (let ci = 0; ci < pts.length; ci++) {
    if (Math.hypot(sx - pts[ci][0], sy - pts[ci][1]) < SQUARE_HANDLE_R) {
      return { kind: "corner", squareIdx: si, cornerIdx: ci };
    }
  }

  if (_ptInQuadPx(sx, sy, pts)) return { kind: "body", squareIdx: si };

  let mx = 0, my = 0;
  for (const [x, y] of pts) { mx += x; my += y; }
  mx /= pts.length; my /= pts.length;
  if (Math.hypot(sx - mx, sy - my) < SQUARE_BODY_MIN_R) {
    return { kind: "body", squareIdx: si };
  }
  return null;
}

function squareHitTest(sx, sy) {
  for (const si of _squareHitOrder()) {
    const hit = _squareHitTestOne(si, sx, sy);
    if (hit) return hit;
  }
  return null;
}

function squareMirrorHitTest(sx, sy) {
  for (const si of _squareHitOrder()) {
    const sq = floatSquares[si];
    if (!sq) continue;
    let hit = null;
    _withSquareMirror(sq, () => {
      hit = _squareHitTestOne(si, sx, sy);
    });
    if (hit) return hit;
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
   INVERSE #1 — shape-relative taper, EXACT (quadratic)
   ==========================================================================
   Assumes the shape is FLAT on the patch plane and the patch is
   UN-WARPED.  A drag on a hinged or warped shape lands the corner
   at the un-hinged, un-warped (u, v) of the cursor. */

function cursorToLocalShapeRelativeExactWith(f, qi, vC, pC, vMax,
                                             sx, sy) {
  if (vMax < 1e-6) return screenToFrame(f, sx, sy);

  const A0 = pC + vC / vMax;
  const B0 = 1 / vMax;

  const [ax, ay] = w2s(cone.ax, cone.ay);
  const Sx = sx - ax;
  const Sy = sy - ay;

  const sc = view.scale;
  const sx0 = sc * f.Cx + view.tx;
  const sxu = sc * f.Ux;
  const sxv = sc * f.Vx;
  const sy0 = -sc * f.Cy + view.ty;
  const syu = -sc * f.Uy;
  const syv = -sc * f.Vy;

  const Dx0 = sx0 - ax;
  const Dxu = sxu;
  const Dxv = sxv;
  const Dy0 = sy0 - ay;
  const Dyu = syu;
  const Dyv = syv;

  const K  = Dyu * Sx - Dxu * Sy;
  const M0 = Dyu * Dx0 - Dxu * Dy0;
  const Mv = Dyu * Dxv - Dxu * Dyv;

  const aQuad = B0 * Mv;
  const bQuad = B0 * M0 - A0 * Mv;
  const cQuad = K - A0 * M0;

  let v = null;

  if (Math.abs(aQuad) < 1e-12) {
    if (Math.abs(bQuad) > 1e-12) v = -cQuad / bQuad;
  } else {
    const disc = bQuad * bQuad - 4 * aQuad * cQuad;
    if (disc >= 0) {
      const sq2 = Math.sqrt(disc);
      const r1 = (-bQuad - sq2) / (2 * aQuad);
      const r2 = (-bQuad + sq2) / (2 * aQuad);

      const valid = (r) => {
        if (!isFinite(r)) return false;
        if (r > vMax - 1e-3) return false;
        if (r < -50 * f.vLen) return false;
        const q = A0 - B0 * r;
        if (q <= 1e-6) return false;
        return true;
      };

      const r1ok = valid(r1);
      const r2ok = valid(r2);
      if (r1ok && r2ok) {
        v = Math.abs(r1 - vC) < Math.abs(r2 - vC) ? r1 : r2;
      } else if (r1ok) {
        v = r1;
      } else if (r2ok) {
        v = r2;
      }
    }
  }

  if (v === null || !isFinite(v)) return screenToFrame(f, sx, sy);

  const q = A0 - B0 * v;
  if (Math.abs(q) < 1e-9) return { u: 0, v };

  let u;
  if (Math.abs(Dxu) >= Math.abs(Dyu)) {
    if (Math.abs(Dxu) < 1e-9) return { u: 0, v };
    u = (Sx / q - Dx0 - Dxv * v) / Dxu;
  } else {
    if (Math.abs(Dyu) < 1e-9) return { u: 0, v };
    u = (Sy / q - Dy0 - Dyv * v) / Dyu;
  }
  return { u, v };
}

function cursorToLocalShapeRelativeExact(f, qi, sq, sx, sy) {
  const h = horizonLocal(qi);
  if (!h) return screenToFrame(f, sx, sy);
  const vMax = shapeVMax(sq);
  const vC = sq.v * f.vLen;
  const pC = shapePerspCentre(sq);
  return cursorToLocalShapeRelativeExactWith(f, qi, vC, pC, vMax, sx, sy);
}

/* ==========================================================================
   INVERSE #2 — full per-vertex quadratic (preserved)
   ========================================================================== */

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
   INVERSE #3 — shape-relative, iterative (preserved)
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
   SHARED EDIT OPERATIONS
   ========================================================================== */

function setShapeFromCornerLocal(sq, cornerIdx, cu, cv, depthScale,
                                 anchorU, anchorV) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q) return;
  const f = patchFrame(qi);
  if (!f) return;

  const dx = cu - sq.u * f.uLen;
  const dy = cv - sq.v * f.vLen;

  const { Ku, Kv } = _shapeAspectScales(f);
  const ox = dx / Ku;
  const oy = dy / Kv;

  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);

  const signs = [[-1, +1], [+1, +1], [+1, -1], [-1, -1]][cornerIdx];
  const sx = signs[0], sy = signs[1];

  const refHWmin = SHAPE_MIN_SCALE * SHAPE_REL_SIZE / 2;
  const refHHmin = SHAPE_MIN_SCALE * SHAPE_REL_SIZE / 2 * depthScale;

  if (anchorU !== undefined && anchorV !== undefined) {
    const midU = (anchorU + cu) / 2;
    const midV = (anchorV + cv) / 2;

    const pu = (cu - anchorU) / (2 * Ku);
    const pv = (cv - anchorV) / (2 * Kv);

    let refHW = pu * cosT + pv * sinT;
    let refHH = pv * cosT - pu * sinT;

    refHW = Math.max(Math.abs(refHW), refHWmin);
    refHH = Math.max(Math.abs(refHH), refHHmin);

    sq.scaleU = refHW / (SHAPE_REL_SIZE / 2);
    sq.scaleV = refHH / (SHAPE_REL_SIZE / 2 * depthScale);
    clampShapeScales(sq);

    const duR = sx * refHW * cosT - sy * refHH * sinT;
    const dvR = sx * refHW * sinT + sy * refHH * cosT;
    const uHat = (anchorU + duR * Ku) / f.uLen;
    const vHat = (anchorV + dvR * Kv) / f.vLen;
    sq.u = uHat;
    sq.v = clampCloneV(qi, vHat);
    return;
  }

  const ux = ox * cosT + oy * sinT;
  const uy = oy * cosT - ox * sinT;

  const refHW = Math.max(sx * ux, refHWmin);
  const refHH = Math.max(sy * uy, refHHmin);

  sq.scaleU = refHW / (SHAPE_REL_SIZE / 2);
  sq.scaleV = refHH / (SHAPE_REL_SIZE / 2 * depthScale);
  clampShapeScales(sq);
}

function setShapeRotationFromCursorLocal(sq, cu, cv, snap, startTheta) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;

  const { Ku, Kv } = _shapeAspectScales(f);
  const ox = (cu / f.uLen - sq.u) / Ku;
  const oy = (cv / f.vLen - sq.v) / Kv;

  if (Math.abs(ox) < 1e-9 && Math.abs(oy) < 1e-9) return;

  const thetaRaw = Math.atan2(-ox, oy);

  if (snap && typeof startTheta === "number") {
    let delta = thetaRaw - startTheta;
    while (delta >  Math.PI) delta -= 2 * Math.PI;
    while (delta < -Math.PI) delta += 2 * Math.PI;
    sq.theta = startTheta +
               Math.round(delta / ROTATE_JUMP_STEP) * ROTATE_JUMP_STEP;
    return;
  }

  sq.theta = thetaRaw;
}

/* ==========================================================================
   QUICK-ROTATE OPERATIONS
   ========================================================================== */

function alignShapeToAxis(sq) {
  const step = Math.PI / 4;
  const theta = sq.theta || 0;
  sq.theta = Math.round(theta / step) * step;
}

function alignSelectedShapeToAxis() {
  if (selectedSquare < 0 || selectedSquare >= floatSquares.length) {
    if (typeof flashStatus === "function") {
      flashStatus("No shape selected", "warn");
    }
    return false;
  }
  alignShapeToAxis(floatSquares[selectedSquare]);
  draw();
  return true;
}

/* ==========================================================================
   CONE-BAND EDIT WRAPPERS
   ========================================================================== */

function resizeSquareFromCorner(sq, cornerIdx, cursorWX, cursorWY,
                                 anchorU, anchorV) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  const [sx, sy] = w2s(cursorWX, cursorWY);
  const lc = cursorToLocalShapeRelativeExact(f, qi, sq, sx, sy);
  if (anchorU !== undefined && anchorV !== undefined) {
    setShapeFromCornerLocal(sq, cornerIdx, lc.u, lc.v,
                             effectiveConeDepth(), anchorU, anchorV);
  } else {
    setShapeFromCornerLocal(sq, cornerIdx, lc.u, lc.v,
                             effectiveConeDepth());
  }
}

function rotateSquareToCursor(sq, cursorWX, cursorWY, snap, startTheta) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  const [sx, sy] = w2s(cursorWX, cursorWY);
  const lc = cursorToLocalShapeRelativeExact(f, qi, sq, sx, sy);
  setShapeRotationFromCursorLocal(sq, lc.u, lc.v, snap, startTheta);
}
"""
