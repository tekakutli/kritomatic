"""Main draw entry point.  No rotation, no context rectangle.

CHROME TEXT IS CACHED AS AN OFFSCREEN BLIT
==========================================
`drawView()` clears the canvas at the top of every frame, so the
chrome text can't be skipped outright.  But `fillText` re-shapes the
whole string each call, and the string only changes on: zoom, image
dims, area count, selection, or the selected area's name.  It does
NOT change on pan, hover, or tint alpha — so pan-only frames (where
`drawView()` is called from the pan branch without touching the
panel) always hit the cache.

The cache keys the rendered text; on a hit, the frame blits a
pre-shaped RGBA canvas with one `drawImage`.  On a miss, the string
is measured once, an offscreen canvas is allocated to fit, and
`fillText` runs there."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { drawImageLayer } from "./pg_view_image.js";

function _chromeText() {
  const sel = board.selectedAreaId
    ? board.areas.find(a => a.id === board.selectedAreaId)
    : null;

  const selLabel = board.areas.length === 0
    ? "NO AREAS — import masks from Krita"
    : (sel ? "AREA " + sel.name + " SELECTED" : "NO AREA SELECTED");

  return "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
       + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
       + "   \u00b7   " + selLabel
       + "   \u00b7   AREAS " + board.areas.length;
}

let _chromeCache = { key: null, canvas: null };

function _drawChromeText() {
  const text = _chromeText();

  let entry = _chromeCache;
  if (entry.key !== text) {
    // Measure once — only on text change, which is rare.
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