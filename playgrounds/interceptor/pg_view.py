"""The interceptor's canvas: zoomable, pannable, showing three layers
of the payload the daemon prepared:

  1. canvas    — payload.canvas_b64 (what the model sees as input)
  2. selection — payload.selection_b64, tinted (what the model edits)
  3. context   — payload.context_b64, tinted (what the model considers
                 context / what the workflow crops to)

Layers 2 and 3 are drawn at low opacity over the canvas so the user
can see exactly what will be affected.  See `pg_view_image.js` for
the tint composition.

When the payload's `output_rotation` is non-zero, the canvas is the
rotated + cropped frame — that's what the model actually consumes,
and it's the right thing to display for review.  There is no
"un-rotated preview" mode: what you see here is what the model gets.
"""

VIEW_JS = r"""
import { board, view, ctx, getCachedImage } from "./pg_state.js";
import { drawCanvasLayer } from "./pg_view_image.js";

function _placeholder(cw, ch, text) {
  ctx.fillStyle = "#5a6774";
  ctx.font = "700 12px 'JetBrains Mono', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cw / 2, ch / 2);
}

export function currentFitRect() {
  const item = board.selectedItem;
  if (!item) return null;
  const payload = item.payload || {};
  const canvasImg = getCachedImage(payload.canvas_b64);
  if (!canvasImg || !canvasImg.complete || !canvasImg.naturalWidth) {
    return null;
  }
  const W = canvasImg.naturalWidth;
  const H = canvasImg.naturalHeight;
  const s = view.zoom;
  return {
    img: canvasImg,
    W, H, s,
    w: W * s, h: H * s,
    x0: view.offsetX, y0: view.offsetY,
  };
}

export function fitView() {
  const item = board.selectedItem;
  if (!item) return;
  const payload = item.payload || {};
  const canvasImg = getCachedImage(payload.canvas_b64);
  if (!canvasImg) return;
  if (!canvasImg.complete || !canvasImg.naturalWidth) {
    canvasImg.addEventListener("load", () => {
      fitView();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    }, { once: true });
    return;
  }
  const W = canvasImg.naturalWidth;
  const H = canvasImg.naturalHeight;
  const cw = window.innerWidth, ch = window.innerHeight;
  const pad = 40;
  const zoom = Math.min((cw - 2 * pad) / W, (ch - 2 * pad) / H);
  view.zoom = zoom;
  view.offsetX = (cw - W * zoom) / 2;
  view.offsetY = (ch - H * zoom) / 2;
}

export function zoomAt(screenX, screenY, factor) {
  const fit = currentFitRect();
  if (!fit) return;
  const wx = (screenX - fit.x0) / fit.s;
  const wy = (screenY - fit.y0) / fit.s;
  const newZoom = Math.max(0.05, Math.min(32, view.zoom * factor));
  if (newZoom === view.zoom) return;
  view.zoom = newZoom;
  view.offsetX = screenX - wx * newZoom;
  view.offsetY = screenY - wy * newZoom;
}

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const item = board.selectedItem;
  if (!item) { _placeholder(cw, ch, "waiting for a task"); return; }

  const fit = currentFitRect();
  if (!fit) { _placeholder(cw, ch, "loading\u2026"); return; }

  drawCanvasLayer(fit);
}
"""