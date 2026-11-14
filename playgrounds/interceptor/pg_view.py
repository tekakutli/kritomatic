"""The interceptor's canvas: zoomable, pannable, two-layer, sig-
dispatched.

`drawView()` computes a content signature for each of the two
stacked canvases and repaints only the layers whose signature
changed.

    image sig  covers:  board.selectedItem identity, image loaded
                        state, view.zoom/offsetX/offsetY, viewport
                        dims + DPR
    overlay sig covers: same base, plus overlay toggles
                        (board.showSelection / showContext /
                        showMultimask)

Placeholder text (`"waiting for a task"`, `"loading…"`) lives on
the OVERLAY layer.  The image layer clears to transparent when
there is no image or the image hasn't loaded yet; the shared pgkit
CSS provides the dark fill under both layers.

CHROME TEXT — CACHED AS AN OFFSCREEN BLIT
=========================================
`_placeholder` runs whenever the overlay repaints in the empty /
loading states.  Two unique strings, so the cache collapses them
to one blit each.  First paint per string: measure the text once,
allocate an offscreen canvas to fit, draw the string there.
Subsequent paints: one `drawImage` at the centered position — no
`measureText`, no `fillText`, no per-frame string work.

PIXEL COST PER FRAME
====================
Image layer:
    clearRect                          (cheap)
    drawImage(raw canvas)              (cheap, GPU accelerated)
    strokeRect border                  (cheap)

Overlay layer (loaded case, all toggles on):
    clearRect                          (cheap)
    N drawImage(tinted multimask)      (cheap, cached blits)
    drawImage(tinted context)          (cheap, cached blit)
    drawImage(tinted selection)        (cheap, cached blit)

Overlay layer (placeholder case):
    clearRect                          (cheap)
    drawImage(cached placeholder)      (cheap)

The only non-cached work is `_buildTinted` in `pg_view_image.js`,
which iterates every pixel of a grayscale mask to tint it.  It runs
exactly once per (mask, color) pair — the results are cached on
`_tintCache` and reused until the tab is closed.

The point of the split is that toggling an overlay checkbox
repaints one layer, not two; and that the raw image blit (which is
still the single largest `drawImage` in the frame) is skipped
entirely when nothing about the image changed.
"""

VIEW_JS = r"""
import {
  ctx, overlayCtx, board, view, _layerSigs, getCachedImage,
} from "./pg_state.js";
import { drawImageLayer, drawTintOverlays } from "./pg_view_image.js";

/* ============================================================
   Fit-rect helpers (unchanged interface; callers in
   pg_dispatch.js use zoomAt / fitView from here)
   ============================================================ */

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

/* ============================================================
   Signatures
   ============================================================ */

function _viewportKey() {
  return window.innerWidth + "x" + window.innerHeight
       + "@" + (window.devicePixelRatio || 1);
}

function _itemKey() {
  const item = board.selectedItem;
  if (!item) return "none";
  const payload = item.payload || {};
  return String(item.task_id || "")
       + ":" + (payload.canvas_b64 ? payload.canvas_b64.length : 0);
}

function _transformKey() {
  return view.zoom + "," + view.offsetX + "," + view.offsetY;
}

function _imageSig() {
  const item = board.selectedItem;
  if (!item) return "none|" + _viewportKey();
  const payload = item.payload || {};
  if (!payload.canvas_b64) return "none|" + _viewportKey();
  const img = getCachedImage(payload.canvas_b64);
  const loaded = (img && img.complete && img.naturalWidth) ? "1" : "0";
  return _itemKey()
       + "|" + loaded
       + "|" + _transformKey()
       + "|" + _viewportKey();
}

function _overlaySig() {
  const item = board.selectedItem;
  if (!item) return "no-item|" + _viewportKey();
  const payload = item.payload || {};
  if (!payload.canvas_b64) return "no-canvas|" + _viewportKey();
  const img = getCachedImage(payload.canvas_b64);
  if (!img || !img.complete || !img.naturalWidth) {
    return "loading|" + _viewportKey();
  }
  // Number of multimask masks helps the sig change when a snapshot
  // gains/loses entries without an item switch.  Cheap: object-key
  // count of a per-task dict.
  const mm = board.multimask;
  const mmMaskCount = (mm && mm.masks)
    ? Object.keys(mm.masks).length : 0;
  return "loaded|" + _itemKey()
       + "|" + _transformKey()
       + "|" + _viewportKey()
       + "|" + (board.showSelection ? "1" : "0")
       + "|" + (board.showContext   ? "1" : "0")
       + "|" + (board.showMultimask ? "1" : "0")
       + "|" + mmMaskCount;
}

/* ============================================================
   Placeholder text — cached offscreen blit
   ============================================================
   Two unique strings ever ("waiting for a task" / "loading…").
   Cache by text; rebuild only on miss.  The blit is centered per-
   frame using targetCtx, so a viewport resize correctly re-centers
   without rebuilding the cache. */

const _placeholderCache = new Map();

function _getPlaceholderCanvas(text) {
  let cached = _placeholderCache.get(text);
  if (cached) return cached;

  // Measure with a throwaway context so the caller's targetCtx
  // state stays untouched.
  const measure = document.createElement("canvas").getContext("2d");
  measure.font = "700 12px 'JetBrains Mono', monospace";
  const w = Math.ceil(measure.measureText(text).width) + 4;

  const c = document.createElement("canvas");
  c.width  = Math.max(1, w);
  c.height = 20;
  const cx = c.getContext("2d");
  cx.font = "700 12px 'JetBrains Mono', monospace";
  cx.fillStyle = "#5a6774";
  cx.textAlign = "left";
  cx.textBaseline = "middle";
  cx.fillText(text, 2, 10);

  _placeholderCache.set(text, c);
  return c;
}

function _drawPlaceholder(targetCtx, cw, ch, text) {
  const c = _getPlaceholderCanvas(text);
  targetCtx.drawImage(
    c,
    Math.round((cw - c.width)  / 2),
    Math.round((ch - c.height) / 2),
  );
}

/* ============================================================
   Layer draws
   ============================================================ */

function _drawImageCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  // Clear to transparent; CSS #c { background: #0a0e14 } shows
  // through as the dark fill.
  ctx.clearRect(0, 0, cw, ch);

  const fit = currentFitRect();
  if (!fit) return;   // no image / not loaded yet — image layer stays empty

  drawImageLayer(fit);
}

function _drawOverlayCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  overlayCtx.clearRect(0, 0, cw, ch);

  const item = board.selectedItem;
  if (!item || !(item.payload && item.payload.canvas_b64)) {
    _drawPlaceholder(overlayCtx, cw, ch, "waiting for a task");
    return;
  }

  const fit = currentFitRect();
  if (!fit) {
    _drawPlaceholder(overlayCtx, cw, ch, "loading\u2026");
    return;
  }

  drawTintOverlays(fit);
}

/* ============================================================
   Public entry point — sig-dispatched
   ============================================================ */

export function drawView() {
  const imgSig = _imageSig();
  const ovlSig = _overlaySig();

  if (imgSig !== _layerSigs.image) {
    _layerSigs.image = imgSig;
    _drawImageCanvas();
  }
  if (ovlSig !== _layerSigs.overlay) {
    _layerSigs.overlay = ovlSig;
    _drawOverlayCanvas();
  }
}
"""