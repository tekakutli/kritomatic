"""Interceptor state: board dict, canvas, draw scheduling, image cache.

Leaf module.  Every other interceptor module imports from here and
nothing here imports any of them.

`board.prompt` is the prompt *override* slot.  An empty string means
"no override — use the payload's own prompt".  The `intercept.prompt`
pgkit field (registered in `pg_panel_bindings.py`) reads from here and
falls back to the selected item's payload prompt when empty, so a
script that inspects the field after selecting a task sees the
payload's prompt.  Setting the field writes here.  The prompt is never
rendered as a DOM input; see `pg_base.py`'s module docstring.
"""

STATE_JS = r"""
export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

export const board = {
  tasks:          {},     // task_id -> summary row
  order:          [],     // task_ids, newest-first
  selectedTaskId: null,
  selectedItem:   null,   // full item incl. payload and result
  view:           "input",
  prompt:         "",     // override slot; "" means "use payload's"
  pollTimer:      null,
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
"""