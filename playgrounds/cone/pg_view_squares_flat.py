"""
pg_view_squares_flat.py — flat-view projection, editing, labels.

The flat (unfolded-cone) view deliberately ignores slope AND warp:
its footprint is the un-warped, un-hinged (phi, s) linear image of
the shape's (u, v) corners, so editing in that band stays exact and
the flat sheet is a pure design-intent view.
"""

SQUARES_FLAT_JS = r"""
/* ==========================================================================
   FLAT-VIEW PROJECTION
   ========================================================================== */

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

function squareFlatCenter(sq) {
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

function setSquareFlatCenter(sq, targetPhi, targetS) {
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

function squareFlatCornersScreen(sq, shift) {
  const corners = squareFlatCorners(sq);
  if (!corners) return null;
  return corners.map(([phi, s]) => flatToScreen(phi + shift, s));
}

function squareFlatRotateHandleScreen(sq, shift) {
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

function flatResizeSquareFromCornerIdx(sq, sx, sy, cornerIdx,
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

function flatRotateSquareToCursor(sq, sx, sy, snap, startTheta) {
  const lc = flatCursorToShapeLocal(sq, sx, sy);
  if (!lc) return;
  setShapeRotationFromCursorLocal(sq, lc.u, lc.v, snap, startTheta);
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

function squareFlatHitTest(sx, sy) {
  if (selectedQuad < 0 || selectedQuad >= quads.length) return null;
  const activeQ = quads[selectedQuad];
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    if (floatSquares[i].quadId !== activeQ.id) continue;
    const hit = _squareFlatHitTestOne(i, sx, sy);
    if (hit) return hit;
  }
  return null;
}

function squareFlatMirrorHitTest(sx, sy) {
  if (selectedQuad < 0 || selectedQuad >= quads.length) return null;
  const activeQ = quads[selectedQuad];
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
   LABEL RENDERING
   ========================================================================== */

function _drawShapeLabel(sx, sy, text, color) {
  if (!text) return;
  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', 'Fira Code', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";

  const w = ctx.measureText(text).width;
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
  const pts = quadCorners(q).map(c => {
    const [wx, wy] = surfacePoint(c.phi, c.s);
    return w2s(wx, wy);
  });
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / 4, cy / 4];
}

function _shapeCentreScreenCone(sq) {
  const pts = squareCornersScreen(sq);
  if (!pts || pts.length < 1) return null;
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / pts.length, cy / pts.length];
}

function _patchCentreScreenFlat(q, shift) {
  const pts = quadCorners(q).map(c =>
    flatToScreen(c.phi + shift, c.s));
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

function drawShapeLabelsCone() {
  for (let qi = 0; qi < quads.length; qi++) {
    const q = quads[qi];
    const hue = patchHue(q);
    const [cx, cy] = _patchCentreScreenCone(q);
    _drawShapeLabel(cx, cy, q.name, _huergb(hue, 0.92));
  }

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    const q = qi >= 0 ? quads[qi] : null;
    const hue = patchHue(q);
    const c = _shapeCentreScreenCone(sq);
    if (!c) continue;
    _drawShapeLabel(c[0], c[1], squareDisplayName(sq),
                    _huergb(hue, 0.95));
  }
}

function drawShapeLabelsFlat() {
  for (let qi = 0; qi < quads.length; qi++) {
    const q = quads[qi];
    const hue = patchHue(q);
    const shifts = _phiCopies(q.phi0, q.phi1);
    for (const shift of shifts) {
      const [cx, cy] = _patchCentreScreenFlat(q, shift);
      if (_flatInsideRect(cx, cy)) {
        _drawShapeLabel(cx, cy, q.name, _huergb(hue, 0.92));
        break;
      }
    }
  }

  if (selectedQuad < 0 || selectedQuad >= quads.length) return;
  const activeQ = quads[selectedQuad];
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
                        _huergb(activeHue, 0.95));
        break;
      }
    }
  }
}
"""
