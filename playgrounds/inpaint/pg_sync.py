"""State synchronizer.

Pushes the playground's current state into the inpaint server's
in-memory cache.  Two things travel:

  · pixel blobs — the canvas and the selected mask — pushed as
    `canvas_b64` and `selection_b64` on the `/sync` payload.  The
    server caches them; nothing is written to Krita.  The daemon
    reads them back via `/canvas_b64` and `/selection_b64` at
    `prepare_workflow` time and substitutes them into the graph
    (the "graph injection" path — no pull node needed).

  · scalar state — the view rotation and the context rectangle — as
    fields on the `/sync` payload.  The daemon queries them back via
    `/rotation` and `/crop_frame` on demand.  No Krita layer is
    written for the context rectangle; the daemon defines
    `@thecontext` at prepare time from these numbers, accounting for
    rotation.

WHY NO KRITA SCRATCH LAYERS
===========================
Pre-Slice-B, the canvas and selection were pushed into Krita scratch
layers (`thecanvas`, `theselection`) via `comfy_set_layer_pixels`,
and the daemon read them back from Krita.  That worked but had three
problems:

  · On boot, autosync's first-canvas-arrival scheduled a sync that
    wrote a full-canvas white mask into a fresh `theselection` paint
    layer — visible garbage on the user's layer stack.

  · The round-trip through Krita was pure overhead: the daemon
    already knows the doc + crop bounds; it just needs the pixels.

  · The scratch layers shadow the user's own layers by name, so
    anything called `thecanvas` / `theselection` silently conflicted.

Slice B replaced the transport with two simple HTTP routes on the
inpaint server (`/canvas_b64`, `/selection_b64`).  The daemon
queries them at prepare time and substitutes the blobs into the
graph — the same graph-injection mechanism `controlnets_playground`
uses for its preprocessed images.  No Krita writes; no shadow layers;
no boot-time artifacts.

`theselection` semantics
========================
Every sync that carries a canvas also carries a `selection_b64`.
When a mask is selected, that's the mask's bytes.  When no mask is
selected, that's an **all-white W×H mask** — the "select all" value
the workflow reads: the model inpaints every pixel inside the
context rect.  See `pg_inpaint.js::buildWhiteMaskB64`.

The previous design pushed an all-black fallback, meaning "select
nothing" — the workflow honoured it by inpainting no pixels, which
looked like a successful run that did nothing.  All-white avoids the
silent no-op.

Sync cadence:

  · State changes (image load, mask select/deselect, mask add/delete,
    context checkbox toggle, context rect drag) → `scheduleSync()`,
    debounced to `SYNC_DEBOUNCE_MS`.

  · Rotation changes → `syncNow()`, immediate.

Change detection: each piece of payload is hashed before push and
skipped if unchanged.  This keeps a mask-select from re-pushing the
whole canvas, and keeps a rotation-only change from re-pushing
anything.

TRACKER ADVANCE ON SUCCESS ONLY
===============================
The four change-detection trackers (`_lastCanvasHash`,
`_lastSelectionB64`, `_lastContextHash`, `_lastRotation`) record
what the server has *received*, not what the client has *tried to
send*.  They are advanced only after `/sync` returns
`{success: true}`.  On failure they stay where they were, so the
next `_doSync` compares the current state against the server's
actual last-known state and retries the delta.

This matters because `/sync` can fail: transient fetch errors, a
playground server restart in the middle of an edit, a daemon GC
pause, an oversized payload.  Without this discipline, the tracker
advances on build (before the `await`), a failure leaves the client
believing it has pushed a delta it never sent, and every subsequent
`_doSync` early-returns on "no change" — the client and the server
silently diverge until the page is reloaded.

With the discipline in place, the recovery path is simply "make one
more state change": the next `scheduleSync()` / `syncNow()` call
recomputes the payload against the *old* trackers, sees the
previously-failed delta still present, and retries it.  No manual
"Sync now" button is needed — which is why the button was removed
when this fix landed.  The `syncNow()` JS function remains exported
(the rotation scrub input calls it for immediate rotation sync, and
the `action.syncNow` pgkit action exposes the primitive to
programmatic callers), but the UI no longer surfaces it.

BOOT GATE
=========
Every session has a potential race: the first `scheduleSync` may fire
(from autosync's first-canvas-arrival) before `_hydrateSharedSelection`
finishes reading `@theselection` from Krita.  Without intervention the
sequence would be:

    t=0     canvas arrives → scheduleSync → debounce
    t=250   flush: no mask yet → push WHITE theselection
    t=600   hydration finishes → push HYDRATED theselection

The daemon sees a white push at t=250, and if a Run fires inside the
gap between t=250 and t=600, the workflow runs with an all-white
(select-everything) selection instead of the one the user had picked
in a previous session.  That is the "window" the gate closes.

`inpaintBootSync` calls `setBootSyncGate(hydrationPromise)` BEFORE it
starts autosync.  Every `_doSync` call while the gate is pending
awaits it, so the first flush lands after hydration completes with
the hydrated (or definitively white-fallback) `selection_b64`.  The
gate clears itself via `.finally`; a 3 s hard cap inside `_doSync`
bounds worst-case latency on a dead daemon.

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
import { buildWhiteMaskB64 } from "./pg_inpaint.js";
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

/* Boot sync gate.  Set once by `inpaintBootSync` to the hydration
   promise; cleared automatically when that promise settles.  Every
   `_doSync` call that fires while the gate is pending awaits it,
   so the first flush of the session carries the hydrated
   `theselection` (or a definitive white-fallback) rather than
   racing it.  See the module docstring, BOOT GATE. */
let _bootGate = null;

export function setBootSyncGate(promise) {
  if (!promise || typeof promise.then !== "function") return;
  // `.finally` clears the gate once hydration settles, success or
  // failure.  A rejected promise becomes an attached catch here so
  // the module never produces an unhandled rejection from a
  // hydration failure that the caller does not await.
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

/**
 * Force an immediate sync, bypassing the debounce timer.
 *
 * Callers:
 *   · the rotation scrub input (`pg_panel_scrubs.js`) — rotation
 *     changes bypass the debounce, because a rotate-then-Run
 *     sequence would otherwise race the daemon's `/rotation`
 *     query;
 *   · boot's post-hydration flush in `inpaintBootSync`, once the
 *     boot gate clears and the canvas + hydrated mask are ready;
 *   · the `action.syncNow` pgkit action, for programmatic callers
 *     (remote_cli, scripts).  This is also the manual retry path
 *     for a failed push: since the trackers only advance on
 *     success, calling `syncNow` after a failure re-attempts the
 *     pending delta.  The in-panel "Sync now" button was removed
 *     because the next state change automatically retries — see
 *     the module docstring, TRACKER ADVANCE ON SUCCESS ONLY — so
 *     a dedicated button added no value.
 */
export function syncNow() {
  if (_timer) { clearTimeout(_timer); _timer = null; }
  _doSync();
}

async function _doSync() {
  _timer = null;

  // Boot gate.  See the module docstring, BOOT GATE.  The 3 s cap
  // bounds worst-case latency on a dead daemon: a hung hydration
  // should not freeze the first sync forever.
  if (_bootGate) {
    try {
      await Promise.race([
        _bootGate,
        new Promise(r => setTimeout(r, 3000)),
      ]);
    } catch (_) { /* gate errors are not sync errors */ }
  }

  const rotation = board.imageHash ? (view.rotation || 0) : 0;
  const payload  = { rotation };
  let changed = false;

  // Pending tracker deltas.  These are committed to the module-level
  // _last* trackers ONLY on `data.success` below.  If the fetch
  // fails, they stay uncommitted, and the next _doSync call sees
  // the current state still differs from the server's last-known
  // state — so it retries the delta.  See the module docstring,
  // TRACKER ADVANCE ON SUCCESS ONLY.
  let pendingCanvasHash   = null;
  let pendingSelectionB64 = null;
  let pendingContextHash  = null;
  let pendingRotation     = null;

  if (board.imageHash
      && board.imageDataUrl
      && board.imageHash !== _lastCanvasHash) {
    payload.canvas_b64 = _stripDataUrl(board.imageDataUrl);
    pendingCanvasHash = board.imageHash;
    changed = true;
  }

  if (board.imageWidth > 0) {
    const idx = selectedMaskIdx();
    let selB64 = null;
    if (idx >= 0) {
      const m = board.masks[idx];
      if (m && m.loaded && m.b64) selB64 = m.b64;
    }
    if (!selB64) {
      // No mask selected: push an all-white W×H mask so the
      // `theselection` value reflects "select all" —
      // "inpaint everything inside the context rect" — rather
      // than a leftover from a previous session.  All-black
      // (the previous behaviour) meant "inpaint nothing": the
      // run succeeded but the pasted result was identical to
      // the input, which was hard to diagnose.
      selB64 = buildWhiteMaskB64();
    }
    if (selB64 && selB64 !== _lastSelectionB64) {
      payload.selection_b64 = selB64;
      pendingSelectionB64 = selB64;
      changed = true;
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
    pendingContextHash = ctxHash;
    changed = true;
  }

  if (!changed && rotation === _lastRotation) {
    return;
  }
  pendingRotation = rotation;

  board.syncing = true;
  try {
    const res = await fetch("/sync", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (data.success) {
      // Commit pending tracker deltas only now that the server has
      // actually received the state.  On a failure, these stay
      // uncommitted so the next _doSync call will retry the delta.
      // See the module docstring, TRACKER ADVANCE ON SUCCESS ONLY.
      if (pendingCanvasHash !== null) {
        _lastCanvasHash = pendingCanvasHash;
      }
      if (pendingSelectionB64 !== null) {
        _lastSelectionB64 = pendingSelectionB64;
      }
      if (pendingContextHash !== null) {
        _lastContextHash = pendingContextHash;
      }
      if (pendingRotation !== null) {
        _lastRotation = pendingRotation;
      }

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