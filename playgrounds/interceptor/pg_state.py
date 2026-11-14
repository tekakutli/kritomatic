"""Interceptor state: board, view transform, two canvases, draw
scheduling, image cache.

Leaf module.  Every other interceptor module imports from here and
nothing here imports any of them.

VIEW
====
`view = { zoom, offsetX, offsetY }` is the display transform.
`zoom` is *absolute* — screen pixels per canvas pixel.  At `zoom = 1`
the canvas is drawn 1:1.  `offsetX, offsetY` is the screen position
of the canvas's top-left corner.

TWO CANVASES
============
The render surface is split into two stacked canvases:

    #c              image layer: raw prepared canvas + border.  The
                    dark fill is provided by the shared pgkit CSS
                    (`canvas { background:#0a0e14 }`); the canvas is
                    cleared to transparent so the CSS shows through.
    #overlayCanvas  UI layer: tint overlays (multimask, context,
                    selection) plus the placeholder text for the
                    no-task / loading states.  Transparent background;
                    `pointer-events: none` so all mouse events still
                    land on `#c`.

`pg_view.js` computes a content signature of each layer's input set
per `drawView()` call and repaints a layer only when its signature
changed.  The sigs live in `_layerSigs`, exported from here so
`resize()` can invalidate them (`canvas.width = …` clears the pixel
buffer regardless of whether the sig would have changed).

The split exists to exploit disjoint change triggers:

  · Pan / zoom move every layer — both repaint (unavoidable).
  · Overlay toggles (selection / context / multimask) touch only
    the overlay — the raw image blit is skipped entirely.
  · Poll refresh or task-status updates touch neither canvas —
    `syncPanelFields` handles those, both sigs are unchanged, both
    layers skip.
  · Task switches change `_itemKey` in both sigs — both repaint once.
  · Image load (asynchronous, after a task switch) flips
    `loaded` from 0 to 1 in both sigs — both repaint once, then
    the overlay's tint layers become drawable.

`_scheduleDraw`      → full `draw()` (canvas + panel syncs).
`_scheduleDrawView`  → `drawView()` only (canvas-only repaint).

A pending `_scheduleDraw` upgrades a queued `_scheduleDrawView`;
a pending view-only scheduler is a no-op if a full draw is already
queued (the full draw calls `drawView` internally).

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

PENDING LOCAL RUNS
==================
`board.pendingLocalRuns` tracks the task ids this client has fired
`_runAction` / `_discardAction` for, and whose terminal status has
not yet been confirmed by a list refresh.  `effectiveStatus(task)`
returns `"running"` for any task in that set regardless of the
task's last-polled status, so:

  · The panel's Run / Discard buttons disable immediately on click
    and stay disabled across the brief window between the click and
    the server's status flip.  Without the set, re-selecting the
    task during that window (against a stale poll cache) would
    re-enable the buttons and a second click would reach
    `handle_run`, which correctly returns `"task is running"` — a
    graceful failure, but a visible flicker.

  · The task-list row does not flip back to `"queued"` on the next
    poll before the server has processed the request.

The set is in-process state only.  It never reaches the server,
never mutates `board.tasks`, and is cleared in `_runAction` /
`_discardAction`'s `finally` block after the post-run refresh.  The
server side already flips `_TASKS[tid].status` to `"running"` under
its lock before doing any work, so the local set is just a mirror of
a fact the server has already committed to; the two cannot disagree
except in the interval after the click and before the server's flip,
which is exactly what the set exists to cover.
"""

STATE_JS = r"""
export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

export const overlayCanvas = document.createElement("canvas");
overlayCanvas.id = "overlayCanvas";
document.body.appendChild(overlayCanvas);
export const overlayCtx = overlayCanvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

/* ============================================================
   Per-layer sig caches — shared with resize().
   ============================================================ */

export const _layerSigs = { image: null, overlay: null };

/* ============================================================
   View
   ============================================================ */

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
  // Task ids this client has fired _runAction / _discardAction for,
  // whose terminal status has not yet been confirmed by a list
  // refresh.  See this module's docstring, "PENDING LOCAL RUNS".
  pendingLocalRuns:    new Set(),
};

/* ============================================================
   Effective status
   ============================================================
   The status the UI should display and gate on.  A task in
   `pendingLocalRuns` reports "running" regardless of the last-
   polled `task.status`, so the Run/Discard buttons disable
   immediately on click and stay disabled across the brief window
   between the click and the server's status flip.

   Every UI consumer that renders or gates on a task's status reads
   through this function, not `task.status` directly: the task-list
   row, the info pane, the result line, and `syncPanelFields`'
   button gating.  That is what keeps the panel consistent during
   the window. */

export function effectiveStatus(task) {
  if (!task) return null;
  if (task.task_id && board.pendingLocalRuns.has(task.task_id)) {
    return "running";
  }
  return task.status;
}

/* ============================================================
   Draw scheduling — two levels
   ============================================================
   `_drawPending`  a RAF is queued.
   `_drawFull`     the queued RAF should call `_drawFn` (draw), not
                   `_drawViewFn` (drawView).

   `_scheduleDraw`     sets `_drawFull = true` and (re)uses the
                       pending RAF.  Upgrades a pending view-only
                       RAF to full.
   `_scheduleDrawView` sets `_drawFull = false` only if no RAF is
                       pending.  A pending full draw is left alone
                       (it will call `drawView` internally). */

let _drawPending = false;
let _drawFull    = false;
let _drawFn      = null;
let _drawViewFn  = null;

export function _setDraw(fn)     { _drawFn = fn; }
export function _setDrawView(fn) { _drawViewFn = fn; }

function _fire() {
  const full = _drawFull;
  _drawPending = false;
  _drawFull = false;
  if (full) { if (_drawFn) _drawFn(); }
  else      { if (_drawViewFn) _drawViewFn(); }
}

export function _scheduleDraw() {
  _drawFull = true;
  if (_drawPending) return;
  _drawPending = true;
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(_fire);
  else setTimeout(_fire, 16);
}

export function _scheduleDrawView() {
  if (_drawPending) return;   // don't downgrade a pending full draw
  _drawFull = false;
  _drawPending = true;
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(_fire);
  else setTimeout(_fire, 16);
}

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;

  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  overlayCanvas.width  = Math.round(cw * _dpr);
  overlayCanvas.height = Math.round(ch * _dpr);
  overlayCanvas.style.width  = cw + "px";
  overlayCanvas.style.height = ch + "px";
  overlayCtx.setTransform(_dpr, 0, 0, _dpr, 0, 0);

  _layerSigs.image = null;
  _layerSigs.overlay = null;

  if (_drawViewFn) _drawViewFn();
  else if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);

/* ============================================================
   Image cache
   ============================================================ */

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