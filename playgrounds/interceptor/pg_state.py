"""Interceptor state: board dict, view transform, canvas, draw
scheduling, image cache.

Leaf module.  Every other interceptor module imports from here and
nothing here imports any of them.

VIEW
====
`view = { zoom, offsetX, offsetY }` is the display transform.
`zoom` is *absolute* — screen pixels per canvas pixel, not a fit
multiplier.  At `zoom = 1` the canvas is drawn 1:1.  `offsetX,
offsetY` is the screen position of the *inner image's* top-left
corner.  The expanded frame draws at
`(offsetX - exp.left * zoom, offsetY - exp.top * zoom)`; the inner
image is the stable reference point that expansion pivots around.

This absolute-scale model is what makes the boundary drag feel
right: expanding the frame doesn't touch `zoom`, so the image
doesn't shrink.  It also makes zoom-at-cursor exact — the same
world pixel lands under the cursor before and after.

`fitView()` (in `pg_view.js`) is the only operation that recomputes
`zoom` from the current frame dims.  It runs once on task select
(when the payload is loaded) and on `Home` / `0`.  Expansion drag
never touches the view.

EXPANSION
=========
`board.expansion` is the four margins around the inner canvas, in
original-canvas pixels.

`board.expandedCanvasEl` is a live `HTMLCanvasElement` at
`W'×H'`, or null when expansion is all zeros.  It is the source of
truth: every rebuild shifts the *current* canvas element by
`(Δleft, Δtop)`, so there's no async b64 re-load to race.  The
base64 the wire needs (Krita open, inpaint run) is derived on
demand via `_getExpandedB64()` in `pg_panel_bindings.js`.  No
`board.expandedCanvasB64` field — the double-source-of-truth was
what allowed the second-drag-wipes-canvas bug.

`board.kritaDocumentName` is the daemon's identifier for the
document opened by `/intercept/krita_open`.  `board.kritaFileHint`
is the temp-file basename.

`board.prompt` is the prompt override slot; empty means "use the
payload's prompt".
"""

STATE_JS = r"""
export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

export const view = {
  zoom: 1,
  offsetX: 0,
  offsetY: 0,
};

export function resetView() {
  view.zoom = 1;
  view.offsetX = 0;
  view.offsetY = 0;
}

export const board = {
  tasks:               {},
  order:               [],
  selectedTaskId:      null,
  selectedItem:        null,
  view:                "input",
  prompt:              "",
  pollTimer:           null,

  expansion:           { left: 0, top: 0, right: 0, bottom: 0 },
  expandedCanvasEl:    null,   // HTMLCanvasElement at W'×H', or null
  kritaDocumentName:   null,
  kritaFileHint:       null,
};

/* --------------------------------------------------------------- */
/*  Draw scheduling                                                 */
/* --------------------------------------------------------------- */

let _drawFn = null;
let _drawScheduled = false;

export function _setDraw(fn) { _drawFn = fn; }

export function _scheduleDraw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  if (typeof requestAnimationFrame === "function") {
    requestAnimationFrame(() => {
      _drawScheduled = false;
      if (_drawFn) _drawFn();
    });
  } else {
    setTimeout(() => {
      _drawScheduled = false;
      if (_drawFn) _drawFn();
    }, 16);
  }
}

/* --------------------------------------------------------------- */
/*  Resize                                                          */
/* --------------------------------------------------------------- */

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);
  if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);

/* --------------------------------------------------------------- */
/*  Image cache for the canvas                                      */
/* --------------------------------------------------------------- */

const _imgCache = {};

export function getCachedImage(b64) {
  if (!b64) return null;
  const c = _imgCache[b64];
  if (c) return c;
  const img = new Image();
  img.onload  = _scheduleDraw;
  img.onerror = _scheduleDraw;
  img.src = "data:image/png;base64," + b64;
  _imgCache[b64] = img;
  return img;
}

/* --------------------------------------------------------------- */
/*  Expansion helpers                                               */
/* --------------------------------------------------------------- */

export function hasExpansion() {
  const e = board.expansion;
  return e.left > 0 || e.top > 0 || e.right > 0 || e.bottom > 0;
}

export function resetExpansionState() {
  board.expansion = { left: 0, top: 0, right: 0, bottom: 0 };
  board.expandedCanvasEl = null;
}
"""