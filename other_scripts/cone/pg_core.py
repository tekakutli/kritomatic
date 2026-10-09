"""
pg_core.py — canvas, layout, view, cone state, patch model.

Two coordinate systems and one surface parameterisation:

    view      orthographic world → screen, y flipped
    (phi, s)  the cone's lateral surface: phi angular, s axial
              s = 0 at the base ring, s = 1 at the apex

A patch is four numbers — [phi0, phi1] × [s0, s1] — a rectangle in
parameter space.  quadCorners is the single reader; every renderer
and hit test asks through it, so corner order is defined once.

A floating square is { quadId, u, v, scaleU, scaleV, theta, slope }
in its patch's local frame; the frame derivation lives in
pg_view_squares.  `slope` is a small pseudo-3D tilt out of the
patch plane; zero leaves the shape flat on the patch.

PATCH SHAPE EDITING
===================
Patches are the planes squares live on, not shapes to be edited.  The
drag paths that would let a corner, edge, or body of a patch be
moved — flatMoveCorner / flatMoveEdge / flatMoveBody and the cone-
side corner drag in pg_dispatch — all still exist in the codebase
and are fully functional; they are simply gated behind one flag.  Set
it to true to re-enable patch reshaping.  Everything else about the
patches is unaffected: they still respond to the apex and the depth,
and shift+click still drops a square on one.

DRAW TOOL
=========
The `drawTool` state and the two `_fitPatchAndShape_*` functions
implement the "Draw Quad" workflow: the user clicks four corners
anywhere on the canvas (all four must be in the same band — cone or
flat), and the tool fits a patch and a rectangle to them.

Two fit modes, chosen by the `#drawFitMode` select in the panel and
read at finish time (so it can be changed mid-draw):

    "shape-first"   (default)  The shape is fitted by least squares
                    to the four clicks mapped into a preliminary
                    patch's local (u, v) frame.  Then a patch of
                    the SAME SIZE as the one produced by the
                    "+ Patch" button — DEFAULT_PATCH_PHI_SPAN ×
                    DEFAULT_PATCH_S_SPAN — is created, centred on
                    the shape's (φ, s) footprint.  The shape is
                    re-fitted in the new patch's frame so its
                    footprint is preserved.

    "patch-first"   The four clicks are projected to (φ, s); their
                    centroid defines the patch's centre.  A patch
                    of the SAME SIZE as the one produced by the
                    "+ Patch" button — DEFAULT_PATCH_PHI_SPAN ×
                    DEFAULT_PATCH_S_SPAN — is created, centred on
                    that centroid.  The shape is fitted by least
                    squares to the clicks mapped into that patch's
                    local (u, v) frame.  The patch is not shrunk
                    to the clicks' bounding box; the plane the
                    patch sits on is the one defined by the
                    clicks' centre, with the patch's default
                    extent around it.

A third draw option, the panel's "No rotate" checkbox (state read at
finish time, like the select), forces the fitted shape's rotation to
zero.  When the checkbox is off (the default), the fit is free to
rotate; when on, the shape's edges align with the flat view's φ and
s axes — the shape reads as north/south aligned on the unfolded
sheet.  The flag applies to both fit modes.

Neither mode reproduces the drawn quad exactly.  The model has no
free quadrilateral primitive — only patches (rectangles in (φ, s))
and shapes (rotated rectangles in each patch's local (u, v)).  Both
modes find the closest pair of those primitives that the model can
store.

Both fits are implemented with the same least-squares solver, which
for four local (u, v) points and a given assignment to the reference
rectangle's TL/TR/BR/BL slots recovers the centre, rotation, and two
reference half-extents in closed form.  Both modes try all 24
assignments and keep the one with the smallest residual.  When the
"No rotate" flag is on, the solver swaps in a degenerate case of the
same math: rotation is pinned to zero, and the two reference half-
extents are the signed projections of the points onto the axes.
"""

