"""Viewer rectangle: hit testing, drag, resize.

The viewer is a rectangle on the @thelayout canvas that defines the
region that will actually be generated. It is:
  · sized by the selected ratio (aspect-ratio-locked)
  · draggable from its interior
  · resizable from any of its four corners
  · always containing the bboxes (bboxes are viewer-normalized)

All functions operate on `board.viewer` (canvas-normalized coords) and
the viewer drag state (`board.viewerDrag*`). Rendering lives in
`pg_view_image.py`; dispatch wiring lives in `pg_dispatch.py`.
"""

VIEWER_JS = r"""
import { board, viewerAspect } from "./pg_state.js";

export const VIEWER_HANDLE_TOL_PX = 10;
export const VIEWER_MIN_SIZE = 0.05;

export function getViewerHandles() {
  const v = board.viewer;
  return [
    { name: 'nw', x: v.x,       y: v.y },
    { name: 'ne', x: v.x + v.w, y: v.y },
    { name: 'sw', x: v.x,       y: v.y + v.h },
    { name: 'se', x: v.x + v.w, y: v.y + v.h },
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
  for (const h of getViewerHandles()) {
    const hpx = r.x0 + h.x * r.w;
    const hpy = r.y0 + h.y * r.h;
    if (Math.abs(sx - hpx) <= tol && Math.abs(sy - hpy) <= tol) {
      return h.name;
    }
  }
  return null;
}

/* cx, cy are canvas image pixels. */
export function hitTestViewerInterior(cx, cy) {
  const v = board.viewer;
  const x1 = v.x * board.imageWidth;
  const y1 = v.y * board.imageHeight;
  const x2 = (v.x + v.w) * board.imageWidth;
  const y2 = (v.y + v.h) * board.imageHeight;
  return cx >= x1 && cx <= x2 && cy >= y1 && cy <= y2;
}

/* Move the viewer by (dxNorm, dyNorm) in canvas-normalized coords. */
export function applyViewerMove(dxNorm, dyNorm) {
  const v = board.viewer;
  const start = board.viewerDragStart;
  v.x = Math.max(0, Math.min(1 - v.w, start.x + dxNorm));
  v.y = Math.max(0, Math.min(1 - v.h, start.y + dyNorm));
}

/* Resize from a corner handle. Aspect ratio is preserved. */
export function applyViewerResize(handle, mouseCx, mouseCy) {
  const v = board.viewer;
  const r = viewerAspect();
  const cw = board.imageWidth;
  const ch = board.imageHeight;
  if (!cw || !ch) return;

  const mx = mouseCx / cw;
  const my = mouseCy / ch;

  let anchorX, anchorY, rawW, rawH;
  if (handle === 'se') {
    anchorX = v.x;       anchorY = v.y;
    rawW = Math.max(VIEWER_MIN_SIZE, mx - anchorX);
    rawH = Math.max(VIEWER_MIN_SIZE, my - anchorY);
  } else if (handle === 'nw') {
    anchorX = v.x + v.w; anchorY = v.y + v.h;
    rawW = Math.max(VIEWER_MIN_SIZE, anchorX - mx);
    rawH = Math.max(VIEWER_MIN_SIZE, anchorY - my);
  } else if (handle === 'ne') {
    anchorX = v.x;       anchorY = v.y + v.h;
    rawW = Math.max(VIEWER_MIN_SIZE, mx - anchorX);
    rawH = Math.max(VIEWER_MIN_SIZE, anchorY - my);
  } else if (handle === 'sw') {
    anchorX = v.x + v.w; anchorY = v.y;
    rawW = Math.max(VIEWER_MIN_SIZE, anchorX - mx);
    rawH = Math.max(VIEWER_MIN_SIZE, my - anchorY);
  } else {
    return;
  }

  // Aspect constraint: rawW * cw / (rawH * ch) = r
  // Choose the dimension that keeps both within the user's drag.
  const newH_by_w = rawW * cw / (r * ch);
  let newW, newH;
  if (newH_by_w <= rawH) {
    newW = rawW;
    newH = newH_by_w;
  } else {
    newH = rawH;
    newW = rawH * r * ch / cw;
  }

  // Compute new position from anchor + size
  let newX, newY;
  if (handle === 'se')      { newX = v.x;      newY = v.y; }
  else if (handle === 'nw') { newX = anchorX - newW; newY = anchorY - newH; }
  else if (handle === 'ne') { newX = v.x;      newY = anchorY - newH; }
  else /* sw */             { newX = anchorX - newW; newY = v.y; }

  // Clamp position to canvas; if clamped, shrink proportionally.
  if (newX < 0) {
    newW = anchorX;
    newH = newW * cw / (r * ch);
    newX = 0;
    newY = (handle === 'sw') ? v.y : anchorY - newH;
  }
  if (newY < 0) {
    newH = anchorY;
    newW = newH * r * ch / cw;
    newY = 0;
    newX = (handle === 'ne') ? v.x : anchorX - newW;
  }
  if (newX + newW > 1) {
    newW = 1 - newX;
    newH = newW * cw / (r * ch);
  }
  if (newY + newH > 1) {
    newH = 1 - newY;
    newW = newH * r * ch / cw;
  }

  v.x = Math.max(0, newX);
  v.y = Math.max(0, newY);
  v.w = Math.max(VIEWER_MIN_SIZE, newW);
  v.h = Math.max(VIEWER_MIN_SIZE, newH);
}
"""