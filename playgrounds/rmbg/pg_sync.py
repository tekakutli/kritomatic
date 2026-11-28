"""State synchronizer.

Pushes the playground's current state into the rmbg server's
in-memory cache.  Everything here mirrors inpaint's `pg_sync.js`,
but with two important differences:

  · **No rotation.**  The rotation field is dropped entirely.

  · **Boundary-aware canvas / mask translation.**  The client
    works in boundary-cropped display coords; the daemon works in
    full-image coords.  This module is where the two frames meet:

        canvas_b64    = the FULL image (not the display crop).
        selection_b64 = the displayed boundary-sized mask, placed
                        at (board.boundary.x, board.boundary.y) in
                        a full-image-sized canvas.  Falls back to
                        an all-white full-canvas mask when no mask
                        is selected.
        boundary_*    = the boundary rect in full-image coords.
        context_*     = the context rect in full-image coords
                        (boundary coords + boundary offset), only
                        meaningful when context_enabled is true.

    The server intersects `boundary ∩ context_rect` (or uses
    `boundary` alone when the checkbox is off) to derive the
    frame the daemon crops to, and the daemon pastes the result
    back at `(frame.cropX, frame.cropY)`.

WHY NO KRITA SCRATCH LAYERS
===========================
Same rationale as inpaint — see that playground's `pg_sync.js`
docstring.  No Krita writes; the daemon queries `/resources` at
prepare time.

`theselection` semantics
========================
White mask = "select all inside the crop".  Black = "select
nothing".  We send white as the no-mask fallback because the
"nothing" case made the workflow look successful while doing
nothing visible.

TRACKER ADVANCE ON SUCCESS ONLY
===============================
The trackers (`_lastCanvasHash`, `_lastSelectionB64`,
`_lastBoundaryHash`, `_lastContextHash`) record what the SERVER
has received.  They advance only on `data.success`.  On failure
they stay put, so the next `_doSync` recomputes the delta
against the server's actual last-known state and retries.
Recovery from a mid-session `/sync` failure is thus "make one
more state change" — no manual retry button needed.

BOOT GATE
=========
Autosync's first-canvas-arrival fires `scheduleSync` before
mask hydration has necessarily settled.  Setting a boot-gate
promise (`setBootSyncGate`) BEFORE autosync starts means every
`_doSync` awaits hydration before sending, so the first push
carries the hydrated (or definitively white-fallback)
`theselection`.  Same design as inpaint's, same 3s hard cap."""

