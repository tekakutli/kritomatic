"""@thesegment auto-sync.

Ported from `playgrounds/inpaint/pg_autosync.py`.  Same three triggers
(spec change, batch-end fallback, first canvas), same registry presence
check, same failure-warn-once-then-quiet cadence.

Differences from the inpaint version:

  · `PLAYGROUND_ID = "segment"`, `SLOT_ID = "@thesegment"`.
  · No `applyPushedCanvas` / `applyPastedCanvas` — segment has no
    WS canvas-push channels; the poll is the sole source of canvas
    updates.
  · No `_lastPushAt` tracking — nothing to dedupe against.

See the inpaint module's docstring for the full design rationale;
the mechanics are identical."""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_krita_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

const POLL_INTERVAL_MS = 500;

const PLAYGROUND_ID = "segment";
const SLOT_ID       = "@thesegment";

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
  const spec = res["@thesegment"];
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
  flashStatus("refreshing @thesegment\u2026", "");
  const img = await fetchResourceImage(spec);
  if (!img || !img.success) {
    flashStatus(
      "@thesegment refresh failed: "
      + ((img && img.message) || "unknown"),
      "bad",
    );
    return;
  }
  if (!img.data_url) {
    flashStatus("@thesegment refresh: no data_url", "bad");
    return;
  }
  applyCanvasImage(
    img.hash || ("thesegment_" + Date.now()),
    img.data_url,
    img.width,
    img.height,
  );
  const label = spec.source === "layer" ? spec.layer_name : spec.source;
  flashStatus("@thesegment \u2192 " + label, "ok");
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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
            + "to pull @thesegment",
          "warn",
        );
      }
      return;
    }

    const batchEnded = (_lastActiveBatches > 0 && activeBatches === 0);
    _lastActiveBatches = activeBatches;

    const specChanged = (specHash !== _lastSpec);
    if (specChanged) _lastSpec = specHash;

    if (spec === null) return;

    if (specChanged || batchEnded) {
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