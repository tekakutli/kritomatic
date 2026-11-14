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

`board.areaPromptOverrides` is the analogous per-task-id dict for
per-area multimask prompts:
`{task_id: {area_id: prompt}}`.  Edits to an area's prompt are
written here; on Run they're shipped to the server, which pushes them
to multimask before resuming.  Cleared on task switch.

RESULTS
=======
`board.selectedItem.result` is the daemon's response data from a
successful resume: `{batch_id, job_id, layer_names, output_count,
timing, prompt_override_applied}`.  `syncPanelFields` in
`pg_panel_fields.js` renders it below the Run / Discard buttons.

MULTIMASK SNAPSHOT
==================
`board.multimask` is the multimask snapshot from the daemon, or null
when the workflow has no `MultiAreaConditioning` node:

    {
      url: "<multimask base>",
      state: {globals, areas},
      masks: {<hash>: <b64>},
    }

`board.showMultimask` gates the per-area tinted overlay in
`pg_view_image.js`.  Default true when the snapshot exists — the
masks are the whole point of reviewing a multimask workflow.

PER-AREA COLOURS
================
`areaColorById(id)` mirrors multimask's own colour logic: hash the
area id to pick from `MASK_COLORS`.  Duplicated (rather than shared)
so this playground has no import-time dependency on multimask; the
list is stable and small.
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

/* ============================================================
   Per-area colours — must match multimask/pg_state.js's
   MASK_COLORS + areaColorById so the same area shows the same
   colour in both playgrounds.
   ============================================================ */

export const MASK_COLORS = [
  { r: 255, g:  90, b: 105 },
  { r: 120, g: 220, b: 255 },
  { r: 255, g: 200, b:  90 },
  { r: 140, g: 225, b: 160 },
  { r: 180, g: 155, b: 255 },
  { r: 255, g: 165, b: 105 },
  { r: 110, g: 220, b: 210 },
  { r: 220, g: 230, b: 120 },
];

export function areaColorById(id) {
  let h = 0;
  const s = String(id || "");
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0;
  }
  return MASK_COLORS[Math.abs(h) % MASK_COLORS.length];
}

export const board = {
  tasks:               {},
  order:               [],
  selectedTaskId:      null,
  selectedItem:        null,
  prompt:              "",
  promptOverrides:     {},
  areaPromptOverrides: {},   // {task_id: {area_id: prompt}}
  pollTimer:           null,
  // Overlay toggles.  selection/context default OFF (raw canvas
  // contract); multimask defaults ON when a snapshot exists (see
  // _selectTask).
  showSelection:       false,
  showContext:         false,
  showMultimask:       false,
  // Multimask snapshot from the daemon, or null.
  multimask:           null,
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