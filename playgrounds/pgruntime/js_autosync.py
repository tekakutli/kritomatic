AUTOSYNC_JS = r"""
/* ==========================================================================
   makeAutosync — layer-slot autosync.

   Polls /comfy/active_state on the playground server; when the
   registered `@slot`'s selector changes, fetches /resource_image
   and applies the result.  Detects daemon death (consecutive
   failures) and self-heals the playground's registry entry via
   /playground_reregister.

   Opts:
     playgroundId            — "magi", "layout", ...
     slotId                  — "@thecharacter", "@thelayout", ...
     fetchActiveState        — async () => {success, data}
     fetchResourceImage      — async (spec) => {success, data_url, hash, width, height}
     applyCanvasImage        — (hash, dataUrl, width, height) => void
     onImage(img)            — optional post-apply hook
     onSpecChanged(spec)     — optional, called before fetch on spec change
     onBatchEnded(spec)      — optional, batch-end trigger (only if enableBatchEnd)
     onDaemonDown()          — optional, fires once per failure streak
     onNotRegistered()       — optional, fires on registry-miss
     onWarn(msg)             — optional, warn-once flash
     enableBatchEnd          — default false
     enableReregister        — default true
     reregisterUrl           — default "/playground_reregister"
     pollIntervalMs          — default 500
     reregisterCooldownMs    — default 2000

   Returns { start, stop, fetchSelectedLayer, resolveSpec }.
   ========================================================================== */

export function makeAutosync(opts) {
  const {
    playgroundId,
    slotId,
    fetchActiveState,
    fetchResourceImage,
    applyCanvasImage,
    onImage = null,
    onSpecChanged = null,
    onBatchEnded = null,
    onDaemonDown = null,
    onNotRegistered = null,
    onWarn = null,
    enableBatchEnd = false,
    enableReregister = true,
    reregisterUrl = "/playground_reregister",
    pollIntervalMs = 500,
    reregisterCooldownMs = 2000,
  } = opts;

  let _lastSpec = null;
  let _bootstrapped = false;
  let _running = false;
  let _timer = null;
  let _daemonDownStreak = 0;
  let _lastReregisterAt = 0;
  let _lastActiveBatches = -1;
  let _warnedBackendDown = false;
  let _warnedNotRegistered = false;

  function _selectorKey(sel) {
    if (!sel || typeof sel !== "object") return "";
    return (sel.source || "active") + ":" + (sel.layer_name || "");
  }

  function _specFromPlayground(data) {
    const playgrounds = (data && data.playgrounds) || [];
    const entry = playgrounds.find(p => p && p.id === playgroundId);
    if (!entry) return null;
    const slots = entry.layer_slots || [];
    const slot = slots.find(s => s && s.id === slotId);
    if (!slot) return null;
    const sel = slot.selector;
    if (!sel || typeof sel !== "object") return null;
    return {
      source: sel.source || "active",
      layer_name: sel.layer_name || null,
    };
  }

  function _specFromConfig(data) {
    const cfg = (data && data.config) || null;
    const res = (cfg && cfg.resources) || {};
    const sel = res[slotId];
    if (!sel || typeof sel !== "object") return null;
    return {
      source: sel.source || "active",
      layer_name: sel.layer_name || null,
    };
  }

  function resolveSpec(data) {
    return _specFromPlayground(data) || _specFromConfig(data);
  }

  async function fetchSelectedLayer(selector) {
    const spec = {
      source: selector.source || "active",
      layer_name: selector.layer_name || null,
    };
    const img = await fetchResourceImage(spec);
    if (!img || !img.success || !img.data_url) {
      if (onWarn) onWarn(
        "layer refresh failed: " + ((img && img.message) || "unknown")
      );
      return;
    }
    applyCanvasImage(
      img.hash || ("layer_" + Date.now()),
      img.data_url, img.width, img.height,
    );
    if (onImage) onImage(img);
  }

  async function _reregisterIfMissing() {
    if (!enableReregister) return;
    const now = Date.now();
    if (now - _lastReregisterAt < reregisterCooldownMs) return;
    _lastReregisterAt = now;
    try {
      await fetch(reregisterUrl, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
    } catch (_) {}
  }

  function _findRegistryEntry(data) {
    const playgrounds = (data && data.playgrounds) || [];
    return playgrounds.find(p => p && p.id === playgroundId) || null;
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
          if (onDaemonDown) onDaemonDown();
          if (onWarn) onWarn(
            "autosync: cannot reach daemon state ("
              + ((resp && resp.message) || "no response") + ")"
          );
        }
        return;
      }
      _warnedBackendDown = false;

      const data = resp.data || null;

      const entry = _findRegistryEntry(data);
      const slotPresent = !!entry
        && (entry.layer_slots || []).some(s => s && s.id === slotId);
      if (!entry || !slotPresent) {
        if (!_warnedNotRegistered) {
          _warnedNotRegistered = true;
          if (onNotRegistered) onNotRegistered(entry);
          if (onWarn) onWarn(
            entry
              ? `slot ${slotId} missing — re-registering`
              : "not registered with daemon — re-registering"
          );
        }
        await _reregisterIfMissing();
        return;
      }
      _warnedNotRegistered = false;

      const spec = resolveSpec(data);
      const specKey = spec === null ? "" : JSON.stringify(spec);
      const activeBatches = (data.active_batches || []).length;

      if (!_bootstrapped) {
        _bootstrapped = true;
        _lastSpec = specKey;
        _lastActiveBatches = activeBatches;
        if (spec !== null) {
          await fetchSelectedLayer(spec);
        }
        return;
      }

      const specChanged = (specKey !== _lastSpec);
      if (specChanged) {
        _lastSpec = specKey;
        if (onSpecChanged) onSpecChanged(spec);
      }

      const batchEnded = enableBatchEnd
        && (_lastActiveBatches > 0 && activeBatches === 0);
      _lastActiveBatches = activeBatches;

      if (spec === null) return;

      if (specChanged) {
        await fetchSelectedLayer(spec);
      } else if (batchEnded) {
        if (onBatchEnded) onBatchEnded(spec);
        await fetchSelectedLayer(spec);
      }
    } finally {
      _running = false;
      _schedule();
    }
  }

  function _schedule() {
    if (document.visibilityState !== "visible") return;
    if (_timer !== null) clearTimeout(_timer);
    _timer = setTimeout(_poll, pollIntervalMs);
  }

  function start() {
    if (_timer !== null) return;
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState !== "visible") return;
      if (_timer !== null) { clearTimeout(_timer); _timer = null; }
      if (!_running) _poll();
    });
    _timer = setTimeout(_poll, 200);
  }

  function stop() {
    if (_timer !== null) { clearTimeout(_timer); _timer = null; }
  }

  return { start, stop, fetchSelectedLayer, resolveSpec };
}
"""