SYNC_JS = r"""
import { board } from "./pg_state.js";
import { selectedMaskIdx } from "./pg_masks.js";
import { SYNC_DEBOUNCE_MS } from "./pg_tuning.js";
import { flashStatus } from "./pgkit/panel_core.js";

let _timer            = null;
let _lastCanvasHash   = "";
let _lastSelectionB64 = "";
let _lastBoundaryHash = "";
let _lastContextHash  = "";
let _warnedSyncFailure = false;
let _bootGate = null;

export function setBootSyncGate(promise) {
  if (!promise || typeof promise.then !== "function") return;
  const gated = promise.finally(() => {
    if (_bootGate === gated) _bootGate = null;
  });
  gated.catch(() => {});
  _bootGate = gated;
}

function _stripDataUrl(b64) {
  return String(b64 || "").replace(/^data:image\/png;base64,/, "");
}

export function scheduleSync() {
  if (_timer) clearTimeout(_timer);
  _timer = setTimeout(_doSync, SYNC_DEBOUNCE_MS);
}

export function syncNow() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  _doSync();
}

/* ============================================================
   Mask helpers — produce FULL-CANVAS-sized selection
   ============================================================
   The mask in `board.masks[idx]` is boundary-sized.  We pad it
   to full-image size so the payload dims match `canvas_b64`.
   Both dimensions use `board.fullImageWidth/Height` and place
   content at `board.boundary.x/y`. */

function _fullSizeMaskFromBoundaryImg(img) {
  const W = board.fullImageWidth;
  const H = board.fullImageHeight;
  if (!W || !H) return null;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.fillStyle = "#000";
  cx.fillRect(0, 0, W, H);
  cx.drawImage(img, board.boundary.x, board.boundary.y);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

function _fullSizeWhiteMask() {
  const W = board.fullImageWidth;
  const H = board.fullImageHeight;
  if (!W || !H) return null;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.fillStyle = "#fff";
  cx.fillRect(0, 0, W, H);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

async function _doSync() {
  _timer = null;

  if (_bootGate) {
    try {
      await Promise.race([
        _bootGate,
        new Promise(r => setTimeout(r, 3000)),
      ]);
    } catch (_) {}
  }

  const payload = {};
  let changed = false;

  let pendingCanvasHash   = null;
  let pendingSelectionB64 = null;
  let pendingBoundaryHash = null;
  let pendingContextHash  = null;

  /* ---- canvas + boundary ---- */
  const hasCanvas = !!board.imageHash && !!board.fullImageDataUrl;
  if (hasCanvas && board.imageHash !== _lastCanvasHash) {
    payload.canvas_b64 = _stripDataUrl(board.fullImageDataUrl);
    pendingCanvasHash = board.imageHash;
    changed = true;
  }

  const boundaryHash = JSON.stringify({
    x: board.boundary.x, y: board.boundary.y,
    w: board.boundary.w, h: board.boundary.h,
  });
  if (hasCanvas && boundaryHash !== _lastBoundaryHash) {
    payload.boundary_x = board.boundary.x;
    payload.boundary_y = board.boundary.y;
    payload.boundary_w = board.boundary.w;
    payload.boundary_h = board.boundary.h;
    pendingBoundaryHash = boundaryHash;
    changed = true;
  }

  /* ---- selection ---- */
  if (board.fullImageWidth > 0 && board.fullImageHeight > 0) {
    const idx = selectedMaskIdx();
    let selB64 = null;
    if (idx >= 0) {
      const m = board.masks[idx];
      if (m && m.loaded && m.img) {
        selB64 = _fullSizeMaskFromBoundaryImg(m.img);
      }
    }
    if (!selB64) {
      selB64 = _fullSizeWhiteMask();
    }
    if (selB64 && selB64 !== _lastSelectionB64) {
      payload.selection_b64 = selB64;
      pendingSelectionB64 = selB64;
      changed = true;
    }
  }

  /* ---- context (in full-image coords) ---- */
  const ctxEnabled = !!board.contextEnabled
                    && board.imageWidth > 0
                    && board.imageHeight > 0;
  const bx = board.boundary.x;
  const by = board.boundary.y;
  const ctxFull = {
    enabled: ctxEnabled,
    x: Math.round((board.context.x || 0) + (ctxEnabled ? bx : 0)),
    y: Math.round((board.context.y || 0) + (ctxEnabled ? by : 0)),
    w: Math.round(board.context.w || 0),
    h: Math.round(board.context.h || 0),
  };
  const contextHash = JSON.stringify(ctxFull);
  if (contextHash !== _lastContextHash) {
    payload.context_enabled = ctxFull.enabled;
    payload.context_x = ctxFull.x;
    payload.context_y = ctxFull.y;
    payload.context_w = ctxFull.w;
    payload.context_h = ctxFull.h;
    pendingContextHash = contextHash;
    changed = true;
  }

  if (!changed) return;

  board.syncing = true;
  try {
    const res = await fetch("/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.success) {
      if (pendingCanvasHash !== null)   _lastCanvasHash   = pendingCanvasHash;
      if (pendingSelectionB64 !== null) _lastSelectionB64 = pendingSelectionB64;
      if (pendingBoundaryHash !== null) _lastBoundaryHash = pendingBoundaryHash;
      if (pendingContextHash !== null)  _lastContextHash  = pendingContextHash;

      board.lastSync  = Date.now();
      board.syncError = null;
      _warnedSyncFailure = false;
    } else {
      board.syncError = data.message || "sync failed";
      if (!_warnedSyncFailure) {
        _warnedSyncFailure = true;
        flashStatus("sync failed: " + board.syncError, "bad");
      }
    }
  } catch (e) {
    board.syncError = e.message || "network error";
    if (!_warnedSyncFailure) {
      _warnedSyncFailure = true;
      flashStatus("sync failed: " + board.syncError, "bad");
    }
  } finally {
    board.syncing = false;
  }

  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}
"""