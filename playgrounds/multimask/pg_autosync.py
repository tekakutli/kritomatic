"""@thecanvas auto-sync.

Polls the daemon's `comfy_status` every 500 ms while the tab is
visible and re-fetches the canvas under three triggers:

  · **Spec change** — the user picked a new source or layer in the
    docker's Playgrounds section (or in the Resources fallback path),
    or loaded a different workflow.  Detected by `specHash !==
    _lastSpec`.

  · **Batch-end fallback** — `active_batches` N>0 → 0 transition.
    Skipped when a WS push arrived in the last 3 s (push handled it).

  · **First canvas** — first poll after boot, if `@thecanvas` is
    non-null and no image is loaded yet.

REGISTRY-FIRST SPEC RESOLUTION
==============================
`@thecanvas` is owned by the inpaint playground.  When inpaint is
registered with the daemon (the normal case), the authoritative
selector lives at:

    playgrounds[id="inpaint"].layer_slots[id="@thecanvas"].selector

The old config-only path (`config.resources["@thecanvas"]`) broke
the moment inpaint started registering: the daemon's panel handler
filters `@thecanvas` out of the Resources section (see
`kritomatic_daemon/handlers/panel.py::_claimed_resource_names`),
so any subsequent panel edit pushes `resources: {}` to
`comfy_apply_config`, and the config-only reader sees nothing.  The
symptom was that multimask went silent after the very first panel
edit — while inpaint (whose autosync was already registry-first)
kept working.

Fallbacks in order:

  1. Inpaint's registry selector (normal path, works whether the
     panel's Resources section is showing @thecanvas or not)
  2. `config.resources["@thecanvas"]` (fallback, for a session
     running multimask without inpaint registered — e.g. a manual
     multimask start with the daemon up but inpaint down)

If neither resolves, `_specOf` returns `null` and the poller goes
quiet until a spec appears.

Multimask does NOT register its own `@thecanvas` slot — inpaint is
the sole owner.  Multimask is a read-only consumer of inpaint's
selector, so the two playgrounds can never diverge on what
`@thecanvas` points at.

PUSH CHANNELS
=============
Two WS push channels feed the canvas, mirroring inpaint's Slice
D + E design:

  · `paste.applied` (per paste) — the daemon publishes the pasted
    PNG + position immediately after each successful paste, before
    it pays the `doc.projection()` cost for the authoritative
    composite.  `applyPastedCanvas` composites it onto the current
    canvas for optimistic display (~20-90 ms).  This is what makes
    "inpaint finishes a run → multimask shows the pasted result"
    feel simultaneous without waiting for the next poll tick.

  · `canvas.pushed` (batch end) — the daemon publishes the full
    `doc.projection()` composite.  `applyPushedCanvas` replaces the
    canvas with that authoritative version (~200-500 ms later).

Both handlers call `applyCanvasImage`, which preserves the area
list across canvas swaps — mask pixel caches are invalidated so the
tinted overlays rebuild against the new frame, but the areas
themselves stay.  The mask-transform cache in
`multimask_server.py::_mask_transform_cache` is keyed by
`(hash, frame_signature)`; the frame signature (theta, crop rect)
doesn't change when inpaint pastes, so cached mask transforms stay
valid across canvas pushes.

The 500 ms poll cadence remains as the fallback for batch-end
updates when the WS is disconnected — see `_poll`'s batch-end
branch and `_lastPushAt` (set by both push handlers so the poll
skips its re-fetch when a push already handled the update)."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

const POLL_INTERVAL_MS = 500;

/* Multimask reads inpaint's registration, not its own.  Multimask
   has no LAYER_SLOTS entry with the daemon — inpaint is the sole
   owner of @thecanvas and this module just consults the selector
   inpaint publishes.  Do NOT change PLAYGROUND_ID to "multimask";
   there is no such registration and the lookup would always fail. */
const PLAYGROUND_ID = "inpaint";
const SLOT_ID       = "@thecanvas";

let _lastSpec = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;
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
   Spec resolution — see the module docstring, REGISTRY-FIRST SPEC
   RESOLUTION.
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
  // @thecanvas spec available" — the poller stays quiet until a
  // spec appears.
  const fromPlayground = _specFromPlayground(data);
  if (fromPlayground) return fromPlayground;
  const fromConfig = _specFromConfig(data);
  if (fromConfig) return fromConfig;
  return null;
}

/* Public: shared spec resolution.  pg_panel_bindings.js's manual
   "Refresh @thecanvas" button uses this so it goes through the
   same registry-first path as the poller. */
export function resolveCanvasSpec(data) {
  return _specOf(data);
}

function _hashSpec(spec) {
  return spec === null ? "" : JSON.stringify(spec);
}

/* ============================================================
   Fetch + apply
   ============================================================ */

/* Public: fetch the pixels behind `spec`, swap the canvas in.
   Used by autosync's poll loop and by pg_panel_bindings.js's
   manual "Refresh @thecanvas" button.

   `applyCanvasImage` (in pg_state.js) preserves the area list
   across the swap — mask pixel caches are invalidated so tinted
   overlays rebuild, but the areas themselves stay.  No state sync
   is scheduled: the canvas is not part of the `/state` payload
   (it comes from `/resource_image` per-request), so pushing it to
   the server would be redundant. */
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
  const label = spec.source === "layer" ? spec.layer_name : spec.source;
  flashStatus("@thecanvas \u2192 " + label, "ok");
  pgkitNotifyEntities();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Push-channel canvas install — batch-end (authoritative)
   ============================================================
   Called from the `canvas.pushed` field's registered `set` handler
   in pg_panel_bindings.js.  Applies the daemon's full
   `doc.projection()` composite (the authoritative post-paste view).

   Dedup:
     · No hash → ignore.
     · Hash matches current board.imageHash → ignore (repeat push
       for the same composite).
     · No b64 → ignore (echo case from pgkitNotifyEntities — the
       field's `get()` returns only the hash, not the bytes).

   On apply, `_lastPushAt` is set so the next poll tick's batch-end
   fallback skips its re-fetch.  `applyCanvasImage` preserves the
   areas list. */
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
  flashStatus("@thecanvas \u2192 pushed", "ok");
  pgkitNotifyEntities();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Push-channel optimistic display — per paste
   ============================================================
   Called from the `paste.applied` field's registered `set` handler.
   `b64` is the exact PNG the Krita layer received, at
   `(paste_x, paste_y)`.  Composite it onto the current canvas for
   optimistic display (~50-90 ms), before the authoritative
   `canvas.pushed` arrives (~200-500 ms later).

   Always takes the composite path (never the full-canvas fast
   path inpaint uses).  Multimask's canvas is usually
   `layer:thecanvas`, not the full composite — the pasted content
   lands in a NEW layer, so the pasted PNG is *additive* on top of
   whatever multimask is currently showing.  Replacing the canvas
   wholesale would lose the base layer.

   Same dedup and `_lastPushAt` rules as `applyPushedCanvas`. */
export function applyPastedCanvas(data) {
  if (!data || typeof data !== "object") return;
  const h = data.hash;
  const b64 = data.b64;
  if (!h || !b64) return;
  if (h === board.imageHash) return;

  _lastPushAt = Date.now();

  const pasteX = Math.round(data.paste_x || 0);
  const pasteY = Math.round(data.paste_y || 0);
  const W = board.imageWidth;
  const H = board.imageHeight;
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
    flashStatus("@thecanvas \u2192 paste", "ok");
    pgkitNotifyEntities();
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
    if (!resp || !resp.success) return;

    const spec = _specOf(resp.data || null);
    const specHash = _hashSpec(spec);
    const activeBatches = ((resp.data && resp.data.active_batches) || []).length;

    if (!_bootstrapped) {
      _bootstrapped = true;
      _lastSpec = specHash;
      _lastActiveBatches = activeBatches;
      if (spec !== null && !board.imageHash) {
        await fetchAndSetCanvas(spec);
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
    if (_timer !== null) {
      clearTimeout(_timer);
      _timer = null;
    }
    if (!_running) _poll();
  });

  _timer = setTimeout(_poll, 200);
}
"""