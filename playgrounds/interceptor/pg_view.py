"""The interceptor's canvas: one letterboxed PNG.

No overlays, no mask tint, no rotation math.  The payload's
canvas_b64 was rotated by the source inpaint playground before it
was base64-encoded, so drawing it at rotation 0 shows exactly what
the model will see.  Once a run succeeds, `board.view` can swap to
the result's image_b64 — which was un-rotated back by
`inpaint_server.handle_inpaint` — and drawing *that* at rotation 0 is
also correct.  The two PNGs may differ in dimensions; `_fitDraw`
handles each independently.
"""

VIEW_JS = r"""
import { board, ctx, getCachedImage } from "./pg_state.js";

function _fitDraw(img, cw, ch) {
  const iw = img.naturalWidth || img.width;
  const ih = img.naturalHeight || img.height;
  if (!iw || !ih) return;
  const pad = 40;
  const sx = (cw - 2 * pad) / iw;
  const sy = (ch - 2 * pad) / ih;
  const s  = Math.min(sx, sy);
  const w  = iw * s;
  const h  = ih * s;
  const x0 = (cw - w) / 2;
  const y0 = (ch - h) / 2;

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(img, x0, y0, w, h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(x0 + 0.5, y0 + 0.5, w - 1, h - 1);
}

function _placeholder(cw, ch, text) {
  ctx.fillStyle = "#5a6774";
  ctx.font = "700 12px 'JetBrains Mono', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cw / 2, ch / 2);
}

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const item = board.selectedItem;
  if (!item) {
    _placeholder(cw, ch, "waiting for a task");
    return;
  }

  let b64 = null;
  if (board.view === "result" && item.result && item.result.image_b64) {
    b64 = item.result.image_b64;
  } else if (item.payload && item.payload.canvas_b64) {
    b64 = item.payload.canvas_b64;
  }

  if (!b64) {
    _placeholder(cw, ch, "no canvas in payload");
    return;
  }

  const img = getCachedImage(b64);
  if (!img || !img.complete || !img.naturalWidth) {
    _placeholder(cw, ch, "loading\u2026");
    return;
  }
  _fitDraw(img, cw, ch);
}
"""