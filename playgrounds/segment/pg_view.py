"""The main `drawView()` entry point, sig-dispatched per layer.

THREE LAYERS, THREE SIGNATURES
==============================
`drawView()` computes a content signature for each of the three
stacked canvases and repaints only the layers whose signature
changed:

    image    #c              image, tinted masks
    region   #regionCanvas   live stroke OR edge dots
    overlay  #overlayCanvas  points, drafts, chrome text

Per-layer repaint triggers:

    image    imageHash, image dims, zoom, pan, viewport,
             selectedMaskIndices, hoveredMaskIdx, masksVersion
    region   selectedMaskIdx + that mask's regionCanvas identity,
             regionStrokeActive, regionStrokeIsErase, imageHash,
             zoom, pan, viewport, masksVersion
    overlay  imageHash, image dims, zoom, pan, viewport,
             mouse position (only when the mouse matters), region
             mode/tool/subtract/size, resize anchor, lasso/freehand
             state, staging count, per-mask point count, chrome text

`view.zoom` and `view.panX/panY` appear in all three (they translate
every layer).  During a brush stroke, `regionStrokeActive` forces a
region repaint every frame regardless of sig (the underlying region
canvas mutates per frame).  When the stroke ends, `regionStrokeActive`
flips false and the sig mismatch catches the transition back to edge
dots.

CHROME TEXT — CACHED AS AN OFFSCREEN BLIT
=========================================
`_drawChromeText` runs whenever the overlay repaints.  The text only
changes on: zoom, image dims, selection, refineModel, region tool /
subtract state, lasso/freehand vertex count, staging count, masks
count.  On pan-only overlay repaints the text is unchanged and the
cache blits a pre-shaped RGBA canvas with one `drawImage` instead of
re-shaping the string with `fillText`.

POINTS ON THE OVERLAY
=====================
Points live on the overlay canvas, not the image canvas.  Point
mutations (add / remove) are discrete events; the overlay repaint is
cheap (arcs + fills).  Keeping points off the image layer means a
point click doesn't redraw the image or the mask tints.

EMPTY STATE
===========
When no image is loaded, the image canvas paints its dark fill and
the empty-state text; region and overlay return early (their pixels
stay transparent)."""

