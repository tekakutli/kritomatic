"""Main draw entry point and chrome text."""

VIEW_JS = r"""
import { ctx, board, view, fittedImageRect } from "./pg_state.js";
import { drawImageLayer } from "./pg_view_image.js";

function _chromeText() {
  if (board.viewingCharacterIdx >= 0) {
    const idx = board.viewingCharacterIdx;
    const total = board.characterBboxes.length;
    const bbox = board.characterBboxes[idx];
    const label = (bbox && bbox.label)
      ? ("\u00ab" + bbox.label + "\u00bb")
      : "(unlabeled)";
    return "VIEWING CHARACTER " + (idx + 1) + "/" + total
         + "   \u00b7   " + label;
  }
  const total = board.characterBboxes.length;
  const analyzed = board.referenceEmbeddings.filter(r => r !== null).length;
  return "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
       + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
       + "   \u00b7   CHAR " + total
       + "   \u00b7   EMB " + analyzed + "/" + total;
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
  if (!r || !board.imageEl) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText(
      "import a reference image, then place the scratch rectangle",
      cw / 2, ch / 2,
    );
    return;
  }

  drawImageLayer(r);
  _drawChrome();
}
"""