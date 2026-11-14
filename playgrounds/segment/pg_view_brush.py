"""The three interactive draft layers that follow the mouse:

  brush cursor    — inverted halo ring around the cursor; anchored
                    during a Shift-drag resize
  lasso draft     — vertex dots + closing preview
  freehand draft  — live polyline with faint fill

These are three distinct layers with one thing in common: they are
transient and drawn only while the user is interacting with the
region tool.  Each is its own private function; the exported names
are the ones `pg_view.js` calls in order.

Draws on the OVERLAY canvas (`overlayCtx`, `#overlayCanvas`).  The
brush cursor follows `board.mouse` on every move; the drafts follow
`board.lassoPoints` and `board.freehandPoints`.  Splitting these onto
their own canvas means a brush-mode mousemove (no button, no state
change beyond the cursor position) repaints only the overlay, not the
image, region, or point layers."""

VIEW_BRUSH_JS = r"""
import { overlayCtx, board, imageToScreen } from "./pg_state.js";
import { selectedMask } from "./pg_masks.js";

/* ============================================================
   Brush cursor
   ============================================================
   The halo ring has two jobs:

     - regular painting: follows the cursor
     - Shift-drag resize: locked to the anchor while only the SIZE
       changes

   Add vs subtract tints the ring warm yellow vs red, matching the
   region edge and lasso drafts' subtract palette. */

function _drawBrushCursor(r) {
  if (!board.regionMode) return;
  if (board.regionTool !== 'brush') return;
  if (!selectedMask()) return;

  const anchor = board.resizeAnchor;
  const cx = anchor ? anchor.x : board.mouse.x;
  const cy = anchor ? anchor.y : board.mouse.y;

  if (!anchor && !board.mouse.inside) return;

  const [sx, sy] = imageToScreen(cx, cy);
  const radiusScreen = Math.max(2, (board.regionSize / 2) * r.s);

  const fillColor = board.regionSubtract
    ? "rgba(255, 130, 150, 0.10)"
    : "rgba(255, 200, 80, 0.10)";
  const ringColor = board.regionSubtract
    ? "rgba(255, 130, 150, 1.0)"
    : "rgba(255, 200, 80, 1.0)";
  const anchorColor = board.regionSubtract
    ? "rgba(255, 160, 180, 1.0)"
    : "rgba(255, 240, 120, 1.0)";

  const coreWidth  = anchor ? 2.2 : 1.8;
  const haloWidth  = coreWidth + 3.2;

  overlayCtx.save();

  overlayCtx.beginPath();
  overlayCtx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  overlayCtx.fillStyle = fillColor;
  overlayCtx.fill();

  overlayCtx.beginPath();
  overlayCtx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  overlayCtx.strokeStyle = "rgba(8, 10, 14, 0.82)";
  overlayCtx.lineWidth = haloWidth;
  overlayCtx.stroke();

  overlayCtx.beginPath();
  overlayCtx.arc(sx, sy, radiusScreen, 0, Math.PI * 2);
  overlayCtx.strokeStyle = anchor ? anchorColor : ringColor;
  overlayCtx.lineWidth = coreWidth;
  overlayCtx.stroke();

  overlayCtx.beginPath();
  overlayCtx.arc(sx, sy, 3.5, 0, Math.PI * 2);
  overlayCtx.fillStyle = "rgba(8, 10, 14, 0.85)";
  overlayCtx.fill();
  overlayCtx.beginPath();
  overlayCtx.arc(sx, sy, 1.5, 0, Math.PI * 2);
  overlayCtx.fillStyle = "rgba(255, 255, 255, 0.95)";
  overlayCtx.fill();

  overlayCtx.restore();
}

/* ============================================================
   Shared halo stroke */

function _haloStroke(drawPath, brightColor, width, dash) {
  const dashes = dash || [];

  overlayCtx.beginPath();
  drawPath();
  overlayCtx.strokeStyle = "rgba(8, 10, 14, 0.82)";
  overlayCtx.lineWidth = width + 3.2;
  overlayCtx.setLineDash(dashes);
  overlayCtx.stroke();

  overlayCtx.beginPath();
  drawPath();
  overlayCtx.strokeStyle = brightColor;
  overlayCtx.lineWidth = width;
  overlayCtx.setLineDash(dashes);
  overlayCtx.stroke();
  overlayCtx.setLineDash([]);
}

/* ============================================================
   Lasso draft */

function _drawLassoDraft() {
  if (board.lassoPoints.length === 0) return;

  const screenPts = board.lassoPoints.map(p => imageToScreen(p.x, p.y));

  const bright      = board.regionSubtract
    ? "rgba(255, 130, 150, 1.0)"
    : "rgba(255, 220, 100, 1.0)";
  const brightSoft  = board.regionSubtract
    ? "rgba(255, 130, 150, 0.90)"
    : "rgba(255, 220, 100, 0.90)";
  const brightFaint = board.regionSubtract
    ? "rgba(255, 130, 150, 0.55)"
    : "rgba(255, 220, 100, 0.55)";
  const vertexColor = board.regionSubtract
    ? "rgba(255, 130, 150, 0.95)"
    : "rgba(255, 220, 100, 0.95)";

  overlayCtx.save();
  overlayCtx.lineJoin = "round";
  overlayCtx.lineCap = "round";

  if (screenPts.length > 1) {
    _haloStroke(() => {
      overlayCtx.moveTo(screenPts[0][0], screenPts[0][1]);
      for (let i = 1; i < screenPts.length; i++) {
        overlayCtx.lineTo(screenPts[i][0], screenPts[i][1]);
      }
    }, bright, 2.8, null);
  }

  {
    const [sx, sy] = imageToScreen(board.mouse.x, board.mouse.y);
    const last = screenPts[screenPts.length - 1];

    _haloStroke(() => {
      overlayCtx.moveTo(last[0], last[1]);
      overlayCtx.lineTo(sx, sy);
    }, brightSoft, 2.4, [7, 5]);

    if (screenPts.length >= 3) {
      const first = screenPts[0];
      _haloStroke(() => {
        overlayCtx.moveTo(sx, sy);
        overlayCtx.lineTo(first[0], first[1]);
      }, brightFaint, 1.8, [6, 6]);
    }
  }

  for (let i = 0; i < screenPts.length; i++) {
    const [px, py] = screenPts[i];
    const isFirst = (i === 0);
    const isLast  = (i === screenPts.length - 1);

    if (isFirst && screenPts.length >= 3) {
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 11, 0, Math.PI * 2);
      overlayCtx.fillStyle = "rgba(8, 10, 14, 0.85)";
      overlayCtx.fill();
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 8.5, 0, Math.PI * 2);
      overlayCtx.fillStyle = vertexColor;
      overlayCtx.fill();
      overlayCtx.strokeStyle = "rgba(255, 255, 255, 1.0)";
      overlayCtx.lineWidth = 2.2;
      overlayCtx.stroke();
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 3, 0, Math.PI * 2);
      overlayCtx.fillStyle = "rgba(255, 255, 255, 1.0)";
      overlayCtx.fill();
    } else if (isLast) {
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 8, 0, Math.PI * 2);
      overlayCtx.fillStyle = "rgba(8, 10, 14, 0.80)";
      overlayCtx.fill();
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 5.5, 0, Math.PI * 2);
      overlayCtx.fillStyle = vertexColor;
      overlayCtx.fill();
      overlayCtx.strokeStyle = "rgba(255, 255, 255, 1.0)";
      overlayCtx.lineWidth = 2;
      overlayCtx.stroke();
    } else {
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 7, 0, Math.PI * 2);
      overlayCtx.fillStyle = "rgba(8, 10, 14, 0.75)";
      overlayCtx.fill();
      overlayCtx.beginPath();
      overlayCtx.arc(px, py, 4.5, 0, Math.PI * 2);
      overlayCtx.fillStyle = vertexColor;
      overlayCtx.fill();
    }
  }

  overlayCtx.restore();
}

/* ============================================================
   Freehand draft */

function _drawFreehandDraft() {
  if (!board.freehandActive) return;
  if (!board.freehandPoints || board.freehandPoints.length === 0) return;

  const screenPts = board.freehandPoints.map(p => imageToScreen(p.x, p.y));
  const isErase = !!board.freehandIsErase;

  const strokeBright = isErase
    ? "rgba(255, 130, 150, 1.0)"
    : "rgba(255, 220, 100, 1.0)";
  const strokeSoft = isErase
    ? "rgba(255, 130, 150, 0.55)"
    : "rgba(255, 220, 100, 0.55)";
  const fillSoft = isErase
    ? "rgba(255, 130, 150, 0.18)"
    : "rgba(255, 220, 100, 0.18)";

  overlayCtx.save();
  overlayCtx.lineJoin = "round";
  overlayCtx.lineCap = "round";

  if (screenPts.length > 1) {
    _haloStroke(() => {
      overlayCtx.moveTo(screenPts[0][0], screenPts[0][1]);
      for (let i = 1; i < screenPts.length; i++) {
        overlayCtx.lineTo(screenPts[i][0], screenPts[i][1]);
      }
    }, strokeBright, 2.8, null);
  }

  if (screenPts.length >= 3) {
    overlayCtx.beginPath();
    overlayCtx.moveTo(screenPts[0][0], screenPts[0][1]);
    for (let i = 1; i < screenPts.length; i++) {
      overlayCtx.lineTo(screenPts[i][0], screenPts[i][1]);
    }
    overlayCtx.closePath();
    overlayCtx.fillStyle = fillSoft;
    overlayCtx.fill();

    const first = screenPts[0];
    const last  = screenPts[screenPts.length - 1];
    _haloStroke(() => {
      overlayCtx.moveTo(last[0], last[1]);
      overlayCtx.lineTo(first[0], first[1]);
    }, strokeSoft, 1.8, [6, 6]);
  }

  const [fx, fy] = screenPts[0];
  overlayCtx.beginPath();
  overlayCtx.arc(fx, fy, 6.5, 0, Math.PI * 2);
  overlayCtx.fillStyle = "rgba(8, 10, 14, 0.85)";
  overlayCtx.fill();
  overlayCtx.beginPath();
  overlayCtx.arc(fx, fy, 4.5, 0, Math.PI * 2);
  overlayCtx.fillStyle = strokeBright;
  overlayCtx.fill();
  overlayCtx.strokeStyle = "rgba(255, 255, 255, 1.0)";
  overlayCtx.lineWidth = 1.8;
  overlayCtx.stroke();

  overlayCtx.restore();
}

export {
  _drawBrushCursor as drawBrushCursor,
  _drawLassoDraft as drawLassoDraft,
  _drawFreehandDraft as drawFreehandDraft,
};
"""