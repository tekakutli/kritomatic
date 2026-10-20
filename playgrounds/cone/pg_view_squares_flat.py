"""
pg_view_squares_flat.py — flat-view projection, editing, labels.

ES module.  The flat (unfolded-cone) view deliberately ignores slope
AND warp: its footprint is the un-warped, un-hinged (phi, s) linear
image of the shape's (u, v) corners, so editing in that band stays
exact.

`squareFlatCorners`, `squareFlatCenter`, `setSquareFlatCenter` live
in `pg_view_squares_core`; this file only owns the *drawing* and
*hit-testing* passes plus the label renderer.
"""

SQUARES_FLAT_JS = r"""
import {
  quads, floatSquares, coneState,
  quadIdxById,
} from "./pg_state.js";
import {
  flatToScreen,
  screenToFlat,
  _phiCopies,
  _phiShiftForPatch,
  _flatInsideRect,
} from "./pg_flat_math.js";
import {
  patchFrame,
  squareCornersLocal,
  squareFlatCorners,
  squareFlatCenter,
  setClonePosition,
  squareRotateHandleLocal,
  _ptInQuadPx,
  _withSquareMirror,
  squareDisplayName,
  patchHue,
  _huergb,
  _huergbLight,
  ROTATE_HANDLE_R,
  FLAT_HANDLE_R,
  SHAPE_DEPTH_FLAT,
} from "./pg_view_squares_core.js";
import {
  setShapeFromCornerLocal,
  setShapeRotationFromCursorLocal,
} from "./pg_view_squares_edit.js";
import { squarePlaneIntersectionFlat } from "./pg_view_squares_proj.js";

/* ==========================================================================
   FLAT-VIEW SCREEN HELPERS
   ========================================================================== */

export function squareFlatCornersScreen(sq, shift) {
  const corners = squareFlatCorners(sq);
  if (!corners) return null;
  return corners.map(([phi, s]) => flatToScreen(phi + shift, s));
}

export function squareFlatRotateHandleScreen(sq, shift) {
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
  const phi  = phiC + (hl[0] / f.uLen) * dPhi + (shift || 0);

  let s = sC + (hl[1] / f.vLen) * dS;

  const S_MAX = 1 - 0.02;
  if (s > S_MAX) s = S_MAX;

  return flatToScreen(phi, s);
}

/* ==========================================================================
   FLAT-VIEW EDIT WRAPPERS
   ========================================================================== */

export function flatCursorToShapeLocal(sq, sx, sy) {
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

export function flatResizeSquareFromCornerIdx(sq, sx, sy, cornerIdx,
                                               anchorU, anchorV) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  if (anchorU !== undefined && anchorV !== undefined) {
    setShapeFromCornerLocal(sq, cornerIdx, lc.u, lc.v, SHAPE_DEPTH_FLAT,
                             anchorU, anchorV);
  } else {
    setShapeFromCornerLocal(sq, cornerIdx, lc.u, lc.v, SHAPE_DEPTH_FLAT);
  }
}

export function flatRotateSquareToCursor(sq, sx, sy, snap, startTheta) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  setShapeRotationFromCursorLocal(sq, lc.u, lc.v, snap, startTheta);
}

export function flatMoveSquareBody(sq, sx, sy) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  setClonePosition(sq, lc.u, lc.v);
}

/* ==========================================================================
   FLAT-VIEW HIT TEST
   ========================================================================== */

function _squareFlatHitTestOne(si, sx, sy) {
  const sq = floatSquares[si];
  if (!sq) return null;

  const corners = squareFlatCorners(sq);
  if (!corners) return null;
  const phis = corners.map(c => c[0]);
  const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));

  for (const shift of shifts) {
    const rh = squareFlatRotateHandleScreen(sq, shift);
    if (rh && Math.hypot(sx - rh[0], sy - rh[1]) < ROTATE_HANDLE_R) {
      return { kind: "rotate", squareIdx: si };
    }
  }

  for (const shift of shifts) {
    const screen = corners.map(([phi, s]) =>
      flatToScreen(phi + shift, s));

    for (let ci = 0; ci < 4; ci++) {
      if (Math.hypot(sx - screen[ci][0], sy - screen[ci][1])
          < FLAT_HANDLE_R) {
        return { kind: "corner", squareIdx: si, cornerIdx: ci };
      }
    }

    if (_ptInQuadPx(sx, sy, screen)) {
      return { kind: "body", squareIdx: si };
    }
  }
  return null;
}

export function squareFlatHitTest(sx, sy) {
  if (coneState.selectedQuad < 0 ||
      coneState.selectedQuad >= quads.length) return null;
  const activeQ = quads[coneState.selectedQuad];
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    if (floatSquares[i].quadId !== activeQ.id) continue;
    const hit = _squareFlatHitTestOne(i, sx, sy);
    if (hit) return hit;
  }
  return null;
}

export function squareFlatMirrorHitTest(sx, sy) {
  if (coneState.selectedQuad < 0 ||
      coneState.selectedQuad >= quads.length) return null;
  const activeQ = quads[coneState.selectedQuad];
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    const sq = floatSquares[i];
    if (sq.quadId !== activeQ.id) continue;
    let hit = null;
    _withSquareMirror(sq, () => {
      hit = _squareFlatHitTestOne(i, sx, sy);
    });
    if (hit) return hit;
  }
  return null;
}

/* ==========================================================================
   LABEL RENDERING — with width cache and viewport cull
   ========================================================================== */

import { ctx } from "./pg_state.js";

function _drawShapeLabel(sx, sy, text, color, cacheObj) {
  if (!text) return;

  const cw = window.innerWidth;
  const ch = window.innerHeight;
  if (sx < -200 || sx > cw + 200 || sy < -20 || sy > ch + 20) return;

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  let w;
  if (cacheObj &&
      cacheObj._labelText === text &&
      typeof cacheObj._labelWidth === "number") {
    w = cacheObj._labelWidth;
  } else {
    w = ctx.measureText(text).width;
    if (cacheObj) {
      cacheObj._labelText  = text;
      cacheObj._labelWidth = w;
    }
  }

  const padX = 6;
  const h = 14;

  ctx.fillStyle = "rgba(10, 14, 20, 0.86)";
  ctx.fillRect(sx - w / 2 - padX, sy - h / 2, w + 2 * padX, h);

  ctx.strokeStyle = color;
  ctx.lineWidth = 0.8;
  ctx.strokeRect(sx - w / 2 - padX + 0.4, sy - h / 2 + 0.4,
                 w + 2 * padX - 0.8, h - 0.8);

  ctx.fillStyle = color;
  ctx.fillText(text, sx, sy + 0.5);
  ctx.restore();
}

function _patchCentreScreenCone(q) {
  const pts = [
    { phi: q.phi0, s: q.s1 }, { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 }, { phi: q.phi0, s: q.s0 },
  ].map(c => {
    const R = cone.depth * Math.tan(cone.halfAngle);
    const r = (1 - c.s) * R;
    const wx = c.s * cone.ax + r * Math.cos(c.phi);
    const wy = c.s * cone.ay + r * Math.sin(c.phi);
    return w2s(wx, wy);
  });
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / 4, cy / 4];
}

import { cone, w2s, view, surfacePoint } from "./pg_state.js";
import { squareCornersScreen } from "./pg_view_squares_proj.js";

function _shapeCentreScreenCone(sq) {
  const pts = squareCornersScreen(sq);
  if (!pts || pts.length < 1) return null;
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / pts.length, cy / pts.length];
}

function _patchCentreScreenFlat(q, shift) {
  const pts = [
    { phi: q.phi0, s: q.s1 }, { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 }, { phi: q.phi0, s: q.s0 },
  ].map(c => flatToScreen(c.phi + shift, c.s));
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / 4, cy / 4];
}

function _shapeCentreScreenFlat(sq, shift) {
  const corners = squareFlatCorners(sq);
  if (!corners) return null;
  const pts = corners.map(([phi, s]) => flatToScreen(phi + shift, s));
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / 4, cy / 4];
}

export function drawShapeLabelsCone() {
  for (let qi = 0; qi < quads.length; qi++) {
    const q = quads[qi];
    const hue = patchHue(q);
    const [cx, cy] = _patchCentreScreenCone(q);
    _drawShapeLabel(cx, cy, q.name, _huergb(hue, 0.92), q);
  }

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    const q = qi >= 0 ? quads[qi] : null;
    const hue = patchHue(q);
    const c = _shapeCentreScreenCone(sq);
    if (!c) continue;
    _drawShapeLabel(c[0], c[1], squareDisplayName(sq),
                    _huergb(hue, 0.95), sq);
  }
}

export function drawShapeLabelsFlat() {
  for (let qi = 0; qi < quads.length; qi++) {
    const q = quads[qi];
    const hue = patchHue(q);
    const shifts = _phiCopies(q.phi0, q.phi1);
    for (const shift of shifts) {
      const [cx, cy] = _patchCentreScreenFlat(q, shift);
      if (_flatInsideRect(cx, cy)) {
        _drawShapeLabel(cx, cy, q.name, _huergb(hue, 0.92), q);
        break;
      }
    }
  }

  if (coneState.selectedQuad < 0 ||
      coneState.selectedQuad >= quads.length) return;
  const activeQ = quads[coneState.selectedQuad];
  const activeHue = patchHue(activeQ);

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    if (sq.quadId !== activeQ.id) continue;

    const corners = squareFlatCorners(sq);
    if (!corners) continue;
    const phis = corners.map(c => c[0]);
    const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));

    for (const shift of shifts) {
      const c = _shapeCentreScreenFlat(sq, shift);
      if (!c) continue;
      if (_flatInsideRect(c[0], c[1])) {
        _drawShapeLabel(c[0], c[1], squareDisplayName(sq),
                        _huergb(activeHue, 0.95), sq);
        break;
      }
    }
  }
}
"""
