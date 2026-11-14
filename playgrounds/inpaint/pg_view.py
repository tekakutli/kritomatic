"""The main `drawView()` entry point, sig-dispatched per layer.

TWO LAYERS, TWO SIGNATURES
==========================
`drawView()` computes a content signature for each of the two
stacked canvases and repaints only the layer whose sig changed:

    image sig  covers:  board.imageHash, view.zoom/panX/panY/
                        rotation, image dims, viewport dims,
                        board.hoveredMaskIdx, board.selectedMaskIndices,
                        board.masks.length, _masksVersion
    overlay sig covers: board.imageHash, view.zoom/panX/panY,
                        image dims, viewport dims,
                        board.inpaintEnabled, board.inpaintContext.*,
                        board.masks.length, board.selectedMaskIndices.length

`view.rotation` is only in the image sig.  Rotating therefore
repaints the image layer only — the context rect and chrome text
never repaint on rotation, which is exactly the property the
previous single-canvas `drawView()` could not exploit.

`board.inpaintContext.*` and `board.hoveredMaskIdx` are in
disjoint sigs — dragging the context rect repaints the overlay
layer only; crossing a mask boundary repaints the image layer
only.

Panel state that changes without moving any field in either sig
(e.g. a `mask.img.onload` for a mask already in `board.masks`)
is covered by `_masksVersion`, a monotonic counter incremented in
`pg_masks.js`.  See `pg_state.py`'s MASKS VERSION section.

SIG CACHE
=========
The cached sigs live in `_layerSigs` (imported from `pg_state.js`,
which `resize()` also writes to clear them).  Kept out of this
module's local scope so `resize()` can invalidate without importing
`pg_view.js` (which imports `pg_state.js` — a cycle).

Sigs are null-initialised; `drawView()` first-call always paints
both layers.

CHROME TEXT IS CACHED AS AN OFFSCREEN BLIT
==========================================
The chrome text can't be skipped outright — `_drawOverlayCanvas`
clears its canvas at the top of each overlay repaint.  But
`fillText` re-shapes the whole string, and the string only changes
on: zoom, mask count, selection count, image dims, or
`inpaintEnabled`.  It does NOT change on pan, hover, context-rect
drag, or tint alpha — and those all skip the overlay repaint
entirely at the sig level now.

The cache keys the rendered text; on a hit, the frame blits a
pre-shaped RGBA canvas with one `drawImage`.  On a miss, the string
is measured once, an offscreen canvas is allocated to fit, and
`fillText` runs there.  Cost on hits drops from "full text shaping"
to "one integer key compare + one `drawImage`".

EMPTY-STATE TEXT
================
The "drop an image to begin" text is drawn on the OVERLAY layer, not
the image layer, so the image layer can return immediately when no
image is loaded.  The image layer's dark fill is provided by CSS
(`canvas { background: #0a0e14 }`, see `pgkit/styles.py`)."""

VIEW_JS = r"""
import {
  ctx, overlayCtx, board, view, fittedImageRect, _layerSigs,
  _masksVersion,
} from "./pg_state.js";
import { drawImageLayer } from "./pg_view_image.js";
import { drawInpaintContext } from "./pg_view_inpaint.js";

/* ============================================================
   Signatures
   ============================================================ */

function _viewportKey() {
  return window.innerWidth + "x" + window.innerHeight
       + "@" + (window.devicePixelRatio || 1);
}

function _imageSig() {
  return board.imageHash
       + "|" + board.imageWidth + "x" + board.imageHeight
       + "|" + view.zoom + "|" + view.panX + "|" + view.panY
       + "|" + view.rotation
       + "|" + _viewportKey()
       + "|" + board.hoveredMaskIdx
       + "|" + board.selectedMaskIndices.join(",")
       + "|" + board.masks.length
       + "|" + _masksVersion;
}

function _overlaySig() {
  return board.imageHash
       + "|" + board.imageWidth + "x" + board.imageHeight
       + "|" + view.zoom + "|" + view.panX + "|" + view.panY
       // NOTE: view.rotation deliberately absent — that is the win.
       + "|" + _viewportKey()
       + "|" + (board.inpaintEnabled ? "1" : "0")
       + "|" + board.inpaintContext.x
       + "|" + board.inpaintContext.y
       + "|" + board.inpaintContext.w
       + "|" + board.inpaintContext.h
       + "|" + (board.inpaintContext.activeHandle || "")
       + "|" + board.masks.length
       + "|" + board.selectedMaskIndices.length;
}

/* ============================================================
   Chrome text — the one-line status bar at top-left
   ============================================================
     single mask selected   "MASK N SELECTED"
     multiple selected      "MULTIPLE SELECTED"
     no masks yet           "IMPORT A MASK to begin"
     masks but none picked  "NO MASK SELECTED"

   When inpaint mode is on, " · INPAINT" is appended so the user
   never loses sight of the mode. */

function _chromeText() {
  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "MASK " + (selIdx + 1) + " SELECTED";
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "IMPORT A MASK to begin";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  if (board.inpaintEnabled) ctxLabel += " \u00b7 INPAINT";

  return "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
       + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
       + "   \u00b7   " + ctxLabel
       + "   \u00b7   MASKS " + board.masks.length;
}

let _chromeCache = { key: null, canvas: null };

function _drawChromeText() {
  const text = _chromeText();

  let entry = _chromeCache;
  if (entry.key !== text) {
    // Measure once — only on text change, which is rare.
    overlayCtx.save();
    overlayCtx.font = "700 10px 'JetBrains Mono', monospace";
    const w = Math.ceil(overlayCtx.measureText(text).width) + 4;
    overlayCtx.restore();

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

  overlayCtx.drawImage(entry.canvas, 14, 14);
}

/* ============================================================
   Layer draws
   ============================================================
   Each clears its own canvas and paints its share.  The image
   layer's dark fill is provided by CSS (`canvas { background:
   #0a0e14 }`); `clearRect` restores transparency and lets the CSS
   background show through.  The overlay's `clearRect` reveals the
   image layer below. */

function _drawImageLayerCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.clearRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) return;

  drawImageLayer(r);
}

function _drawOverlayCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  overlayCtx.clearRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) {
    overlayCtx.fillStyle = "#5a6774";
    overlayCtx.font = "700 12px 'JetBrains Mono', monospace";
    overlayCtx.textAlign = "center";
    overlayCtx.textBaseline = "middle";
    overlayCtx.fillText("drop an image to begin", cw / 2, ch / 2);
    return;
  }

  drawInpaintContext(r);
  _drawChromeText();
}

/* ============================================================
   Public entry point — sig-dispatched
   ============================================================ */

export function drawView() {
  const imgSig = _imageSig();
  const ovlSig = _overlaySig();

  if (imgSig !== _layerSigs.image) {
    _layerSigs.image = imgSig;
    _drawImageLayerCanvas();
  }
  if (ovlSig !== _layerSigs.overlay) {
    _layerSigs.overlay = ovlSig;
    _drawOverlayCanvas();
  }
}
"""