"""@thecanvas auto-sync for multimask.

Polls the daemon's `comfy_status` every 500 ms while the tab is
visible and, whenever the `@thecanvas` resource spec in
`config.resources` changes, re-fetches the corresponding pixels and
swaps in the returned image via `applyCanvasImage` (which preserves
the area list).

The trigger is a spec change, not a composite change.  Generation
does NOT trigger a refresh; only an explicit `@thecanvas`
re-selection does.

Pause on tab-hide: `document.visibilityState` is checked before every
reschedule.  A hidden tab does not poll at all.  On visibility-change
back to `"visible"`, the pending timer is cancelled and an immediate
poll runs.

Boot-time: on the first poll, if `@thecanvas` is non-null and no
image is loaded yet, fetch it.  Otherwise leave the current image
alone."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyEntities } from "./pgkit/remote_client.js";

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
  pgkitNotifyEntities();
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
    if (_timer !== null) {
      clearTimeout(_timer);
      _timer = null;
    }
    if (!_running) _poll();
  });

  _timer = setTimeout(_poll, 200);
}
"""
