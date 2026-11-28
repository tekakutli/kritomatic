"""@thecanvas auto-sync.

Same poller structure as inpaint's, minus rotation-related
concerns.  Polls the daemon's `comfy_status` every 500 ms while
the tab is visible and re-fetches the canvas under three
triggers:

  · Spec change   — the user picked a new source/layer in the
                    docker's Playgrounds section.
  · Batch-end     — `active_batches.length` transitions from
                    >0 to 0.  Fallback for the WS push channel.
  · First canvas  — first poll after boot, if `@thecanvas` is
                    non-null and no image is loaded yet.

REGISTRY PRESENCE CHECK
=======================
Same defence-in-depth as inpaint: if the daemon's sweep
removes our registry entry (or the slot goes missing), POST
`/playground_reregister` (at most once per 2s).  The daemon's
own sweep was patched to not misread a 4xx/5xx as "dead", but
the check survives because it costs nothing.

PUSH CHANNELS
=============
`canvas.pushed` (batch-end authoritative) and `paste.applied`
(per-paste optimistic) are wired up by `pg_panel_bindings.js`'s
field registration.  Both handlers live here as
`applyPushedCanvas` / `applyPastedCanvas`.  Same fast-path /
composite-path split as inpaint — see that playground's
`pg_autosync.js` docstring for the rationale.

The batch-end fallback re-fetch skips when a push arrived in
the last 3 s (otherwise the poll and the push race for the
same composite)."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_krita_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";
import { scheduleSync } from "./pg_sync.js";

const POLL_INTERVAL_MS = 500;
const PLAYGROUND_ID = "rmbg";
const SLOT_ID       = "@thecanvas";
const REREGISTER_COOLDOWN_MS = 2000;

let _lastSpec = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;
let _warnedNoWorkflow = false;
let _warnedBackendDown = false;
let _warnedNotRegistered = false;
let _lastReregisterAt = 0;
let _lastActiveBatches = -1;
let _lastPushAt = 0;

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
  const fromPlayground = _specFromPlayground(data);
  if (fromPlayground) return fromPlayground;
  const fromConfig = _specFromConfig(data);
  if (fromConfig) return fromConfig;
  return null;
}

export function resolveCanvasSpec(data) {
  return _specOf(data);
}

function _hashSpec(spec) {
  return spec === null ? "" : JSON.stringify(spec);
}

function _findRegistryEntry(data) {
  const playgrounds = (data && data.playgrounds) || [];
  return playgrounds.find(p => p && p.id === PLAYGROUND_ID) || null;
}

async function _reregisterIfMissing() {
  const now = Date.now();
  if (now - _lastReregisterAt < REREGISTER_COOLDOWN_MS) return;
  _lastReregisterAt = now;
  try {
    await fetch("/playground_reregister", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch (_) {}
}

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
  scheduleSync();
  const label = spec.source === "layer" ? spec.layer_name : spec.source;
  flashStatus("@thecanvas \u2192 " + label, "ok");
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

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
    data.width || board.fullImageWidth,
    data.height || board.fullImageHeight,
  );
  scheduleSync();
  flashStatus("@thecanvas \u2192 pushed", "ok");
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

export function applyPastedCanvas(data) {
  if (!data || typeof data !== "object") return;
  const h = data.hash;
  const b64 = data.b64;
  if (!h || !b64) return;
  if (h === board.imageHash) return;

  _lastPushAt = Date.now();

  const pasteX = Math.round(data.paste_x || 0);
  const pasteY = Math.round(data.paste_y || 0);
  const W = board.fullImageWidth;
  const H = board.fullImageHeight;
  const pW = Math.round(data.width || 0);
  const pH = Math.round(data.height || 0);

  if (W > 0 && H > 0
      && pasteX === 0 && pasteY === 0
      && pW === W && pH === H) {
    applyCanvasImage(h, "data:image/png;base64," + b64, W, H);
    scheduleSync();
    flashStatus("@thecanvas \u2192 paste", "ok");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return;
  }

  if (!board.fullImageEl || !W || !H) return;

  const img = new Image();
  img.onload = () => {
    const c = document.createElement("canvas");
    c.width = W;
    c.height = H;
    const cx = c.getContext("2d");
    cx.drawImage(board.fullImageEl, 0, 0, W, H);
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

async function _poll() {
  _timer = null;
  if (_running) return;
  _running = true;
  try {
    const resp = await fetchActiveState();

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

    const entry = _findRegistryEntry(resp.data || null);
    const slotPresent = !!entry
      && (entry.layer_slots || []).some(s => s && s.id === SLOT_ID);
    if (!entry || !slotPresent) {
      if (!_warnedNotRegistered) {
        _warnedNotRegistered = true;
        flashStatus(
          entry
            ? `slot ${SLOT_ID} missing \u2014 re-registering`
            : "not registered with daemon \u2014 re-registering",
          "warn",
        );
      }
      await _reregisterIfMissing();
      return;
    }
    _warnedNotRegistered = false;

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
        _warnedNoWorkflow = true;
        flashStatus(
          "autosync: no workflow loaded \u2014 load one in the docker "
            + "to pull @thecanvas",
          "warn",
        );
      }
      return;
    }

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