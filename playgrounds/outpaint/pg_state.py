"""Outpaint state: area (plain rect + image), viewer (pixel-space
rect), view, and the generated-image stack.

The viewer rect is stored in **area pixel coordinates**
(`{pxX, pxY, pxW, pxH}`).  Its aspect ratio is not a stored field —
it is derived from the currently displayed image
(`board.areaImageW / board.areaImageH`) inside `viewerAspect()`.
The viewer *is* the image: as soon as an image is loaded, the viewer
aspect equals the image aspect, and the contain-fit inside the
viewer degenerates to an exact full fill (no letterboxing).

Area resize does NOT alter the viewer — it only clamps the viewer's
position if the new area would otherwise not contain it, and clamps
the area dims up to at least the viewer size.

SOURCE vs PREVIEW vs GENERATED STACK
====================================
Two image channels exist:

  · **Source** (`@thecanvas`) — the live Krita layer picked in the
    docker's Playground Input row.  Loaded via `applyCanvasImage()`:
    contain-fit inside the viewer, aspect follows the image.

  · **Generated stack** (`board.generatedStack`) — outpaint results
    received from the daemon's event bus.  Each entry is
    `{id, hash, b64, dataUrl, width, height, ts}`.  Entries are
    never auto-displayed: they sit in the stack until the user
    clicks one, which calls `previewGenerated(idx)`.

`board.previewIndex` is `-1` while showing the source, or `>= 0`
(the stack index) while previewing a generated result.  Previewing
saves the source's current area+viewer state in
`board.savedSourceView` and resizes the area to the generated
image's exact dimensions (full-size preview); `previewSource()`
restores from that snapshot.  The restore is best-effort — if the
user resized the area *during* preview, that resize is discarded,
which is the intent: preview is temporary.

`bufferIsGenerated()` is now `previewIndex >= 0` — the previous
"hash prefix" heuristic is gone.

RENDER SCALE — NO FIT-TO-WINDOW
===============================
`view.zoom` is pixels-of-screen per pixel-of-area, and it is the
*only* thing that scales the rendered area.  There is no automatic
fit-to-window: a 400-px area renders at 400 screen px at zoom=1, a
4000-px area renders at 4000 screen px at zoom=1 (extending
off-screen as needed), and both scale identically with wheel zoom.
Pan and zoom are the user's navigation, not an auto-fit heuristic.

One-shot auto-fit at boot lives in `pg_panel.js`
(`_fitAreaIfOverflow`) and is the *only* place where the scale is
computed from window size.  It never fires during a drag and never
fires on `resize()`, so the boundary cannot reappear through any
interactive path.

AREA CORNER DRAG
================
The area's four corners are draggable for resizing (no aspect lock —
the area is a plain rect, not a viewport).  The area model has no
origin offset: it always spans `(0, 0) → (areaWidth, areaHeight)`.
`beginAreaDrag` / `updateAreaDrag` / `endAreaDrag` implement the drag
with the *opposite* corner anchored:

  · SE drag → `areaWidth`/`areaHeight` grow/shrink; viewer pos fixed.
  · NW drag → area shrinks/grows from top-left; viewer position is
    shifted in area-pixel space so it stays visually put.
  · NE drag → width grows right, height shrinks/grows from top;
    viewer Y is shifted.
  · SW drag → height grows down, width shrinks/grows from left;
    viewer X is shifted.

The mouse → area-pixel conversion uses a **frozen `s`** captured at
drag-start (in `beginAreaDrag`).  Since `s = view.zoom` is not
window-dependent anymore, this frozen `s` is also just `view.zoom`,
which doesn't change mid-drag — so the drag is guaranteed stable
regardless.

STATE SYNC TO SERVER
====================
The viewer rect and area dims are mirrored to the server via
`/state` (POST `{op: "set", state: {...}}`).  The server's `/params`
route computes the `OUTPAINT_*_PAD` values from that state, which the
daemon queries at generation time to feed the corresponding workflow
parameters.  See `outpaint_server.py`'s PARAM VALUES for the
connection.

**The server must always see the SOURCE-mode layout, never the
PREVIEW layout.**  Preview mode (`previewIndex >= 0`) resizes the
area to the previewed image's exact dims and fills the viewer to
the whole area.  If that layout reached the server, `handle_params`
would compute:

    LEFT   = viewer.pxX           = 0
    TOP    = viewer.pxY           = 0
    RIGHT  = area_w - pxX - pxW   = 0
    BOTTOM = area_h - pxY - pxH   = 0

...i.e. all four pads = 0, and the next generation would outpaint
with zero padding, producing the same image as its input.  That is
exactly the "generate while previewing → get the input back as the
output" bug.  The user saw it as "if I forget to click Show source
before Run, the next generated image is identical to the input".

The fix is in `_doSync()`: when `board.previewIndex >= 0`, the
posted state is taken from `board.savedSourceView` (the source
snapshot `previewGenerated()` took on entry) instead of the current
preview-visible fields.  Preview remains a pure display mode; it
never touches what the daemon sees.

The payload still includes a `ratio` string, computed from
`board.sourceImageW/H` (persistent source dims — always current,
even while previewing) in source mode, and from `viewerAspect()`
otherwise.  The server stores it but never uses it for pad math
(pads derive from `viewer.px*` and `area_dims` only), so the field
is informational.

`scheduleSync()` debounces (250 ms), so dragging the viewer or
scrubbing area dims produces at most one round-trip per debounce
window.  `syncNow()` bypasses the debounce and is used at drag-end
so that the first generation triggered after a drag sees current
values, not the last debounced snapshot.

`_syncInFlight` prevents overlapping POSTs.  If a sync is in flight
when a state change fires, the change is dropped — the next change
(or the next explicit `syncNow`) will pick up the latest state.  A
brief "server state is one debounce behind the browser" window is
acceptable; a queue of stacked POSTs racing each other is not.
"""

