"""The payload's canvas, plus opt-in selection / context tint
overlays.

DEFAULT: the canvas is drawn as-is, with no overlay.  This is the raw
prepared image the model receives from the daemon — nothing else.

When `board.showSelection` is on, the selection mask is tinted cyan
at 45% over the canvas.  When `board.showContext` is on, the context
mask is tinted purple at 25%.  Both are opt-in verification aids, not
the default view.

Tint mechanics
==============
The masks are grayscale PNGs (white = 1, black = 0).  `_buildTinted`
reads the R channel of each decoded pixel as the mask value.  Both
mask sources produce PNGs whose R channel encodes the mask:
  · grayscale mask PNGs (from `_all_white_qimage` / `read_layer_mask`)
    have R=G=B=mask;
  · ARGB32-then-PNG mask sources have mask copied into R by the
    daemon before encode.

So `data[i] / 255` is always the mask value regardless of encode
path.

Cache
=====
The tinted canvas is cached per `(b64, color, alpha)`.  Repeat draws
on the same task (pan, zoom) reuse the cached tint instead of
re-tinting every frame.  Cache is unbounded; a normal session sees a
handful of tasks and the memory cost is negligible.  If a session
ever loads many distinct masks, evict by LRU.
"""

VIEW_IMAGE_JS = r"""
import { ctx, board, getCachedImage } from "./pg_state.js";

// Cache the tinted canvas per (b64, color, alpha) so a repeat draw
// on the same task (e.g. pan, zoom) does not re-tint every frame.
const _tintCache = new Map();

function _cacheKey(b64, color, alpha) {
  return b64.length + ":" + b64.slice(-32) + ":"
       + color.r + "," + color.g + "," + color.b + ":" + alpha;
}

function _buildTinted(b64, color) {
  const img = getCachedImage(b64);
  if (!img || !img.complete || !img.naturalWidth) return null;
  const w = img.naturalWidth;
  const h = img.naturalHeight;

  const off = document.createElement("canvas");
  off.width = w;
  off.height = h;
  const octx = off.getContext("2d");
  octx.drawImage(img, 0, 0);
  const srcData = octx.getImageData(0, 0, w, h).data;

  const out = octx.createImageData(w, h);
  const dst = out.data;
  for (let i = 0; i < srcData.length; i += 4) {
    const m = srcData[i] / 255;          // R channel holds mask value
    const a = srcData[i + 3] / 255;      // respect source alpha too
    dst[i]     = color.r;
    dst[i + 1] = color.g;
    dst[i + 2] = color.b;
    dst[i + 3] = Math.round(255 * m * a);
  }

  const tinted = document.createElement("canvas");
  tinted.width = w;
  tinted.height = h;
  tinted.getContext("2d").putImageData(out, 0, 0);
  return tinted;
}

function _drawTintedLayer(b64, x0, y0, w, h, color, alpha) {
  if (!b64) return;
  const key = _cacheKey(b64, color, alpha);
  let tinted = _tintCache.get(key);
  if (!tinted) {
    tinted = _buildTinted(b64, color);
    if (!tinted) return;  // image not loaded yet — draw on next frame
    _tintCache.set(key, tinted);
  }

  ctx.save();
  ctx.globalAlpha = alpha;
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(tinted, x0, y0, w, h);
  ctx.restore();
}

export function drawCanvasLayer(fit) {
  // 1. The raw canvas.  This is what the model receives — the whole
  //    point of the interceptor.  Drawn first, always.
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(fit.img, fit.x0, fit.y0, fit.w, fit.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(fit.x0 + 0.5, fit.y0 + 0.5, fit.w - 1, fit.h - 1);

  const item = board.selectedItem;
  if (!item || !item.payload) return;
  const p = item.payload;

  // 2. Optional overlays.  Both default OFF; the user turns them on
  //    from the Canvas section to verify mask placement.  Context is
  //    drawn first (larger region, lower alpha) so the selection
  //    tint composites on top cleanly when both are enabled.
  if (board.showContext) {
    _drawTintedLayer(p.context_b64, fit.x0, fit.y0, fit.w, fit.h,
                     { r: 180, g: 155, b: 255 }, 0.25);
  }
  if (board.showSelection) {
    _drawTintedLayer(p.selection_b64, fit.x0, fit.y0, fit.w, fit.h,
                     { r: 120, g: 220, b: 255 }, 0.45);
  }
}
"""