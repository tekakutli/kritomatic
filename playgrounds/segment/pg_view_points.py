"""Staging points (dashed, hollow) and per-mask points (filled, solid).

Staging points are what the user places before pressing Segment All;
per-mask points are what the user places to refine a specific mask.
They share the point-drawing code but differ in fill mode so a glance
at the canvas tells you which you're looking at.

Draws on the OVERLAY canvas (`overlayCtx`, `#overlayCanvas`).  Points
are simple 2D primitives (arcs + fills); they belong with the drafts
and chrome text, not with the image layer.  Point mutations are
discrete events (click), and each fires exactly one overlay repaint."""

VIEW_POINTS_JS = r"""
import { overlayCtx, board, imageToScreen } from "./pg_state.js";

/* ============================================================
   One point
   ============================================================ */

function _drawPoint(x, y, isPositive, filled) {
  const [sx, sy] = imageToScreen(x, y);
  overlayCtx.beginPath();
  overlayCtx.arc(sx, sy, 6, 0, Math.PI * 2);
  if (filled) {
    overlayCtx.fillStyle = isPositive
      ? "rgba(0, 229, 255, 0.92)"
      : "rgba(255, 90, 105, 0.92)";
    overlayCtx.fill();
    overlayCtx.strokeStyle = "#0a0e14";
    overlayCtx.lineWidth = 1.5;
    overlayCtx.stroke();
  } else {
    overlayCtx.strokeStyle = isPositive
      ? "rgba(0, 229, 255, 0.70)"
      : "rgba(255, 90, 105, 0.70)";
    overlayCtx.lineWidth = 1.6;
    overlayCtx.setLineDash([3, 2]);
    overlayCtx.stroke();
    overlayCtx.setLineDash([]);
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