STATE_JS = r"""
import {
  VIEW_ZOOM_MIN, VIEW_ZOOM_MAX, VIEW_PAN_MARGIN, VIEWER_MIN_SIZE_PX,
} from "./pg_tuning.js";

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");

let _dpr = window.devicePixelRatio || 1;

export const view = { zoom: 1, panX: 0, panY: 0 };

export function resetView() {
  view.zoom = 1;
  view.panX = 0;
  view.panY = 0;
}

export function clampPan() {
  if (!board.areaWidth || !board.areaHeight) return;
  const cw = window.innerWidth, ch = window.innerHeight;
  const m  = VIEW_PAN_MARGIN;
  // No fit-to-window: scale is view.zoom, always.  See module
  // docstring, RENDER SCALE — NO FIT-TO-WINDOW.
  const s = view.zoom;
  const w = board.areaWidth  * s;
  const h = board.areaHeight * s;
  const baseX = (cw - w) / 2;
  const baseY = (ch - h) / 2;
  view.panX = Math.max(m - baseX - w, Math.min(cw - m - baseX, view.panX));
  view.panY = Math.max(m - baseY - h, Math.min(ch - m - baseY, view.panY));
}

export function zoomAt(sx, sy, nz) {
  const r0 = fittedImageRect();
  if (!r0) return;
  const ix = (sx - r0.x0) / r0.s;
  const iy = (sy - r0.y0) / r0.s;
  view.zoom = Math.max(VIEW_ZOOM_MIN, Math.min(VIEW_ZOOM_MAX, nz));
  const r1 = fittedImageRect();
  view.panX += sx - (r1.x0 + ix * r1.s);
  view.panY += sy - (r1.y0 + iy * r1.s);
  clampPan();
}

export const board = {
  areaWidth:  1024,
  areaHeight: 1024,

  // Currently displayed image (either the source or the
  // currently-previewed generated item).  `areaImageHash` is set
  // from `sourceImageHash` or the previewed stack entry's `id`.
  areaImageEl:      null,
  areaImageHash:    null,
  areaImageDataUrl: null,
  areaImageW:       0,
  areaImageH:       0,

  // Persistent snapshot of the source (@thecanvas) image.  Always
  // kept up to date by applyCanvasImage, even while a generated
  // item is being previewed.  The visible fields above mirror these
  // only when previewIndex < 0.
  sourceImageEl:      null,
  sourceImageHash:    null,
  sourceImageDataUrl: null,
  sourceImageW:       0,
  sourceImageH:       0,

  // Generated-image stack.  Each entry: {id, hash, b64, dataUrl,
  // width, height, ts}.  Populated by pg_autosync.js on
  // outpaint_result events.  Never auto-displayed — the user must
  // click a thumbnail to preview it.  See module docstring,
  // SOURCE vs PREVIEW vs GENERATED STACK.
  generatedStack: [],
  // -1 = showing source; >= 0 = previewing generatedStack[previewIndex].
  previewIndex: -1,
  // Snapshot of area+viewer taken when entering preview mode.
  // Restored by previewSource().  Null while not previewing.
  //
  // Also read by _doSync(): while previewing, the server is sent
  // THIS snapshot's state (the source layout) instead of the
  // preview-visible fields, so /params continues to report the
  // user's configured pads rather than 0/0/0/0.  See module
  // docstring, STATE SYNC TO SERVER.
  savedSourceView: null,

  // Viewer: absolute pixel coords in area space.  Aspect follows
  // `viewerAspect()` (i.e. the fetched image).  NEVER resized by
  // area-dims changes.
  viewer: {
    pxX: 50, pxY: 50, pxW: 900, pxH: 900,
  },

  viewerDragActive:  false,
  viewerDragHandle:  null,
  viewerDragStart:   null,
  viewerDragStartIx: 0,
  viewerDragStartIy: 0,

  // Area corner drag state.  `areaDragStart` captures { w, h, pxX,
  // pxY, sx, sy, s } at drag start; `s` is view.zoom at start (it
  // doesn't change mid-drag, so freezing is defensive rather than
  // load-bearing).
  areaDragActive: false,
  areaDragHandle: null,
  areaDragStart:  null,

  mouse: { x: 0, y: 0, inside: false },
};

/* Aspect ratio of the currently displayed image.  Falls back to 1.0
 * (square) before any image has loaded. */
export function viewerAspect() {
  const w = board.areaImageW;
  const h = board.areaImageH;
  if (w > 0 && h > 0) return w / h;
  return 1.0;
}

/* True iff the buffer is currently showing a generated image (i.e.
 * the user has clicked a stack thumbnail).  Send to Krita requires
 * this — it never sends the source.  See module docstring,
 * SOURCE vs PREVIEW vs GENERATED STACK. */
export function bufferIsGenerated() {
  return board.previewIndex >= 0
      && board.previewIndex < board.generatedStack.length;
}

/* Return the currently previewed stack entry, or null if the source
 * is showing. */
export function getPreviewed() {
  if (!bufferIsGenerated()) return null;
  return board.generatedStack[board.previewIndex];
}

/* Notify any listener (currently pg_panel.js) that the stack or
 * preview index changed.  Fires synchronously on the caller's
 * thread — pg_panel re-renders the thumbnail strip. */
function _notifyStackChanged() {
  try {
    window.dispatchEvent(new CustomEvent("pg:stackChanged"));
  } catch (_) {
    // CustomEvent not available (very old browsers) — the panel
    // will still update on the next draw via onDrawComplete.
  }
}

/* Append one generated entry to the stack.  Does NOT change the
 * current preview selection.  Called by pg_autosync.js when a new
 * outpaint_result arrives from the daemon.  Returns the new index. */
export function pushGeneratedToStack(entry) {
  const id = entry.id || ("gen_" + (entry.ts || Date.now()));
  board.generatedStack.push({
    id:      id,
    hash:    entry.hash || id,
    b64:     entry.b64 || "",
    dataUrl: entry.dataUrl || ("data:image/png;base64," + (entry.b64 || "")),
    width:   Math.max(0, Number(entry.width)  || 0),
    height:  Math.max(0, Number(entry.height) || 0),
    ts:      Number(entry.ts) || Date.now(),
  });
  _notifyStackChanged();
  return board.generatedStack.length - 1;
}

/* Preview stack[idx] at full size: resize the area to the image's
 * exact dimensions and make the viewer fill the whole area.  The
 * source's current area+viewer is saved for restore. */
export function previewGenerated(idx) {
  if (idx < 0 || idx >= board.generatedStack.length) return;
  if (board.previewIndex < 0) {
    // Entering preview from source mode: snapshot the source view.
    board.savedSourceView = {
      areaWidth:  board.areaWidth,
      areaHeight: board.areaHeight,
      viewer:     { ...board.viewer },
    };
  }
  board.previewIndex = idx;
  _applyPreviewedImage();
  scheduleSync();
  _notifyStackChanged();
}

/* Return to showing the source (@thecanvas).  Restores the saved
 * area+viewer if one exists; otherwise falls back to the current
 * source image's natural dims and contain-fit viewer. */
export function previewSource() {
  if (board.previewIndex < 0) return;
  board.previewIndex = -1;

  if (board.savedSourceView) {
    board.areaWidth  = board.savedSourceView.areaWidth;
    board.areaHeight = board.savedSourceView.areaHeight;
    board.viewer = { ...board.savedSourceView.viewer };
    board.savedSourceView = null;
  } else if (board.sourceImageW > 0 && board.sourceImageH > 0) {
    // Defensive fallback: no saved view but we have a source
    // image's dims.  Fit it.
    board.areaWidth  = Math.max(VIEWER_MIN_SIZE_PX, board.sourceImageW);
    board.areaHeight = Math.max(VIEWER_MIN_SIZE_PX, board.sourceImageH);
    resetViewerToFit();
  }

  _applySourceImage();
  clampPan();
  _scheduleDraw();
  scheduleSync();
  _notifyStackChanged();
}

/* Delete stack[idx].  If it was the active preview, fall back to
 * the adjacent item; if the stack becomes empty, return to source. */
export function deleteFromStack(idx) {
  if (idx < 0 || idx >= board.generatedStack.length) return;
  board.generatedStack.splice(idx, 1);

  if (board.generatedStack.length === 0) {
    if (board.previewIndex >= 0) {
      previewSource();
      return;  // previewSource already notified
    }
    // Nothing to preview; notify so the panel removes the strip.
    _notifyStackChanged();
    return;
  }

  if (board.previewIndex === idx) {
    const newIdx = Math.min(idx, board.generatedStack.length - 1);
    board.previewIndex = newIdx;
    _applyPreviewedImage();
    scheduleSync();
  } else if (board.previewIndex > idx) {
    board.previewIndex -= 1;
  }
  _notifyStackChanged();
}

/* Internal: set visible image fields from the current
 * generatedStack[previewIndex].  Resizes area to the image and
 * fills the viewer. */
function _applyPreviewedImage() {
  const entry = board.generatedStack[board.previewIndex];
  if (!entry) return;

  board.areaImageHash    = entry.id;
  board.areaImageDataUrl = entry.dataUrl;
  board.areaImageW       = entry.width;
  board.areaImageH       = entry.height;

  const img = new Image();
  img.onload = () => {
    board.areaImageEl = img;
    if (!board.areaImageW)  board.areaImageW  = img.naturalWidth;
    if (!board.areaImageH)  board.areaImageH  = img.naturalHeight;

    // Full-size preview: area = image dims, viewer covers area.
    board.areaWidth  = Math.max(VIEWER_MIN_SIZE_PX, board.areaImageW);
    board.areaHeight = Math.max(VIEWER_MIN_SIZE_PX, board.areaImageH);
    board.viewer.pxX = 0;
    board.viewer.pxY = 0;
    board.viewer.pxW = board.areaWidth;
    board.viewer.pxH = board.areaHeight;

    clampPan();
    _scheduleDraw();
  };
  img.onerror = () => { board.areaImageEl = null; _scheduleDraw(); };
  img.src = entry.dataUrl;
  _scheduleDraw();
}

/* Internal: mirror the source snapshot into the visible fields. */
function _applySourceImage() {
  board.areaImageHash    = board.sourceImageHash;
  board.areaImageDataUrl = board.sourceImageDataUrl;
  board.areaImageW       = board.sourceImageW;
  board.areaImageH       = board.sourceImageH;
  board.areaImageEl      = board.sourceImageEl;
}

/* Clamp viewer position (not size) so it fits inside the area. */
export function clampViewerPos() {
  const v = board.viewer;
  v.pxX = Math.max(0, Math.min(board.areaWidth  - v.pxW, v.pxX));
  v.pxY = Math.max(0, Math.min(board.areaHeight - v.pxH, v.pxY));
}

/* Fit the viewer inside the current area at ~90% of the larger
 * fit dimension, aspect-locked to the current image and centred.
 * Called once at boot, and again from applyCanvasImage() when a new
 * image arrives with a different aspect (via refitViewerAspect).
 * NOT called on area resize — that would violate "area resize must
 * not change viewer proportions". */
export function resetViewerToFit() {
  const v = board.viewer;
  const r = viewerAspect();
  const cw = board.areaWidth;
  const ch = board.areaHeight;
  if (!cw || !ch) return;

  const maxW = 0.9 * cw;
  const maxH = 0.9 * ch;
  let w = maxW;
  let h = w / r;
  if (h > maxH) {
    h = maxH;
    w = h * r;
  }
  v.pxW = Math.max(VIEWER_MIN_SIZE_PX, w);
  v.pxH = Math.max(VIEWER_MIN_SIZE_PX, h);
  v.pxX = Math.max(0, (cw - v.pxW) / 2);
  v.pxY = Math.max(0, (ch - v.pxH) / 2);
  _scheduleDraw();
  scheduleSync();
}

/* Re-fit viewer width/height to the current image aspect, keeping
 * the current centre and clamping to area.  Called when a new source
 * image arrives with a different aspect ratio (see
 * applyCanvasImage). */
export function refitViewerAspect() {
  const v = board.viewer;
  const r = viewerAspect();
  const aw = board.areaWidth;
  const ah = board.areaHeight;

  const cx = v.pxX + v.pxW / 2;
  const cy = v.pxY + v.pxH / 2;

  let w = v.pxW;
  let h = w / r;
  if (h > v.pxH) {
    h = v.pxH;
    w = h * r;
  }
  if (w > aw) { w = aw; h = w / r; }
  if (h > ah) { h = ah; w = h * r; }
  w = Math.max(VIEWER_MIN_SIZE_PX, w);
  h = Math.max(VIEWER_MIN_SIZE_PX, h);

  v.pxW = w;
  v.pxH = h;
  v.pxX = Math.max(0, Math.min(aw - w, cx - w / 2));
  v.pxY = Math.max(0, Math.min(ah - h, cy - h / 2));
  _scheduleDraw();
  scheduleSync();
}

/* Change area dims.  Enforces:
 *   · areaW ≥ viewer.pxW     (constraint 4: area ≥ viewer size)
 *   · areaH ≥ viewer.pxH
 *   · viewer.pxX/Y clamped so viewer is fully inside the new area.
 * Does NOT touch viewer.pxW / viewer.pxH (constraint 3).
 * Returns the actual new dims (may be larger than requested). */
export function setAreaDims(w, h) {
  const v = board.viewer;
  const nw = Math.max(
    VIEWER_MIN_SIZE_PX,
    Math.floor(Number.isFinite(w) ? w : board.areaWidth),
    Math.ceil(v.pxW),
  );
  const nh = Math.max(
    VIEWER_MIN_SIZE_PX,
    Math.floor(Number.isFinite(h) ? h : board.areaHeight),
    Math.ceil(v.pxH),
  );
  board.areaWidth  = nw;
  board.areaHeight = nh;
  clampViewerPos();
  clampPan();
  _scheduleDraw();
  scheduleSync();
  return { w: nw, h: nh };
}

/* ============================================================
   State sync to server (for playground-owned params)
   ============================================================
   Mirrors {area_dims, viewer} to /state so the server can compute
   the OUTPAINT_*_PAD values that the daemon queries at generation
   time.  See the module docstring, STATE SYNC TO SERVER. */

let _syncTimer = null;
let _syncInFlight = false;

export function scheduleSync() {
  if (_syncTimer) clearTimeout(_syncTimer);
  _syncTimer = setTimeout(_doSync, 250);
}

export function syncNow() {
  if (_syncTimer) { clearTimeout(_syncTimer); _syncTimer = null; }
  _doSync();
}

/* Build the {area_dims, viewer, ratio} payload that goes to the
 * server.  Two modes:
 *
 *   · Source mode (previewIndex < 0): use the live fields —
 *     that's the user's configured outpaint layout.
 *
 *   · Preview mode (previewIndex >= 0): use board.savedSourceView
 *     (the snapshot taken on entering preview) instead of the
 *     current visible fields.  Preview mode resizes the area to
 *     the previewed image's exact dims and fills the viewer to
 *     the whole area, so the live fields would compute all-zero
 *     pads and the next generation would outpaint with no padding
 *     — returning the input unchanged.  See the module docstring,
 *     STATE SYNC TO SERVER.
 *
 * The `ratio` field is informational (the server stores it but
 * never uses it for pad math).  In preview mode it is computed from
 * the persistent source image dims, not `viewerAspect()` — that
 * function returns the *previewed* image's aspect while a preview
 * is active, which is not what we want here. */
function _buildSyncPayload() {
  if (board.previewIndex >= 0 && board.savedSourceView) {
    const sv = board.savedSourceView;
    const sw = board.sourceImageW || sv.viewer.pxW;
    const sh = board.sourceImageH || sv.viewer.pxH;
    const ratio = sh > 0 ? (sw / sh).toFixed(4) : "1.0000";
    return {
      area_dims: [sv.areaWidth, sv.areaHeight],
      viewer: {
        pxX:  sv.viewer.pxX,
        pxY:  sv.viewer.pxY,
        pxW:  sv.viewer.pxW,
        pxH:  sv.viewer.pxH,
        ratio: ratio,
      },
    };
  }
  return {
    area_dims: [board.areaWidth, board.areaHeight],
    viewer: {
      pxX: board.viewer.pxX,
      pxY: board.viewer.pxY,
      pxW: board.viewer.pxW,
      pxH: board.viewer.pxH,
      ratio: viewerAspect().toFixed(4),
    },
  };
}

async function _doSync() {
  _syncTimer = null;
  if (_syncInFlight) return;
  _syncInFlight = true;
  try {
    await fetch("/state", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        op: "set",
        state: _buildSyncPayload(),
      }),
    });
  } catch (_) {
    // Network errors are transient; next change retries.
  } finally {
    _syncInFlight = false;
  }
}

/* ============================================================
   Area corner drag
   ============================================================ */

export function beginAreaDrag(handle, sx, sy) {
  const r = fittedImageRect();
  if (!r) return false;
  board.areaDragActive = true;
  board.areaDragHandle = handle;
  board.areaDragStart = {
    w:   board.areaWidth,
    h:   board.areaHeight,
    pxX: board.viewer.pxX,
    pxY: board.viewer.pxY,
    sx, sy,
    s:   r.s,   // view.zoom at drag start
  };
  return true;
}

export function updateAreaDrag(sx, sy) {
  if (!board.areaDragActive) return;
  const st = board.areaDragStart;
  if (!st || !st.s) return;

  // Convert screen delta to area-pixel delta using the frozen scale.
  const dax = (sx - st.sx) / st.s;
  const day = (sy - st.sy) / st.s;

  const MIN = VIEWER_MIN_SIZE_PX;
  const pxW = board.viewer.pxW;
  const pxH = board.viewer.pxH;

  let newW   = st.w;
  let newH   = st.h;
  let newPxX = st.pxX;
  let newPxY = st.pxY;

  const hnd = board.areaDragHandle;
  if (hnd === 'se') {
    newW = st.w + dax;
    newH = st.h + day;
  } else if (hnd === 'nw') {
    newW = st.w - dax;
    newH = st.h - day;
    newPxX = st.pxX - dax;
    newPxY = st.pxY - day;
  } else if (hnd === 'ne') {
    newW = st.w + dax;
    newH = st.h - day;
    newPxY = st.pxY - day;
  } else if (hnd === 'sw') {
    newW = st.w - dax;
    newH = st.h + day;
    newPxX = st.pxX - dax;
  } else {
    return;
  }

  // Area must always be at least MIN and at least the viewer size —
  // this is what enforces "area fully contains viewer" during drag.
  newW = Math.max(MIN, pxW, newW);
  newH = Math.max(MIN, pxH, newH);

  // Viewer must stay inside the area.
  newPxX = Math.max(0, Math.min(newW - pxW, newPxX));
  newPxY = Math.max(0, Math.min(newH - pxH, newPxY));

  board.areaWidth  = newW;
  board.areaHeight = newH;
  board.viewer.pxX = newPxX;
  board.viewer.pxY = newPxY;

  clampPan();
  _scheduleDraw();
}

export function endAreaDrag() {
  board.areaDragActive = false;
  board.areaDragHandle = null;
  board.areaDragStart  = null;
  scheduleSync();
}

/* Apply the fetched source image (autosync path).  The source
 * snapshot is always updated; the visible fields are only updated
 * when the source is currently being shown (previewIndex < 0).  A
 * same-aspect refresh leaves the user's viewer placement alone; a
 * genuinely different aspect re-fits via refitViewerAspect
 * (centre-preserving).  While previewing a generated item, the
 * source update is silent — when the user clicks "Show source", the
 * latest source is what appears.  See module docstring,
 * SOURCE vs PREVIEW vs GENERATED STACK. */
export function applyCanvasImage(hash, dataUrl, width, height) {
  const prevSourceW = board.sourceImageW;
  const prevSourceH = board.sourceImageH;
  const prevSourceAspect = (prevSourceW > 0 && prevSourceH > 0)
    ? prevSourceW / prevSourceH
    : null;

  board.sourceImageHash    = hash;
  board.sourceImageDataUrl = dataUrl;
  board.sourceImageW       = Math.max(0, Number(width)  || 0);
  board.sourceImageH       = Math.max(0, Number(height) || 0);

  if (typeof console !== "undefined" && console.log) {
    console.log(
      "[state] applyCanvasImage (SOURCE): hash="
      + String(hash).slice(0, 12) + " "
      + board.sourceImageW + "x" + board.sourceImageH
      + (board.previewIndex >= 0 ? " (silent, previewing)" : "")
    );
  }

  const img = new Image();
  img.onload = () => {
    board.sourceImageEl = img;
    if (!board.sourceImageW) board.sourceImageW = img.naturalWidth;
    if (!board.sourceImageH) board.sourceImageH = img.naturalHeight;

    // While previewing, do NOT touch the visible fields.
    if (board.previewIndex >= 0) return;

    // Mirror source → visible.
    _applySourceImage();

    const newAspect = (board.sourceImageW > 0 && board.sourceImageH > 0)
      ? board.sourceImageW / board.sourceImageH
      : 1.0;

    if (prevSourceAspect === null
        || Math.abs(newAspect - prevSourceAspect) > 0.001) {
      refitViewerAspect();
    } else {
      _scheduleDraw();
    }
  };
  img.onerror = () => { board.sourceImageEl = null; _scheduleDraw(); };
  img.src = dataUrl;
  _scheduleDraw();
}

export function clearAreaImage() {
  board.areaImageEl      = null;
  board.areaImageHash    = null;
  board.areaImageDataUrl = null;
  board.areaImageW       = 0;
  board.areaImageH       = 0;
  board.sourceImageEl      = null;
  board.sourceImageHash    = null;
  board.sourceImageDataUrl = null;
  board.sourceImageW       = 0;
  board.sourceImageH       = 0;
  _scheduleDraw();
}

/* Viewer-normalised → area-pixel.  Kept for compatibility with code
 * that still asks for viewer-normalised coordinates. */
export function canvasPxToViewerNorm(cx, cy) {
  const v = board.viewer;
  if (!v.pxW || !v.pxH) return [0, 0];
  return [(cx - v.pxX) / v.pxW, (cy - v.pxY) / v.pxH];
}

export function viewerNormToCanvasPx(vnx, vny) {
  const v = board.viewer;
  return [v.pxX + vnx * v.pxW, v.pxY + vny * v.pxH];
}

let _firKey = null;
let _firVal = null;

export function fittedImageRect() {
  const cw = window.innerWidth, ch = window.innerHeight;
  if (!board.areaWidth || !board.areaHeight) return null;

  const key = cw + "," + ch + "," + board.areaWidth + ","
            + board.areaHeight + "," + view.zoom + ","
            + view.panX + "," + view.panY;
  if (key === _firKey) return _firVal;

  // No fit-to-window: scale is view.zoom, always.  See module
  // docstring, RENDER SCALE — NO FIT-TO-WINDOW.
  const s = view.zoom;
  const w = board.areaWidth  * s;
  const h = board.areaHeight * s;
  const x0 = (cw - w) / 2 + view.panX;
  const y0 = (ch - h) / 2 + view.panY;

  _firKey = key;
  _firVal = { x0, y0, w, h, s };
  return _firVal;
}

export function imageToScreen(x, y) {
  const r = fittedImageRect();
  if (!r) return [x, y];
  return [r.x0 + x * r.s, r.y0 + y * r.s];
}

export function screenToImage(sx, sy) {
  const r = fittedImageRect();
  if (!r) return [0, 0];
  return [(sx - r.x0) / r.s, (sy - r.y0) / r.s];
}

let _drawScheduled = false;
let _drawFn = null;

export function _setDraw(fn) { _drawFn = fn; }

export function _scheduleDraw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  const fire = () => {
    _drawScheduled = false;
    if (_drawFn) _drawFn();
  };
  if (typeof requestAnimationFrame === "function") requestAnimationFrame(fire);
  else setTimeout(fire, 16);
}

export function resize() {
  _dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * _dpr);
  canvas.height = Math.round(ch * _dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(_dpr, 0, 0, _dpr, 0, 0);
  clampPan();
  if (_drawFn) _drawFn();
}
window.addEventListener("resize", resize);
"""