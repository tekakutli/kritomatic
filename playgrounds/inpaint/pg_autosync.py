"""@thecanvas auto-sync.

Polls the daemon's `comfy_status` every 500 ms while the tab is
visible and, whenever the `@thecanvas` resource spec in
`config.resources` changes — the user picked a new source or layer in
the docker's Resources section, or in the web panel at
comfy2krita_web.py — re-fetches the corresponding pixels and swaps in
the returned image via `applyCanvasImage`.

The trigger is a spec change (`active` → `layer X`, `layer X` →
`layer Y`, etc.), not a composite change.  Generation does NOT trigger
a refresh; only an explicit `@thecanvas` re-selection does.

Pause on tab-hide: `document.visibilityState` is checked before every
reschedule.  A hidden tab does not poll at all.  On visibility-change
back to `"visible"`, the pending timer is cancelled and an immediate
poll runs, so re-focusing the tab updates instantly rather than
waiting for the next tick.

Boot-time behavior: on the first poll, if `@thecanvas` is non-null and
the playground has no image loaded yet, fetch and display it.  If the
user already picked or imported an image, leave it alone — the poller
only overwrites on subsequent spec changes.

Absent `@thecanvas` (legacy workflows without native-v2 slots) is a
no-op: the poller sees `null`, matches its own `null`, and stops."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_krita_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

/* Cadence while visible.  The daemon's `comfy_status` is cheap on
   localhost (~1 ms per call; the proxy health probe is the slowest
   bit at ~1 ms) so 500 ms is a small constant cost.  Below ~200 ms
   we would start visibly competing with the daemon's own request
   throughput for no user-visible benefit — human perception of
   "instant" bottoms out around 100 ms. */
const POLL_INTERVAL_MS = 500;

let _lastSpec = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;

function _specOf(cfg) {
  const res = (cfg && cfg.resources) || {};
  const spec = res["@thecanvas"];
  if (!spec || typeof spec !== "object") return null;
  return {
    source: spec.source || "active",
    layer_name: spec.layer_name || null,
  };
}

function _hashSpec(spec) {
  return spec === null ? "" : JSON.stringify(spec);
}

async function _fetchAndSet(spec) {
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
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _poll() {
  _timer = null;
  if (_running) return;
  _running = true;
  try {
    const resp = await fetchActiveState();
    if (!resp || !resp.success) return;
    const cfg = (resp.data && resp.data.config) || null;
    const spec = _specOf(cfg);
    const specHash = _hashSpec(spec);

    if (!_bootstrapped) {
      _bootstrapped = true;
      _lastSpec = specHash;
      if (spec !== null && !board.imageHash) {
        await _fetchAndSet(spec);
      }
      return;
    }

    if (specHash === _lastSpec) return;
    _lastSpec = specHash;
    if (spec === null) return;
    await _fetchAndSet(spec);
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
