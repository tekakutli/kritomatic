"""The main draw entry point.

Single canvas.  `drawView()` computes a content signature over
every piece of state that affects the visible output, and short-
circuits on a repeat call where nothing changed.  This is what
keeps hover-change and panel-diff repaints cheap — the
`pg_dispatch.js` mousemove handler triggers a `drawView()` on
every hover transition, and without the sig that would be a full
canvas repaint per pixel of cursor movement.

`_masksVersion` is imported from `pg_state.js` as a live ES
binding — it's bumped by `pg_masks.js` on every mask mutation
that doesn't move any other sig field (`img.onload`, `setMasks`,
`replaceMaskAt`, `applyCanvasImage`'s cache invalidation).  Its
inclusion in the image sig is what makes those mutations
trigger a repaint without changing `masks.length`,
`selectedMaskIndices`, or `hoveredMaskIdx`.

CHROME TEXT
===========
The chrome text (`IMAGE W×H · ZOOM N× · MASK K/N · CONTENT
W×H`) is cached as an offscreen blit.  On a hit, `drawImage`
costs ~1 μs vs ~50 μs for `fillText` of the same string."""

VIEW_JS = r"""
import {
  ctx, board, view, fittedImageRect, _masksVersion,
} from "./pg_state.js";
import { drawImageLayer } from "./pg_view_image.js";
import { drawContext } from "./pg_view_context.js";

let _lastSig = null;

function _sig() {
  return board.imageHash
       + "|" + board.imageWidth + "x" + board.imageHeight
       + "|" + board.fullImageWidth + "x" + board.fullImageHeight
       + "|" + board.boundary.x + "," + board.boundary.y
       + "|" + view.zoom + "|" + view.panX + "|" + view.panY
       + "|" + window.innerWidth + "x" + window.innerHeight
       + "|" + (window.devicePixelRatio || 1)
       + "|" + board.hoveredMaskIdx
       + "|" + board.selectedMaskIndices.join(",")
       + "|" + board.masks.length
       + "|" + (board.contextEnabled ? "1" : "0")
       + "|" + board.context.x + "," + board.context.y
       + "|" + board.context.w + "," + board.context.h
       + "|" + (board.context.activeHandle || "")
       + "|" + _masksVersion;
}

/* ============================================================
   Chrome text
   ============================================================ */

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

  if (board.contextEnabled) ctxLabel += " \u00b7 CONTEXT";

  const contentDim =
    board.fullImageWidth && board.fullImageHeight
      ? "   \u00b7   CONTENT " + board.imageWidth + "\u00d7" + board.imageHeight
        + " \u00f7 " + board.fullImageWidth + "\u00d7" + board.fullImageHeight
      : "";

  return "IMAGE " + board.imageWidth + "\u00d7" + board.imageHeight
       + "   \u00b7   ZOOM " + view.zoom.toFixed(2) + "\u00d7"
       + "   \u00b7   " + ctxLabel
       + "   \u00b7   MASKS " + board.masks.length
       + contentDim;
}

let _chromeCache = { key: null, canvas: null };

function _drawChromeText() {
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

/* ============================================================
   Public entry point
   ============================================================ */

export function drawView() {
  const sig = _sig();
  if (sig === _lastSig) return;
  _lastSig = sig;

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
      "load a workflow in the docker to pull @thecanvas",
      cw / 2, ch / 2,
    );
    return;
  }

  drawImageLayer(r);
  drawContext(r);
  _drawChromeText();
}

/* Force a full repaint on the next `drawView()` call.  Called by
   `pg_dispatch.js`'s Home-key handler and the Reset View button
   handler — `resetView()` changes no `board` field, so the sig
   would be identical.  The sig is purely a cache; we need to
   defeat it on manual reset. */
export function invalidateDrawSig() { _lastSig = null; }
"""