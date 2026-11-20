"""@thecanvas auto-sync.

Polls the daemon's `comfy_status` every 500 ms while the tab is
visible and re-fetches the canvas under three triggers:

  · **Spec change** — the user picked a new source or layer in the
    docker's Playgrounds section (or in the Resources fallback path),
    or loaded a different workflow.  Detected by `specHash !==
    _lastSpec`.

  · **Batch end fallback** — `active_batches.length` transitions from
    `>0` to `0`.  This is a *fallback* for the canvas-push path (see
    below): if the WS push channel is disconnected, the poll still
    picks up the post-generation canvas.  Skipped if a push arrived
    in the last 3 s (the push already handled it), so the two paths
    don't race.

  · **First canvas** — the very first poll after boot, if
    `@thecanvas` is non-null and the playground has no image loaded
    yet.

PUSH CHANNELS (Slices D + E)
============================
Two WS push channels feed the canvas:

  · `paste.applied` (Slice E, per paste, fastest) — the daemon
    publishes the pasted PNG + position immediately after each
    successful paste, before it pays the `doc.projection()` cost
    for the authoritative composite.  `applyPastedCanvas` composites
    it over the pre-paste canvas.  For the common inpaint case
    (checkbox off, no rotation) the pasted PNG is the full new
    canvas, so the code takes a fast-path that just replaces
    `board.imageEl` — ~20 ms (PNG decode only, no compositing).

  · `canvas.pushed` (Slice D, once at batch end, authoritative) —
    the daemon publishes the full `doc.projection()` composite.
    `applyPushedCanvas` replaces the optimistic display with ground
    truth.  For inpaint this is visually identical to the optimistic
    version (same pixels, different PNG encoding), so no flicker.

For the common inpaint case the user sees: Krita shows the paste,
then ~20-90 ms later the playground does (via paste.applied), then
~200 ms later the authoritative canvas.pushed arrives and silently
replaces it.  The perceived-latency path never touches
`doc.projection()`.

The 500 ms poll cadence remains as the fallback for batch-end
updates when the WS is disconnected — see `_poll`'s batch-end
branch and `_lastPushAt` (set by both push handlers so the poll
skips its re-fetch when a push already handled the update).

SPEC SOURCE
===========
When the playground is registered with the daemon (the normal case),
`@thecanvas`'s spec comes from the registry entry the daemon writes
on `playground_set_layer`:

    playgrounds[id="inpaint"].layer_slots[id="@thecanvas"].selector

The docker's Playgrounds section is the only UI that writes this,
which is why the Resources section no longer shows `@thecanvas` when
inpaint is registered — the two would show duplicate sources of truth.

Fallbacks, in order:

  1. Playground registry selector (above) — normal path.
  2. `config.resources["@thecanvas"]` — used when the registry entry
     exists but the selector has not been set yet (first boot before
     the user touches the Playgrounds row), or when the playground
     is not registered at all (old workflow loaded with a manual
     inpaint start).

No further default is applied.  A registered playground with a
`@thecanvas` slot and no explicit selector is a transient
pre-first-pick state; the code returns `null` for that case so the
poller stays quiet until the user picks a source.

The `resolveCanvasSpec(data)` helper below is the shared entry point
for this resolution.  It's exported so `pg_panel_bindings.js`'s
manual "Refresh @thecanvas" button goes through the exact same
registry-first / config-fallback path as the poller.  Before this
helper was exported, the manual button read the config only and
warned "@thecanvas not set in daemon config" whenever the workflow
relied on the playground registry slot.

Pause on tab-hide: `document.visibilityState` is checked before every
reschedule.  A hidden tab does not poll at all.  On visibility-change
back to `"visible"`, the pending timer is cancelled and an immediate
poll runs, so re-focusing the tab updates instantly rather than
waiting for the next tick.

Absent `@thecanvas` (no workflow loaded yet, or a workflow whose
config does not declare `@thecanvas`) is a no-op: the poller sees
`null`, matches its own `null`, and stops.  To make this less silent,
the first poll flashes a "load a workflow" warning when the spec is
null and no image is loaded; subsequent polls stay quiet until the
state changes, so the warning is a one-shot nudge, not a per-tick
spam.

Failure handling: the poller calls `fetchActiveState()`, which can
fail (`/comfy/active_state` unreachable, daemon down).  A single
failure is a transient hiccup; a repeated failure across consecutive
polls means the daemon is down or the backend routes are broken.
`_warnedBackendDown` tracks the transition: the first failure flashes
a warning, subsequent failures stay silent, and a success clears the
flag so the next failure flashes again.  This gives the operator one
line in the terminal when something is wrong, rather than either zero
lines (silent) or one line per 500 ms (useless)."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_krita_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { scheduleSync } from "./pg_sync.js";

/* Cadence while visible.  The daemon's `comfy_status` is cheap on
   localhost (~1 ms per call; the proxy health probe is the slowest
   bit at ~1 ms) so 500 ms is a small constant cost.  Below ~200 ms
   we would start visibly competing with the daemon's own request
   throughput for no user-visible benefit — human perception of
   "instant" bottoms out around 100 ms.

   Post-Slice-D, the poll is no longer the primary path for
   post-generation canvas updates — the WS push channels handle
   those.  The poll remains as: (a) the fast-path detector for spec
   changes and (b) the fallback for batch-end canvas updates when
   the WS is disconnected. */
const POLL_INTERVAL_MS = 500;

const PLAYGROUND_ID = "inpaint";
const SLOT_ID       = "@thecanvas";

let _lastSpec = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;
let _warnedNoWorkflow = false;
let _warnedBackendDown = false;
/* Tracks active-batches count from the last poll.  Used to detect
   a batch-end transition (N>0 → 0) as the fallback path for
   post-generation canvas updates.  -1 means "not yet polled". */
let _lastActiveBatches = -1;
/* Timestamp of the most recent WS canvas push (either paste.applied
   or canvas.pushed).  Used to skip the batch-end fallback re-fetch
   on the poll tick immediately after a push, so push and poll don't
   race for the same update. */
let _lastPushAt = 0;

/* ============================================================
   Spec resolution — see the module docstring, SPEC SOURCE
   ============================================================ */

function _selectorToSpec(sel) {
  if (!sel || typeof sel !== "object") return null;
  return {
    source: sel.source || "active",
    layer_name: sel.layer_name || null,
  };
}

function _specFromPlayground(data) {
  const playgrounds = (data && data.playgrounds) || [];
  const entry = playgrounds.find(p => p && p.id === PLAYGROUND_ID);
  if (!entry) return null;
  const slots = entry.layer_slots || [];
  const slot = slots.find(s => s && s.id === SLOT_ID);
  if (!slot) return null;
  return _selectorToSpec(slot.selector);
}

function _specFromConfig(data) {
  const cfg = (data && data.config) || null;
  const res = (cfg && cfg.resources) || {};
  const spec = res["@thecanvas"];
  return _selectorToSpec(spec);
}

function _specOf(data) {
  // Registry first, config fallback.  Returning null means "no
  // @thecanvas spec available" — the poller warns once and stays
  // quiet until a spec appears.
  const fromPlayground = _specFromPlayground(data);
  if (fromPlayground) return fromPlayground;
  const fromConfig = _specFromConfig(data);
  if (fromConfig) return fromConfig;
  return null;
}

/* Public: shared spec resolution.  pg_panel_bindings.js's manual
   "Refresh @thecanvas" button calls this so it goes through the
   same registry-first / config-fallback path as the poller.  Before
   this export existed, the manual button read the config only and
   warned "@thecanvas not set in daemon config" whenever the
   workflow relied on the playground registry slot. */
export function resolveCanvasSpec(data) {
  return _specOf(data);
}

function _hashSpec(spec) {
  return spec === null ? "" : JSON.stringify(spec);
}

/* ============================================================
   Fetch + apply
   ============================================================ */

/* Public: fetch the pixels behind `spec`, swap the canvas in, and
   schedule a state sync so the inpaint server's cache reflects the
   new canvas.  Used by autosync's poll loop and by
   pg_panel_bindings.js's manual "Refresh @thecanvas" button.

   `scheduleSync` is called unconditionally — the debounced `_doSync`
   compares `board.imageHash` against `_lastCanvasHash` and skips if
   unchanged, so the extra call is cheap when nothing actually
   changed.  The previous version gated `scheduleSync` on "first
   canvas arrival this session" (`wasEmpty`), which left the server
   cache stale after a spec-change or batch-end re-fetch — the
   workflow would run on the previous canvas.  See the module
   docstring, "Post-generation refresh" and `pg_sync.py`'s
   TRACKER ADVANCE ON SUCCESS ONLY. */
export async function fetchAndSetCanvas(spec) {
  flashStatus("refreshing @thecanvas\u2026", "");
  const img = await fetchResourceImage(spec);
  if (!img || !img.success) {
    flashStatus(
      "@thecanvas refresh failed: "
      + ((img && img.message) || "unknown"),
      "bad",
    );
    return;
  }
  if (!img.data_url) {
    flashStatus("@thecanvas refresh: no data_url", "bad");
    return;
  }
  applyCanvasImage(
    img.hash || ("thecanvas_" + Date.now()),
    img.data_url,
    img.width,
    img.height,
  );
  // Always schedule a sync: the debounced _doSync compares the
  // current board.imageHash against _lastCanvasHash and skips if
  // unchanged, so calling this unconditionally is safe and cheap.
  // Needed so a spec-change or batch-end re-fetch reaches the
  // inpaint server's cache — otherwise the workflow would run on
  // the previous canvas.
  scheduleSync();
  const label = spec.source === "layer" ? spec.layer_name : spec.source;
  flashStatus("@thecanvas \u2192 " + label, "ok");
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Push-channel canvas install (Slice D)
   ============================================================
   Called from the `canvas.pushed` field's registered `set` handler
   in `pg_panel_bindings.js`, which is invoked by
   `pgkitRemoteApplyPatch` when a `patch` message arrives over the
   `/remote` WebSocket.  See the module docstring, PUSH CHANNELS.

   `data` is the daemon's published payload:
       {hash, b64, width, height, doc_name}

   Dedup rules:
     · No hash → ignore (malformed).
     · Hash matches current board.imageHash → ignore (repeat push
       for the same composite).
     · No b64 → ignore (this is an echo of the field's current
       value from `pgkitNotifyAll`; the echo carries only the
       hash, not the bytes).

   On apply:
     · Set `_lastPushAt` so the next poll tick skips its batch-end
       fallback re-fetch (the push already handled it).
     · `applyCanvasImage` swaps the canvas and clears mask pixel
       caches against the new frame.
     · `scheduleSync()` pushes the new `canvas_b64` into the
       inpaint server's cache so the next Run uses the new pixels. */
export function applyPushedCanvas(data) {
  if (!data || typeof data !== "object") return;
  const h = data.hash;
  if (!h) return;
  if (h === board.imageHash) return;
  if (!data.b64) return;
  _lastPushAt = Date.now();
  applyCanvasImage(
    h,
    "data:image/png;base64," + data.b64,
    data.width || board.imageWidth,
    data.height || board.imageHeight,
  );
  scheduleSync();
  flashStatus("@thecanvas \u2192 pushed", "ok");
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Paste-channel optimistic display (Slice E)
   ============================================================
   Called from the `paste.applied` field's registered `set` handler
   in `pg_panel_bindings.js`, invoked by `pgkitRemoteApplyPatch`
   when a `patch` message arrives over the `/remote` WebSocket.

   `data` is the daemon's published payload:
       {hash, b64, paste_x, paste_y, width, height, doc_name}

   `b64` is the exact PNG the Krita layer received.  Two display
   paths:

     · **Fast path — full-canvas overwrite.**  If paste_x=0,
       paste_y=0, and the pasted PNG is exactly W×H (the common
       inpaint case with the context checkbox off), the pasted PNG
       IS the new canvas.  Just call `applyCanvasImage` with it
       directly.  ~20 ms (browser PNG decode only).

     · **Composite path — partial paste.**  Draw the pre-paste
       canvas, draw the pasted PNG at (paste_x, paste_y), encode
       back to a data URL, apply.  ~50-90 ms for typical canvases
       (canvas composite + PNG re-encode + decode).

   Dedup:
     · No hash → ignore.
     · Hash matches current board.imageHash → ignore (repeat push
       for the same paste).
     · No b64 → ignore (echo case from pgkitNotifyAll — the field's
       `get()` returns only the hash, not the bytes).

   The authoritative `canvas.pushed` (Slice D) still arrives ~200 ms
   later and replaces the optimistic composite with ground truth.
   For the common case they're visually identical (same pixels,
   different PNG encoding).  For edge cases (blend modes,
   user-painted content mid-batch) the authoritative version
   corrects any drift this local composite missed.

   `_lastPushAt` is set on success so the poll's batch-end fallback
   (which would otherwise re-fetch the same composite) skips its
   work — see `_poll`'s `pushedRecently` check. */
export function applyPastedCanvas(data) {
  if (!data || typeof data !== "object") return;
  const h = data.hash;
  const b64 = data.b64;
  if (!h || !b64) return;
  if (h === board.imageHash) return;

  // Set before async work so a poll that fires mid-composite sees
  // `pushedRecently = true` and skips its batch-end re-fetch.
  _lastPushAt = Date.now();

  const pasteX = Math.round(data.paste_x || 0);
  const pasteY = Math.round(data.paste_y || 0);
  const W = board.imageWidth;
  const H = board.imageHeight;
  const pW = Math.round(data.width || 0);
  const pH = Math.round(data.height || 0);

  // Fast path: full-canvas overwrite (common inpaint case with
  // context checkbox off).  The pasted PNG is the entire new
  // canvas; skip composite entirely.
  if (W > 0 && H > 0
      && pasteX === 0 && pasteY === 0
      && pW === W && pH === H) {
    applyCanvasImage(h, "data:image/png;base64," + b64, W, H);
    scheduleSync();
    flashStatus("@thecanvas \u2192 paste", "ok");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return;
  }

  // Composite path: no base canvas to composite onto.
  if (!board.imageEl || !W || !H) return;

  const img = new Image();
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const cx = c.getContext("2d");
    cx.drawImage(board.imageEl, 0, 0, W, H);
    cx.drawImage(img, pasteX, pasteY);
    const dataUrl = c.toDataURL("image/png");
    applyCanvasImage(h, dataUrl, W, H);
    scheduleSync();
    flashStatus("@thecanvas \u2192 paste", "ok");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  };
  img.onerror = () => {};
  img.src = "data:image/png;base64," + b64;
}

/* ============================================================
   Poll loop
   ============================================================ */

async function _poll() {
  _timer = null;
  if (_running) return;
  _running = true;
  try {
    const resp = await fetchActiveState();

    // ---- backend reachability ----
    //
    // `fetchActiveState` returns `{success: false, message: ...}` when
    // `/comfy/active_state` is missing, unreachable, or the daemon
    // behind it failed.  A single failure is transient; a persistent
    // one means the daemon is down or the server route is broken.  See
    // the module docstring, "Failure handling".
    if (!resp || !resp.success) {
      if (!_warnedBackendDown) {
        _warnedBackendDown = true;
        flashStatus(
          "autosync: cannot reach daemon state ("
            + ((resp && resp.message) || "no response") + ")",
          "warn",
        );
      }
      return;
    }
    _warnedBackendDown = false;

    const spec = _specOf(resp.data || null);
    const specHash = _hashSpec(spec);
    const activeBatches = ((resp.data && resp.data.active_batches) || []).length;

    if (!_bootstrapped) {
      _bootstrapped = true;
      _lastSpec = specHash;
      _lastActiveBatches = activeBatches;

      if (spec !== null && !board.imageHash) {
        await fetchAndSetCanvas(spec);
      } else if (spec === null && !board.imageHash && !_warnedNoWorkflow) {
        // No workflow loaded (or the workflow does not declare
        // @thecanvas).  Without one, `@thecanvas` has no layer to
        // resolve to, so the canvas stays empty until the docker (or
        // CLI) loads a workflow.  Flash once; the next poll after a
        // workflow load will fire fetchAndSetCanvas normally.
        _warnedNoWorkflow = true;
        flashStatus(
          "autosync: no workflow loaded \u2014 load one in the docker "
            + "to pull @thecanvas",
          "warn",
        );
      }
      return;
    }

    // Batch-end fallback: if the WS push channels are disconnected,
    // the poll is still the safety net for post-generation canvas
    // updates.  Skip if a push arrived in the last 3 s — the push
    // already updated the canvas, so re-fetching would just get
    // the same composite again (and cost another projection()).
    const pushedRecently = (Date.now() - _lastPushAt) < 3000;
    const batchEnded = (_lastActiveBatches > 0 && activeBatches === 0);
    _lastActiveBatches = activeBatches;

    const specChanged = (specHash !== _lastSpec);
    if (specChanged) _lastSpec = specHash;

    if (spec === null) return;

    if (specChanged || (batchEnded && !pushedRecently)) {
      await fetchAndSetCanvas(spec);
    }
  } finally {
    _running = false;
    _schedule();
  }
}

function _schedule() {
  if (document.visibilityState !== "visible") return;
  if (_timer !== null) clearTimeout(_timer);
  _timer = setTimeout(_poll, POLL_INTERVAL_MS);
}

export function startAutosync() {
  if (_timer !== null) return;

  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState !== "visible") return;
    // Cancel any pending slow-tick timer and poll immediately.
    // If a poll is already in flight (_running), the in-flight
    // poll's finally-block will reschedule; we do not double-fire.
    if (_timer !== null) {
      clearTimeout(_timer);
      _timer = null;
    }
    if (!_running) _poll();
  });

  // First poll shortly after boot — feels immediate on page load
  // without racing the rest of the boot sequence.
  _timer = setTimeout(_poll, 200);
}
"""