VIEW_JS = r"""
import {
  ctx, regionCtx, overlayCtx,
  board, view, fittedImageRect, _layerSigs, _getMasksVersion,
} from "./pg_state.js";
import { selectedMask } from "./pg_masks.js";
import { drawImageLayer } from "./pg_view_image.js";
import { drawRegionLayer } from "./pg_view_region.js";
import { drawPoints } from "./pg_view_points.js";
import {
  drawBrushCursor, drawLassoDraft, drawFreehandDraft,
} from "./pg_view_brush.js";

/* ============================================================
   Signatures
   ============================================================ */

function _viewportKey() { return window.innerWidth + "x" + window.innerHeight; }
function _transformKey() {
  return view.zoom + "," + view.panX + "," + view.panY;
}

function _imageSig() {
  return (board.imageHash || "none")
       + "|" + board.imageWidth + "x" + board.imageHeight
       + "|" + _transformKey()
       + "|" + _viewportKey()
       + "|" + _getMasksVersion()
       + "|" + board.selectedMaskIndices.join(",")
       + "|" + board.hoveredMaskIdx;
}

function _regionSig() {
  const idx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  if (idx < 0) return "none";
  const m = board.masks[idx];
  if (!m) return "none";
  return (board.imageHash || "none")
       + "|" + idx
       + "|" + (m.regionCanvas ? "1" : "0")
       + "|" + (board.regionStrokeActive ? "1" : "0")
       + "|" + (board.regionStrokeIsErase ? "1" : "0")
       + "|" + _transformKey()
       + "|" + _viewportKey()
       + "|" + _getMasksVersion();
}

function _overlaySig() {
  const sm = selectedMask();
  // Only include mouse coords when a draft actually depends on them;
  // otherwise overlay repaints for no reason on every mousemove.
  const mouseMatters =
    (board.regionMode && board.regionTool === 'brush' && sm) ||
    board.lassoPoints.length > 0 ||
    board.freehandActive;

  return (board.imageHash || "none")
       + "|" + board.imageWidth + "x" + board.imageHeight
       + "|" + _transformKey()
       + "|" + _viewportKey()
       + "|" + (mouseMatters
                ? (board.mouse.x + "," + board.mouse.y
                   + "," + (board.mouse.inside ? 1 : 0))
                : "")
       + "|" + (board.regionMode ? 1 : 0)
       + "|" + board.regionTool
       + "|" + (board.regionSubtract ? 1 : 0)
       + "|" + board.regionSize
       + "|" + (board.resizeAnchor
                ? (board.resizeAnchor.x + "," + board.resizeAnchor.y)
                : "")
       + "|" + board.lassoPoints.length
       + "|" + (board.freehandActive ? 1 : 0)
       + "|" + board.freehandPoints.length
       + "|" + board.stagingPoints.length
       + "|" + (sm ? sm.points.length : 0)
       + "|" + (sm ? sm.id : "")
       + "|" + _chromeText(sm);
}

/* ============================================================
   Chrome text — the one-line status bar at top-left
   ============================================================
   Label priority:

     single mask selected   "REFINING MASK N via SAM2" + region/lasso/freehand
     multiple selected      "MULTIPLE SELECTED"
     no masks yet           "STAGING MODE — place points then Segment All"
     masks but none picked  "NO MASK SELECTED" */

function _chromeText(sm) {
  const selIdx = (board.selectedMaskIndices.length === 1)
    ? board.selectedMaskIndices[0] : -1;
  let ctxLabel;
  if (selIdx >= 0) {
    ctxLabel = "REFINING MASK " + (selIdx + 1)
             + " via " + board.refineModel.toUpperCase();
    if (sm && sm.regionCanvas) {
      ctxLabel += " \u00b7 REGION (" + board.regionTool
               + (board.regionSubtract ? " \u00b7 subtract" : "") + ")";
    }
    if (board.lassoPoints.length > 0) {
      ctxLabel += " \u00b7 LASSO " + board.lassoPoints.length + " pt";
    }
    if (board.freehandActive) {
      ctxLabel += " \u00b7 FREEHAND " + board.freehandPoints.length + " pt";
    }
  } else if (board.selectedMaskIndices.length > 1) {
    ctxLabel = "MULTIPLE SELECTED";
  } else if (board.masks.length === 0) {
    ctxLabel = "STAGING MODE \u2014 place points then Segment All";
  } else {
    ctxLabel = "NO MASK SELECTED";
  }

  return "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
       + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
       + "   \u00b7   " + ctxLabel
       + "   \u00b7   STAGING " + board.stagingPoints.length
       + "   \u00b7   MASKS " + board.masks.length;
}

let _chromeCache = { key: null, canvas: null };

function _drawChromeText(sm) {
  const text = _chromeText(sm);

  let entry = _chromeCache;
  if (entry.key !== text) {
    // Measure once — only on text change.
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
   ============================================================ */

function _drawImageCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) {
    ctx.fillStyle = "#5a6774";
    ctx.font = "700 12px 'JetBrains Mono', monospace";
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.fillText("drop an image to begin", cw / 2, ch / 2);
    return;
  }

  drawImageLayer(r);
}

function _drawRegionCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  regionCtx.clearRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) return;

  const sm = selectedMask();
  if (sm && sm.regionCanvas) drawRegionLayer(r, sm);
}

function _drawOverlayCanvas() {
  const cw = window.innerWidth, ch = window.innerHeight;
  overlayCtx.clearRect(0, 0, cw, ch);

  const r = fittedImageRect();
  if (!r || !board.imageEl) return;

  const sm = selectedMask();
  drawPoints(r, sm);
  drawBrushCursor(r);
  drawLassoDraft();
  drawFreehandDraft();
  _drawChromeText(sm);
}

/* ============================================================
   Public entry point — sig-dispatched
   ============================================================ */

export function drawView() {
  const imgSig = _imageSig();
  const regSig = _regionSig();
  const ovlSig = _overlaySig();

  if (imgSig !== _layerSigs.image) {
    _layerSigs.image = imgSig;
    _drawImageCanvas();
  }
  if (board.regionStrokeActive || regSig !== _layerSigs.region) {
    _layerSigs.region = regSig;
    _drawRegionCanvas();
  }
  if (ovlSig !== _layerSigs.overlay) {
    _layerSigs.overlay = ovlSig;
    _drawOverlayCanvas();
  }
}
"""