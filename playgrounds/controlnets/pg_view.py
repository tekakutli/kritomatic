"""Main draw entry point."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { makeChromeText } from "./pgruntime/js/chrome.js";
import { drawImageLayer } from "./pg_view_image.js";

const _drawChromeText = makeChromeText(ctx, () => {
  const parts = [];
  parts.push("IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight);
  parts.push("ZOOM " + view.zoom.toFixed(2) + "\u00d7");
  if (board.viewingControlnetTag) {
    parts.push("VIEWING " + board.viewingControlnetTag);
  } else {
    parts.push("CONTROLNETS " + board.controlnets.length);
  }
  return parts.join("   \u00b7   ");
});

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
    ctx.fillText(
      "import @thecontrolnet, then pick a layer in the docker",
      cw / 2, ch / 2,
    );
    return;
  }

  ctx.imageSmoothingQuality = "high";
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  drawImageLayer(r);
  _drawChromeText();
}
"""
