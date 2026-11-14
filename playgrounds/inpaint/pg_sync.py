"""State synchronizer.

Pushes the playground's current state into the daemon so the docker's
Run button can find it.  Two things travel:

  · pixel blobs — the canvas and the selected mask — pushed into the
    Krita scratch layers `thecanvas` and `theselection` via
    `comfy_set_layer_pixels`.  The daemon reads them from Krita at
    prepare time, same as any other `source="layer"` resource.

  · scalar state — the view rotation and the context rectangle — as
    fields on the `/sync` payload.  The daemon queries them back via
    `/rotation` and `/context` on demand.  No Krita layer is written
    for the context rectangle; the daemon defines `@thecontext` at
    prepare time from these numbers, accounting for rotation.

Rotation is not part of the pixel push.  It travels as a scalar, and
the daemon queries it at prepare and at paste time.  The value's
lifetime is scoped to the playground process: kill the server and the
daemon's `/rotation` call fails, falls back to 0, and no rotation
happens.

Same for the context rect: no `@thecontext` layer is created.  The
daemon queries `/context` at prepare time; if `enabled`, it uses those
coords (rotated, translated into the crop frame) as the context mask;
if not, it falls back to the `@thecontext` layer's own bbox (if any)
or the whole canvas.

Sync cadence:

  · State changes (image load, mask select/deselect, mask add/delete,
    context checkbox toggle, context rect drag) → `scheduleSync()`,
    debounced to `SYNC_DEBOUNCE_MS`.

  · Rotation changes → `syncNow()`, immediate.

Change detection: each piece of payload is hashed before push and
skipped if unchanged.  This keeps a mask-select from re-pushing the
whole canvas, and keeps a rotation-only change from re-pushing
anything.

FAILURE MIRRORING
=================
`/sync` failures are shown persistently in `#statusLine` (via
`board.syncError`, written by `pg_panel_fields.js`).  They are ALSO
mirrored to the playground server's stdout through
`pgkit/panel_core.js`'s `flashStatus` → `./__log` path, but only on
the *transition* from "sync working" to "sync broken":

    · first failure after a success  → one `flashStatus(..., "bad")`
                                       line (transient `#status` popup
                                       + one stdout line)
    · subsequent failures            → silent on stdout (`#statusLine`
                                       still updates the persistent
                                       message in case the reason
                                       changed)
    · success                        → clears the warn-streak flag, so
                                       the next failure warns again

This mirrors autosync's failure handling in `pg_autosync.js`: same
"warn once per streak" cadence, same reset-on-success contract.  The
difference is class: autosync's pull failures are `"warn"` (display
may be stale, everything else works); sync's push failures are
`"bad"` (docker's next Run will operate on stale state).  A
`"warn"` mirror is silenced by `?pgkit_no_mirror_warn=1`; a `"bad"`
one is not — deliberate, because a broken push path is a functional
problem the operator should see.

The class choice also matches interceptor's `_runAction` /
`_discardAction` failures, which use `flashStatus(..., "bad")` for
resume failures.  The two playgrounds now agree on "hard failure →
bad, soft failure → warn"."""

SYNC_JS = r"""
import { board, view } from "./pg_state.js";
import { selectedMaskIdx } from "./pg_masks.js";
import { SYNC_DEBOUNCE_MS } from "./pg_tuning.js";
import { flashStatus } from "./pgkit/panel_core.js";

let _timer            = null;
let _lastCanvasHash   = "";
let _lastSelectionB64 = "";
let _lastContextHash  = "";
let _lastRotation     = null;
/* Warn-streak flag for /sync failures.  False after a success,
   set true on the first failure, kept true across subsequent
   failures.  Flipping back to false on success is what lets the
   *next* failure warn again.  See the module docstring,
   FAILURE MIRRORING. */
let _warnedSyncFailure = false;

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

async function _doSync() {
  _timer = null;

  const rotation = board.imageHash ? (view.rotation || 0) : 0;
  const payload  = { rotation };
  let changed = false;

  if (board.imageHash
      && board.imageDataUrl
      && board.imageHash !== _lastCanvasHash) {
    payload.canvas_b64 = _stripDataUrl(board.imageDataUrl);
    _lastCanvasHash = board.imageHash;
    changed = true;
  }

  if (board.imageWidth > 0) {
    const idx = selectedMaskIdx();
    if (idx >= 0) {
      const m = board.masks[idx];
      if (m && m.loaded && m.b64 && m.b64 !== _lastSelectionB64) {
        payload.selection_b64 = m.b64;
        _lastSelectionB64 = m.b64;
        changed = true;
      }
    }
  }

  // Context rect: scalar fields only.  No Krita layer is written.
  // The daemon queries `/context` at inpaint time to build the actual
  // mask in the frame it needs.
  const ctxEnabled = !!board.inpaintEnabled && board.imageWidth > 0;
  const cx = Math.round(board.inpaintContext.x || 0);
  const cy = Math.round(board.inpaintContext.y || 0);
  const cw = Math.round(board.inpaintContext.w || 0);
  const ch = Math.round(board.inpaintContext.h || 0);
  payload.context_enabled = ctxEnabled;
  payload.context_x = cx;
  payload.context_y = cy;
  payload.context_w = cw;
  payload.context_h = ch;

  const ctxHash = JSON.stringify(
    {e: ctxEnabled, x: cx, y: cy, w: cw, h: ch});
  if (ctxHash !== _lastContextHash) {
    _lastContextHash = ctxHash;
    changed = true;
  }

  if (!changed && rotation === _lastRotation) {
    return;
  }
  _lastRotation = rotation;

  board.syncing = true;
  try {
    const res = await fetch("/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.success) {
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