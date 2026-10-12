"""
pg_view_squares_core.py — model, frame, geometry, lifecycle.

The stable half of the square system: state, constants, the patch
frame, shape dimensions and corners, the mirror wrapper, the
horizon, and the shape lifecycle (add / clone / delete).  Nothing
here depends on the projection or on the warp chart — this file is
edited only when the SHAPE MODEL itself changes.
"""

SQUARES_CORE_JS = r"""
/* ==========================================================================
   STATE
   ========================================================================== */

const floatSquares = [];
let nextSquareId = 1;
let selectedSquare = -1;

/* ==========================================================================
   SHAPE DIMENSIONS
   ========================================================================== */

const SHAPE_REL_SIZE = 2.0;

const SHAPE_DEFAULT_SCALE = 1.0;
const SHAPE_MIN_SCALE     = 0.03;
const SHAPE_MAX_SCALE     = 10.00;

const SHAPE_SLOPE_MIN = 0;
const SHAPE_SLOPE_MAX = 1;

let SHAPE_STANDING_HEIGHT_MULT = 2.00;

/* ==========================================================================
   PATCH-RELATIVE ASPECT SCALING
   ========================================================================== */

const SHAPE_U_REF = 3.2;
const SHAPE_V_REF = 4.0;

function _shapeAspectScales(f) {
  if (!f || f.uLen <= 0 || f.vLen <= 0) {
    return { Ku: 1, Kv: 1 };
  }
  return { Ku: f.uLen / SHAPE_U_REF, Kv: f.vLen / SHAPE_V_REF };
}

/* ==========================================================================
   SHAPE DEPTH — ONE PER VIEW
   ========================================================================== */

const SHAPE_DEPTH_CONE_UNIT = 1.00;
let   SHAPE_DEPTH_CONE      = 1.00;
const SHAPE_DEPTH_FLAT      = 1.00;

function effectiveConeDepth() {
  return SHAPE_DEPTH_CONE * SHAPE_DEPTH_CONE_UNIT;
}

const WEDGE_BASE_REACH = 2.50;

const ROTATE_HANDLE_OFFSET = 0.25;
const ROTATE_HANDLE_R      = 10;

const ROTATE_JUMP_STEP = Math.PI / 12;

const U_LIMIT = 3.0;

/* ==========================================================================
   PATCH HUE PALETTE
   ========================================================================== */

const PATCH_HUES = [
  { r: 255, g: 200, b:  90 },
  { r: 120, g: 220, b: 255 },
  { r: 255, g: 130, b: 210 },
  { r: 140, g: 225, b: 160 },
  { r: 180, g: 155, b: 255 },
  { r: 255, g: 165, b: 105 },
  { r: 110, g: 220, b: 210 },
  { r: 220, g: 230, b: 120 },
];

function patchHue(q) {
  if (!q) return PATCH_HUES[0];
  const n = PATCH_HUES.length;
  const idx = ((q.id - 1) % n + n) % n;
  return PATCH_HUES[idx];
}

function _huergb(h, a) {
  return "rgba(" + h.r + ", " + h.g + ", " + h.b + ", " + a + ")";
}

function _huergbLight(h) {
  const r = Math.round(h.r * 0.7 + 255 * 0.3);
  const g = Math.round(h.g * 0.7 + 255 * 0.3);
  const b = Math.round(h.b * 0.7 + 255 * 0.3);
  return "rgb(" + r + ", " + g + ", " + b + ")";
}

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
   MIRROR
   ========================================================================== */

function _withQuadPhiShifted(q, dPhi, fn) {
  const phi0 = q.phi0, phi1 = q.phi1;
  q.phi0 = phi0 + dPhi;
  q.phi1 = phi1 + dPhi;
  try { fn(); } finally {
    q.phi0 = phi0;
    q.phi1 = phi1;
  }
}

function _squareHasMirror(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return false;
  const q = quads[qi];
  return !!(q && q.mirror);
}

function _withSquareMirror(sq, fn) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q || !q.mirror) return;

  const angle = (typeof q.mirrorAngle === "number")
                  ? q.mirrorAngle : Math.PI;
  const flip  = q.mirrorFlip !== false;

  const phi0   = q.phi0;
  const phi1   = q.phi1;
  const theta0 = sq.theta || 0;
  const slope0 = sq.slope || 0;
  const u0     = sq.u;

  q.phi0 = phi0 + angle;
  q.phi1 = phi1 + angle;

  if (flip) {
    sq.theta = -theta0;
    sq.u     = -u0;
  }

  try {
    fn();
  } finally {
    q.phi0   = phi0;
    q.phi1   = phi1;
    sq.theta = theta0;
    sq.slope = slope0;
    sq.u     = u0;
  }
}

function _patchNormalPhi(qi) {
  if (qi < 0 || qi >= quads.length) return 0;
  const q = quads[qi];
  const TAU = 2 * Math.PI;
  let phi = (q.phi0 + q.phi1) / 2;
  phi = ((phi % TAU) + TAU) % TAU;
  return phi;
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
   STANDING-HEIGHT SCALE FOR THE HINGE
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
  return vExtentS * r.h * SHAPE_STANDING_HEIGHT_MULT;
}

function _shapeStandingScale(f, sq, qi, hingeV, depthScale) {
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

function _clipPolygonToVMax(corners, vMaxLocal) {
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
   CLAMP — v only
   ========================================================================== */

function clampCloneV(qi, vHat) {
  const h = horizonLocal(qi);
  if (!h) return vHat;
  const vMin = -WEDGE_BASE_REACH;
  const vMax = h.vHat + WEDGE_BASE_REACH;
  if (vMax <= vMin) return vMin;
  return Math.max(vMin, Math.min(vMax, vHat));
}

/* ==========================================================================
   SHAPE V-EXTENT
   ========================================================================== */

function _vExtentNormFromDims(f, dims, theta) {
  const hwHat = (dims.w / 2) / f.uLen;
  const hhHat = (dims.h / 2) / f.vLen;
  const cosT = Math.abs(Math.cos(theta || 0));
  const sinT = Math.abs(Math.sin(theta || 0));
  return hwHat * sinT + hhHat * cosT;
}

function shapeVExtentNorm(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return 0;
  const f = patchFrame(qi);
  if (!f) return 0;
  const dims = squareDims(sq);
  return _vExtentNormFromDims(f, dims, sq.theta || 0);
}

/* ==========================================================================
   SHAPE POSITION
   ========================================================================== */

function setClonePosition(sq, uLocal, vLocal) {
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

  const id = nextSquareId++;
  floatSquares.push({
    id: id,
    name: "S" + id,
    quadId: q.id,
    u: uHat, v: vHat,
    scaleU: SHAPE_DEFAULT_SCALE,
    scaleV: SHAPE_DEFAULT_SCALE,
    theta: 0,
    slope: 0,
    visualBottom: false,
  });
  selectedSquare = floatSquares.length - 1;
  syncQuadList();
  draw();
}

function addSquareAtCenter(quadIdx) { addSquareAt(quadIdx, 0, 0); }

function cloneSquare(idx) {
  if (idx < 0 || idx >= floatSquares.length) return;
  const src = floatSquares[idx];
  if (!src) return;

  const newId = nextSquareId++;
  floatSquares.push({
    id:     newId,
    name:   "S" + newId,
    quadId: src.quadId,
    u:      src.u,
    v:      src.v,
    scaleU: src.scaleU,
    scaleV: src.scaleV,
    theta:  src.theta || 0,
    slope:  src.slope || 0,
    visualBottom: !!src.visualBottom,
  });
  selectedSquare = floatSquares.length - 1;
  syncQuadList();
  draw();
}

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
  return squareDimsWith(sq, SHAPE_DEPTH_FLAT);
}

function squareDimsCone(sq) {
  return squareDimsWith(sq, effectiveConeDepth());
}

function squareDimsWith(sq, depthScale) {
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

function squareWorldWidth(sq) {
  return SHAPE_REL_SIZE * sq.scaleU;
}

function squareWorldHeight(sq) {
  return SHAPE_REL_SIZE * sq.scaleV;
}

function squareCenterLocal(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  return [sq.u * f.uLen, sq.v * f.vLen];
}

function squareCornersLocal(sq) {
  return squareCornersLocalWith(sq, SHAPE_DEPTH_FLAT);
}

function squareCornersLocalCone(sq) {
  return squareCornersLocalWith(sq, effectiveConeDepth());
}

function squareCornersLocalWith(sq, depthScale) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;

  const refHW = SHAPE_REL_SIZE / 2 * sq.scaleU;
  const refHH = SHAPE_REL_SIZE / 2 * sq.scaleV * depthScale;

  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);

  const rawCorners = [
    [-refHW, +refHH], [+refHW, +refHH], [+refHW, -refHH], [-refHW, -refHH],
  ];

  const { Ku, Kv } = _shapeAspectScales(f);

  return rawCorners.map(([du, dv]) => {
    const duR = du * cosT - dv * sinT;
    const dvR = du * sinT + dv * cosT;
    const uHat = sq.u + (duR * Ku) / f.uLen;
    const vHat = sq.v + (dvR * Kv) / f.vLen;
    return [uHat * f.uLen, vHat * f.vLen];
  });
}

function squareAnchorCornerLocal(sq, cornerIdx, depthScale) {
  const corners = squareCornersLocalWith(sq, depthScale);
  if (!corners || corners.length < 4) return null;
  const anchorIdx = (cornerIdx + 2) % 4;
  return corners[anchorIdx];
}

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
  return squareRotateHandleLocalWith(sq, SHAPE_DEPTH_FLAT);
}

function squareRotateHandleLocalCone(sq) {
  return squareRotateHandleLocalWith(sq, effectiveConeDepth());
}

function squareRotateHandleLocalWith(sq, depthScale) {
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
   SHARED HIT-TEST HELPERS
   ========================================================================== */

function _ptInQuadPx(px, py, poly) {
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

function squareDisplayName(sq) {
  return (sq && sq.name) ? sq.name : ("S" + (sq ? sq.id : "?"));
}
"""
