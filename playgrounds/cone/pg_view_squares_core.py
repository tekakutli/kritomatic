"""
pg_view_squares_core.py — model, frame, geometry, lifecycle.

ES module.  Shape model + geometry + hit-test helpers.  Everything
here except a few cache singletons is stateless — the state lives
in pg_state.

IMPORTS FROM pg_state
=====================
    quads, floatSquares, coneState, SHAPE_STATE
    quadIdxById, horizonLocal, clampCloneV, _reclampShapesOnPatch
    _TAU, cone

IMPORTS FROM pg_flat_math
=========================
    flatRect, FLAT_S_MIN, FLAT_S_MAX

CALLED BY
=========
    pg_view_squares_warp / proj / edit / flat
    pg_view_cone / pg_view_flat
    pg_view_squares_flat (squareFlatCorners used by pg_core fit)
    pg_core._fitPatchAndShape_shapeFirst
    pg_panel_fields (squareWorldWidth, squareFlatCenter)
    pg_panel_scrubs (_patchPhiCenter, squareWorldWidth/Height,
                     squareFlatCenter, SHAPE_SLOPE_MIN/MAX,
                     SHAPE_STANDING_HEIGHT_MULT via SHAPE_STATE)
    pg_kra (many)
    pg_export (many)

PATCH-FRAME CACHE — WHY IT KEYS ON CONE STATE
=============================================
`patchFrame` computes a patch's world-space basis (`Cx, Cy, Ux, Uy,
Vx, Vy, uLen, vLen, det`) from the patch's (phi0, phi1, s0, s1) AND
from four cone parameters: `cone.ax`, `cone.ay`, `cone.depth`,
`cone.halfAngle`.  The frame is the reference the whole square
pipeline reads — `squareCornersLocal`, `squareCornersScreen`,
`squareFlatCorners`, `squareSetFlatCenter`, `_shapeStandingScale`,
`frameToWorld` / `screenToFrame`.  Every one of those depends on the
frame.

The cache hit check must therefore compare against all six inputs.
Historically it only compared `(phi0, phi1, s0, s1)` — the patch
values, not the cone values.  That was wrong: dragging the apex,
zooming (depth), or moving the half-angle slider all changed the
cone without touching the patch's own parameters, so the cache
returned a frame frozen at whatever cone state had been current on
the last miss.  The squares inside the patch therefore did not
re-project when the cone changed, even though the patches themselves
(which bypass the frame and read `surfacePoint` directly) did.

The visible symptom was that zooming the cone left the squares
sitting at the depth-15 screen position while the cone shrank around
them; and, before the apex dual-intent fix, that the squares
appeared to "follow" the apex during a clamp — actually the anchor
in `projectShapePoint` (`w2s(cone.ax, cone.ay)`) was moving while
the frame's target stayed frozen, so the perspective interpolation
squeezed the squares toward the anchor.

Including the four cone values in the check makes the frame
invalidated exactly when it should be — on apex drag, on depth
change, on half-angle change — and left valid on square drag, patch
drag, selection change, and every other mutation that does not alter
the cone's geometry.
"""

