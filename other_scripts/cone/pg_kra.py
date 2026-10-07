"""
pg_kra.py — panel-side glue for the "Generate .kra" button.

Three export modes:

    text_warp_mode = "square"  (default)
        One group + one mask per square.  Precise per-square
        perspective; text individually rotated to read along the
        square's own longest side.  Rectangles optional.

    text_warp_mode = "patch"
        One group + one mask per PATCH.  All texts on the patch
        share the patch's mask.  Texts are placed so that after the
        patch's mask they land at exactly the screen position,
        rotation, and size they would have had in square mode.

    text_warp_mode = "text"
        One group + one mask per TEXT.  Each text's mask is a copy
        of the SQUARE'S own mask (same source rect, same destination
        quad).  In effect, each text lives alone inside a group whose
        mask projects the full flat rectangle of its square onto the
        square's projected corners — exactly the geometry that square
        mode uses, but with a text-only layer inside instead of a
        rectangle-plus-text layer.

        This is what produces correct placement, correct rotation,
        correct size, and no clipping: it reuses the geometry that
        is already known to work in square mode.

POSITION-AWARE ALIGNMENT
========================
The text's visual anchor is computed in the flat square's frame by
_flatAnchorFromVisual, which returns the CENTER of the text's visual
box for the requested position.  The alignment sent to the daemon is
chosen from that same requested position — a top-right anchor uses
right alignment, a top-left anchor uses left, a top or center anchor
uses center — so that editing a label later grows it away from the
side it is attached to, rather than half in each direction from its
center.  _anchorForPosition shifts the anchor along the reading
direction by the amount appropriate to that alignment.

Do NOT use squareFlatCorners here — that returns the unfolded-cone
footprint, which loses the perspective.
"""

