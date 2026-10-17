"""
pgkit.math — common JavaScript math helpers.

These are the small functions that showed up identically in both
projects' `pg_kra.py` files: colour conversion, affine
decomposition, homography construction and application, and a
handful of polygon predicates.  Moving them here means a bug fix
in any one of them reaches every project.

Everything here is client-side JS emitted as a Python string.  The
functions carry no state and depend on nothing in the project —
they are pure helpers.
"""

MATH_JS = r"""
/* ==========================================================================
   COLOR
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

/* ==========================================================================
   AFFINE DECOMPOSITION
   ==========================================================================
   Any invertible 2x2 matrix M can be written uniquely as

       M = R(theta) * Shx(k) * S(sx, sy)

   where R is a rotation, Shx is a horizontal shear (x gains k*y
   per unit y), and S is a diagonal scale.  Solving the components:

       sx        = sqrt(M00^2 + M10^2)
       sy        = det(M) / sx
       theta_rad = atan2(M10, M00)
       k         = (M01*M00 + M11*M10) / det

   with M in column-vector form M = [[M00, M01], [M10, M11]].
   Returns null for a degenerate matrix. */

function _decomposeAffineLocal(M00, M01, M10, M11) {
  const det = M00 * M11 - M01 * M10;
  const sxRaw = Math.sqrt(M00 * M00 + M10 * M10);
  if (sxRaw < 1e-9 || Math.abs(det) < 1e-9) return null;

  const syRaw = det / sxRaw;
  const rotationRad = Math.atan2(M10, M00);
  const shear = (M01 * M00 + M11 * M10) / det;

  return {
    rotation_rad: rotationRad,
    rotation_deg: rotationRad * 180 / Math.PI,
    shear: shear,
    scale_x: sxRaw,
    scale_y: syRaw,
    det: det,
  };
}

/* ==========================================================================
   HOMOGRAPHY
   ==========================================================================
   `_computeHomography(src, dst)` takes four {x, y} pairs on each
   side and returns the 3x3 matrix that maps src[i] to dst[i].  The
   solver is a straightforward Gaussian elimination on the 8x8
   linear system the four correspondences produce. */

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

/* ==========================================================================
   POLYGONS
   ========================================================================== */

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

/* Direction of the polygon's bottom edge as it appears on screen:
   the edge whose midpoint has the largest y, returned as a unit
   vector from the edge's left endpoint to its right endpoint, so
   bx >= 0. */
function _bottomEdgeDir(pts) {
  if (!pts || pts.length < 3) return null;
  const n = pts.length;
  let bestMidY = -Infinity;
  let bestI = -1;
  for (let i = 0; i < n; i++) {
    const a = pts[i];
    const b = pts[(i + 1) % n];
    const midY = (a[1] + b[1]) / 2;
    if (midY > bestMidY) {
      bestMidY = midY;
      bestI = i;
    }
  }
  if (bestI < 0) return null;
  let a = pts[bestI];
  let b = pts[(bestI + 1) % n];
  if (a[0] > b[0]) { const t = a; a = b; b = t; }
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return null;
  return [dx / len, dy / len];
}
"""
