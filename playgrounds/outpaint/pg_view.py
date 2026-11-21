"""Main draw entry point."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { drawImageLayer } from "./pg_view_image.js";

function _chromeText() {
  return "AREA " + board.areaWidth + "\u00d7" + board.areaHeight
       + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7";
}

let _chromeCache = { key: null, canvas: null };

function _drawChrome() {
  const text = _chromeText();
  let entry = _chromeCache;
  if (entry.key !== text) {
    ctx.save();
    ctx.font = "700 10px 'JetBrains Mono', monospace";
    const w = Math.ceil(ctx.measureText(text).width) + 4;
    ctx.restore();

    const c = document.createElement("canvas");
    c.width  = Math.max(1, w);
    c.height = 20;
    const cx = c.getContext("2d");
    cx.font = "700 10px 'JetBrains Mono', monospace";
    cx.fillStyle = "#5a6774";
    cx.textAlign = "left";
    cx.textBaseline = "top";
    cx.fillText(text, 0, 0);
    entry = _chromeCache = { key: text, canvas: c };
  }
  ctx.drawImage(entry.canvas, 14, 14);
}

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(
      "set an area size in the panel, then drag the viewer",
      cw / 2, ch / 2,
    );
    return;
  }

  drawImageLayer(r);
  _drawChrome();
}
"""