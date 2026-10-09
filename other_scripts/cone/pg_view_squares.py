"""
pg_view_squares.py — rectangles on each patch's plane.

A patch defines a local 2D frame (origin C, axes U, V).  U and V are
unit length but generally not perpendicular.

THE MODEL
=========
A shape is a rectangle in the patch's own (u, v) plane, rotated by
an angle, with independent width and height multipliers, and
optionally tilted out of the patch plane:

    { id, name, quadId, u, v, scaleU, scaleV, theta, slope }

    u, v      centre in NORMALIZED patch coordinates
    scaleU    width multiplier (reference frame)
    scaleV    height multiplier (reference frame)
    theta     rotation angle, radians, in the REFERENCE frame
    slope     tilt out of the patch plane, radians, about the
              reference U axis (see SLOPE below)
    name      user-editable label, rendered as a pill at the centre

SHAPE DIMENSIONS — REFERENCE FRAME
==================================
A shape is a square in a REFERENCE frame.  When rendering, the
reference offset (du, dv) is mapped to the patch's actual (û, v̂) by
a per-patch diagonal (Ku, Kv):

    û_offset = du · Ku        v̂_offset = dv · Kv

and then to local (u, v) via (·uLen, ·vLen).  The reference square's
half-extents are:

    refHW = SHAPE_REL_SIZE / 2 · scaleU
    refHH = SHAPE_REL_SIZE / 2 · scaleV · depthScale

A shape drawn on a patch therefore has

    world width  = SHAPE_REL_SIZE · scaleU · Ku
    world height = SHAPE_REL_SIZE · scaleV · depthScale · Kv

Contiguity between two shapes placed at adjacent û / v̂ values is
exact whenever the shape's reference half-extent in û matches their
û-spacing — independent of any Ku, Kv value, because both shapes
are mapped through the same (Ku, Kv) and the reference halves cancel
in the (û, v̂) → (u, v) round trip.  The only requirement is that
shapes that must butt against each other are placed at adjacent û /
v̂ values by the same drag.

PATCH-RELATIVE SHAPE SIZE — WHAT Ku, Kv DO
==========================================
A patch is defined in the cone's parameter space by [phi0, phi1] ×
[s0, s1].  Its screen footprint in the CONE view scales with the
cone's radius R = depth · tan(halfAngle): growing the depth makes
every patch on the cone grow proportionally.  Its footprint in the
FLAT view is invariant to depth — the flat view draws in (phi, s),
which the depth does not touch.

A shape, by contrast, must decide what "its own size" means.  Two
behaviours are possible, and the choice is made by how Ku, Kv are
defined:

    world-anchored (historical, SHAPE_CONE_SQUARE = false)

        Ku = Kv = 1.  The shape's world width is SHAPE_REL_SIZE ·
        scaleU — a fixed number of world units.  On the cone that
        world size shrinks toward the apex (the perspective taper
        handles that), and in the flat view the shape's footprint
        scales as 1 / uLen, so changing the cone depth visibly
        changes how big the shape looks relative to its patch.
        This is what produced the "squares change size when the
        depth changes, unlike patches" report.

    patch-relative (current default)

        Ku = uLen / SHAPE_U_REF, Kv = vLen / SHAPE_V_REF.  The
        shape's world width is SHAPE_REL_SIZE · scaleU · uLen /
        SHAPE_U_REF, i.e. a fixed FRACTION SHAPE_REL_SIZE /
        SHAPE_U_REF of the patch's own U-extent.  The shape is a
        sticker on the patch: it grows and shrinks with the patch,
        in both bands, in exact lockstep.  In the flat view, the
        shape's (φ, s) footprint becomes

            φ-span = SHAPE_REL_SIZE · scaleU · dPhi / SHAPE_U_REF

        which is independent of uLen, hence of depth — matching
        what patches do.

The reference lengths SHAPE_U_REF, SHAPE_V_REF are the patch's
typical uLen and vLen at the DEFAULT cone configuration (depth 15,
half-angle 30°, default patch at s0 = 0.22, s1 = 0.72, φ-span 0.72).
They are the calibration point: at that configuration Ku = Kv = 1,
so a fresh scene's shapes look exactly as they did before this
change.  Moving the depth slider away from 15 now scales both the
patch and every shape on it, together.

CONTIGUITY
==========
Every renderer projects the shape's reference-frame corners through
the same per-view map:

    cone view   (du, dv) → (û, v̂) via (Ku, Kv) →
                (u, v) via (uLen, vLen) → world (frame)
    flat view   (du, dv) → (û, v̂) via (Ku, Kv) →
                (phi, s) via (dPhi, dS)

A shared (û, v̂) edge maps to the same (phi, s) edge regardless of
uLen and vLen, so two shapes placed at adjacent û / v̂ values touch
when the shape's reference half-extent matches their û / v̂ spacing.

ANCHORED RESIZE (SHIFT-HELD)
============================
By default a corner drag resizes the shape about its own centre:
all four sides move symmetrically, the centre stays where it was,
and the shape's stored (u, v) does not change.

Holding Shift while dragging a corner switches to an ANCHORED
resize.  The corner DIAGONALLY OPPOSITE the one being dragged stays
fixed in place, and only the two edges that meet at the dragged
corner move.  The shape's centre therefore shifts: it ends up at
the midpoint between the fixed anchor and the cursor.

The anchor is captured at the moment the drag begins, from the
shape's pre-drag state, and it does not move for the duration of
the drag.  Both the cone-band and the flat-band corner drags use
the same anchor; the difference between them is which depth
multiplier (effectiveConeDepth() vs SHAPE_DEPTH_FLAT) is folded
into the anchor's v-extent.

The math is the same linear map the renderer uses, just solved for
the new centre and half-extents given one fixed corner and one
moving corner:

    pu = (cursor_u − anchor_u) / (2 · Ku)
    pv = (cursor_v − anchor_v) / (2 · Kv)

    refHW = |pu · cos θ + pv · sin θ|
    refHH = |pv · cos θ − pu · sin θ|

    centre = anchor + R(θ) · (sx · refHW, sy · refHH)

with (sx, sy) the reference sign pair of the DRAGGED corner.  Both
half-extents are clamped to SHAPE_MIN_SCALE and the centre is
recomputed from the clamped half-extents and the fixed anchor, so
the anchor stays exactly put even when the cursor is dragged onto
it.

If Shift is pressed or released mid-drag, the next motion event
switches resize modes; the anchor is not re-captured, so a later
switch back to anchored mode may jump slightly.  This mirrors how
Shift is handled by the other drags (body, rotate, patch).

SHAPE DEPTH
===========
The "Shape depth" slider still exists and still multiplies the
shape's drawn HEIGHT via effectiveConeDepth().  Its reference unit
is 1.00, so at the default the two bands agree exactly.  Any other
value deliberately breaks that agreement — a v̂-adjacent pair that
touches in one band leaves a gap of  h_v̂ · vLen · (1 − DEPTH)  in
the other.  See the panel module for the slider semantics.

SLOPE
=====
Each shape carries a `slope` angle (radians).  Zero leaves the
shape flat on the patch plane — the historic behaviour.  Non-zero
tilts the shape out of the plane about its own reference U axis,
using a lightweight pseudo-3D model that only the CONE view reads:

    A point at local v = vC + dv moves to

        in-plane   v  = vC + dv · cos(slope)
        out-plane  h  = -dv · sin(slope)

    along the OUTWARD surface normal at the patch centre.

The in-plane part (v · cos) goes through the usual perspective
taper.  The out-of-plane part becomes a screen-space offset along
the direction the patch's outward normal projects to in the cone
view, which is the radial direction at the patch's angular centre:

    screen Δ = h · ( cos φ_c , -sin φ_c ) · view.scale

So a positive slope tips the +v edge (the one pointing toward the
apex) INTO the cone's cavity; the −v edge swings outward by the
same amount.  Slope is clamped to ±85° to avoid the degenerate
edge-on case.

The flat (unfolded-cone) view deliberately IGNORES slope: the
shape's (φ, s) footprint there is the un-tilted one, so editing
corners and rotating in that band behaves exactly as before.  The
inverse solvers below also ignore slope; to edit a tilted shape's
corners by dragging in the cone view, set its slope back to 0
first.

THE PERSPECTIVE — SHAPE-RELATIVE TAPER
======================================
The cone band is a perspective projection of the patch's plane.  A
point's apparent shrink toward the apex depends on its v̂ through the
horizon factor

    persp(v̂) = (v̂_A − v̂) / v̂_A

The FULLY PER-VERTEX model evaluates each corner's factor at that
corner's own v̂.  That is physically correct, but it also meant that
rotating the shape in place changed the four factors.

This module keeps the taper but makes it SHAPE-RELATIVE.  For a shape
whose centre is at local v_C and whose centre's factor is

    p_C = persp(v̂_C)

a corner at local v = v_C + dv gets the factor

    p(dv) = p_C − dv / vMax

with vMax = v̂_A · vLen.  This is the first-order Taylor expansion of
the true horizon factor around the shape's own centre.  Because the
horizon factor is linear in v̂, the expansion is exact.

THE HORIZON CLIPS, IT DOES NOT PUSH
===================================
A corner at local v greater than vMaxLocal = h.vHat · vLen has a
negative shape-relative factor; its projection crosses through the
apex and the polygon folds.  Rather than clamping the shape's centre
to keep every corner inside the band, the shape is allowed to travel
through the horizon and the polygon is CLIPPED to v ≤ vMaxLocal in
the shape's OWN local (u, v) coordinates.

_clipPolygonToVMax performs the Sutherland-Hodgman pass.  Its output
may have 0, 3, 4, or 5 vertices depending on how the shape straddles
the line.  Every consumer — the renderer, the hit test, the centre
computation — iterates the clipped vertex list rather than assuming
four corners.

INVERSION — SOLVERS, PRESERVED SIDE BY SIDE
===========================================

    cursorToLocalShapeRelativeExactWith  (quadratic, shape-relative,
                                          explicit parameters)
    cursorToLocalShapeRelativeExact      (quadratic, shape-relative,
                                          parameters read from the
                                          shape)
    cursorToLocalFullPersp               (quadratic, per-vertex)
    cursorToLocalWithPersp               (iterative, shape-relative)

All four read the shape as if it were FLAT on the patch plane: the
slope is not inverted.  Dragging a corner in the cone view when the
shape is tilted will therefore land the dragged corner on the
un-tilted (u, v) of the cursor.  Reset slope to 0 for a precise
corner drag.

SHAPE POSITION BOUND (u)
========================
setClonePosition clamps the shape's stored u to ±U_LIMIT.  The clamp
exists because the inverse solvers can return an arbitrarily large
|u| when the cursor sits near the horizon or when the root picker
lands on the far branch.

PHI WRAP AND THE FLAT VIEW
==========================
The flat-view coordinates are (phi, s), and phi is a circle.  A patch
that drifts past the seam draws its wrapped copies via _phiCopies,
and the shapes on it have to be drawn and hit-tested at every copy
too, including the rotate handle.

ROTATION
========
Rotation is a plain rotation in the shape's own REFERENCE frame:

    du' = du·cosθ − dv·sinθ
    dv' = du·sinθ + dv·cosθ

Since the reference shape is a square (at scaleU = scaleV = 1), a
90° rotation returns the same SET of reference corners; and because
the map (du, dv) → local is a linear map that does not depend on
θ, the mapped shape is the same SET of points at 0° and 90°.

PLANE INTERSECTION
==================
When a shape is tilted out of its patch plane, the shape's plane
cuts the patch plane along a line: the pivot axis.  The projection
tilts about local v = vC (the shape's centre along the patch's V
axis), so the intersection segment is the chord of the shape's own
local quad along v = vC.  It is drawn as a faint dashed hint so a
tilted shape reads as "pivoted about this axis" rather than
floating arbitrarily.  With slope = 0 the line is not drawn.

CLONING
=======
cloneSquare(idx) duplicates a shape at the EXACT same location as
its source: same quadId, same (u, v), same scaleU / scaleV, same
theta, same slope.  The clone overlaps its source pixel-for-pixel
until it is dragged away, so the operation reads as "stamp another
copy right here".  The clone receives a fresh id and default name
and becomes the new selection.

MIRROR
======
Every patch carries a boolean `mirror`.  When it is true, every
square on the patch is drawn a second time on the patch's mirror —
the same patch shifted by π in φ, i.e. on the diametrically
opposite side of the cone.  The mirror copy shares the square's
full local state (u, v, theta, scaleU, scaleV, slope): it is a
pure visual clone, not an independent object.  Dragging or editing
the original updates both; the mirror itself is not hit-testable.

LABELS
======
Every patch and every shape carries a user-editable name.
"""


