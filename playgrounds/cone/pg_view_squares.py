"""
pg_view_squares.py — rectangles on each patch's plane.

A patch defines a local 2D frame (origin C, axes U, V).  U and V are
unit length but generally not perpendicular.

THE MODEL
=========
A shape is a rectangle in the patch's own (u, v) plane, rotated by
an angle, with independent width and height multipliers, and
optionally hinged out of the patch plane:

    { id, name, quadId, u, v, scaleU, scaleV, theta, slope,
      visualBottom }

    u, v        centre in NORMALIZED patch coordinates
    scaleU      width multiplier (reference frame)
    scaleV      height multiplier (reference frame)
    theta       rotation angle, radians, in the REFERENCE frame
    slope       hinge parameter, unitless, in [0, 1]
    visualBottom
                per-square meta flag read only by the KRA export
    name        user-editable label

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

PATCH-RELATIVE SHAPE SIZE — WHAT Ku, Kv DO
==========================================
Ku = uLen / SHAPE_U_REF, Kv = vLen / SHAPE_V_REF.  The shape's world
width is SHAPE_REL_SIZE · scaleU · uLen / SHAPE_U_REF, i.e. a fixed
fraction of the patch's own U-extent.  The shape is a sticker on the
patch: it grows and shrinks with the patch, in both bands, in exact
lockstep.

CONTIGUITY
==========
A shared (û, v̂) edge maps to the same (phi, s) edge regardless of
uLen and vLen, so two shapes placed at adjacent û / v̂ values touch
when the shape's reference half-extent matches their û / v̂ spacing.

ANCHORED RESIZE (DEFAULT) AND CENTRED RESIZE (SHIFT-HELD)
=========================================================
By default a corner drag ANCHORS the shape: the corner diagonally
opposite the one being dragged stays fixed in place.  Holding Shift
switches to a CENTRED resize: the shape resizes about its own centre.
The anchor is captured at the moment the drag begins.

SHAPE DEPTH
===========
The "Shape depth" slider multiplies the shape's drawn HEIGHT via
effectiveConeDepth().  The flat view reads the fixed SHAPE_DEPTH_FLAT.

SLOPE
=====
Each shape carries a `slope` parameter in [0, 1].  Zero leaves the
shape flat on the patch plane.  At 1 the shape has rotated a full 90
degrees out of the patch plane about its near edge (the edge whose
corners have the smallest local v).

STANDING HEIGHT — SHAPE-DRIVEN SCALE
====================================
The scale factor `_shapeStandingScale` multiplies the raw
out-of-plane displacement `d·sin θ` by

    (world_u_extent / world_v_extent) · perspAt(hinge v̂)

so a hinged shape stands as a square on screen rather than as the
patch's aspect-warped rectangle.

PLANE WARP
==========
Each patch carries a `warp` parameter in [0, 1] that reparametrises
the patch's plane.  The plane itself is NEVER moved and NEVER bent:
it is always the patch's own tangential plane.  Only the CHART used
to address points on that plane changes.

    warp = 0    the historic axial (rectangular) chart, aligned
                with the patch's own U and V axes.

    warp = 1    a concentric chart centred on the apex: the apex
                is the pole, lines of constant u are arcs around
                the apex, and lines of constant v are radial
                spokes from it.

    in between  the two world positions blend linearly.

Both charts map to the SAME plane, so a shape moved in +u still
slides across the patch's surface, a shape moved in +v still heads
toward the horizon, and no square ever crosses the horizon at any
warp value.  The visible consequence of the concentric chart is
that a horizontal line of contiguous squares has its nearer-apex
vertexes pulled together — they trace a smaller arc around the
apex — while the far-side vertexes drift apart.

The chart is built in world coordinates.  Let (aX, aY) be the apex
in world coords and C = (f.Cx, f.Cy) the patch centre; set
r0 = |C − apex| and θ0 = atan2(Cy − aY, Cx − aX).  Then

    θ(u) = θ0 + u / r0
    ρ(v) = r0 − v
    world = apex + ρ · (cos θ, sin θ)

(0, 0) maps to C.  ∂world/∂v at C is exactly the patch's V
direction — which points exactly toward the apex for a patch built
on the cone — and ∂world/∂u at C is the tangent direction
perpendicular to the apex-C line, matching U up to the small
tilt-angle correction.  The transition from warp = 0 is smooth.

Only the CONE view reads warp.  The flat (unfolded-cone) view is
unchanged, so corner editing, rotation, hit tests, scene save/load,
and every KRA export mode behave exactly as before at warp = 0.
A drag on a strongly warped square tracks the cursor approximately
rather than exactly, with a discrepancy that vanishes at warp = 0.
See pg_view_squares_warp.py for the implementation.

SHAPE POSITION BOUND (u)
========================
setClonePosition clamps the shape's stored u to ±U_LIMIT.

PHI WRAP AND THE FLAT VIEW
==========================
The flat view draws the un-warped (φ, s) footprint, so editing in
that band stays exact and the flat sheet is a pure design-intent
view.

CLONING
=======
cloneSquare(idx) duplicates a shape at the EXACT same location as
its source.

MIRROR
======
Every patch carries a boolean `mirror`.  When it is true, every
square on the patch is drawn a second time on the patch's mirror —
the same patch shifted by `mirrorAngle` in φ.

The MIRROR FLIP toggle determines whether the mirror is a faithful
copy of the original or a reflected ghost.

LABELS
======
Every patch and every shape carries a user-editable name.

MODULE LAYOUT
=============
The JS is split across five files so that edits to one part — the
warp chart, the projection, the edit solvers, the flat view — do
not require touching the others:

    pg_view_squares_core.py     model, frame, corners, dims,
                                mirror, horizon, lifecycle
    pg_view_squares_warp.py     the plane-warp chart
    pg_view_squares_proj.py     cone-view projection and rendering
    pg_view_squares_edit.py     drag solvers, edit operations,
                                cone-band hit testing
    pg_view_squares_flat.py     flat-view projection, edit
                                wrappers, hit testing, labels

This file assembles them into the single FLOAT_SQUARES_JS string the
HTML bundle expects.  Order matters only for readability — every
name is a function or a const initialized before any function that
reads it is called.
"""

from pg_view_squares_core import SQUARES_CORE_JS
from pg_view_squares_warp import SQUARES_WARP_JS
from pg_view_squares_proj import SQUARES_PROJ_JS
from pg_view_squares_edit import SQUARES_EDIT_JS
from pg_view_squares_flat import SQUARES_FLAT_JS


FLOAT_SQUARES_JS = (
    SQUARES_CORE_JS
    + SQUARES_WARP_JS
    + SQUARES_PROJ_JS
    + SQUARES_EDIT_JS
    + SQUARES_FLAT_JS
)
