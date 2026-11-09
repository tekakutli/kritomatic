"""The interceptor's canvas: zoomable, pannable, with an expansion
boundary on the *frame* edges.

`currentFitRect()` returns the screen placement of the expanded
frame — `x0, y0` is the frame's top-left (which is *not* the inner
image's top-left when expansion > 0).  `x0 = offsetX - exp.left *
zoom`, so shifting the frame's left margin moves `x0` left by the
same amount at fixed zoom.  The inner image draws at `offsetX,
offsetY` and does not move when expansion changes.

`fitView()` computes `zoom` from the frame's dims and centers the
frame.  Runs once on task select (after payload load) and on
`Home` / `0`.  It is the *only* operation that touches `zoom`.

`zoomAt(sx, sy, factor)` keeps the world pixel under the cursor
fixed.  In the absolute-zoom model this reduces to a closed-form
offset adjustment — no fixed-point iteration.

`edgeHandlePositions(fit)` places handles at the *outer frame
edges*.  Dragging the left handle outward extends the frame
leftward; the handle follows the cursor because `x0` moves with
`Δleft * zoom`, which the drag delta converts to exactly.

The boundary line traces the outer frame.  When expansion = 0 the
frame edge coincides with the payload edge, so the boundary looks
like a payload border — same drawing, same handles, same behavior
— but the moment expansion becomes nonzero the frame draws larger
than the payload and the boundary moves outward with it.
"""

VIEW_JS = r"""
import { board, view, ctx, getCachedImage } from "./pg_state.js";

const HANDLE_R     = 8;
const HANDLE_HIT_R = 12;

function _placeholder(cw, ch, text) {
  ctx.fillStyle = "#5a6774";
  ctx.font = "700 12px 'JetBrains Mono', monospace";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(text, cw / 2, ch / 2);
}

/* Fit `Wp × Hp` into the viewport and center the frame.  Sets
   view.zoom, view.offsetX, view.offsetY. */
export function fitView() {
  const item = board.selectedItem;
  if (!item) return;

  let Wp = 0, Hp = 0;
  if (board.expandedCanvasEl) {
    Wp = board.expandedCanvasEl.width;
    Hp = board.expandedCanvasEl.height;
  } else if (item.payload) {
    const pImg = getCachedImage(item.payload.canvas_b64);
    if (!pImg || !pImg.complete || !pImg.naturalWidth) return;
    Wp = pImg.naturalWidth;
    Hp = pImg.naturalHeight;
  } else {
    return;
  }
  if (!Wp || !Hp) return;

  const cw = window.innerWidth, ch = window.innerHeight;
  const pad = 40;
  const zoom = Math.min((cw - 2 * pad) / Wp, (ch - 2 * pad) / Hp);

  const exp = board.expansion;
  const frameX0 = (cw - Wp * zoom) / 2;
  const frameY0 = (ch - Hp * zoom) / 2;

  view.zoom = zoom;
  view.offsetX = frameX0 + exp.left * zoom;
  view.offsetY = frameY0 + exp.top  * zoom;
}

/* Return the current on-screen placement of the frame being shown.
   `x0, y0, w, h` are the frame's screen rect; the inner image is at
   `(offsetX, offsetY)` inside it. */
export function currentFitRect() {
  const item = board.selectedItem;
  if (!item) return null;

  let img = null, Wp = 0, Hp = 0;

  if (board.view === "result" && item.result && item.result.image_b64) {
    const rImg = getCachedImage(item.result.image_b64);
    if (!rImg || !rImg.complete || !rImg.naturalWidth) return null;
    img = rImg;
    Wp = rImg.naturalWidth;
    Hp = rImg.naturalHeight;
  } else if (board.expandedCanvasEl) {
    img = board.expandedCanvasEl;
    Wp = img.width;
    Hp = img.height;
  } else if (item.payload && item.payload.canvas_b64) {
    const pImg = getCachedImage(item.payload.canvas_b64);
    if (!pImg || !pImg.complete || !pImg.naturalWidth) return null;
    img = pImg;
    Wp = pImg.naturalWidth;
    Hp = pImg.naturalHeight;
  } else {
    return null;
  }

  const exp = board.expansion;
  const s = view.zoom;
  const frameX0 = view.offsetX - exp.left * s;
  const frameY0 = view.offsetY - exp.top  * s;

  return {
    img, Wp, Hp, s,
    w: Wp * s, h: Hp * s,
    x0: frameX0, y0: frameY0,
  };
}

/* Zoom at cursor.  Keeps the world pixel under (screenX, screenY)
   fixed across the zoom change. */
export function zoomAt(screenX, screenY, factor) {
  const fit = currentFitRect();
  if (!fit) return;

  const wx = (screenX - fit.x0) / fit.s;
  const wy = (screenY - fit.y0) / fit.s;

  const newZoom = Math.max(0.05, Math.min(32, view.zoom * factor));
  if (newZoom === view.zoom) return;
  view.zoom = newZoom;

  const exp = board.expansion;
  // want: new_x0 + wx * newZoom = screenX
  // new_x0 = offsetX - exp.left * newZoom
  // => offsetX = screenX - wx * newZoom + exp.left * newZoom
  view.offsetX = screenX - wx * newZoom + exp.left * newZoom;
  view.offsetY = screenY - wy * newZoom + exp.top  * newZoom;
}

/* Handles at the OUTER frame edges. */
export function edgeHandlePositions(fit) {
  return [
    { side: "left",   x: fit.x0,             y: fit.y0 + fit.h / 2 },
    { side: "right",  x: fit.x0 + fit.w,     y: fit.y0 + fit.h / 2 },
    { side: "top",    x: fit.x0 + fit.w / 2, y: fit.y0 },
    { side: "bottom", x: fit.x0 + fit.w / 2, y: fit.y0 + fit.h },
  ];
}

export function edgeHitAt(sx, sy) {
  const fit = currentFitRect();
  if (!fit) return null;
  for (const h of edgeHandlePositions(fit)) {
    if (Math.hypot(sx - h.x, sy - h.y) <= HANDLE_HIT_R) return h.side;
  }
  return null;
}

function _drawBoundary(fit) {
  const active = board.expansion.left > 0 || board.expansion.top > 0
              || board.expansion.right > 0 || board.expansion.bottom > 0;

  ctx.save();
  ctx.strokeStyle = active
    ? "rgba(180, 155, 255, 1.0)"
    : "rgba(180, 155, 255, 0.45)";
  ctx.lineWidth = 2;
  ctx.setLineDash([6, 4]);
  ctx.strokeRect(fit.x0, fit.y0, fit.w, fit.h);
  ctx.setLineDash([]);
  ctx.restore();

  ctx.save();
  for (const h of edgeHandlePositions(fit)) {
    ctx.beginPath();
    ctx.arc(h.x, h.y, HANDLE_R, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(180, 155, 255, 1.0)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 2;
    ctx.stroke();
  }
  ctx.restore();
}

export function drawView() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.fillStyle = "#0a0e14";
  ctx.fillRect(0, 0, cw, ch);

  const item = board.selectedItem;
  if (!item) { _placeholder(cw, ch, "waiting for a task"); return; }

  const fit = currentFitRect();
  if (!fit) { _placeholder(cw, ch, "loading\u2026"); return; }

  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(fit.img, fit.x0, fit.y0, fit.w, fit.h);

  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 1;
  ctx.strokeRect(fit.x0 + 0.5, fit.y0 + 0.5, fit.w - 1, fit.h - 1);

  _drawBoundary(fit);
}
"""