KRA_JS = r"""
/* ==========================================================================
   .KRA EXPORT
   ========================================================================== */

function _hueToHex(h) {
  const to2 = (v) => Math.max(0, Math.min(255, Math.round(v)))
                        .toString(16).padStart(2, "0");
  return "#" + to2(h.r) + to2(h.g) + to2(h.b);
}

function _hueToHexLight(h) {
  const mix = (v) => Math.round(v * 0.5 + 255 * 0.5);
  const to2 = (v) => v.toString(16).padStart(2, "0");
  return "#" + to2(mix(h.r)) + to2(mix(h.g)) + to2(mix(h.b));
}

function _polygonCentroid(pts) {
  let cx = 0, cy = 0;
  for (const [x, y] of pts) { cx += x; cy += y; }
  return [cx / pts.length, cy / pts.length];
}

function _polygonArea(pts) {
  let area = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    area += a[0] * b[1] - b[0] * a[1];
  }
  return Math.abs(area) / 2;
}

function _unclippedProjectedCorners(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;
  const local = squareCornersLocalCone(sq);
  if (!local) return null;

  const vC = sq.v * f.vLen;
  const pC = shapePerspCentre(sq);
  const vMax = shapeVMax(sq);
  const phiC = _patchNormalPhi(qi);
  const slope = sq.slope || 0;

  return local.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax, phiC, slope));
}

function _longestEdgeDir(pts) {
  let bestLen = 0, bx = 0, by = 0;
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const dx = b[0] - a[0];
    const dy = b[1] - a[1];
    const len = Math.hypot(dx, dy);
    if (len > bestLen) {
      bestLen = len;
      bx = dx / len;
      by = dy / len;
    }
  }
  if (bestLen < 1e-6) return null;
  return [bx, by];
}

function _shapeLabelScreenAngleDeg(sq, screenPts) {
  if (!screenPts || screenPts.length < 3) return 0;

  const baseDir = _longestEdgeDir(screenPts);
  if (!baseDir) return 0;

  let bx = baseDir[0];
  let by = baseDir[1];
  if (bx < 0) { bx = -bx; by = -by; }

  return Math.atan2(by, bx) * 180 / Math.PI;
}

function _shapeLabelFlatRotationDeg(sq, screenPts) {
  if (!screenPts || screenPts.length < 4) return 0;

  const ux = screenPts[1][0] - screenPts[0][0];
  const uy = screenPts[1][1] - screenPts[0][1];

  const vx = screenPts[3][0] - screenPts[0][0];
  const vy = screenPts[3][1] - screenPts[0][1];

  const angleU = Math.atan2(uy, ux) * 180 / Math.PI;
  const angleV = Math.atan2(vy, vx) * 180 / Math.PI;

  const target = _shapeLabelScreenAngleDeg(sq, screenPts);

  const candidates = [
    { r:   0, projected: angleU },
    { r:  90, projected: angleV },
    { r: 180, projected: angleU + 180 },
    { r: 270, projected: angleV + 180 },
  ];

  const angDiff = (a, b) => {
    let d = a - b;
    while (d >  180) d -= 360;
    while (d < -180) d += 360;
    return Math.abs(d);
  };

  let best = candidates[0];
  let bestD = angDiff(candidates[0].projected, target);
  for (let i = 1; i < candidates.length; i++) {
    const d = angDiff(candidates[i].projected, target);
    if (d < bestD) { bestD = d; best = candidates[i]; }
  }
  return best.r;
}

function _classifyCornersInReadingFrame(visualPts, readingAngleDeg) {
  const rad = readingAngleDeg * Math.PI / 180;
  const bx = Math.cos(rad);
  const by = Math.sin(rad);
  const dx = -by;
  const dy = bx;

  const coords = [];
  for (let i = 0; i < 4; i++) {
    const px = visualPts[i][0];
    const py = visualPts[i][1];
    coords.push({
      X: px * bx + py * by,
      Y: px * dx + py * dy,
    });
  }

  let tlI = 0, trI = 0, brI = 0, blI = 0;
  let bestTL = Infinity, bestTR = -Infinity;
  let bestBR = -Infinity, bestBL = Infinity;
  for (let i = 0; i < 4; i++) {
    const X = coords[i].X, Y = coords[i].Y;
    const sTL = X + Y;
    const sTR = X - Y;
    const sBR = X + Y;
    const sBL = X - Y;

    if (sTL < bestTL) { bestTL = sTL; tlI = i; }
    if (sTR > bestTR) { bestTR = sTR; trI = i; }
    if (sBR > bestBR) { bestBR = sBR; brI = i; }
    if (sBL < bestBL) { bestBL = sBL; blI = i; }
  }

  return { tl: tlI, tr: trI, br: brI, bl: blI };
}

function _flatAnchorFromVisual(visualPts, W, H, position,
                               padFrac, fontSize, textLen,
                               readingAngleDeg, flatRotationDeg) {
  if (!position || position === "center" ||
      !visualPts || visualPts.length < 4) {
    return { x: W / 2, y: H / 2 };
  }

  const pad = padFrac * Math.min(W, H);

  const textPixelLen = textLen * fontSize * 0.55;
  const textPixelH = fontSize;
  let textHalfW, textHalfH;
  if (flatRotationDeg === 90 || flatRotationDeg === 270) {
    textHalfW = textPixelH / 2;
    textHalfH = textPixelLen / 2;
  } else {
    textHalfW = textPixelLen / 2;
    textHalfH = textPixelH / 2;
  }

  const roles = _classifyCornersInReadingFrame(visualPts, readingAngleDeg);

  const flatCorners = [[0, 0], [W, 0], [W, H], [0, H]];

  function anchorForCorner(idx) {
    const fx = flatCorners[idx][0];
    const fy = flatCorners[idx][1];
    const dirX = (fx < W / 2) ? 1 : ((fx > W / 2) ? -1 : 0);
    const dirY = (fy < H / 2) ? 1 : ((fy > H / 2) ? -1 : 0);
    return {
      x: fx + dirX * (pad + textHalfW),
      y: fy + dirY * (pad + textHalfH),
    };
  }

  const aTL = anchorForCorner(roles.tl);
  const aTR = anchorForCorner(roles.tr);
  const aBR = anchorForCorner(roles.br);
  const aBL = anchorForCorner(roles.bl);

  if (position === "tl") return aTL;
  if (position === "tr") return aTR;
  if (position === "br") return aBR;
  if (position === "bl") return aBL;
  if (position === "top") {
    return { x: (aTL.x + aTR.x) / 2, y: (aTL.y + aTR.y) / 2 };
  }
  if (position === "bottom") {
    return { x: (aBL.x + aBR.x) / 2, y: (aBL.y + aBR.y) / 2 };
  }
  if (position === "left") {
    return { x: (aTL.x + aBL.x) / 2, y: (aTL.y + aBL.y) / 2 };
  }
  if (position === "right") {
    return { x: (aTR.x + aBR.x) / 2, y: (aTR.y + aBR.y) / 2 };
  }
  return { x: W / 2, y: H / 2 };
}

/* ==========================================================================
   POSITION-AWARE ALIGNMENT
   ==========================================================================
   _flatAnchorFromVisual returns the anchor of the text's visual CENTER
   for the requested position.  The alignment the daemon writes then
   determines where, along the reading direction, that anchor sits
   relative to the text's visual box:

       left    anchor at the box's -half-length end (start)
       center  anchor at the box's middle
       right   anchor at the box's +half-length end

   So the visual box keeps the same center regardless of alignment,
   and the anchor shifts along the reading direction by:

       left    -halfLen * (cos R, sin R)
       center   0
       right   +halfLen * (cos R, sin R)

   The alignment is chosen from the requested visual position: a text
   anchored at a right-hand corner or edge uses right alignment; at a
   left-hand corner or edge uses left; at the top, bottom, or center
   uses center.  This makes editing a label grow it away from its
   anchoring side — a top-right label grows leftward and downward
   from its top-right corner, a top-left label grows rightward and
   downward from its top-left corner, and so on.

   The approximate text half-length (textLen * fontSize * 0.55 / 2) is
   the same estimate used by _flatAnchorFromVisual; it is accurate to
   a few percent of the true width for the sans-serif default and is
   only used to place the anchor, not to set the font size. */

function _alignmentForPosition(position) {
    if (position === "tr" || position === "br" || position === "right") {
        return "right";
    }
    if (position === "top" || position === "bottom" || position === "center") {
        return "center";
    }
    return "left";
}

function _anchorForPosition(anchorCenter, flatRotation, textLen, fontSize,
                            position) {
    const alignment = _alignmentForPosition(position);
    const rad = flatRotation * Math.PI / 180;
    const halfLen = (textLen * fontSize * 0.55) / 2;

    let dx = 0, dy = 0;
    if (alignment === "left") {
        dx = -halfLen * Math.cos(rad);
        dy = -halfLen * Math.sin(rad);
    } else if (alignment === "right") {
        dx = halfLen * Math.cos(rad);
        dy = halfLen * Math.sin(rad);
    }

    return {
        x: anchorCenter.x + dx,
        y: anchorCenter.y + dy,
        alignment: alignment,
    };
}

/* ==========================================================================
   SQUARE MODE
   ========================================================================== */

function buildKraShapes(options) {
  options = options || {};
  const textPos = options.text_position || "center";
  const padFrac = (typeof options.text_padding === "number")
                    ? options.text_padding : 0.06;
  const colorMode = options.text_color_mode || "color";

  const shapes = [];
  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi < 0) continue;
    const q = quads[qi];
    if (!q) continue;

    const corners = _unclippedProjectedCorners(sq);
    if (!corners || corners.length < 4) continue;

    const lens = (function () {
      const n = corners.length;
      const s = [];
      for (let k = 0; k < n; k++) {
        const a = corners[k];
        const b = corners[(k + 1) % n];
        s.push(Math.hypot(b[0] - a[0], b[1] - a[1]));
      }
      return s;
    })();
    const W = (lens[0] + lens[2]) / 2;
    const H = (lens[1] + lens[3]) / 2;
    if (W <= 0.5 || H <= 0.5) continue;

    const hue = patchHue(q);
    const hex = _hueToHex(hue);
    const hexLight = _hueToHexLight(hue);

    const textColor = (colorMode === "black") ? "#000000" : hexLight;

    const screenCorners = squareCornersScreen(sq) || corners;
    const flatRotation = _shapeLabelFlatRotationDeg(sq, screenCorners);
    const readingAngle  = _shapeLabelScreenAngleDeg(sq, screenCorners);

    const name = squareDisplayName(sq);
    const fontNat = Math.min(W, H) * 0.40;

    const anchorCenter = _flatAnchorFromVisual(
      screenCorners, W, H, textPos, padFrac, fontNat, name.length,
      readingAngle, flatRotation
    );
    const anchor = _anchorForPosition(
      anchorCenter, flatRotation, name.length, fontNat, textPos
    );

    shapes.push({
      name:            name,
      points:          corners.map(([x, y]) => [x, y]),
      natural_w:       W,
      natural_h:       H,
      flat_rotation:   flatRotation,
      text_anchor:     anchor,
      fill:            hex,
      fill_opacity:    0.40,
      stroke:          hex,
      stroke_width:    2.0,
      stroke_opacity:  1.0,
      text_color:      textColor,
    });
  }
  return shapes;
}

/* ==========================================================================
   HOMOGRAPHY HELPERS
   ========================================================================== */

function _computeHomography(src, dst) {
  const A = [];
  const b = [];
  for (let i = 0; i < 4; i++) {
    const sx = src[i].x, sy = src[i].y;
    const dx = dst[i].x, dy = dst[i].y;
    A.push([sx, sy, 1, 0, 0, 0, -sx * dx, -sy * dx]);
    b.push(dx);
    A.push([0, 0, 0, sx, sy, 1, -sx * dy, -sy * dy]);
    b.push(dy);
  }
  const n = 8;
  for (let i = 0; i < n; i++) {
    let piv = i;
    for (let j = i + 1; j < n; j++) {
      if (Math.abs(A[j][i]) > Math.abs(A[piv][i])) piv = j;
    }
    const Atmp = A[i]; A[i] = A[piv]; A[piv] = Atmp;
    const btmp = b[i]; b[i] = b[piv]; b[piv] = btmp;
    for (let j = i + 1; j < n; j++) {
      const f = A[j][i] / A[i][i];
      for (let k = i; k < n; k++) A[j][k] -= f * A[i][k];
      b[j] -= f * b[i];
    }
  }
  const h = new Array(8).fill(0);
  for (let i = n - 1; i >= 0; i--) {
    let s = b[i];
    for (let k = i + 1; k < n; k++) s -= A[i][k] * h[k];
    h[i] = s / A[i][i];
  }
  return [
    [h[0], h[1], h[2]],
    [h[3], h[4], h[5]],
    [h[6], h[7], 1.0],
  ];
}

function _invert3x3(M) {
  const a = M[0][0], b = M[0][1], c = M[0][2];
  const d = M[1][0], e = M[1][1], f = M[1][2];
  const g = M[2][0], h = M[2][1], i = M[2][2];
  const A =  (e * i - f * h);
  const B = -(d * i - f * g);
  const C =  (d * h - e * g);
  const det = a * A + b * B + c * C;
  if (Math.abs(det) < 1e-15) return null;
  const inv = 1 / det;
  return [
    [A * inv,        (c * h - b * i) * inv, (b * f - c * e) * inv],
    [B * inv,        (a * i - c * g) * inv, (c * d - a * f) * inv],
    [C * inv,        (b * g - a * h) * inv, (a * e - b * d) * inv],
  ];
}

function _applyHomography(M, x, y) {
  const w = M[2][0] * x + M[2][1] * y + M[2][2];
  if (Math.abs(w) < 1e-15) return null;
  return {
    x: (M[0][0] * x + M[0][1] * y + M[0][2]) / w,
    y: (M[1][0] * x + M[1][1] * y + M[1][2]) / w,
  };
}

function _patchProjectiveJacobian(H, fx, fy) {
  const w = H[2][0] * fx + H[2][1] * fy + H[2][2];
  if (Math.abs(w) < 1e-12) return null;
  const x = (H[0][0] * fx + H[0][1] * fy + H[0][2]) / w;
  const y = (H[1][0] * fx + H[1][1] * fy + H[1][2]) / w;
  const invW = 1 / w;
  return {
    J11: (H[0][0] - x * H[2][0]) * invW,
    J21: (H[1][0] - y * H[2][0]) * invW,
    J12: (H[0][1] - x * H[2][1]) * invW,
    J22: (H[1][1] - y * H[2][1]) * invW,
  };
}

function _invert2x2(J) {
  const det = J.J11 * J.J22 - J.J12 * J.J21;
  if (Math.abs(det) < 1e-12) return null;
  return {
    i11:  J.J22 / det,
    i12: -J.J12 / det,
    i21: -J.J21 / det,
    i22:  J.J11 / det,
  };
}

function _squareCornersInPatchUV(sq) {
  const refHW = SHAPE_REL_SIZE / 2 * sq.scaleU;
  const refHH = SHAPE_REL_SIZE / 2 * sq.scaleV;
  const cosT  = Math.cos(sq.theta || 0);
  const sinT  = Math.sin(sq.theta || 0);
  const raw = [
    [-refHW, +refHH],
    [+refHW, +refHH],
    [+refHW, -refHH],
    [-refHW, -refHH],
  ];
  return raw.map(([du, dv]) => {
    const duR = du * cosT - dv * sinT;
    const dvR = du * sinT + dv * cosT;
    return {
      u: sq.u + duR / SHAPE_U_REF,
      v: sq.v + dvR / SHAPE_V_REF,
    };
  });
}

function _bilinearInterpQuad(corners, u, v) {
  const TL = corners[0], TR = corners[1], BR = corners[2], BL = corners[3];
  const w00 = (1 - u) * (1 - v);
  const w10 = u * (1 - v);
  const w11 = u * v;
  const w01 = (1 - u) * v;
  return {
    u: w00 * TL.u + w10 * TR.u + w11 * BR.u + w01 * BL.u,
    v: w00 * TL.v + w10 * TR.v + w11 * BR.v + w01 * BL.v,
  };
}

/* ==========================================================================
   PATCH MODE
   ========================================================================== */

function _projectedPatchCorners(q) {
  const corners = quadCorners(q);
  return corners.map(c => {
    const [wx, wy] = surfacePoint(c.phi, c.s);
    return w2s(wx, wy);
  });
}

function _patchFlatDimensions(projCorners) {
  const d01 = Math.hypot(projCorners[1][0] - projCorners[0][0],
                          projCorners[1][1] - projCorners[0][1]);
  const d32 = Math.hypot(projCorners[2][0] - projCorners[3][0],
                          projCorners[2][1] - projCorners[3][1]);
  const d03 = Math.hypot(projCorners[3][0] - projCorners[0][0],
                          projCorners[3][1] - projCorners[0][1]);
  const d12 = Math.hypot(projCorners[2][0] - projCorners[1][0],
                          projCorners[2][1] - projCorners[1][1]);
  return {
    w: (d01 + d32) / 2,
    h: (d03 + d12) / 2,
  };
}

/* ==========================================================================
   PATCH MODE — one group + one mask per patch
   ==========================================================================
   One Krita group per patch, one transform mask per group.  All the
   texts on that patch live inside the group.  The mask maps an
   extended flat rectangle to the patch's projected quad, so the
   texts are warped by the patch's own perspective.

   Position: for each text we compute the exact desired screen anchor
   using the same logic as square and text mode, then invert the
   patch's homography at that screen point to get the flat patch
   coordinate.  The patch mask maps that flat coordinate back to the
   desired screen point, so the visual result is identical to text
   mode.

   Size: the mask's local linear scale at the text's flat position is
   sqrt(|det J|), where J is the projective Jacobian of the patch's
   homography.  To render at a desired screen size, the flat font
   size is divided by that scale.

   Rotation: for a desired screen reading direction (cos T, sin T),
   the flat reading direction must be J^-1 * (cos T, sin T), so the
   flat rotation is atan2 of that.

   Source rect: the natural patch rectangle is [0, W] x [0, H], but a
   text whose desired screen point lies outside the projected patch
   quad has its flat coordinate outside that rectangle.  Krita's
   transform mask only warps content inside its source rect, so the
   source is extended to cover every text's flat extent.  The
   destination corners are computed by the same homography, so the
   mapping is unchanged where the original rect was; it just covers
   more area. */

function buildKraPatches(options) {
  options = options || {};
  const colorMode = options.text_color_mode || "color";
  const textPos = options.text_position || "center";
  const padFrac = (typeof options.text_padding === "number")
                    ? options.text_padding : 0.06;
  const drawRects = options.draw_rectangles !== false;

  const patches = [];
  for (let qi = 0; qi < quads.length; qi++) {
    const q = quads[qi];

    const sqsOnPatch = [];
    for (let i = 0; i < floatSquares.length; i++) {
      if (floatSquares[i].quadId === q.id) {
        sqsOnPatch.push(floatSquares[i]);
      }
    }
    if (sqsOnPatch.length === 0) continue;

    const projCorners = _projectedPatchCorners(q);
    const dims = _patchFlatDimensions(projCorners);
    if (dims.w <= 0.5 || dims.h <= 0.5) continue;

    const patchH = _computeHomography(
      [{x: 0, y: 0}, {x: dims.w, y: 0},
       {x: dims.w, y: dims.h}, {x: 0, y: dims.h}],
      [{x: projCorners[0][0], y: projCorners[0][1]},
       {x: projCorners[1][0], y: projCorners[1][1]},
       {x: projCorners[2][0], y: projCorners[2][1]},
       {x: projCorners[3][0], y: projCorners[3][1]}]
    );
    const patchHinv = _invert3x3(patchH);
    if (!patchHinv) continue;

    const hue = patchHue(q);
    const hex = _hueToHex(hue);
    const hexLight = _hueToHexLight(hue);
    const textColor = (colorMode === "black") ? "#000000" : hexLight;

    const rects = [];
    const pending = [];

    /* Start the source bbox from the natural patch rectangle so the
       patch's own outline is covered even if a text ends up placed
       close to a corner.  Extend from there as we place texts. */
    let bbMinX = 0, bbMinY = 0, bbMaxX = dims.w, bbMaxY = dims.h;

    for (const sq of sqsOnPatch) {
      const unclipped = _unclippedProjectedCorners(sq);
      if (!unclipped || unclipped.length < 4) continue;
      const screenCorners = squareCornersScreen(sq) || unclipped;

      const lens = (function () {
        const n = unclipped.length;
        const s = [];
        for (let k = 0; k < n; k++) {
          const a = unclipped[k];
          const b = unclipped[(k + 1) % n];
          s.push(Math.hypot(b[0] - a[0], b[1] - a[1]));
        }
        return s;
      })();
      const W_sq = (lens[0] + lens[2]) / 2;
      const H_sq = (lens[1] + lens[3]) / 2;
      if (W_sq <= 0.5 || H_sq <= 0.5) continue;

      const name = squareDisplayName(sq);

      if (drawRects) {
        rects.push({
          name: name,
          points: screenCorners.map(([x, y]) => [x, y]),
          fill: hex,
          fill_opacity: 0.40,
          stroke: hex,
          stroke_width: 2.0,
          stroke_opacity: 1.0,
        });
      }

      /* Desired screen anchor: identical to square mode's. */
      const flatRotSq = _shapeLabelFlatRotationDeg(sq, screenCorners);
      const readAngSq = _shapeLabelScreenAngleDeg(sq, screenCorners);
      const fontNat = Math.min(W_sq, H_sq) * 0.40;

      const anchorCenterFlatSq = _flatAnchorFromVisual(
        screenCorners, W_sq, H_sq, textPos, padFrac,
        fontNat, name.length, readAngSq, flatRotSq
      );
      const anchorFlatSq = _anchorForPosition(
        anchorCenterFlatSq, flatRotSq, name.length, fontNat, textPos
      );

      /* Square homography: flat square rectangle -> the square's
         unclipped projected quad.  The same construction the square
         and text modes use. */
      const sqH = _computeHomography(
        [{x: 0, y: 0}, {x: W_sq, y: 0},
         {x: W_sq, y: H_sq}, {x: 0, y: H_sq}],
        [{x: unclipped[0][0], y: unclipped[0][1]},
         {x: unclipped[1][0], y: unclipped[1][1]},
         {x: unclipped[2][0], y: unclipped[2][1]},
         {x: unclipped[3][0], y: unclipped[3][1]}]
      );

      /* Where the anchor should land on screen. */
      const desiredScreen = _applyHomography(
        sqH, anchorFlatSq.x, anchorFlatSq.y
      );
      if (!desiredScreen ||
          !isFinite(desiredScreen.x) || !isFinite(desiredScreen.y)) continue;

      /* Flat patch position that the patch mask maps to desiredScreen. */
      const flatPatch = _applyHomography(
        patchHinv, desiredScreen.x, desiredScreen.y
      );
      if (!flatPatch ||
          !isFinite(flatPatch.x) || !isFinite(flatPatch.y)) continue;

      const px = flatPatch.x;
      const py = flatPatch.y;

      /* Local Jacobian of the patch's homography, at the text's
         position.  Used for size and rotation. */
      const J = _patchProjectiveJacobian(patchH, px, py);
      if (!J) continue;

      const det = J.J11 * J.J22 - J.J12 * J.J21;
      let localScale = Math.sqrt(Math.abs(det));
      if (localScale < 1e-3) localScale = 1.0;

      /* Flat font size so the on-screen size matches fontNat. */
      const font_px = Math.max(6, fontNat / localScale);

      /* Flat rotation so the on-screen reading direction matches
         readAngSq. */
      let flatRotation = 0;
      const Jinv = _invert2x2(J);
      if (Jinv) {
        const rad = readAngSq * Math.PI / 180;
        const cosT = Math.cos(rad);
        const sinT = Math.sin(rad);
        const flatDx = Jinv.i11 * cosT + Jinv.i12 * sinT;
        const flatDy = Jinv.i21 * cosT + Jinv.i22 * sinT;
        if (Math.abs(flatDx) > 1e-12 || Math.abs(flatDy) > 1e-12) {
          flatRotation = Math.atan2(flatDy, flatDx) * 180 / Math.PI;
        }
      }

      pending.push({
        name: name,
        px: px,
        py: py,
        font_px: font_px,
        rotation: flatRotation,
        alignment: anchorFlatSq.alignment,
      });

      /* Extend the source bbox by the text's flat extent.  The text
         extends from (px, py) along the reading direction by an
         amount that depends on its alignment, with a perpendicular
         half-height of font_px/2. */
      const rad = flatRotation * Math.PI / 180;
      const cosR = Math.cos(rad);
      const sinR = Math.sin(rad);
      const h2 = font_px / 2;
      const textLenPx = name.length * font_px * 0.55;

      let extStart, extEnd;
      if (anchorFlatSq.alignment === "right") {
        extStart = textLenPx; extEnd = 0;
      } else if (anchorFlatSq.alignment === "center") {
        extStart = textLenPx / 2; extEnd = textLenPx / 2;
      } else {
        extStart = 0; extEnd = textLenPx;
      }

      const localCorners = [
        [-extStart, -h2], [extEnd, -h2],
        [ extEnd,   h2], [-extStart, h2],
      ];
      for (const [lx, ly] of localCorners) {
        const rx = lx * cosR - ly * sinR;
        const ry = lx * sinR + ly * cosR;
        bbMinX = Math.min(bbMinX, px + rx);
        bbMaxX = Math.max(bbMaxX, px + rx);
        bbMinY = Math.min(bbMinY, py + ry);
        bbMaxY = Math.max(bbMaxY, py + ry);
      }
    }

    if (pending.length === 0) continue;

    /* A small margin so nothing sits right at the edge of the source. */
    const MARGIN = 4;
    bbMinX -= MARGIN;
    bbMinY -= MARGIN;
    bbMaxX += MARGIN;
    bbMaxY += MARGIN;

    /* Shift everything to non-negative coordinates.  Krita's vector
       engine is happier without negative shape positions, and the
       shift is transparent: it changes only where inside the group
       the content lives, not where it renders after the mask. */
    const offX = bbMinX;
    const offY = bbMinY;
    const Wshift = bbMaxX - bbMinX;
    const Hshift = bbMaxY - bbMinY;

    const texts = [];
    for (const p of pending) {
      texts.push({
        text: p.name,
        x: p.px - offX,
        y: p.py - offY,
        font_px: p.font_px,
        flat_rotation: p.rotation,
        alignment: p.alignment,
        color: textColor,
      });
    }

    /* Mask source: the shifted bbox.  Destination: the same corners
       through the patch's homography at their UNSHIFTED positions. */
    const srcPts = [
      [0, 0], [Wshift, 0], [Wshift, Hshift], [0, Hshift],
    ];
    const unshiftedCorners = [
      [bbMinX, bbMinY], [bbMaxX, bbMinY],
      [bbMaxX, bbMaxY], [bbMinX, bbMaxY],
    ];
    const dstPts = unshiftedCorners.map(([fx, fy]) => {
      const p = _applyHomography(patchH, fx, fy);
      return p ? [p.x, p.y] : [0, 0];
    });

    patches.push({
      name: q.name,
      src_pts: srcPts,
      dst_pts: dstPts,
      texts: texts,
      rects: rects,
    });
  }
  return patches;
}

/* ==========================================================================
   TEXT MODE — one group + one mask per text, mask = square mask
   ==========================================================================
   Each text is placed inside its own group.  The group's mask is a
   copy of the geometry that SQUARE MODE uses: source = [0, W]x[0, H]
   of the square's flat rectangle; destination = the square's four
   projected corners.  Since that geometry is known to place text
   correctly, and since each text sits inside a group whose only
   content is that text, the result is a correct-per-text export
   with one mask per text and no clipping.

   Rectangles, when enabled, are returned in a parallel `rects`
   array as direct polygons with the square's projected corners. */
function buildKraTexts(options) {
  options = options || {};
  const colorMode = options.text_color_mode || "color";
  const textPos   = options.text_position || "center";
  const padFrac   = (typeof options.text_padding === "number")
                      ? options.text_padding : 0.06;
  const drawRects = options.draw_rectangles !== false;

  const items = [];
  const rects = [];

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    const qi = quadIdxById(sq.quadId);
    if (qi < 0) continue;
    const q = quads[qi];
    if (!q) continue;

    const unclipped = _unclippedProjectedCorners(sq);
    if (!unclipped || unclipped.length < 4) continue;

    const screenCorners = squareCornersScreen(sq) || unclipped;

    const lens = (function () {
      const n = unclipped.length;
      const s = [];
      for (let k = 0; k < n; k++) {
        const a = unclipped[k];
        const b = unclipped[(k + 1) % n];
        s.push(Math.hypot(b[0] - a[0], b[1] - a[1]));
      }
      return s;
    })();
    const W_sq = (lens[0] + lens[2]) / 2;
    const H_sq = (lens[1] + lens[3]) / 2;
    if (W_sq <= 0.5 || H_sq <= 0.5) continue;

    const hue = patchHue(q);
    const hex = _hueToHex(hue);
    const hexLight = _hueToHexLight(hue);
    const textColor = (colorMode === "black") ? "#000000" : hexLight;

    const name = squareDisplayName(sq);

    if (drawRects) {
      rects.push({
        name: name,
        points: screenCorners.map(([x, y]) => [x, y]),
        fill: hex,
        fill_opacity: 0.40,
        stroke: hex,
        stroke_width: 2.0,
        stroke_opacity: 1.0,
      });
    }

    /* Same anchor logic as square mode.  The mask uses the FULL
       square flat rectangle as its source, exactly like square mode,
       so the text lands where square mode puts it. */
    const flatRotation = _shapeLabelFlatRotationDeg(sq, screenCorners);
    const readingAngle = _shapeLabelScreenAngleDeg(sq, screenCorners);
    const fontNat = Math.min(W_sq, H_sq) * 0.40;

    const anchorCenter = _flatAnchorFromVisual(
      screenCorners, W_sq, H_sq, textPos, padFrac,
      fontNat, name.length,
      readingAngle, flatRotation
    );
    const anchor = _anchorForPosition(
      anchorCenter, flatRotation, name.length, fontNat, textPos
    );

    const srcPts = [
      [0, 0],
      [W_sq, 0],
      [W_sq, H_sq],
      [0, H_sq],
    ];
    const dstPts = unclipped.map(([x, y]) => [x, y]);

    items.push({
      name: name,
      src_w: W_sq,
      src_h: H_sq,
      text_x: anchor.x,
      text_y: anchor.y,
      font_px: Math.max(6, fontNat),
      rotation: flatRotation,
      alignment: anchor.alignment,
      color: textColor,
      src_pts: srcPts,
      dst_pts: dstPts,
    });
  }
  return { items: items, rects: rects };
}

/* ==========================================================================
   POINT OF VIEW
   ========================================================================== */

function _coneBandDimensions() {
  const w = Math.max(1, Math.round(window.innerWidth));
  const h = Math.max(1, Math.round(layout.coneH));
  return { width: w, height: h };
}

/* ==========================================================================
   META-OPTIONS
   ========================================================================== */

function readKraOptionsFromDOM() {
  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");
  const colEl  = document.getElementById("kraTextColor");
  const wrpEl  = document.getElementById("kraTextWarp");

  const text_position = posEl ? posEl.value : "center";

  let text_padding = padEl ? parseFloat(padEl.value) : 0.06;
  if (!isFinite(text_padding) || text_padding < 0) text_padding = 0.06;

  const draw_rectangles = rectEl ? !!rectEl.checked : true;

  let text_color_mode = colEl ? colEl.value : "color";
  if (text_color_mode !== "color" && text_color_mode !== "black") {
    text_color_mode = "color";
  }

  let text_warp_mode = wrpEl ? wrpEl.value : "square";
  if (text_warp_mode !== "square" &&
      text_warp_mode !== "patch" &&
      text_warp_mode !== "text") {
    text_warp_mode = "square";
  }

  return {
    text_position:   text_position,
    text_padding:    text_padding,
    draw_rectangles: draw_rectangles,
    text_color_mode: text_color_mode,
    text_warp_mode:  text_warp_mode,
  };
}

function applyKraOptionsToDOM(opts) {
  if (!opts || typeof opts !== "object") return;

  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");
  const colEl  = document.getElementById("kraTextColor");
  const wrpEl  = document.getElementById("kraTextWarp");

  if (posEl && typeof opts.text_position === "string") {
    posEl.value = opts.text_position;
  }
  if (padEl && typeof opts.text_padding === "number") {
    padEl.value = String(opts.text_padding);
  }
  if (rectEl && typeof opts.draw_rectangles === "boolean") {
    rectEl.checked = opts.draw_rectangles;
  }
  if (colEl && typeof opts.text_color_mode === "string") {
    colEl.value = opts.text_color_mode;
  }
  if (wrpEl && typeof opts.text_warp_mode === "string") {
    wrpEl.value = opts.text_warp_mode;
  }
}

async function generateKraFromScene() {
  if (floatSquares.length === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("No shapes to export", "warn");
    }
    return;
  }

  const options = readKraOptionsFromDOM();
  const coneBand = _coneBandDimensions();

  const body = {
    doc_name:    "Cone Scene",
    output_path: null,
    options:     options,
    cone_band:   coneBand,
  };

  if (options.text_warp_mode === "text") {
    const result = buildKraTexts(options);
    if (result.items.length === 0) {
      if (typeof flashStatus === "function") {
        flashStatus("No exportable texts", "warn");
      }
      return;
    }
    body.texts = result.items;
    body.rects = result.rects;
  } else if (options.text_warp_mode === "patch") {
    const patches = buildKraPatches(options);
    if (patches.length === 0) {
      if (typeof flashStatus === "function") {
        flashStatus("No exportable patches", "warn");
      }
      return;
    }
    body.patches = patches;
  } else {
    const shapes = buildKraShapes(options);
    if (shapes.length === 0) {
      if (typeof flashStatus === "function") {
        flashStatus("No exportable shapes", "warn");
      }
      return;
    }
    body.shapes = shapes;
  }

  const defaultPath = "/tmp/cone_scene.kra";
  const outputPath = window.prompt("Output .kra path:", defaultPath);
  if (!outputPath) return;
  body.output_path = outputPath;

  const count = (body.texts   ? body.texts.length
               : body.patches ? body.patches.length
                              : body.shapes.length);
  const kind  = (body.texts   ? "text(s)"
               : body.patches ? "patch(es)"
                              : "shape(s)");

  if (typeof flashStatus === "function") {
    flashStatus("Generating .kra (" + count + " " + kind + ")...", "");
  }

  try {
    const response = await fetch("/generate-kra", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const result = await response.json();
    if (result && result.success) {
      if (typeof flashStatus === "function") {
        flashStatus(result.message || "Done", "ok");
      }
    } else {
      const msg = (result && result.message) ? result.message : "unknown error";
      if (typeof flashStatus === "function") {
        flashStatus("Failed: " + msg, "bad");
      }
    }
  } catch (e) {
    if (typeof flashStatus === "function") {
      flashStatus("Request failed: " + e.message, "bad");
    }
  }
}
"""
