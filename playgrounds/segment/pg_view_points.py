"""Staging points (dashed, hollow) and per-mask points (filled, solid).

Staging points are what the user places before pressing Segment All;
per-mask points are what the user places to refine a specific mask.
They share the point-drawing code but differ in fill mode so a glance
at the canvas tells you which you're looking at."""

VIEW_POINTS_JS = r"""
import { ctx, board, imageToScreen } from "./pg_state.js";

/* ============================================================
   One point
   ============================================================ */

function _drawPoint(x, y, isPositive, filled) {
  const [sx, sy] = imageToScreen(x, y);
  ctx.beginPath();
  ctx.arc(sx, sy, 6, 0, Math.PI * 2);
  if (filled) {
    ctx.fillStyle = isPositive
      ? "rgba(0, 229, 255, 0.92)"
      : "rgba(255, 90, 105, 0.92)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.5;
    ctx.stroke();
  } else {
    ctx.strokeStyle = isPositive
      ? "rgba(0, 229, 255, 0.70)"
      : "rgba(255, 90, 105, 0.70)";
    ctx.lineWidth = 1.6;
    ctx.setLineDash([3, 2]);
    ctx.stroke();
    ctx.setLineDash([]);
  }
}

/* ============================================================
   All points
   ============================================================ */

export function drawPoints(r, sm) {
  for (const p of board.stagingPoints) {
    _drawPoint(p.x, p.y, p.isPositive, false);
  }
  if (sm) {
    for (const p of sm.points) {
      _drawPoint(p.x, p.y, p.isPositive, true);
    }
  }
}
"""
