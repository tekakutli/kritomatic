"""The main `drawView()` entry point.

In the old 800-line file this was one function that did everything.
Now it is the orchestrator: call the layer renderers in order, then
draw the header text.  The per-layer work lives in `pg_view_*.py`.

Adding a new layer (a new mask channel, a composited preview image)
means: write a new `pg_view_*.py`, add one call to `drawView()`,
done — no other file is touched.

DRAW ORDER
==========
  1. image + selected-mask tinted overlays     (pg_view_image)
  2. live region preview OR region edge dots   (pg_view_region)
  3. staging points + per-mask points          (pg_view_points)
  4. brush cursor, lasso draft, freehand draft (pg_view_brush)
  5. chrome text (top-left status line)

The order matches z-order intent: region overlays sit on top of the
image; points sit on top of the overlays (they must be visible against
any mask); drafts sit on top of everything else because they follow
the cursor.

The inpaint context rectangle that used to render as step 3 now lives
in the standalone inpaint playground (`playgrounds/inpaint`)."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { selectedMask } from "./pg_masks.js";
import { drawImageLayer } from "./pg_view_image.js";
import { drawRegionLayer } from "./pg_view_region.js";
import { drawPoints } from "./pg_view_points.js";
import {
  drawBrushCursor, drawLassoDraft, drawFreehandDraft,
} from "./pg_view_brush.js";

/* ============================================================
   Header text — the one-line status bar at top-left
   ============================================================
   The label follows this priority ladder:

     single mask selected   "REFINING MASK N via SAM2" + region/lasso/freehand
     multiple selected      "MULTIPLE SELECTED"
     no masks yet           "STAGING MODE — place points then Segment All"
     masks but none picked  "NO MASK SELECTED" */

function _drawChromeText(sm) {
  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "REFINING MASK " + (selIdx + 1)
             + " via " + board.refineModel.toUpperCase();
    if (sm && sm.regionCanvas) {
      ctxLabel += " \u00b7 REGION (" + board.regionTool
               + (board.regionSubtract ? " \u00b7 subtract" : "") + ")";
    }
    if (board.lassoPoints.length > 0) {
      ctxLabel += " \u00b7 LASSO " + board.lassoPoints.length + " pt";
    }
    if (board.freehandActive) {
      ctxLabel += " \u00b7 FREEHAND " + board.freehandPoints.length + " pt";
    }
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "STAGING MODE \u2014 place points then Segment All";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
    + "   \u00b7   " + ctxLabel
    + "   \u00b7   STAGING " + board.stagingPoints.length
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

  /* ---- 2. region (only when a mask is selected) ---- */
  const sm = selectedMask();
  if (sm && sm.regionCanvas) drawRegionLayer(r, sm);

  /* ---- 3. points ---- */
  drawPoints(r, sm);

  /* ---- 4. cursor drafts ---- */
  drawBrushCursor(r);
  drawLassoDraft();
  drawFreehandDraft();

  /* ---- 5. chrome ---- */
  _drawChromeText(sm);
}
"""
