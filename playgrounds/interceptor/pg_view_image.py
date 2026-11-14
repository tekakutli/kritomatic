"""The payload's raw canvas, plus opt-in tint overlays.

`drawImageLayer` draws the raw prepared PNG (what the model sees as
input) and its border on `#c`.  Nothing else — no tints, no chrome.
Repaints only when the image or transform changed.

`drawTintOverlays` draws the three optional mask overlays on
`#overlayCanvas`:

  · multimask  — per-area colours (mirrors multimask's own palette)
  · context    — purple at 25%
  · selection  — cyan at 45%

Draw order: multimask → context → selection.  The selection tint
that matters most (what the model actually edits) has the final say
visually.

Tint mechanics
==============
`_buildTinted` reads the R channel of each decoded pixel as the mask
value.  All three mask sources produce PNGs whose R channel encodes
the mask: grayscale mask PNGs (multimask's `/mask` response and the
daemon's `_all_white_qimage` / `read_layer_mask`) have R=G=B=mask;
ARGB32-then-PNG sources have mask copied into R before encode.  So
`data[i] / 255` is always the mask value regardless of source.

Cache
=====
The tinted canvas is cached per `(b64, color)` — alpha is applied
at draw time so it is not part of the key.  Repeat draws on the
same task (pan, zoom, toggle flips) reuse the cached tint instead
of re-tinting every frame.  `_buildTinted` is the only non-trivial
cost in the draw path, and the cache reduces it to once per
(mask, colour) pair per session.
"""

VIEW_IMAGE_JS = r"""
import {
  ctx, overlayCtx, board, getCachedImage, areaColorById,
} from "./pg_state.js";

// Cache the tinted canvas per (b64, color).  Alpha is applied at
// draw time, so it is not part of the key.
const _tintCache = new Map();

function _cacheKey(b64, color) {
  return b64.length + ":" + b64.slice(-32) + ":"
       + color.r + "," + color.g + "," + color.b;
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

function _drawTintedLayer(targetCtx, b64, x0, y0, w, h, color, alpha) {
  if (!b64) return;
  const key = _cacheKey(b64, color);
  let tinted = _tintCache.get(key);
  if (!tinted) {
    tinted = _buildTinted(b64, color);
    if (!tinted) return;   // image not loaded yet — next frame will retry
    _tintCache.set(key, tinted);
  }

  targetCtx.save();
  targetCtx.globalAlpha = alpha;
  targetCtx.imageSmoothingEnabled = true;
  targetCtx.imageSmoothingQuality = "high";
  targetCtx.drawImage(tinted, x0, y0, w, h);
  targetCtx.restore();
}

function _drawMultimaskAreas(targetCtx, fit) {
  const mm = board.multimask;
  if (!mm || !mm.state || !mm.masks) return;
  const areas = mm.state.areas || [];
  for (const area of areas) {
    const maskMeta = area.mask || {};
    const h = maskMeta.hash;
    if (!h) continue;
    const b64 = mm.masks[h];
    if (!b64) continue;
    const color = areaColorById(area.id);
    _drawTintedLayer(targetCtx, b64, fit.x0, fit.y0, fit.w, fit.h,
                     color, 0.30);
  }
}

/* ============================================================
   Public — image layer (on #c)
   ============================================================ */

export function drawImageLayer(fit) {
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(fit.img, fit.x0, fit.y0, fit.w, fit.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(fit.x0 + 0.5, fit.y0 + 0.5, fit.w - 1, fit.h - 1);
}

/* ============================================================
   Public — overlay layer (on #overlayCanvas)
   ============================================================ */

export function drawTintOverlays(fit) {
  const item = board.selectedItem;
  if (!item || !item.payload) return;
  const p = item.payload;

  if (board.showMultimask) {
    _drawMultimaskAreas(overlayCtx, fit);
  }
  if (board.showContext) {
    _drawTintedLayer(overlayCtx, p.context_b64,
                     fit.x0, fit.y0, fit.w, fit.h,
                     { r: 180, g: 155, b: 255 }, 0.25);
  }
  if (board.showSelection) {
    _drawTintedLayer(overlayCtx, p.selection_b64,
                     fit.x0, fit.y0, fit.w, fit.h,
                     { r: 120, g: 220, b: 255 }, 0.45);
  }
}
"""