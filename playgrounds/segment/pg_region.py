"""Region canvas: lazy allocation, stroke painting, lasso fill, clear.

Every entry point calls `_scheduleDraw()` on state changes (region
visibility, stroke completion) so the canvas reflects the region
promptly.  The actual region-tool dispatch (brush vs lasso vs
freehand, add vs subtract) lives in `pg_dispatch.js`; this module
owns only the canvas-level mutations."""

REGION_JS = r"""
import { board, _scheduleDraw } from "./pg_state.js";
import { selectedMask } from "./pg_masks.js";

/* ============================================================
   Region canvas
   ============================================================ */

export function _ensureRegionCanvas(mask) {
  if (mask.regionCanvas) return mask.regionCanvas;
  const c = document.createElement("canvas");
  c.width  = board.imageWidth;
  c.height = board.imageHeight;
  mask.regionCanvas = c;
  return c;
}

export function clearSelectedMaskRegion() {
  const m = selectedMask();
  if (!m || !m.regionCanvas) return false;
  m.regionCanvas = null;
  m._regionEdgePoints = null;
  m._regionChains = null;
  board.regionStrokeActive = false;
  _scheduleDraw();
  return true;
}

export function paintRegionStroke(ix, iy, isErase, lastX, lastY) {
  const m = selectedMask();
  if (!m) return false;
  const c = _ensureRegionCanvas(m);
  const rctx = c.getContext("2d");
  const r = Math.max(1, board.regionSize) / 2;
  const color = isErase ? "rgba(0,0,0,1)" : "rgba(255, 200, 80, 1)";
  rctx.globalCompositeOperation = isErase ? "destination-out" : "source-over";

  const dist = Math.hypot(ix - lastX, iy - lastY);
  const stepPx = Math.max(1, r * 0.5);
  const steps = Math.max(1, Math.ceil(dist / stepPx));
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    const px = lastX + (ix - lastX) * t;
    const py = lastY + (iy - lastY) * t;
    rctx.beginPath();
    rctx.arc(px, py, r, 0, Math.PI * 2);
    rctx.fillStyle = color;
    rctx.fill();
  }

  board.regionStrokeActive = true;
  board.regionStrokeIsErase = !!isErase;
  m._regionEdgePoints = null;   // invalidate edge cache
  m._regionChains = null;       // invalidate even-dot chain cache
  return true;
}

export function fillLassoPolygonInRegion(points, isErase) {
  const m = selectedMask();
  if (!m || points.length < 3) return false;
  const c = _ensureRegionCanvas(m);
  const rctx = c.getContext("2d");
  rctx.globalCompositeOperation = isErase ? "destination-out" : "source-over";
  rctx.beginPath();
  rctx.moveTo(points[0].x, points[0].y);
  for (let i = 1; i < points.length; i++) rctx.lineTo(points[i].x, points[i].y);
  rctx.closePath();
  rctx.fillStyle = isErase ? "rgba(0,0,0,1)" : "rgba(255, 200, 80, 1)";
  rctx.fill();
  m._regionEdgePoints = null;
  m._regionChains = null;
  return true;
}
"""
