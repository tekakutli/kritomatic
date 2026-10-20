"""
pg_core.py — draw tool fitting, addDrawPoint, finishDrawTool, resize.

ES module.  The draw tool's two least-squares fit solvers
(`_fitPatchAndShape_patchFirst` / `_fitPatchAndShape_shapeFirst`),
the click accumulator (`addDrawPoint`), the finisher
(`finishDrawTool`), and `resize()`.  Everything else that used to
live in `pg_core.py` moved to `pg_state` / `pg_flat_math` /
`pg_view_squares_core` (see the module docstrings there for the
full map of which function moved where).

CYCLE BREAKING
==============
`finishDrawTool` calls the after-change hook from `pg_state` instead
of importing `syncQuadList` and `draw` directly.  `pg_state` has no
imports of its own, so nothing here creates a cycle.
"""

CORE_JS = r"""
import {
  quads, floatSquares, coneState, SHAPE_STATE, drawTool,
  canvas, ctx, env, view, layout,
  w2s, s2w,
  projectToConeSurface, quadIdxById,
  DEFAULT_PATCH_PHI_SPAN, DEFAULT_PATCH_S_SPAN,
  notifyChanged, fitView, cancelDrawTool, callDraw,
} from "./pg_state.js";
import {
  patchFrame,
  squareFlatCorners,
  clampShapeScales,
  SHAPE_REL_SIZE, SHAPE_U_REF, SHAPE_V_REF,
} from "./pg_view_squares_core.js";
import {
  screenToFlat, FLAT_S_MIN, FLAT_S_MAX,
} from "./pg_flat_math.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   LEAST-SQUARES RECTANGLE FIT
   ========================================================================== */

function _fitShapeFromFourPoints(localPts, f, signs, noRotate) {
  const cx = localPts.reduce((a, p) => a + p[0], 0) / 4;
  const cy = localPts.reduce((a, p) => a + p[1], 0) / 4;

  let a = 0, b = 0, c = 0, d = 0;
  for (let i = 0; i < 4; i++) {
    const sx = signs[i][0], sy = signs[i][1];
    const ox = localPts[i][0] - cx;
    const oy = localPts[i][1] - cy;
    a += ox * sx;
    b += ox * sy;
    c += oy * sx;
    d += oy * sy;
  }
  a /= 4; b /= 4; c /= 4; d /= 4;

  const Ku = f.uLen / SHAPE_U_REF;
  const Kv = f.vLen / SHAPE_V_REF;

  if (noRotate) {
    const refHW = Math.abs(a) / Ku;
    const refHH = Math.abs(d) / Kv;
    if (refHW < 1e-6 || refHH < 1e-6) return null;
    return {
      u: cx / f.uLen,
      v: cy / f.vLen,
      theta: 0,
      scaleU: refHW / (SHAPE_REL_SIZE / 2),
      scaleV: refHH / (SHAPE_REL_SIZE / 2),
    };
  }

  const refHW = Math.sqrt((a * a) / (Ku * Ku) + (c * c) / (Kv * Kv));
  const refHH = Math.sqrt((b * b) / (Ku * Ku) + (d * d) / (Kv * Kv));
  if (refHW < 1e-6 || refHH < 1e-6) return null;

  const cosT = a / (Ku * refHW);
  const sinT = c / (Kv * refHW);
  const theta = Math.atan2(sinT, cosT);

  return {
    u: cx / f.uLen,
    v: cy / f.vLen,
    theta,
    scaleU: refHW / (SHAPE_REL_SIZE / 2),
    scaleV: refHH / (SHAPE_REL_SIZE / 2),
  };
}

function _shapeLocalCorner(fit, sign, f) {
  const Ku = f.uLen / SHAPE_U_REF;
  const Kv = f.vLen / SHAPE_V_REF;
  const refHW = fit.scaleU * SHAPE_REL_SIZE / 2;
  const refHH = fit.scaleV * SHAPE_REL_SIZE / 2;
  const du = sign[0] * refHW;
  const dv = sign[1] * refHH;
  const cosT = Math.cos(fit.theta);
  const sinT = Math.sin(fit.theta);
  const duR = du * cosT - dv * sinT;
  const dvR = du * sinT + dv * cosT;
  return [fit.u * f.uLen + duR * Ku, fit.v * f.vLen + dvR * Kv];
}

function _bestRectangleFit(localPts, f, noRotate) {
  const signs = [[-1, 1], [1, 1], [1, -1], [-1, -1]];
  let best = null;

  function* permutations(arr, k = 0) {
    if (k === arr.length) { yield arr.slice(); return; }
    for (let i = k; i < arr.length; i++) {
      [arr[k], arr[i]] = [arr[i], arr[k]];
      yield* permutations(arr, k + 1);
      [arr[k], arr[i]] = [arr[i], arr[k]];
    }
  }

  for (const perm of permutations([0, 1, 2, 3])) {
    const assigned = perm.map(i => localPts[i]);
    const fit = _fitShapeFromFourPoints(assigned, f, signs, noRotate);
    if (!fit) continue;

    let err = 0;
    for (let i = 0; i < 4; i++) {
      const pred = _shapeLocalCorner(fit, signs[i], f);
      err += Math.hypot(pred[0] - assigned[i][0],
                        pred[1] - assigned[i][1]);
    }
    if (!best || err < best.err) best = { fit, err };
  }
  return best;
}

/* ==========================================================================
   PROJECT CLICKS TO (φ, s)
   ========================================================================== */

function _projectDrawPoints(screenPts) {
  const isFlat = screenPts.every(p => p[1] >= layout.dividerY);
  const isCone = screenPts.every(p => p[1] <  layout.dividerY);
  if (!isFlat && !isCone) return null;

  const params = screenPts.map(([sx, sy]) => {
    if (isFlat) {
      const p = screenToFlat(sx, sy);
      return [p.phi, p.s];
    }
    const [wx, wy] = s2w(sx, sy);
    const p = projectToConeSurface(wx, wy, 0.5);
    return [p.phi, p.s];
  });

  let basePhi = params[0][0];
  for (let i = 1; i < 4; i++) {
    while (params[i][0] - basePhi >  Math.PI) params[i][0] -= 2 * Math.PI;
    while (params[i][0] - basePhi < -Math.PI) params[i][0] += 2 * Math.PI;
  }

  return { params, isFlat };
}

/* ==========================================================================
   BUILD DEFAULT-SIZED PATCH AROUND A CENTRE
   ========================================================================== */

function _defaultPatchAround(phiC, sC) {
  const halfPhi = DEFAULT_PATCH_PHI_SPAN / 2;
  const halfS   = DEFAULT_PATCH_S_SPAN   / 2;

  let p0 = phiC - halfPhi;
  let p1 = phiC + halfPhi;
  let s0 = sC - halfS;
  let s1 = sC + halfS;

  if (s0 < FLAT_S_MIN) { s1 += FLAT_S_MIN - s0; s0 = FLAT_S_MIN; }
  if (s1 > FLAT_S_MAX) { s0 -= s1 - FLAT_S_MAX; s1 = FLAT_S_MAX; }
  if (s0 < FLAT_S_MIN) s0 = FLAT_S_MIN;

  return { phi0: p0, phi1: p1, s0, s1 };
}

/* ==========================================================================
   CREATE PATCH + SHAPE
   ========================================================================== */

function _pushPatchAndShapeFromParams(params, phiMin, phiMax, sMin, sMax,
                                      noRotate) {
  const id = coneState.nextQuadId++;
  const q = {
    id, name: "Q" + id,
    phi0: phiMin, phi1: phiMax,
    s0:   sMin,   s1:   sMax,
    mirror: false,
    mirrorAngle: Math.PI,
    mirrorFlip: true,
    warp: 0,
  };
  quads.push(q);
  const qi = quads.length - 1;
  coneState.selectedQuad = qi;

  const f = patchFrame(qi);
  if (!f) { quads.pop(); return null; }

  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;

  const localPts = params.map(([phi, s]) => {
    const uHat = (phi - phiC) / dPhi;
    const vHat = (s   - sC)   / dS;
    return [uHat * f.uLen, vHat * f.vLen];
  });

  const best = _bestRectangleFit(localPts, f, noRotate);
  if (!best) { quads.pop(); return null; }

  const fit = best.fit;
  const sqId = coneState.nextSquareId++;
  const sq = {
    id: sqId,
    name: "S" + sqId,
    quadId: q.id,
    u: fit.u,
    v: fit.v,
    scaleU: fit.scaleU,
    scaleV: fit.scaleV,
    theta: fit.theta,
    slope: 0,
  };
  clampShapeScales(sq);
  floatSquares.push(sq);
  coneState.selectedSquare = floatSquares.length - 1;

  return { patch: q, shape: sq };
}

/* ==========================================================================
   MODE 1: PATCH-FIRST
   ========================================================================== */

function _fitPatchAndShape_patchFirst(screenPts, noRotate) {
  if (screenPts.length !== 4) return null;
  const proj = _projectDrawPoints(screenPts);
  if (!proj) {
    flashStatus("Draw all four points in one band", "warn");
    return null;
  }
  const params = proj.params;

  const phis = params.map(p => p[0]);
  const ss   = params.map(p => p[1]);

  const phiC = (Math.min(...phis) + Math.max(...phis)) / 2;
  const sC   = (Math.min(...ss)   + Math.max(...ss))   / 2;

  const bounds = _defaultPatchAround(phiC, sC);

  return _pushPatchAndShapeFromParams(
    params, bounds.phi0, bounds.phi1, bounds.s0, bounds.s1, noRotate
  );
}

/* ==========================================================================
   MODE 2: SHAPE-FIRST (default)
   ========================================================================== */

function _fitPatchAndShape_shapeFirst(screenPts, noRotate) {
  const prelim = _fitPatchAndShape_patchFirst(screenPts, noRotate);
  if (!prelim) return null;
  const P = prelim.patch;
  const S = prelim.shape;

  const qi = quadIdxById(P.id);
  if (qi < 0) return prelim;

  const corners = squareFlatCorners(S);
  if (!corners || corners.length < 4) return prelim;

  let phiMin = Infinity, phiMax = -Infinity;
  let sMin   = Infinity, sMax   = -Infinity;
  for (const [phi, s] of corners) {
    phiMin = Math.min(phiMin, phi);
    phiMax = Math.max(phiMax, phi);
    sMin   = Math.min(sMin, s);
    sMax   = Math.max(sMax, s);
  }

  const phiC = (phiMin + phiMax) / 2;
  const sC   = (sMin   + sMax)   / 2;

  const bounds = _defaultPatchAround(phiC, sC);
  P.phi0 = bounds.phi0;
  P.phi1 = bounds.phi1;
  P.s0   = bounds.s0;
  P.s1   = bounds.s1;

  const f1 = patchFrame(qi);
  if (!f1) return prelim;

  const phiC1 = (P.phi0 + P.phi1) / 2;
  const sC1   = (P.s0   + P.s1)   / 2;
  const dPhi1 = P.phi1 - P.phi0;
  const dS1   = P.s1   - P.s0;

  const newLocalPts = corners.map(([phi, s]) => {
    const uHat = (phi - phiC1) / dPhi1;
    const vHat = (s   - sC1)   / dS1;
    return [uHat * f1.uLen, vHat * f1.vLen];
  });

  const best = _bestRectangleFit(newLocalPts, f1, noRotate);
  if (!best) return prelim;

  const fit = best.fit;
  S.u      = fit.u;
  S.v      = fit.v;
  S.theta  = fit.theta;
  S.scaleU = fit.scaleU;
  S.scaleV = fit.scaleV;
  clampShapeScales(S);

  return { patch: P, shape: S };
}

/* ==========================================================================
   MODE DISPATCHER
   ========================================================================== */

function _fitPatchAndShape(screenPts, mode, noRotate) {
  if (screenPts.length !== 4) return null;
  if (mode === "patch-first") {
    return _fitPatchAndShape_patchFirst(screenPts, noRotate);
  }
  return _fitPatchAndShape_shapeFirst(screenPts, noRotate);
}

/* ==========================================================================
   DRAW TOOL — ACCUMULATE AND FINISH
   ========================================================================== */

export function addDrawPoint(sx, sy) {
  if (!drawTool.active) return;
  if (drawTool.points.length >= 4) return;
  drawTool.points.push([sx, sy]);
  if (drawTool.points.length === 4) {
    finishDrawTool();
  } else {
    callDraw();
  }
}

export function finishDrawTool() {
  if (drawTool.points.length !== 4) return;
  const pts = drawTool.points.slice();

  const modeEl   = document.getElementById("drawFitMode");
  const noRotEl  = document.getElementById("drawNoRotate");
  const mode     = modeEl  ? modeEl.value       : "shape-first";
  const noRotate = noRotEl ? !!noRotEl.checked  : false;

  const result = _fitPatchAndShape(pts, mode, noRotate);
  if (!result) {
    flashStatus("Could not fit patch/shape", "bad");
    cancelDrawTool();
    return;
  }

  const tag = (mode === "shape-first") ? "shape\u2192patch" : "patch\u2192shape";
  const rot = noRotate ? " \u00B7 axis-aligned" : "";
  flashStatus(tag + rot + ":  " + result.patch.name
              + " + " + result.shape.name, "ok");
  cancelDrawTool();
  notifyChanged();
}

/* ==========================================================================
   RESIZE
   ========================================================================== */

export function resize() {
  env.dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  canvas.width  = Math.round(cw * env.dpr);
  canvas.height = Math.round(ch * env.dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);

  layout.coneH     = Math.round(ch * 0.58);
  layout.dividerY  = layout.coneH;
  layout.reservedY = layout.coneH + 4;
  layout.reservedH = ch - layout.reservedY;

  fitView();
  callDraw();
}
window.addEventListener("resize", resize);
"""
