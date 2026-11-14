"""The payload's canvas, plus opt-in tint overlays.

Layers, in order of drawing:
  1. Raw prepared canvas (always).
  2. Multimask per-area masks, each tinted with its own colour
     (opt-in via `board.showMultimask`, default on when snapshot
     exists — the areas are the whole point of reviewing a multimask
     workflow).
  3. Context mask, purple (opt-in via `board.showContext`).
  4. Selection mask, cyan (opt-in via `board.showSelection`).

Order matters when multiple overlays are on simultaneously:
multimask first (largest total area, lowest alpha per area),
context next, selection last — so the pixel that matters most
(what the model edits) has the final say visually.

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
The tinted canvas is cached per `(b64, color, alpha)`.  Repeat draws
on the same task (pan, zoom) reuse the cached tint instead of
re-tinting every frame.  Cache is unbounded; a normal session sees a
handful of tasks and masks.  If a session ever loads many distinct
masks, evict by LRU.
"""

VIEW_IMAGE_JS = r"""
import { ctx, board, getCachedImage, areaColorById } from "./pg_state.js";

// Cache the tinted canvas per (b64, color) — alpha is applied at
// draw time so it does not need to be part of the key.
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

function _drawTintedLayer(b64, x0, y0, w, h, color, alpha) {
  if (!b64) return;
  const key = _cacheKey(b64, color);
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

function _drawMultimaskAreas(fit) {
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
    _drawTintedLayer(b64, fit.x0, fit.y0, fit.w, fit.h, color, 0.30);
  }
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

  // 2. Multimask area tints (opt-in, default on when snapshot exists).
  if (board.showMultimask) {
    _drawMultimaskAreas(fit);
  }

  // 3. Context tint (opt-in, default off).
  if (board.showContext) {
    _drawTintedLayer(p.context_b64, fit.x0, fit.y0, fit.w, fit.h,
                     { r: 180, g: 155, b: 255 }, 0.25);
  }

  // 4. Selection tint (opt-in, default off).  Last, so the "what
  //    the model actually edits" tint has visual priority.
  if (board.showSelection) {
    _drawTintedLayer(p.selection_b64, fit.x0, fit.y0, fit.w, fit.h,
                     { r: 120, g: 220, b: 255 }, 0.45);
  }
}
"""