CORE_JS = r"""
/* ==========================================================================
   CANVAS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx    = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

/* ==========================================================================
   LAYOUT
   ========================================================================== */

const layout = {
  coneH:     0,
  dividerY:  0,
  reservedY: 0,
  reservedH: 0,
};

/* ==========================================================================
   VIEW
   ========================================================================== */

const view = {
  scale: 20, tx: 0, ty: 0,
  zoom: 1, panX: 0, panY: 0,
};

function w2s(x, y) { return [x * view.scale + view.tx,
                             -y * view.scale + view.ty]; }
function s2w(sx, sy) { return [(sx - view.tx) / view.scale,
                               -(sy - view.ty) / view.scale]; }

/* ==========================================================================
   CONE
   ========================================================================== */

const cone = {
  ax: 0, ay: 0,
  depth: 15,
  halfAngle: Math.PI / 6,
  ringCount: 14,
  meridianCount: 24,
};

function clampApex() {
  const R = cone.depth * Math.tan(cone.halfAngle);
  const maxR = R * 0.98;
  const dr = Math.hypot(cone.ax, cone.ay);
  if (dr > maxR && dr > 1e-9) {
    const s = maxR / dr;
    cone.ax *= s;
    cone.ay *= s;
  }
}

function coneR() { return cone.depth * Math.tan(cone.halfAngle); }

function surfacePoint(phi, s) {
  const R = coneR();
  const r = (1 - s) * R;
  return [s * cone.ax + r * Math.cos(phi),
          s * cone.ay + r * Math.sin(phi)];
}

/* ==========================================================================
   PATCH MODEL
   ==========================================================================
   Every patch:  [phi0, phi1] × [s0, s1].  Corner order:

       0   (phi0, s1)   top-left    near apex
       1   (phi1, s1)   top-right   near apex
       2   (phi1, s0)   bottom-right near base
       3   (phi0, s0)   bottom-left  near base

   The lateral edges lie on meridians; every meridian ends at the
   apex, so both lateral edges point at the peak.  S_MAX < 1 keeps a
   patch from collapsing into the apex. */

const S_MIN = 0.00;
const S_MAX = 0.90;

/* Default patch dimensions.  These are the parameters the "+ Patch"
   button uses when it creates a patch, and the parameters both draw
   modes use when they size the patch they create.  The three are
   kept in one place so they cannot drift apart. */

const DEFAULT_PATCH_PHI_SPAN = 0.72;
const DEFAULT_PATCH_S_SPAN   = 0.50;
const DEFAULT_PATCH_S_CENTER = 0.47;

/* Shape editing of patches is locked away.  Set true to re-enable
   corner / edge / body dragging on patches in either band; the
   underlying drag math is unaffected by this flag and would take
   over on the very next click. */
const PATCH_SHAPE_EDIT_ENABLED = false;

const quads = [];
let nextQuadId = 1;
let selectedQuad = -1;

function quadCorners(q) {
  return [
    { phi: q.phi0, s: q.s1 },
    { phi: q.phi1, s: q.s1 },
    { phi: q.phi1, s: q.s0 },
    { phi: q.phi0, s: q.s0 },
  ];
}

function addQuad() {
  const id  = nextQuadId++;
  const idx = quads.length;
  const halfPhi = DEFAULT_PATCH_PHI_SPAN / 2;
  const halfS   = DEFAULT_PATCH_S_SPAN   / 2;

  let phiCenter = Math.PI / 2 + idx * 0.70;
  phiCenter = ((phiCenter % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
  if (phiCenter - halfPhi < 0)                phiCenter = halfPhi;
  if (phiCenter + halfPhi > 2 * Math.PI)      phiCenter = 2 * Math.PI - halfPhi;

  quads.push({
    id, name: "Q" + id,
    phi0: phiCenter - halfPhi,
    phi1: phiCenter + halfPhi,
    s0: DEFAULT_PATCH_S_CENTER - halfS,
    s1: DEFAULT_PATCH_S_CENTER + halfS,
    mirror: false,
  });
  selectedQuad = quads.length - 1;
  syncQuadList();
  draw();
}

/* Duplicate a patch, together with every square that lives on it.
   The clone gets a fresh id and default name; its squares likewise.
   The clone is placed one patch-width to the right in φ so it does
   not sit on top of the source.  φ wraps harmlessly, so pushing past
   2π is fine.  Square (u, v, scaleU, scaleV, θ, slope) are copied
   as-is, so the clone's squares sit in the same spot on the new
   patch as they did on the old. */
function cloneQuad(idx) {
  if (idx < 0 || idx >= quads.length) return;
  const src = quads[idx];
  const id  = nextQuadId++;
  const dPhi = src.phi1 - src.phi0;

  quads.push({
    id,
    name: "Q" + id,
    phi0: src.phi0 + dPhi,
    phi1: src.phi1 + dPhi,
    s0:   src.s0,
    s1:   src.s1,
    mirror: !!src.mirror,
  });

  for (let i = 0; i < floatSquares.length; i++) {
    const sq = floatSquares[i];
    if (sq.quadId !== src.id) continue;
    const newSqId = nextSquareId++;
    floatSquares.push({
      id:     newSqId,
      name:   "S" + newSqId,
      quadId: id,
      u:      sq.u,
      v:      sq.v,
      scaleU: sq.scaleU,
      scaleV: sq.scaleV,
      theta:  sq.theta || 0,
      slope:  sq.slope || 0,
    });
  }

  selectedQuad   = quads.length - 1;
  selectedSquare = -1;
  syncQuadList();
  draw();
}

function deleteQuad(idx) {
  if (idx < 0 || idx >= quads.length) return;
  const removed = quads.splice(idx, 1)[0];
  if (typeof deleteSquaresForQuad === "function") {
    deleteSquaresForQuad(removed.id);
  }
  if (selectedQuad >= quads.length) selectedQuad = quads.length - 1;
  syncQuadList();
  draw();
}

/* ==========================================================================
   INVERSE PROJECTION — cursor world point → (phi, s) on the surface
   ==========================================================================
   For fixed s, the closest surface point to T is on the ring at that
   s, with squared distance expressed as a quadratic in s:

       A·s² + B·s + C = 0
       A = |apex|² − R²
       B = −2 · (apex·T − R²)
       C = |T|² − R²

   Two real roots: the one nearer the caller's current s wins, so a
   drag stays on one branch of the surface.  No real root: forty-step
   sample over [S_MIN, S_MAX]. */
function projectToConeSurface(wx, wy, currentS) {
  const Px = cone.ax, Py = cone.ay;
  const R  = coneR();
  const R2 = R * R;
  const p2 = Px * Px + Py * Py;
  const t2 = wx * wx + wy * wy;
  const pt = Px * wx + Py * wy;

  const A = p2 - R2;
  const B = -2 * (pt - R2);
  const C = t2 - R2;

  const roots = [];
  if (Math.abs(A) < 1e-6) {
    if (Math.abs(B) > 1e-9) roots.push(-C / B);
  } else {
    const disc = B * B - 4 * A * C;
    if (disc >= 0) {
      const sq = Math.sqrt(disc);
      roots.push((-B - sq) / (2 * A));
      roots.push((-B + sq) / (2 * A));
    }
  }

  let bestS = null, bestDelta = Infinity;
  for (const s of roots) {
    if (s < S_MIN - 0.01 || s > S_MAX + 0.01) continue;
    const c = Math.max(S_MIN, Math.min(S_MAX, s));
    const d = Math.abs(c - currentS);
    if (d < bestDelta) { bestDelta = d; bestS = c; }
  }

  if (bestS === null) {
    bestDelta = Infinity;
    for (let i = 0; i <= 40; i++) {
      const s = S_MIN + (S_MAX - S_MIN) * i / 40;
      const cx = s * Px, cy = s * Py;
      const r  = (1 - s) * R;
      const d  = Math.abs(Math.hypot(wx - cx, wy - cy) - r);
      if (d < bestDelta) { bestDelta = d; bestS = s; }
    }
    if (bestS === null) bestS = Math.max(S_MIN, Math.min(S_MAX, currentS));
  }

  const cx = bestS * Px, cy = bestS * Py;
  const dx = wx - cx, dy = wy - cy;
  const phi = (Math.abs(dx) < 1e-9 && Math.abs(dy) < 1e-9)
              ? 0 : Math.atan2(dy, dx);
  return { phi, s: bestS };
}

/* ==========================================================================
   TRANSIENT STATE
   ==========================================================================
   Five drag modes, mutually exclusive at any moment:

       dragApex        apex tilt / depth pull from the cone band
       dragQuadVertex  a patch's corner, either band (gated)
       dragSquare      a floating square from the cone band
       flatDrag        a patch's corner/edge/body from the flat band
                       (gated)
       flatSquareDrag  a floating square from the flat band */

const state = {
  dragApex:       null,
  dragQuadVertex: null,
  dragSquare:     null,
  flatDrag:       null,
  flatSquareDrag: null,
  dragPatchBody:  null,
  mouse: { sx: 0, sy: 0, inside: false },
};

/* ==========================================================================
   DRAW TOOL
   ==========================================================================
   The Draw Quad workflow.  Toggled by the panel button; while active,
   the canvas mousedown handler diverts clicks into addDrawPoint
   instead of the usual hit-test chain.  On the fourth click
   finishDrawTool runs, which fits a patch + rectangle and clears the
   transient points.

   The fit mode and the rotation lock are both read from the panel at
   finish time, so either can be changed mid-draw. */

const drawTool = {
  active: false,
  points: [],
  hover: null,
};

function toggleDrawTool() {
  drawTool.active = !drawTool.active;
  drawTool.points = [];
  drawTool.hover = null;
  const btn = document.getElementById("drawQuadBtn");
  if (btn) {
    btn.textContent = drawTool.active ? "Cancel Draw" : "Draw Quad";
    btn.style.borderColor = drawTool.active ? "#00e5ff" : "";
    btn.style.color = drawTool.active ? "#00e5ff" : "";
  }
  canvas.style.cursor = "crosshair";
  draw();
}

function cancelDrawTool() {
  drawTool.active = false;
  drawTool.points = [];
  drawTool.hover = null;
  const btn = document.getElementById("drawQuadBtn");
  if (btn) {
    btn.textContent = "Draw Quad";
    btn.style.borderColor = "";
    btn.style.color = "";
  }
  canvas.style.cursor = "crosshair";
  draw();
}

function addDrawPoint(sx, sy) {
  if (!drawTool.active) return;
  if (drawTool.points.length >= 4) return;
  drawTool.points.push([sx, sy]);
  if (drawTool.points.length === 4) {
    finishDrawTool();
  } else {
    draw();
  }
}

function finishDrawTool() {
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
  syncQuadList();
  draw();
}

/* ==========================================================================
   LEAST-SQUARES RECTANGLE FIT
   ==========================================================================
   Given four local (u, v) points and an assignment of each to one of
   the reference rectangle's corners (signs = TL, TR, BR, BL as
   [sx, sy] pairs), recover the centre, rotation, and two reference
   half-extents in closed form.  Exact when the assigned points form
   a rotated rectangle under the patch's aspect scaling.

   When noRotate is true, the rotation is forced to zero and only the
   centre and the two half-extents are fitted.  The half-extents are
   then the signed projections of the points onto the patch's U and
   V axes, divided by the aspect scales — the closed-form LS solution
   for an axis-aligned rectangle.  This makes the shape read as
   north/south aligned in the flat view: its edges are parallel to
   the φ and s axes there. */

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
    theta: theta,
    scaleU: refHW / (SHAPE_REL_SIZE / 2),
    scaleV: refHH / (SHAPE_REL_SIZE / 2),
  };
}

/* Local (u, v) of a corner of the fitted shape, given the fit
   parameters and the reference-frame sign of that corner. */
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

/* Try all 24 assignments of the four local points to the four
   reference corners; return the fit whose rectangle best reproduces
   the assigned points, in local (u, v). */
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
   DRAW TOOL — PROJECT CLICKS TO (φ, s)
   ==========================================================================
   Returns { params: [[φ, s] × 4], isFlat: bool } or null on a mixed-
   band click sequence.  The φ coordinates are unwrapped around the
   first point so a click sequence crossing the seam does not blow up
   the bounding box into a full-turn span. */

function _projectDrawPoints(screenPts) {
  const isFlat = screenPts.every(p => p[1] >= layout.dividerY);
  const isCone = screenPts.every(p => p[1] <  layout.dividerY);
  if (!isFlat && !isCone) return null;

  const params = screenPts.map(([sx, sy]) => {
    if (isFlat) {
      const p = screenToFlat(sx, sy);
      return [p.phi, p.s];
    } else {
      const [wx, wy] = s2w(sx, sy);
      const p = projectToConeSurface(wx, wy, 0.5);
      return [p.phi, p.s];
    }
  });

  let basePhi = params[0][0];
  for (let i = 1; i < 4; i++) {
    while (params[i][0] - basePhi >  Math.PI) params[i][0] -= 2 * Math.PI;
    while (params[i][0] - basePhi < -Math.PI) params[i][0] += 2 * Math.PI;
  }

  return { params, isFlat };
}

/* ==========================================================================
   DRAW TOOL — BUILD A DEFAULT-SIZED PATCH AROUND A CENTRE
   ==========================================================================
   Both fit modes produce a patch of DEFAULT_PATCH_PHI_SPAN ×
   DEFAULT_PATCH_S_SPAN, centred on a (φ, s) point the mode computed.
   The s-bounds are clamped to [FLAT_S_MIN, FLAT_S_MAX] the same way
   the panel's patch-move does: when one edge hits a wall, the whole
   interval slides and the other edge advances.  Returns
   { phi0, phi1, s0, s1 }. */

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
   DRAW TOOL — CREATE PATCH + SHAPE FROM PARAMS
   ==========================================================================
   Common tail for both fit modes.  Given the four (φ, s) points, the
   desired patch bounds, and the rotation lock flag, push the patch
   and shape onto the model.  Returns { patch, shape } or null. */

function _pushPatchAndShapeFromParams(params, phiMin, phiMax, sMin, sMax,
                                      noRotate) {
  const id = nextQuadId++;
  const q = {
    id, name: "Q" + id,
    phi0: phiMin, phi1: phiMax,
    s0:   sMin,   s1:   sMax,
    mirror: false,
  };
  quads.push(q);
  const qi = quads.length - 1;
  selectedQuad = qi;

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
  const sqId = nextSquareId++;
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
  selectedSquare = floatSquares.length - 1;

  return { patch: q, shape: sq };
}

/* ==========================================================================
   DRAW TOOL — MODE 1: PATCH-FIRST
   ==========================================================================
   The four clicks are projected to (φ, s); their centroid defines
   the patch's centre.  A patch of the default size is created,
   centred on that centroid, with the s-bounds sliding off the sheet
   walls the same way the panel's patch-move does.  The shape is
   fitted by least squares to the clicks mapped into that patch's
   local (u, v) frame.

   This is the mode that keeps the patch's plane where the clicks
   put it (their centroid) without letting the patch's size follow
   the clicks' spread.  The patch is always DEFAULT_PATCH_PHI_SPAN ×
   DEFAULT_PATCH_S_SPAN, regardless of how wide or narrow the four
   points were. */

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
   DRAW TOOL — MODE 2: SHAPE-FIRST  (default)
   ==========================================================================
   Run the patch-first fit as a preliminary to get a shape.  Then:

     1.  Compute the shape's (φ, s) footprint via squareFlatCorners,
         which is the correspondence the flat view draws with.
     2.  Create a patch of the SAME SIZE as the "+ Patch" button
         produces — DEFAULT_PATCH_PHI_SPAN × DEFAULT_PATCH_S_SPAN —
         centred on the footprint's (φ, s) centroid.
     3.  Re-fit the shape in the new patch's frame, using the
         footprint corners as the fit target, so the shape's (φ, s)
         footprint is preserved.

   The visible result: a default-sized patch at the drawn location,
   with the fitted rectangle sitting at its centre.  The patch's size
   does not depend on how wide or narrow the four clicks were.

   Step 3 is a re-projection through a different frame, so the final
   shape is the least-squares rectangle in the new frame that best
   matches the old footprint.  Because the new patch differs from
   the preliminary one only by its frame's origin and scale, and the
   correspondence (u, v) → (φ, s) is linear, the refit is exact when
   the original footprint was already a rectangle under that
   correspondence.

   Both the preliminary fit and the refit are performed with the
   same noRotate flag, so when the rotation lock is on the final
   shape is axis-aligned in the flat view: its edges run parallel to
   the φ and s axes. */

function _fitPatchAndShape_shapeFirst(screenPts, noRotate) {
  const prelim = _fitPatchAndShape_patchFirst(screenPts, noRotate);
  if (!prelim) return null;
  const P = prelim.patch;
  const S = prelim.shape;

  const qi = quadIdxById(P.id);
  if (qi < 0) return prelim;

  /* Snapshot the shape's (φ, s) footprint BEFORE the patch moves;
     after P is resized, squareFlatCorners would return different
     values because it reads the patch's frame. */
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

  /* Map the snapshotted (φ, s) corners into the new patch's local
     (u, v).  This is the same linear correspondence used by
     squareFlatCorners, applied to the shape's old footprint. */
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
   DRAW TOOL — MODE DISPATCHER
   ========================================================================== */

function _fitPatchAndShape(screenPts, mode, noRotate) {
  if (screenPts.length !== 4) return null;
  if (mode === "patch-first") {
    return _fitPatchAndShape_patchFirst(screenPts, noRotate);
  }
  return _fitPatchAndShape_shapeFirst(screenPts, noRotate);
}

/* ==========================================================================
   RESIZE / FIT
   ========================================================================== */

function resize() {
  dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  canvas.width  = Math.round(cw * dpr);
  canvas.height = Math.round(ch * dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  layout.coneH     = Math.round(ch * 0.58);
  layout.dividerY  = layout.coneH;
  layout.reservedY = layout.coneH + 4;
  layout.reservedH = ch - layout.reservedY;

  fitView();
  draw();
}
window.addEventListener("resize", resize);

function fitView() {
  view.scale = 20 * view.zoom;
  view.tx = window.innerWidth / 2 + view.panX;
  view.ty = layout.coneH / 2 + view.panY;
}

function resetView() {
  view.zoom = 1; view.panX = 0; view.panY = 0;
  cone.ax = 0; cone.ay = 0;
  cone.depth = 15;
  cone.halfAngle = Math.PI / 6;
  cone.ringCount = 14;
  cone.meridianCount = 24;
  syncPanelSliders();
  fitView();
  draw();
}
"""