FLOAT_SQUARES_JS = r"""
/* ==========================================================================
   STATE
   ========================================================================== */

const floatSquares = [];
let nextSquareId = 1;
let selectedSquare = -1;

/* ==========================================================================
   SHAPE DIMENSIONS
   ==========================================================================
   A shape is a square in a REFERENCE frame.  The reference
   half-extents are

       refHW = SHAPE_REL_SIZE / 2 · scaleU
       refHH = SHAPE_REL_SIZE / 2 · scaleV · depthScale

   The reference offset (du, dv) maps to the patch's (û, v̂) through
   a per-patch diagonal (Ku, Kv), then to local (u, v) through
   (uLen, vLen).  See the module docstring, "SHAPE DIMENSIONS —
   REFERENCE FRAME". */

/* The reference square's side, in REFERENCE units.  Its world size
   is this times (scaleU · Ku, scaleV · Kv · depthScale); with the
   patch-relative aspect scaling defined below, that makes the shape
   cover a fixed FRACTION SHAPE_REL_SIZE / SHAPE_U_REF of its
   patch's U-extent (and SHAPE_REL_SIZE / SHAPE_V_REF of its
   V-extent), independent of the cone's depth or half-angle. */
const SHAPE_REL_SIZE = 2.0;

/* Per-shape multipliers on the two reference axes.  Set to 1.0 for
   a reference square; corner-drag resizes move them. */
const SHAPE_DEFAULT_SCALE = 1.0;
const SHAPE_MIN_SCALE     = 0.03;
const SHAPE_MAX_SCALE     = 10.00;

/* ==========================================================================
   SHAPE SLOPE
   ==========================================================================
   How far a shape may tilt out of the patch plane, about its own
   reference U axis.  Zero leaves the shape flat on the patch.
   Positive slope tips the +v edge (the one that points toward the
   apex) INTO the cavity of the cone; negative slope tips the −v
   edge.  See the SLOPE section in the module docstring.

   ±85° is the hard bound.  At ±90° the shape would be edge-on to
   the patch plane, the projection would collapse to a line, and
   the corner/rotate handle offsets would become singular. */

const SHAPE_SLOPE_MIN = -85 * Math.PI / 180;
const SHAPE_SLOPE_MAX =  85 * Math.PI / 180;

/* ==========================================================================
   PATCH-RELATIVE ASPECT SCALING
   ==========================================================================
   The shape is a sticker on the patch: its size is defined as a
   fraction of the patch's own U- and V-extents, so that patches and
   the shapes on them scale together — in both bands — whenever the
   cone's depth or half-angle changes.

   The aspect scales are

       Ku = f.uLen / SHAPE_U_REF
       Kv = f.vLen / SHAPE_V_REF

   The reference lengths SHAPE_U_REF, SHAPE_V_REF are the patch's
   typical uLen and vLen at the DEFAULT cone configuration (depth
   15, half-angle 30°, default patch at s0 = 0.22, s1 = 0.72,
   φ-span 0.72).  At that configuration Ku = Kv = 1, so a fresh
   scene's shapes look exactly as they did under the previous
   world-anchored scheme (Ku = Kv = 1 everywhere).  Scaling away
   from the reference is now coherent: a bigger patch means a
   bigger shape, always, at the same fraction of the patch.

   Compare the module docstring, "PATCH-RELATIVE SHAPE SIZE". */
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
   ==========================================================================
   Two independent depth multipliers, one per view.  The panel's
   "Shape depth" slider drives SHAPE_DEPTH_CONE only; the flat view
   reads SHAPE_DEPTH_FLAT, a fixed constant.

   The unit is 1.00: at the default slider position the two bands
   agree exactly and edge-to-edge contiguity holds across bands.
   Any other slider value deliberately opens a gap between the
   bands' drawn heights. */

const SHAPE_DEPTH_CONE_UNIT = 1.00;
let   SHAPE_DEPTH_CONE      = 1.00;
const SHAPE_DEPTH_FLAT      = 1.00;

function effectiveConeDepth() {
  return SHAPE_DEPTH_CONE * SHAPE_DEPTH_CONE_UNIT;
}

const WEDGE_BASE_REACH = 2.50;

/* ROTATE_HANDLE_OFFSET is in REFERENCE units — the handle sits this
   far past the reference square's +v̂ edge. */
const ROTATE_HANDLE_OFFSET = 0.25;
const ROTATE_HANDLE_R      = 10;

const ROTATE_JUMP_STEP = Math.PI / 12;

const U_LIMIT = 3.0;

/* ==========================================================================
   PATCH HUE PALETTE
   ========================================================================== */

const PATCH_HUES = [
  { r: 255, g: 200, b:  90 },   // amber
  { r: 120, g: 220, b: 255 },   // cyan
  { r: 255, g: 130, b: 210 },   // magenta
  { r: 140, g: 225, b: 160 },   // green
  { r: 180, g: 155, b: 255 },   // violet
  { r: 255, g: 165, b: 105 },   // orange
  { r: 110, g: 220, b: 210 },   // teal
  { r: 220, g: 230, b: 120 },   // lime
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
   ==========================================================================
   Every patch carries a boolean `mirror`.  When it is true, every
   square on the patch is drawn a second time on the patch's mirror —
   the same patch shifted by π in φ, i.e. on the diametrically
   opposite side of the cone.  The mirror copy shares the square's
   full local state (u, v, theta, scaleU, scaleV, slope): it is a
   pure visual clone, not an independent object.  Dragging or
   editing the original updates both; the mirror itself is not hit-
   testable.

   The renderer shifts the patch's φ bounds in place, runs the same
   drawing routines, then restores them.  Every square geometry
   function reads the patch through `quads[qi]`, so the shifted
   patch is what they see; nothing in the per-square math needs to
   know a mirror is being drawn.  The shift is restored in a
   `finally`, so an exception cannot leave the model shifted. */

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

/* The mirror draws the same square on the diametrically-opposite
   side of the cone, offset by `mirrorAngle` in φ (default π).  When
   `mirrorFlip` is true, the shape's θ is negated for the duration of
   the mirror draw, which cancels the 180° frame rotation that the
   φ-shift introduces — so a tilted square on the mirror tilts the
   same way it does on the original instead of appearing upside
   down. */
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
  const u0     = sq.u;     // was: const v0 = sq.v;

  q.phi0 = phi0 + angle;
  q.phi1 = phi1 + angle;

  if (flip) {
    /* Reflect the shape through the patch's U axis.  A rectangle
       has no internal left/right asymmetry, so negating θ alone
       only rotates it; negating slope alone only tips it.  The
       real "flip" is in the shape's POSITION along the patch's V
       axis, which combined with the two sign flips is exactly the
       point reflection v → -v, θ → -θ, slope → -slope.  This is
       what makes the mirror read as the original flipped
       top-to-bottom on the patch rather than as a rotated ghost. */
    sq.theta = -theta0;
    sq.slope = -slope0;
    sq.u     = -u0;        // was: sq.v = -v0;
  }

  try {
    fn();
  } finally {
    q.phi0   = phi0;
    q.phi1   = phi1;
    sq.theta = theta0;
    sq.slope = slope0;
    sq.u     = u0;         // was: sq.v = v0;
  }
}

/* The direction that the patch's outward surface normal projects to,
   in the cone view, is the radial direction at the patch's angular
   centre.  When the apex is off-centre the axis is not vertical,
   but the projection of "outward" is still radial in the xy plane,
   so this is the correct screen direction to slide the tilted shape
   along.  Returned in world φ (radians, [0, 2π)). */
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

/* ==========================================================================
   SHAPE PERSPECTIVE PARAMETERS
   ========================================================================== */

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
   HORIZON CLIP
   ==========================================================================
   Sutherland-Hodgman against the half-plane v ≤ vMaxLocal. */

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
   SHAPE V-EXTENT (preserved, not on the render path)
   ==========================================================================
   Reports the shape's half-extent along v̂, in NORMALIZED v̂ units,
   accounting for the rotation and the aspect correction.  The slope
   is not folded in here: this is the footprint the shape would have
   if it were flat on the patch plane. */

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
  });
  selectedSquare = floatSquares.length - 1;
  syncQuadList();
  draw();
}

function addSquareAtCenter(quadIdx) { addSquareAt(quadIdx, 0, 0); }

/* Duplicate a square at the EXACT same location as its source: same
   patch (quadId), same normalized (u, v), same scaleU / scaleV,
   same theta, same slope.  The clone overlaps the source
   pixel-for-pixel until it is dragged away.  It receives a fresh id
   and default name, and becomes the new selection. */
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
   ==========================================================================
   A shape's drawn dimensions in the patch's world-local (u, v) units.

   The shape is a square in a REFERENCE frame, with half-extents

       refHW = SHAPE_REL_SIZE / 2 · scaleU
       refHH = SHAPE_REL_SIZE / 2 · scaleV · depthScale

   The reference offset (du, dv) maps to the patch's (û, v̂) through
   the per-patch diagonal (Ku, Kv), then to local (u, v) through
   (uLen, vLen).  See _shapeAspectScales for Ku and Kv.

   The world extents reported here are the shape's ACTUAL on-patch
   extents — SHAPE_REL_SIZE · scaleU · Ku in u and SHAPE_REL_SIZE ·
   scaleV · depthScale · Kv in v — which for the patch-relative
   aspect scaling is a fixed fraction of the patch's own uLen /
   vLen.  This is what the cone view draws.

   squareDims / squareCornersLocal / squareRotateHandleLocal are the
   FLAT-view variants (SHAPE_DEPTH_FLAT); the ...Cone siblings are
   the CONE-view variants (effectiveConeDepth()). */

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

/* The shape's intrinsic size in REFERENCE units, independent of the
   patch it sits on and of the cone's depth / half-angle.  These are
   what the panel's Square W / Square H fields read and write; see
   pg_panel.py. */

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

/* The shape's four corners in the patch's local (u, v) frame.

   Rotation happens in the REFERENCE frame, where the shape is a
   square (at scaleU = scaleV = 1).  The reference offset is then
   mapped to (û, v̂) through (Ku, Kv) and to local through
   (uLen, vLen).  Because that map does not depend on θ, a 90°
   rotation in the reference frame is a 90° rotation of the drawn
   shape, so the set of local corners is the same at θ = 0° and 90°.

   Slope is not applied here.  The flat view (via these local
   corners) sees the un-tilted footprint; the cone view applies the
   tilt later, in projectShapePoint. */

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

  /* Reference half-extents. */
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

/* The local (u, v) of the corner DIAGONALLY OPPOSITE the given
   corner — the anchor a Shift-held corner drag keeps fixed.  Same
   math as squareCornersLocalWith but at the opposite sign pair.
   Corners are ordered [TL, TR, BR, BL], so the opposite of corner
   i is corner (i + 2) % 4.  depthScale is folded into the
   reference v half-extent, matching squareCornersLocalWith; pass
   effectiveConeDepth() from the cone band and SHAPE_DEPTH_FLAT
   from the flat band. */
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

/* The rotate handle sits on the shape's +v̂ axis in the REFERENCE
   frame, at reference offset (0, refHH + ROTATE_HANDLE_OFFSET).  It
   rotates with the shape and maps through the same (Ku, Kv). */

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
   PROJECTION — shape-relative taper, WITH SLOPE
   ==========================================================================
   The single place slope is realised.  Given a point's local (u, v)
   on the patch plane, the projection proceeds in three steps:

     1.  Decompose the point's offset from the shape's centre along
         the reference V axis into an in-plane part and an
         out-of-plane part:

             dv          = vLocal - vC
             vInPlane    = vC + dv · cos(slope)
             hOut        =   - dv · sin(slope)

     2.  Project the in-plane point through the usual shape-relative
         perspective taper:

             p = pC - (vInPlane - vC) / vMax

     3.  Add the out-of-plane part as a screen-space offset along
         the direction the patch's outward surface normal projects
         to.  That direction is the radial direction at the patch's
         angular centre φc:  in screen space it is
         ( cos φc , -sin φc ), because screen y is flipped.

             px += hOut ·  cos(φc) · view.scale
             py += hOut · -sin(φc) · view.scale

   Positive slope drops the +v side (dv > 0) INTO the cone — the
   outward normal points away from the axis, so hOut < 0 for dv > 0
   moves the point toward the axis. */

function projectShapePoint(f, qi, uLocal, vLocal, vC, pC, vMax,
                           phiC, slope) {
  const dv   = vLocal - vC;
  const cosS = Math.cos(slope || 0);
  const sinS = Math.sin(slope || 0);

  const vInPlane = vC + dv * cosS;

  const [wx, wy] = frameToWorld(f, uLocal, vInPlane);
  const [sx, sy] = w2s(wx, wy);
  const [ax, ay] = w2s(cone.ax, cone.ay);
  const p  = pC - (vInPlane - vC) / vMax;
  let px = ax + p * (sx - ax);
  let py = ay + p * (sy - ay);

  const hN = -dv * sinS;
  if (hN !== 0) {
    px += hN *  Math.cos(phiC) * view.scale;
    py += hN * -Math.sin(phiC) * view.scale;
  }

  return [px, py];
}

function squareCornersScreen(sq) {
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

  return clipped.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax, phiC, slope));
}

function squareRotateHandleScreen(sq) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;

  if (!squareCornersScreen(sq)) return null;

  const hl = squareRotateHandleLocalCone(sq);
  if (!hl) return null;

  const h = horizonLocal(qi);
  if (!h) return null;

  const vMaxLocal = h.vHat * f.vLen;
  const HANDLE_GAP = 0.02 * f.vLen;
  const hv = Math.min(hl[1], vMaxLocal - HANDLE_GAP);

  const vC    = sq.v * f.vLen;
  const pC    = shapePerspCentre(sq);
  const vMax  = shapeVMax(sq);
  const phiC  = _patchNormalPhi(qi);
  const slope = sq.slope || 0;

  return projectShapePoint(f, qi, hl[0], hv, vC, pC, vMax, phiC, slope);
}

/* ==========================================================================
   PLANE INTERSECTION
   ==========================================================================
   When a shape is tilted out of its patch plane, its plane cuts the
   patch plane along a line: the pivot axis.  The projection tilts
   about local v = vC (the shape's centre along the patch's V axis),
   so the intersection segment is the piece of the line v = vC that
   lies inside the shape's own local quad.  It is drawn as a faint
   dashed hint so a tilted shape reads as "pivoted about this axis"
   rather than floating arbitrarily.  With slope = 0 the line is not
   drawn. */

function squarePlaneIntersectionLocal(sq) {
  const slope = sq.slope || 0;
  if (Math.abs(slope) < 1e-6) return null;

  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return null;
  const f = patchFrame(qi);
  if (!f) return null;

  const corners = squareCornersLocalCone(sq);
  if (!corners || corners.length < 3) return null;

  const vC = sq.v * f.vLen;

  /* Intersect the line v = vC with every edge of the quad. */
  const pts = [];
  const n = corners.length;
  for (let i = 0; i < n; i++) {
    const A = corners[i];
    const B = corners[(i + 1) % n];
    const da = A[1] - vC;
    const db = B[1] - vC;
    if (Math.abs(da) < 1e-9) pts.push([A[0], vC]);
    if (Math.abs(db) < 1e-9) pts.push([B[0], vC]);
    if (da * db < 0) {
      const t = da / (da - db);
      pts.push([A[0] + (B[0] - A[0]) * t, vC]);
    }
  }

  if (pts.length < 2) return null;

  /* The two most distant intersections are the chord endpoints. */
  let best  = [pts[0], pts[1]];
  let bestD = -1;
  for (let i = 0; i < pts.length; i++) {
    for (let j = i + 1; j < pts.length; j++) {
      const dx = pts[i][0] - pts[j][0];
      const dy = pts[i][1] - pts[j][1];
      const d  = dx * dx + dy * dy;
      if (d > bestD) { bestD = d; best = [pts[i], pts[j]]; }
    }
  }
  return best;
}

/* Cone-band screen endpoints.  Both lie at local v = vC, so the
   projection's slope term is zero there and they read as the same
   line the un-tilted shape would show: exactly the pivot. */
function squarePlaneIntersectionScreen(sq) {
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

  return local.map(([u, v]) =>
    projectShapePoint(f, qi, u, v, vC, pC, vMax, phiC, slope));
}

/* Flat-band (phi, s) endpoints, using the same linear (u, v) →
   (phi, s) correspondence the shape's footprint itself uses. */
function squarePlaneIntersectionFlat(sq) {
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
   RENDERING
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
  const vLocal = -WEDGE_BASE_REACH * f.vLen;
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

function drawFloatSquares() {
  for (let i = 0; i < floatSquares.length; i++) {
    if (i === selectedSquare) continue;
    const sq = floatSquares[i];
    drawFloatSquare(sq, false, false);
    _withSquareMirror(sq, () => drawFloatSquare(sq, false, true));
  }
  if (selectedSquare >= 0 && selectedSquare < floatSquares.length) {
    const sq = floatSquares[selectedSquare];
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

  /* Intersection with the patch plane, when tilted. */
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

  /* The mirror gets no corner or rotate handles — it is a visual
     clone, not a second editable object. */
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

/* ==========================================================================
   HIT TESTING — cone band
   ========================================================================== */

const SQUARE_HANDLE_R   = 10;
const SQUARE_BODY_MIN_R = 8;

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

function squareHitTest(sx, sy) {
  const order = [];
  if (selectedSquare >= 0 && selectedSquare < floatSquares.length) {
    order.push(selectedSquare);
  }
  for (let i = 0; i < floatSquares.length; i++) {
    if (i !== selectedSquare) order.push(i);
  }
  for (const si of order) {
    const sq = floatSquares[si];
    const pts = squareCornersScreen(sq);
    if (!pts || pts.length < 3) continue;

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
   These solvers assume the shape is FLAT on the patch plane.  A
   tilted shape's corner drag therefore lands the corner at the
   un-tilted (u, v) of the cursor.  Reset slope to 0 for precise
   corner editing in the cone view. */

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

/* Corner resize.  The cursor's offset from the shape's centre is
   mapped to the REFERENCE frame by inverting the same map the
   renderer applies:

       local_u_offset = duR · Ku
       local_v_offset = dvR · Kv

   so duR = local_u_offset / Ku and dvR = local_v_offset / Kv.  The
   resulting duR, dvR are un-rotated to recover the reference half-
   extents, hence the new scaleU / scaleV.

   NOTE — the earlier version divided by uLen / vLen *in addition*
   to Ku / Kv, which made the computed half-extent 1/uLen (resp.
   1/vLen) of the correct value and collapsed the shape to a
   fraction of its size the moment a corner was grabbed.

   depthScale is the multiplier the height is drawn at:
   effectiveConeDepth() from the cone band, SHAPE_DEPTH_FLAT from the
   flat band.  It enters only through the reference height's
   normalizer, so the corner follows the cursor in the band the drag
   came from.

   ANCHORED BRANCH — when anchorU / anchorV are supplied, the drag
   switches to the Shift-held anchored resize: the OPPOSITE corner
   stays fixed in place at (anchorU, anchorV) and only the two edges
   meeting at the dragged corner move.  The shape's centre shifts to
   the midpoint between the anchor and the cursor.  Both half-extents
   are clamped to SHAPE_MIN_SCALE, then the centre is recomputed from
   the clamped half-extents and the fixed anchor so the anchor stays
   exactly put even when the cursor lands on it.  See the module
   docstring, ANCHORED RESIZE (SHIFT-HELD), for the derivation. */

function setShapeFromCornerLocal(sq, cornerIdx, cu, cv, depthScale,
                                 anchorU, anchorV) {
  const qi = quadIdxById(sq.quadId);
  if (qi < 0) return;
  const q = quads[qi];
  if (!q) return;
  const f = patchFrame(qi);
  if (!f) return;

  /* Cursor offset from the shape centre, in local (u, v). */
  const dx = cu - sq.u * f.uLen;
  const dy = cv - sq.v * f.vLen;

  /* Local → reference, via the inverse of (duR · Ku, dvR · Kv). */
  const { Ku, Kv } = _shapeAspectScales(f);
  const ox = dx / Ku;
  const oy = dy / Kv;

  /* Un-rotate, in the reference frame. */
  const cosT = Math.cos(sq.theta || 0);
  const sinT = Math.sin(sq.theta || 0);

  const signs = [[-1, +1], [+1, +1], [+1, -1], [-1, -1]][cornerIdx];
  const sx = signs[0], sy = signs[1];

  const refHWmin = SHAPE_MIN_SCALE * SHAPE_REL_SIZE / 2;
  const refHHmin = SHAPE_MIN_SCALE * SHAPE_REL_SIZE / 2 * depthScale;

  /* ---- anchored branch (Shift-held) ----------------------------
     The anchor is the OPPOSITE corner's local position, captured
     at drag start.  We solve for the new half-extents and centre
     from the anchor and the cursor alone; the shape's own rotation
     and the patch's (Ku, Kv) are the same maps the renderer uses,
     so the anchor stays exactly put.

     Derivation (theta = the shape's own theta, unchanged):

         pu = (cursor_u - anchor_u) / (2 * Ku)
         pv = (cursor_v - anchor_v) / (2 * Kv)

         sx*refHW = pu*cosT + pv*sinT
         sy*refHH = pv*cosT - pu*sinT

     so |. | gives the two positive half-extents.  The new centre
     is the midpoint of anchor and cursor in local coordinates. */

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

    /* Recompute the centre from the anchor using the (possibly
       clamped) half-extents and the dragged corner's reference
       sign pair, so the anchor stays exactly fixed even when a
       half-extent has been clamped to its minimum. */
    const duR = sx * refHW * cosT - sy * refHH * sinT;
    const dvR = sx * refHW * sinT + sy * refHH * cosT;
    const uHat = (anchorU + duR * Ku) / f.uLen;
    const vHat = (anchorV + dvR * Kv) / f.vLen;
    sq.u = uHat;
    sq.v = clampCloneV(qi, vHat);
    return;
  }

  /* ---- default branch: resize about the FIXED centre -----------
     The cursor's offset from the shape's centre sets the new
     reference half-extents; the centre is not moved, so all four
     sides move symmetrically. */

  const ux = ox * cosT + oy * sinT;
  const uy = oy * cosT - ox * sinT;

  const refHW = Math.max(sx * ux, refHWmin);
  const refHH = Math.max(sy * uy, refHHmin);

  sq.scaleU = refHW / (SHAPE_REL_SIZE / 2);
  sq.scaleV = refHH / (SHAPE_REL_SIZE / 2 * depthScale);
  clampShapeScales(sq);
}

/* Rotate-handle tracking.  The cursor's offset from the shape centre
   is mapped to the REFERENCE frame; the handle sits on the reference
   +v̂ axis, so the raw angle is atan2(−ox_ref, oy_ref). */

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
   ==========================================================================
   resizeSquareFromCorner takes an optional anchor.  When both
   anchorU and anchorV are supplied, the resize is anchored on that
   fixed local point (the opposite corner) instead of on the shape's
   centre.  pg_dispatch reads Shift at each move event and passes the
   drag-start anchor when Shift is held. */

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

/* ==========================================================================
   FLAT-VIEW PROJECTION
   ==========================================================================
   The flat view ignores slope: it draws the shape's un-tilted (φ, s)
   footprint, so editing in that band stays exact. */

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
   ==========================================================================
   flatResizeSquareFromCornerIdx mirrors resizeSquareFromCorner: when
   anchorU / anchorV are supplied the resize is anchored on that
   fixed local point (the opposite corner); otherwise the shape
   resizes about its own centre.  The flat band uses SHAPE_DEPTH_FLAT
   for the depth multiplier, so the anchor is captured and used at
   the same scale. */

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

function squareFlatHitTest(sx, sy) {
  if (selectedQuad < 0 || selectedQuad >= quads.length) return null;
  const activeQ = quads[selectedQuad];
  for (let i = floatSquares.length - 1; i >= 0; i--) {
    const sq = floatSquares[i];
    if (sq.quadId !== activeQ.id) continue;

    const corners = squareFlatCorners(sq);
    if (!corners) continue;
    const phis = corners.map(c => c[0]);
    const shifts = _phiCopies(Math.min(...phis), Math.max(...phis));

    for (const shift of shifts) {
      const rh = squareFlatRotateHandleScreen(sq, shift);
      if (rh && Math.hypot(sx - rh[0], sy - rh[1]) < ROTATE_HANDLE_R) {
        return { kind: "rotate", squareIdx: i };
      }
    }

    for (const shift of shifts) {
      const screen = corners.map(([phi, s]) =>
        flatToScreen(phi + shift, s));

      for (let ci = 0; ci < 4; ci++) {
        if (Math.hypot(sx - screen[ci][0], sy - screen[ci][1])
            < FLAT_HANDLE_R) {
          return { kind: "corner", squareIdx: i, cornerIdx: ci };
        }
      }

      if (_ptInQuadPx(sx, sy, screen)) {
        return { kind: "body", squareIdx: i };
      }
    }
  }
  return null;
}

/* ==========================================================================
   DISPLAY NAMES
   ========================================================================== */

function squareDisplayName(sq) {
  return (sq && sq.name) ? sq.name : ("S" + (sq ? sq.id : "?"));
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
