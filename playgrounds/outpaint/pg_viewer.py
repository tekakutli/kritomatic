"""Viewer rectangle: hit testing, drag, resize — in area pixels.

The viewer is `{pxX, pxY, pxW, pxH, ratio}` in area-pixel coordinates.
Aspect ratio locked to `ratio`.  All positions are clamped so the
viewer is always fully inside the area.

AREA HANDLES
============
The area's own four corners are also hit-tested here — they live in
*screen* space (the area always spans `(r.x0, r.y0) → (r.x0+r.w,
r.y0+r.h)` on screen), not area-pixel space, because the area is
the whole fitted rect and has no origin offset in the model.  The
actual drag logic lives in `pg_state.js` (`beginAreaDrag`,
`updateAreaDrag`, `endAreaDrag`); this module only surfaces the
hit-test so `pg_dispatch.js` can route mousedown correctly.
"""

VIEWER_JS = r"""
import { board, viewerAspect } from "./pg_state.js";
import { VIEWER_MIN_SIZE_PX } from "./pg_tuning.js";

export const VIEWER_HANDLE_TOL_PX = 10;

export function getViewerHandlesPx() {
  const v = board.viewer;
  return [
    { name: 'nw', x: v.pxX,           y: v.pxY },
    { name: 'ne', x: v.pxX + v.pxW,   y: v.pxY },
    { name: 'sw', x: v.pxX,           y: v.pxY + v.pxH },
    { name: 'se', x: v.pxX + v.pxW,   y: v.pxY + v.pxH },
  ];
}

export function viewerCursorFor(handle) {
  if (handle === 'move') return 'move';
  if (handle === 'nw' || handle === 'se') return 'nwse-resize';
  if (handle === 'ne' || handle === 'sw') return 'nesw-resize';
  return 'default';
}

/* sx, sy are screen pixels; r is fittedImageRect(). */
export function hitTestViewerHandle(sx, sy, r) {
  if (!r) return null;
  const tol = VIEWER_HANDLE_TOL_PX;
  for (const h of getViewerHandlesPx()) {
    const hx = r.x0 + h.x * r.s;
    const hy = r.y0 + h.y * r.s;
    if (Math.abs(sx - hx) <= tol && Math.abs(sy - hy) <= tol) {
      return h.name;
    }
  }
  return null;
}

/* cx, cy are area pixels. */
export function hitTestViewerInterior(cx, cy) {
  const v = board.viewer;
  return cx >= v.pxX && cx <= v.pxX + v.pxW
      && cy >= v.pxY && cy <= v.pxY + v.pxH;
}

/* (dxPx, dyPx) are area-pixel deltas from the drag start. */
export function applyViewerMove(dxPx, dyPx) {
  const v = board.viewer;
  const start = board.viewerDragStart;
  v.pxX = Math.max(0, Math.min(board.areaWidth  - v.pxW, start.pxX + dxPx));
  v.pxY = Math.max(0, Math.min(board.areaHeight - v.pxH, start.pxY + dyPx));
}

/* Resize from a corner handle.  (mouseCx, mouseCy) are area pixels.
 * Aspect ratio is preserved.  The anchor corner (opposite to the
 * dragged handle) stays fixed.  The viewer NEVER exceeds the area
 * bounds — if the drag pushes past an edge, the size is capped with
 * aspect lock so the anchor still lands exactly where it was. */
export function applyViewerResize(handle, mouseCx, mouseCy) {
  const v = board.viewer;
  const r = viewerAspect();
  const aw = board.areaWidth;
  const ah = board.areaHeight;
  if (!aw || !ah) return;

  const mx = mouseCx;
  const my = mouseCy;
  const MIN = VIEWER_MIN_SIZE_PX;

  // anchor = the corner that stays fixed (opposite of dragged handle);
  // maxW / maxH = how far the viewer can grow from that anchor
  // before hitting an area edge.
  let anchorX, anchorY, maxW, maxH, desiredW, desiredH;
  if (handle === 'se') {
    anchorX = v.pxX;         anchorY = v.pxY;
    maxW = aw - anchorX;     maxH = ah - anchorY;
    desiredW = mx - anchorX; desiredH = my - anchorY;
  } else if (handle === 'nw') {
    anchorX = v.pxX + v.pxW; anchorY = v.pxY + v.pxH;
    maxW = anchorX;          maxH = anchorY;
    desiredW = anchorX - mx; desiredH = anchorY - my;
  } else if (handle === 'ne') {
    anchorX = v.pxX;         anchorY = v.pxY + v.pxH;
    maxW = aw - anchorX;     maxH = anchorY;
    desiredW = mx - anchorX; desiredH = anchorY - my;
  } else if (handle === 'sw') {
    anchorX = v.pxX + v.pxW; anchorY = v.pxY;
    maxW = anchorX;          maxH = ah - anchorY;
    desiredW = anchorX - mx; desiredH = my - anchorY;
  } else {
    return;
  }

  // Floor the requested delta before aspect-fit so a drag toward the
  // anchor doesn't produce a negative size.
  desiredW = Math.max(1, desiredW);
  desiredH = Math.max(1, desiredH);

  // Aspect-fit inside the user's requested box.
  let fitW = desiredW;
  let fitH = fitW / r;
  if (fitH > desiredH) {
    fitH = desiredH;
    fitW = fitH * r;
  }

  // MIN floor first (may overflow the anchor box in pathological
  // aspect ratios — a 100:1 viewer can't satisfy both MIN and a
  // tight anchor simultaneously).
  if (fitW < MIN) { fitW = MIN; fitH = fitW / r; }
  if (fitH < MIN) { fitH = MIN; fitW = fitH * r; }

  // Hard cap to the anchor box, aspect-corrected.  This is what
  // keeps the viewer inside the area AND the anchor corner fixed.
  if (fitW > maxW) { fitW = maxW; fitH = fitW / r; }
  if (fitH > maxH) { fitH = maxH; fitW = fitH * r; }

  // Position from anchor.  The anchor corner is exact.
  let fx, fy;
  if (handle === 'se')      { fx = anchorX;        fy = anchorY; }
  else if (handle === 'nw') { fx = anchorX - fitW; fy = anchorY - fitH; }
  else if (handle === 'ne') { fx = anchorX;        fy = anchorY - fitH; }
  else /* sw */             { fx = anchorX - fitW; fy = anchorY; }

  // Defensive position clamp (should be no-op after maxW/maxH cap,
  // but keeps the viewer inside the area if any rounding slipped).
  v.pxX = Math.max(0, Math.min(aw - fitW, fx));
  v.pxY = Math.max(0, Math.min(ah - fitH, fy));
  v.pxW = fitW;
  v.pxH = fitH;
}

/* ============================================================
   Area corner handles — screen-space
   ============================================================
   The area is the whole fitted rect: its corners are at the four
   corners of `r` on screen.  Unlike the viewer, the area has no
   origin offset in the model, so hit-testing happens directly in
   screen space.  Aspect ratio is NOT locked (the area is a plain
   rect — "not constrained into keeping the ratio" per the feature
   spec). */

export function getAreaHandlesScreen(r) {
  if (!r) return [];
  return [
    { name: 'nw', x: r.x0,       y: r.y0 },
    { name: 'ne', x: r.x0 + r.w, y: r.y0 },
    { name: 'sw', x: r.x0,       y: r.y0 + r.h },
    { name: 'se', x: r.x0 + r.w, y: r.y0 + r.h },
  ];
}

export function hitTestAreaHandle(sx, sy, r) {
  if (!r) return null;
  const tol = VIEWER_HANDLE_TOL_PX;
  for (const h of getAreaHandlesScreen(r)) {
    if (Math.abs(sx - h.x) <= tol && Math.abs(sy - h.y) <= tol) {
      return h.name;
    }
  }
  return null;
}
"""