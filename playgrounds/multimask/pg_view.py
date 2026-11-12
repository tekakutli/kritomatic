"""Main draw entry point.  No rotation, no context rectangle."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { drawImageLayer } from "./pg_view_image.js";

function _drawChromeText() {
  const sel = board.selectedAreaId
    ? board.areas.find(a => a.id === board.selectedAreaId)
    : null;

  const selLabel = board.areas.length === 0
    ? "NO AREAS — import masks from Krita"
    : (sel ? "AREA " + sel.name + " SELECTED" : "NO AREA SELECTED");

  ctx.save();
  ctx.font = "700 10px 'JetBrains Mono', monospace";
  ctx.fillStyle = "#5a6774";
  ctx.textAlign = "left";
  ctx.textBaseline = "top";
  ctx.fillText(
    "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
    + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
    + "   \u00b7   " + selLabel
    + "   \u00b7   AREAS " + board.areas.length,
    14, 14,
  );
  ctx.restore();
}

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
    ctx.fillText("drop an image or import from Krita to begin",
                 cw / 2, ch / 2);
    return;
  }

  drawImageLayer(r);
  _drawChromeText();
}
"""