SQUARES_CORE_JS = r"""
import {
  quads, floatSquares, coneState, SHAPE_STATE,
  quadIdxById, horizonLocal, clampCloneV, _reclampShapesOnPatch,
  _TAU, cone,
} from "./pg_state.js";
import { flatRect, FLAT_S_MIN, FLAT_S_MAX } from "./pg_flat_math.js";

/* ==========================================================================
   SHAPE CONSTANTS
   ========================================================================== */

export const SHAPE_REL_SIZE = 2.0;
export const SHAPE_U_REF    = 3.2;
export const SHAPE_V_REF    = 4.0;

export const SHAPE_DEFAULT_SCALE = 1.0;
export const SHAPE_MIN_SCALE     = 0.03;
export const SHAPE_MAX_SCALE     = 10.00;

export const SHAPE_SLOPE_MIN = 0;
export const SHAPE_SLOPE_MAX = 1;

const SHAPE_DEPTH_CONE_UNIT = 1.00;
export const SHAPE_DEPTH_FLAT = 1.00;

export function effectiveConeDepth() {
  return SHAPE_STATE.depthCone * SHAPE_DEPTH_CONE_UNIT;
}

export const ROTATE_HANDLE_OFFSET = 0.25;
export const ROTATE_HANDLE_R      = 10;
export const ROTATE_JUMP_STEP     = Math.PI / 12;
export const U_LIMIT = 3.0;

export const FLAT_HANDLE_R = 10;
export const FLAT_EDGE_R   = 6;
export const QUAD_MIN_SIZE_PHI = 0.06;
export const QUAD_MIN_SIZE_S   = 0.04;

/* ==========================================================================
   PALETTE + STRING CACHES
   ========================================================================== */

export const PATCH_HUES = [
  { r: 255, g: 200, b:  90 },
  { r: 120, g: 220, b: 255 },
  { r: 255, g: 130, b: 210 },
  { r: 140, g: 225, b: 160 },
  { r: 180, g: 155, b: 255 },
  { r: 255, g: 165, b: 105 },
  { r: 110, g: 220, b: 210 },
  { r: 220, g: 230, b: 120 },
];

export function patchHue(q) {
  if (!q) return PATCH_HUES[0];
  const n = PATCH_HUES.length;
  const idx = ((q.id - 1) % n + n) % n;
  return PATCH_HUES[idx];
}

const _rgbaCache = new Map();
export function _huergb(h, a) {
  const key = h.r + "," + h.g + "," + h.b + "," + a;
  let s = _rgbaCache.get(key);
  if (s !== undefined) return s;
  s = "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
  _rgbaCache.set(key, s);
  return s;
}

const _rgbaLightCache = new Map();
export function _huergbLight(h) {
  const key = h.r + "," + h.g + "," + h.b;
  let s = _rgbaLightCache.get(key);
  if (s !== undefined) return s;
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  s = "rgb(" + r + ", " + g + ", " + b + ")";
  _rgbaLightCache.set(key, s);
  return s;
}

/* ==========================================================================
   ASPECT SCALING
   ========================================================================== */

export function _shapeAspectScales(f) {
  if (!f || f.uLen <= 0 || f.vLen <= 0) return { Ku: 1, Kv: 1 };
  return { Ku: f.uLen / SHAPE_U_REF, Kv: f.vLen / SHAPE_V_REF };
}

/* ==========================================================================
   PATCH FRAME — memoized on patch bounds AND cone state
   ==========================================================================
   The cache hit check compares against every input the frame
   computation reads. Historically it compared only the patch's own
   (phi0, phi1, s0, s1); the four cone values below were missing, so
   an apex drag or a wheel zoom left the frame frozen at the previous
   cone geometry. Every downstream consumer of the frame — square
   corners, square screen projection, flat-view corners, standing
   height, screenToFrame / frameToWorld — inherited the freeze, which
   is what made squares stay put when the cone zoomed and (before the
   apex dual-intent fix) appear to "follow" the apex when it clamped.

   The check is intentionally strict on all six values. If any one of
   them changes the frame is recomputed; the recompute happens at most
   once per patch per frame, and re-hits on the subsequent per-square
   lookups. Cost per zoom frame during a scroll: one recompute per
   patch, not per square. */

const _patchFrameCache = new Map();

export function patchFrame(quadIdx) {
  if (quadIdx < 0 || quadIdx >= quads.length) return null;
  const q = quads[quadIdx];

  const cached = _patchFrameCache.get(q.id);
  if (cached &&
      cached.phi0  === q.phi0  && cached.phi1  === q.phi1  &&
      cached.s0    === q.s0    && cached.s1    === q.s1    &&
      cached.ax    === cone.ax && cached.ay    === cone.ay &&
      cached.depth === cone.depth &&
      cached.halfAngle === cone.halfAngle) {
    return cached.frame;
  }

  const corners = [
    { phi: q.phi0, s: q.s1 }, { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 }, { phi: q.phi0, s: q.s0 },
  ];
  const c = corners.map(v => {
    const R = cone.depth * Math.tan(cone.halfAngle);
    const r = (1 - v.s) * R;
    return [v.s * cone.ax + r * Math.cos(v.phi),
            v.s * cone.ay + r * Math.sin(v.phi)];
  });
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

  const frame = { Cx, Cy, Ux, Uy, Vx, Vy, det, uLen, vLen };
  _patchFrameCache.set(q.id, {
    phi0: q.phi0, phi1: q.phi1, s0: q.s0, s1: q.s1,
    ax: cone.ax, ay: cone.ay,
    depth: cone.depth, halfAngle: cone.halfAngle,
    frame,
  });
  return frame;
}

export function frameToWorld(f, u, v) {
  return [f.Cx + u * f.Ux + v * f.Vx,
          f.Cy + u * f.Uy + v * f.Vy];
}

export function frameToScreen(f, u, v) {
  const [wx, wy] = frameToWorld(f, u, v);
  return w2s(wx, wy);
}

export function screenToFrame(f, sx, sy) {
  const [wx, wy] = s2w(sx, sy);
  const dx = wx - f.Cx;
  const dy = wy - f.Cy;
  return {
    u: (dx * f.Vy - dy * f.Vx) / f.det,
    v: (f.Ux * dy - f.Uy * dx) / f.det,
  };
}

// w2s / s2w come from pg_state
import { w2s, s2w, view } from "./pg_state.js";

/* ==========================================================================
   MIRROR / PHI SHIFT
   ========================================================================== */

export function _withQuadPhiShifted(q, dPhi, fn) {
  const phi0 = q.phi0, phi1 = q.phi1;
  q.phi0 = phi0 + dPhi;
  q.phi1 = phi1 + dPhi;
  try { fn(); } finally {
    q.phi0 = phi0;
    q.phi1 = phi1;
  }
}

export function _squareHasMirror(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return false;
  const q = quads[qi];
  return !!(q && q.mirror);
}

export function _withSquareMirror(sq, fn) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q || !q.mirror) return;

  const angle = (typeof q.mirrorAngle === "number") ? q.mirrorAngle : Math.PI;
  const flip  = q.mirrorFlip !== false;

  const phi0   = q.phi0, phi1 = q.phi1;
  const theta0 = sq.theta || 0;
  const slope0 = sq.slope || 0;
  const u0     = sq.u;

  q.phi0 = phi0 + angle;
  q.phi1 = phi1 + angle;

  if (flip) {
    sq.theta = -theta0;
    sq.u     = -u0;
  }

  try { fn(); } finally {
    q.phi0   = phi0;
    q.phi1   = phi1;
    sq.theta = theta0;
    sq.slope = slope0;
    sq.u     = u0;
  }
}

export function _patchNormalPhi(qi) {
  if (qi < 0 || qi >= quads.length) return 0;
  const q = quads[qi];
  const TAU = 2 * Math.PI;
  let phi = (q.phi0 + q.phi1) / 2;
  phi = ((phi % TAU) + TAU) % TAU;
  return phi;
}

/* ==========================================================================
   PATCH CORNERS IN LOCAL FRAME
   ========================================================================== */

export function patchCornersLocal(quadIdx) {
  if (quadIdx < 0 || quadIdx >= quads.length) return null;
  const f = patchFrame(quadIdx);
  if (!f) return null;
  const q = quads[quadIdx];
  const corners = [
    { phi: q.phi0, s: q.s1 }, { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 }, { phi: q.phi0, s: q.s0 },
  ];
  return corners.map(c => {
    const R = cone.depth * Math.tan(cone.halfAngle);
    const r = (1 - c.s) * R;
    const wx = c.s * cone.ax + r * Math.cos(c.phi);
    const wy = c.s * cone.ay + r * Math.sin(c.phi);
    const dx = wx - f.Cx, dy = wy - f.Cy;
    return [
      (dx * f.Vy - dy * f.Vx) / f.det,
      (f.Ux * dy - f.Uy * dx) / f.det,
    ];
  });
}

/* ==========================================================================
   HORIZON / PERSPECTIVE
   ========================================================================== */

export function perspAt(qi, vHat) {
  const h = horizonLocal(qi);
  if (!h || h.vHat <= 1e-6) return 1;
  const p = (h.vHat - vHat) / h.vHat;
  return Math.max(0, p);
}

export function shapePerspCentre(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return 1;
  return perspAt(qi, sq.v);
}

export function shapeVMax(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return 1;
  const f = patchFrame(qi);
  if (!f) return 1;
  const h = horizonLocal(qi);
  if (!h) return 1;
  return h.vHat * f.vLen;
}

/* ==========================================================================
   STANDING HEIGHT
   ========================================================================== */

function _flatVExtentPxForShape(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return null;
  const dS = q.s1 - q.s0;
  if (Math.abs(dS) < 1e-9) return null;

  const vExtentLocal = SHAPE_REL_SIZE * (sq.scaleV || 1)
                       * (f.vLen / SHAPE_V_REF);
  const vExtentS = vExtentLocal * dS / f.vLen;
  const r = flatRect();
  return vExtentS * r.h * SHAPE_STATE.standingMult;
}

export function _shapeStandingScale(f, sq, qi, hingeV, depthScale) {
  if (!f || !sq) return 1;
  const flatVExtentPx = _flatVExtentPxForShape(sq);
  if (!flatVExtentPx || !isFinite(flatVExtentPx) ||
      flatVExtentPx < 1e-6) return 1;
  const Kv = f.vLen / SHAPE_V_REF;
  const shapeWorldH = SHAPE_REL_SIZE * (sq.scaleV || 1) * depthScale * Kv;
  if (shapeWorldH < 1e-9) return 1;
  const scale = flatVExtentPx / (shapeWorldH * view.scale);
  return (isFinite(scale) && scale > 0) ? scale : 1;
}

/* ==========================================================================
   HORIZON CLIP
   ========================================================================== */

export function _clipPolygonToVMax(corners, vMaxLocal) {
  const out = [];
  const n = corners.length;
  for (let i = 0; i < n; i++) {
    const A = corners[i];
    const B = corners[(i + 1) % n];
    const aIn = A[1] <= vMaxLocal;
    const bIn = B[1] <= vMaxLocal;
    if (aIn) out.push(A);
    if (aIn !== bIn) {
      const t = (vMaxLocal - A[1]) / (B[1] - A[1]);
      out.push([A[0] + (B[0] - A[0]) * t, vMaxLocal]);
    }
  }
  return out;
}

/* ==========================================================================
   V-EXTENT / POSITION / LIFECYCLE HELPERS
   ========================================================================== */

function _vExtentNormFromDims(f, dims, theta) {
  const hwHat = (dims.w / 2) / f.uLen;
  const hhHat = (dims.h / 2) / f.vLen;
  const cosT = Math.abs(Math.cos(theta || 0));
  const sinT = Math.abs(Math.sin(theta || 0));
  return hwHat * sinT + hhHat * cosT;
}

export function shapeVExtentNorm(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return 0;
  const f = patchFrame(qi);
  if (!f) return 0;
  const dims = squareDims(sq);
  return _vExtentNormFromDims(f, dims, sq.theta || 0);
}

export function setClonePosition(sq, uLocal, vLocal) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const f = patchFrame(qi);
  if (!f) return;
  let uHat = uLocal / f.uLen;
  if (uHat >  U_LIMIT) uHat =  U_LIMIT;
  if (uHat < -U_LIMIT) uHat = -U_LIMIT;
  sq.u = uHat;
  sq.v = clampCloneV(qi, vLocal / f.vLen);
}

export function clampShapeScales(sq) {
  sq.scaleU = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sq.scaleU));
  sq.scaleV = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sq.scaleV));
}

/* ==========================================================================
   GEOMETRY — dims, corners, handles
   ========================================================================== */

export function squareDims(sq) { return squareDimsWith(sq, SHAPE_DEPTH_FLAT); }
export function squareDimsCone(sq) { return squareDimsWith(sq, effectiveConeDepth()); }

export function squareDimsWith(sq, depthScale) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return { w: 0, h: 0 };
  const q = quads[qi];
  if (!q) return { w: 0, h: 0 };
  const f = patchFrame(qi);
  if (!f) return { w: 0, h: 0 };
  const { Ku, Kv } = _shapeAspectScales(f);
  return {
    w: SHAPE_REL_SIZE * sq.scaleU * Ku,
    h: SHAPE_REL_SIZE * sq.scaleV * depthScale * Kv,
  };
}

export function squareWorldWidth(sq)  { return SHAPE_REL_SIZE * sq.scaleU; }
export function squareWorldHeight(sq) { return SHAPE_REL_SIZE * sq.scaleV; }

export function squareCenterLocal(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  return [sq.u * f.uLen, sq.v * f.vLen];
}

export function squareCornersLocal(sq)     { return squareCornersLocalWith(sq, SHAPE_DEPTH_FLAT); }
export function squareCornersLocalCone(sq) { return squareCornersLocalWith(sq, effectiveConeDepth()); }

export function squareCornersLocalWith(sq, depthScale) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const refHW = SHAPE_REL_SIZE / 2 * sq.scaleU;
  const refHH = SHAPE_REL_SIZE / 2 * sq.scaleV * depthScale;
  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);
  const raw = [
    [-refHW, +refHH], [+refHW, +refHH], [+refHW, -refHH], [-refHW, -refHH],
  ];
  const { Ku, Kv } = _shapeAspectScales(f);
  return raw.map(([du, dv]) => {
    const duR = du * cosT - dv * sinT;
    const dvR = du * sinT + dv * cosT;
    const uHat = sq.u + (duR * Ku) / f.uLen;
    const vHat = sq.v + (dvR * Kv) / f.vLen;
    return [uHat * f.uLen, vHat * f.vLen];
  });
}

export function squareAnchorCornerLocal(sq, cornerIdx, depthScale) {
  const corners = squareCornersLocalWith(sq, depthScale);
  if (!corners || corners.length < 4) return null;
  return corners[(cornerIdx + 2) % 4];
}

export function squareCornersLocalUnrotated(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const uC = sq.u * f.uLen;
  const vC = sq.v * f.vLen;
  const dims = squareDims(sq);
  const hw = dims.w / 2, hh = dims.h / 2;
  return [
    [uC - hw, vC + hh], [uC + hw, vC + hh],
    [uC + hw, vC - hh], [uC - hw, vC - hh],
  ];
}

export function squareRotateHandleLocal(sq)     { return squareRotateHandleLocalWith(sq, SHAPE_DEPTH_FLAT); }
export function squareRotateHandleLocalCone(sq) { return squareRotateHandleLocalWith(sq, effectiveConeDepth()); }

export function squareRotateHandleLocalWith(sq, depthScale) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const refHH = SHAPE_REL_SIZE / 2 * sq.scaleV * depthScale;
  const k = refHH + ROTATE_HANDLE_OFFSET;
  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);
  const duR = -k * sinT;
  const dvR =  k * cosT;
  const { Ku, Kv } = _shapeAspectScales(f);
  const uHat = sq.u + (duR * Ku) / f.uLen;
  const vHat = sq.v + (dvR * Kv) / f.vLen;
  return [uHat * f.uLen, vHat * f.vLen];
}

/* ==========================================================================
   FLAT CORNERS — from a (u, v) footprint
   ========================================================================== */

export function squareFlatCorners(sq) {
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

export function squareFlatCenter(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return null;
  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;
  return {
    phi: phiC + sq.u * dPhi,
    s:   sC   + sq.v * dS,
  };
}

export function setSquareFlatCenter(sq, targetPhi, targetS) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return;
  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;
  if (Math.abs(dPhi) < 1e-9 || Math.abs(dS) < 1e-9) return;

  const curPhi = phiC + sq.u * dPhi;
  let deltaPhi = targetPhi - curPhi;
  while (deltaPhi >  Math.PI) deltaPhi -= 2 * Math.PI;
  while (deltaPhi < -Math.PI) deltaPhi += 2 * Math.PI;
  const newPhi = curPhi + deltaPhi;
  const newU = (newPhi - phiC) / dPhi;
  const newV = (targetS - sC) / dS;
  setClonePosition(sq, newU * f.uLen, newV * f.vLen);
}

/* ==========================================================================
   PATCH DRAG MUTATORS
   ========================================================================== */

export function applyPatchDragFromStart(q, dp, curPhi, curS, constrain) {
  let rawPhi = curPhi - dp.startPhi;
  while (rawPhi >  Math.PI) rawPhi -= 2 * Math.PI;
  while (rawPhi < -Math.PI) rawPhi += 2 * Math.PI;
  const rawS = curS - dp.startS;

  let dPhi = rawPhi;
  const dS = rawS;
  if (constrain) dPhi = 0;

  q.phi0 = dp.startPhi0 + dPhi;
  q.phi1 = dp.startPhi1 + dPhi;

  let ns0 = dp.startS0 + dS;
  let ns1 = dp.startS1 + dS;
  if (ns0 < FLAT_S_MIN) { ns1 += FLAT_S_MIN - ns0; ns0 = FLAT_S_MIN; }
  if (ns1 > FLAT_S_MAX) { ns0 -= ns1 - FLAT_S_MAX; ns1 = FLAT_S_MAX; }
  if (ns0 < FLAT_S_MIN) ns0 = FLAT_S_MIN;
  q.s0 = ns0;
  q.s1 = ns1;

  _reclampShapesOnPatch(q);
}

export function flatMoveCorner(q, cornerIdx, phi, s) {
  if (cornerIdx === 0) {
    q.phi0 = Math.max(0, Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
    q.s1   = Math.min(1, Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 1) {
    q.phi1 = Math.min(2 * Math.PI, Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
    q.s1   = Math.min(1, Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 2) {
    q.phi1 = Math.min(2 * Math.PI, Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
    q.s0   = Math.max(0, Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  } else if (cornerIdx === 3) {
    q.phi0 = Math.max(0, Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
    q.s0   = Math.max(0, Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  }
  _reclampShapesOnPatch(q);
}

export function flatMoveEdge(q, edge, phi, s) {
  if (edge === "top") {
    q.s1 = Math.min(1, Math.max(s, q.s0 + QUAD_MIN_SIZE_S));
  } else if (edge === "bottom") {
    q.s0 = Math.max(0, Math.min(s, q.s1 - QUAD_MIN_SIZE_S));
  } else if (edge === "left") {
    q.phi0 = Math.max(0, Math.min(phi, q.phi1 - QUAD_MIN_SIZE_PHI));
  } else if (edge === "right") {
    q.phi1 = Math.min(2 * Math.PI, Math.max(phi, q.phi0 + QUAD_MIN_SIZE_PHI));
  }
  _reclampShapesOnPatch(q);
}

export function flatMoveBody(q, dPhi, dS) {
  q.phi0 += dPhi;
  q.phi1 += dPhi;

  let ns0 = q.s0 + dS, ns1 = q.s1 + dS;
  if (ns0 < 0) { ns1 += 0 - ns0; ns0 = 0; }
  if (ns1 > 1) { ns0 -= ns1 - 1; ns1 = 1; }
  if (ns0 < 0) ns0 = 0;
  q.s0 = ns0; q.s1 = ns1;

  _reclampShapesOnPatch(q);
}

/* ==========================================================================
   SHARED HIT-TEST HELPERS
   ========================================================================== */

export function _ptInQuadPx(px, py, poly) {
  let inside = false;
  const n = poly.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > py) !== (yj > py)) &&
        (px < (xj - xi) * (py - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

export function squareDisplayName(sq) {
  return (sq && sq.name) ? sq.name : ("S" + (sq ? sq.id : "?"));
}
"""
