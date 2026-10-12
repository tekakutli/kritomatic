"""
pg_view_squares_warp.py — the plane-warp chart.

A single function.  At warp = 0 it is the identity (frameToWorld).
At warp = 1 the SAME plane is addressed with a RADIALLY-STRETCHED
chart: the u-coefficient is scaled by (r0 − v) / r0, where r0 is
the distance from the apex to the patch centre and v is the shape's
own v-coordinate.  In between the u-scale interpolates linearly
between 1 (warp = 0) and (r0 − v) / r0 (warp = 1).

THE PLANE IS NEVER MOVED AND NEVER BENT
=======================================
Every warped point is still expressible as  C + a·U + b·V  with
real a, b — exactly as at warp = 0.  The plane is the same plane.
Only the coefficients a, b change.  Under no circumstance does the
chart leave the patch's own tangential plane, and under no
circumstance does it evaluate the cone's curved surface.

WHY NOT A CONCENTRIC (ARC) CHART
================================
An earlier revision used a polar chart centred on the apex: constant
u advanced an ANGLE around the apex, so a shape's corners traced an
arc.  That chart has the "space grows with distance from apex"
property, but it also produces massive angular extent when the
patch's own u-width is comparable to r0: for a patch 4.38 units
wide at r0 = 2.07, a single square wraps 76° around the apex and
the shape is nothing like a square on screen.  The radial chart
below has the same spacing-vs-radius property — spacing per unit u
at radius ρ is ρ/r0 — but keeps each shape LINEAR in u, so shapes
do not fan out and do not depend on the viewing angle.

THE PROPERTIES THAT MATTER
==========================
Let ρ = r0 − v be the point's radial distance from the apex (v
points toward the apex, so larger v means smaller ρ, as at
warp = 0).

    * At the patch centre (v = 0) the u-scale is 1, so the chart
      matches the axial chart EXACTLY along the row through C.
      Moving the warp slider never resizes a shape sitting on the
      patch centre; the crossover radius is the patch centre.

    * Space between adjacent shapes in a row at constant v scales
      as ρ/r0.  Rows nearer the apex have LESS space between their
      squares; rows further from it have MORE.  This is exactly the
      behaviour described: "the space between the squares grows,
      less nearer the apex and more further away from it."

    * The v-coefficient is unmodified (b = v), so the horizon clip
      (v ≤ vMax) still collapses points at vMax onto the apex on
      screen and the perspective taper in the projection is
      unchanged.

    * The chart is linear in u at fixed v, so a shape's u-extent is
      scaled uniformly.  A shape whose top and bottom sit at
      different v's becomes a trapezoid (its top and bottom edges
      scale differently); it does NOT curve, and it does NOT fan
      around the apex.

This is the file to edit when the warp curve itself changes;
nothing else in the codebase needs to know how the chart works.
"""

SQUARES_WARP_JS = r"""
/* ==========================================================================
   PLANE WARP — the chart
   ==========================================================================
   Maps a patch-local (u, v) point to a world position, applying the
   patch's `warp` parameter.

   Both charts below are charts on the SAME 2D plane — the patch's
   own tangential plane.  The plane itself is never moved and never
   bent.  Only the way (u, v) addresses it changes.

     warp = 0   the historic axial chart:
                  world = C + u·U + v·V.

     warp = 1   the same plane, addressed with a u-scale that grows
                with distance from the apex:
                  world = C + u·(ρ/r0)·U + v·V,
                where ρ = r0 − v is the point's radial distance
                from the apex and r0 is the distance from the apex
                to the patch centre C.

     in between the u-scale interpolates linearly between 1 and
                ρ/r0.

   Every warped point is still expressible as  C + a·U + b·V, so
   the chart never leaves the patch's own plane.  Nothing below
   evaluates a curved surface.

   Consequences:

     * At v = 0 the u-scale is 1 for every warp value, so the row
       through the patch centre is unchanged.

     * Spacing per unit u at radius ρ is ρ/r0.  Rows nearer the
       apex have less space between their squares; rows further
       from it have more.

     * A shape at (u0, v0) with corners at (u0 ± hw, v0 ± hh) has
       its top edge scaled by (r0 − (v0 − hh))/r0 and its bottom
       edge by (r0 − (v0 + hh))/r0.  The shape becomes a trapezoid
       (narrower toward the apex, wider away from it), but it does
       not curve and does not fan around the apex.

   See the PLANE WARP section of pg_view_squares.py.
   ========================================================================== */

function _warpedFrameToWorld(f, q, u, v) {
  /* Axial chart — the warp = 0 result. */
  const ax = f.Cx + u * f.Ux + v * f.Vx;
  const ay = f.Cy + u * f.Uy + v * f.Vy;

  const warp = (q && typeof q.warp === "number")
                 ? Math.max(0, Math.min(1, q.warp)) : 0;
  if (warp < 1e-6 || !q) return [ax, ay];

  /* Distance from the apex to the patch centre.  If the apex sits
     on the patch centre there is no radius to stretch from, so the
     chart degenerates to the axial one. */
  const dx0 = f.Cx - cone.ax;
  const dy0 = f.Cy - cone.ay;
  const r0  = Math.hypot(dx0, dy0);
  if (r0 < 1e-6) return [ax, ay];

  /* u-scale: 1 at warp = 0, (r0 − v)/r0 at warp = 1.  Interpolated
     linearly by warp.  Clamped non-negative so a shape dragged past
     the apex does not mirror its u-axis; the horizon clip handles
     the rest of that case. */
  let uScale = 1 - warp * v / r0;
  if (uScale < 0) uScale = 0;

  const cx = f.Cx + u * uScale * f.Ux + v * f.Vx;
  const cy = f.Cy + u * uScale * f.Uy + v * f.Vy;

  return [cx, cy];
}
"""
