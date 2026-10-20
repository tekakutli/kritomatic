"""
pg_view_squares_proj.py — cone-view projection and rendering.

ES module.  Turns a shape's local (u, v) into a screen position,
applying the hinge (slope), the warp chart, and the perspective
taper — in that order.  Also draws the patches' horizon and
base-reach guides, and the shapes themselves on the cone band.

The perspective taper is applied at every warp value.  The warp
chart stays on the patch's own plane, so the plane's 3D look — the
shrink toward the apex — is unchanged by warp.

STAGE 1 OPTIMIZATION (preserved)
================================
`squareRotateHandleScreen` computes the local clip directly from
the (u, v) corners instead of calling `squareCornersScreen` as a
guard.  Projection runs exactly once per square per frame.
"""

SQUARES_PROJ_JS = r"""
import {
  cone, quads, floatSquares, coneState,
  quadIdxById, horizonLocal, view, w2s, s2w, ctx,
} from "./pg_state.js";
import {
  patchFrame,
  squareCornersLocalCone,
  squareRotateHandleLocalCone,
  _clipPolygonToVMax,
  shapePerspCentre,
  shapeVMax,
  _patchNormalPhi,
  _shapeStandingScale,
  effectiveConeDepth,
  patchHue,
  _huergb,
  _huergbLight,
  _withSquareMirror,
  SHAPE_DEPTH_FLAT,
  frameToWorld,
} from "./pg_view_squares_core.js";
import { _warpedFrameToWorld } from "./pg_view_squares_warp.js";

/* ==========================================================================
   PROJECTION — hinge, warp, taper
   ========================================================================== */

export function projectShapePoint(f, qi, uLocal, vLocal, vC, pC, vMax,
                                  phiC, slopeParam, hingeV, standingScale) {
  const s = Math.max(0, Math.min(1, slopeParam || 0));

  let vInPlane = vLocal;
  let hOut = 0;

  if (s > 1e-6 && hingeV !== undefined && isFinite(hingeV)) {
    const theta = s * Math.PI / 2;
    const cosT  = Math.cos(theta);
    const sinT  = Math.sin(theta);
    const d     = vLocal - hingeV;
    vInPlane = hingeV + d * cosT;

    const scaleFactor = (standingScale !== undefined &&
                         isFinite(standingScale) && standingScale > 0)
                          ? standingScale : 1;
    hOut = d * sinT * scaleFactor;
  }

  const q = (qi >= 0 && qi < quads.length) ? quads[qi] : null;
  const [wx, wy] = _warpedFrameToWorld(f, q, uLocal, vInPlane);

  const [sx, sy] = w2s(wx, wy);
  const [ax, ay] = w2s(cone.ax, cone.ay);

  const p = pC - (vInPlane - vC) / vMax;

  let px = ax + p * (sx - ax);
  let py = ay + p * (sy - ay);

  if (hOut !== 0) {
    px += hOut * -Math.cos(phiC) * view.scale;
    py += hOut *  Math.sin(phiC) * view.scale;
  }

  return [px, py];
}

export function squareCornersScreen(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const local = squareCornersLocalCone(sq);
  if (!local) return null;

  const h = horizonLocal(qi);
  if (!h) return null;
  const vMaxLocal = h.vHat * f.vLen;
  const clipped = _clipPolygonToVMax(local, vMaxLocal);
  if (clipped.length < 3) return null;

  const vC    = sq.v * f.vLen;
  const pC    = shapePerspCentre(sq);
  const vMax  = shapeVMax(sq);
  const phiC  = _patchNormalPhi(qi);
  const slope = sq.slope || 0;

  let hingeV = Infinity;
  for (const [u, v] of local) {
    if (v < hingeV) hingeV = v;
  }

  const standingScale = _shapeStandingScale(
    f, sq, qi, hingeV, effectiveConeDepth());

  return clipped.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax, phiC, slope, hingeV,
                      standingScale));
}

export function squareRotateHandleScreen(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;

  const h = horizonLocal(qi);
  if (!h) return null;
  const vMaxLocal = h.vHat * f.vLen;

  const local = squareCornersLocalCone(sq);
  if (!local) return null;
  const clipped = _clipPolygonToVMax(local, vMaxLocal);
  if (clipped.length < 3) return null;

  const hl = squareRotateHandleLocalCone(sq);
  if (!hl) return null;

  const HANDLE_GAP = 0.02 * f.vLen;
  const hv = Math.min(hl[1], vMaxLocal - HANDLE_GAP);

  const vC    = sq.v * f.vLen;
  const pC    = shapePerspCentre(sq);
  const vMax  = shapeVMax(sq);
  const phiC  = _patchNormalPhi(qi);
  const slope = sq.slope || 0;

  let hingeV = Infinity;
  for (const [u, v] of local) {
    if (v < hingeV) hingeV = v;
  }

  const standingScale = _shapeStandingScale(
    f, sq, qi, hingeV, effectiveConeDepth());

  return projectShapePoint(f, qi, hl[0], hv, vC, pC, vMax, phiC,
                            slope, hingeV, standingScale);
}

/* ==========================================================================
   PLANE INTERSECTION
   ========================================================================== */

function squarePlaneIntersectionLocal(sq) {
  const slope = sq.slope || 0;
  if (slope < 1e-6) return null;

  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;

  const corners = squareCornersLocalCone(sq);
  if (!corners || corners.length < 3) return null;

  const sorted = corners.slice().sort((a, b) => a[1] - b[1]);
  return [sorted[0], sorted[1]];
}

export function squarePlaneIntersectionScreen(sq) {
  const local = squarePlaneIntersectionLocal(sq);
  if (!local) return null;

  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const h = horizonLocal(qi);
  if (!h) return null;

  const vC    = sq.v * f.vLen;
  const pC    = shapePerspCentre(sq);
  const vMax  = shapeVMax(sq);
  const phiC  = _patchNormalPhi(qi);
  const slope = sq.slope || 0;

  let hingeV = Infinity;
  const allCorners = squareCornersLocalCone(sq);
  if (allCorners) {
    for (const [u, v] of allCorners) {
      if (v < hingeV) hingeV = v;
    }
  }

  const standingScale = _shapeStandingScale(
    f, sq, qi, hingeV, effectiveConeDepth());

  return local.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax, phiC, slope, hingeV,
                      standingScale));
}

export function squarePlaneIntersectionFlat(sq) {
  const local = squarePlaneIntersectionLocal(sq);
  if (!local) return null;

  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const q = quads[qi];
  const f = patchFrame(qi);
  if (!f) return null;

  const phiC = (q.phi0 + q.phi1) / 2;
  const sC   = (q.s0   + q.s1)   / 2;
  const dPhi = q.phi1 - q.phi0;
  const dS   = q.s1   - q.s0;

  return local.map(([u, v]) => [
    phiC + (u / f.uLen) * dPhi,
    sC   + (v / f.vLen) * dS,
  ]);
}

/* ==========================================================================
   RENDERING — cone band
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
  const vLocal = -2.50 * f.vLen;
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

export function drawFloatSquares() {
  for (let i = 0; i < floatSquares.length; i++) {
    if (i === coneState.selectedSquare) continue;
    const sq = floatSquares[i];
    drawFloatSquare(sq, false, false);
    _withSquareMirror(sq, () => drawFloatSquare(sq, false, true));
  }
  if (coneState.selectedSquare >= 0 &&
      coneState.selectedSquare < floatSquares.length) {
    const sq = floatSquares[coneState.selectedSquare];
    drawFloatSquare(sq, true, false);
    _withSquareMirror(sq, () => drawFloatSquare(sq, false, true));
  }
}

function _drawRotateHandle(sx, sy, hue) {
  ctx.beginPath();
  ctx.arc(sx, sy, 6.0, 0, Math.PI * 2);
  ctx.fillStyle = _huergbLight(hue);
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

function drawFloatSquare(sq, selected, isMirror) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q) return;
  const hue = patchHue(q);

  if (selected && !isMirror) {
    drawHorizon(qi);
    drawBaseReach(qi);
  }

  const pts = squareCornersScreen(sq);
  if (!pts || pts.length < 3) return;

  const fillAlpha   = isMirror ? 0.06 : (selected ? 0.30 : 0.12);
  const strokeAlpha = isMirror ? 0.55 : (selected ? 1.00 : 0.72);
  const lineW       = isMirror ? 1.0  : (selected ? 2.0  : 1.3);

  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1]);
  ctx.closePath();
  ctx.fillStyle = _huergb(hue, fillAlpha);
  ctx.fill();

  ctx.lineJoin = "round";
  ctx.strokeStyle = _huergb(hue, strokeAlpha);
  ctx.lineWidth = lineW;
  ctx.stroke();

  if (sq.slope && Math.abs(sq.slope) > 1e-6) {
    const isect = squarePlaneIntersectionScreen(sq);
    if (isect) {
      ctx.save();
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = _huergb(hue,
        isMirror ? 0.32 : (selected ? 0.65 : 0.42));
      ctx.lineWidth   = 1.0;
      ctx.beginPath();
      ctx.moveTo(isect[0][0], isect[0][1]);
      ctx.lineTo(isect[1][0], isect[1][1]);
      ctx.stroke();
      ctx.restore();
    }
  }

  if (!selected || isMirror) return;

  for (const [sx, sy] of pts) {
    ctx.beginPath();
    ctx.arc(sx, sy, 4.5, 0, Math.PI * 2);
    ctx.fillStyle = _huergbLight(hue);
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  }

  const rh = squareRotateHandleScreen(sq);
  if (rh) {
    let topIdx = 0;
    for (let k = 1; k < pts.length; k++) {
      if (pts[k][1] < pts[topIdx][1]) topIdx = k;
    }
    ctx.beginPath();
    ctx.moveTo(pts[topIdx][0], pts[topIdx][1]);
    ctx.lineTo(rh[0], rh[1]);
    ctx.strokeStyle = _huergb(hue, 0.45);
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 3]);
    ctx.stroke();
    ctx.setLineDash([]);

    _drawRotateHandle(rh[0], rh[1], hue);
  }
}
"""
