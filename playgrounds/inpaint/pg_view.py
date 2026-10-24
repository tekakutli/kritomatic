"""The main `drawView()` entry point.

Layers, in order:

  1. image + selected-mask tinted overlays     (pg_view_image)
  2. inpaint context rectangle                 (pg_view_inpaint)
  3. chrome text (top-left status line)

Adding a new layer means: write a new `pg_view_*.py`, add one call to
`drawView()`, done — no other file is touched."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { selectedMask } from "./pg_masks.js";
import { drawImageLayer } from "./pg_view_image.js";
import { drawInpaintContext } from "./pg_view_inpaint.js";

/* ============================================================
   Header text — the one-line status bar at top-left
   ============================================================
     single mask selected   "MASK N SELECTED"
     multiple selected      "MULTIPLE SELECTED"
     no masks yet           "IMPORT A MASK to begin"
     masks but none picked  "NO MASK SELECTED"

   When inpainting is enabled, " · INPAINT" is appended so the user
   never loses sight of the mode they enabled without seeing the rect
   (e.g. after panning/zooming the rect off-screen). */

function _drawChromeText(sm) {
  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "MASK " + (selIdx + 1) + " SELECTED";
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "IMPORT A MASK to begin";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  if (board.inpaintEnabled) ctxLabel += " \u00b7 INPAINT";

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
    + "   \u00b7   " + ctxLabel
    + "   \u00b7   MASKS " + board.masks.length,
    14, 14,
  );
  ctx.restore();
}

/* ============================================================
   Main draw
   ============================================================ */

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;

  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("drop an image to begin", cw / 2, ch / 2);
    return;
  }

  /* ---- 1. image + mask overlays ---- */
  drawImageLayer(r);

  /* ---- 2. inpaint context rect ---- */
  drawInpaintContext(r);

  /* ---- 3. chrome ---- */
  _drawChromeText(selectedMask());
}
"""
