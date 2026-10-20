"""
pg_view_squares_warp.py — the plane-warp chart.

ES module.  One function, `_warpedFrameToWorld`.  At warp = 0 it is
the identity (frameToWorld).  At warp = 1 the SAME plane is
addressed with a u-scale that grows with distance from the apex.
In between, the u-scale interpolates linearly.

THE PLANE IS NEVER MOVED AND NEVER BENT
=======================================
Every warped point is still expressible as  C + a·U + b·V  with
real a, b — exactly as at warp = 0.  The plane is the same plane.
Only the coefficients a, b change.
"""

SQUARES_WARP_JS = r"""
import { cone } from "./pg_state.js";

/* ==========================================================================
   PLANE WARP — the chart
   ========================================================================== */

export function _warpedFrameToWorld(f, q, u, v) {
  /* Axial chart — the warp = 0 result. */
  const ax = f.Cx + u * f.Ux + v * f.Vx;
  const ay = f.Cy + u * f.Uy + v * f.Vy;

  const warp = (q && typeof q.warp === "number")
                 ? Math.max(0, Math.min(1, q.warp)) : 0;
  if (warp < 1e-6 || !q) return [ax, ay];

  /* Distance from the apex to the patch centre. */
  const dx0 = f.Cx - cone.ax;
  const dy0 = f.Cy - cone.ay;
  const r0  = Math.hypot(dx0, dy0);
  if (r0 < 1e-6) return [ax, ay];

  /* u-scale: 1 at warp = 0, (r0 − v)/r0 at warp = 1. */
  let uScale = 1 - warp * v / r0;
  if (uScale < 0) uScale = 0;

  const cx = f.Cx + u * uScale * f.Ux + v * f.Vx;
  const cy = f.Cy + u * uScale * f.Uy + v * f.Vy;

  return [cx, cy];
}
"""
