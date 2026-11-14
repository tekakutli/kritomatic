"""Interceptor state: board, view transform, canvas, draw scheduling,
image cache.

Leaf module.  Every other interceptor module imports from here and
nothing here imports any of them.

VIEW
====
`view = { zoom, offsetX, offsetY }` is the display transform.
`zoom` is *absolute* — screen pixels per canvas pixel.  At `zoom = 1`
the canvas is drawn 1:1.  `offsetX, offsetY` is the screen position
of the canvas's top-left corner.

PROMPT OVERRIDES
================
`board.prompt` reflects the current textarea value.
`board.promptOverrides` is a per-task-id dict of user-typed overrides
so switching between tasks preserves each task's edit.  `_selectTask`
in `pg_panel_bindings.js` restores the override (or the payload's
original prompt if no override yet) on selection.

RESULTS
=======
`board.selectedItem.result` is the daemon's response data from a
successful resume: `{batch_id, job_id, layer_names, output_count,
timing, prompt_override_applied}`.  `syncPanelFields` in
`pg_panel_fields.js` renders it below the Run / Discard buttons.

OVERLAYS
========
`board.showSelection` and `board.showContext` gate the two tinted
mask overlays in `pg_view_image.js`.  Both default OFF so the canvas
shows the raw prepared image the model receives.  Toggling a flag
redraws via `_scheduleDraw()`.

The toggles are session-scoped (no persistence).  Reloading the
interceptor tab resets them to OFF, which matches the "raw image by
default" contract.
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
  prompt:              "",
  promptOverrides:     {},
  pollTimer:           null,
  // Overlay toggles.  Both OFF by default so the canvas shows the
  // raw prepared image; toggled on for verification aid.
  showSelection:       false,
  showContext:         false,
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
/*  Image cache                                                     */
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
"""