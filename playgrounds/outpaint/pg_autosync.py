"""Layer-slot autosync — pull the selected @thecanvas layer into
the area as an image, and push generated outpaint results into the
client-side stack.

Two independent update channels share this poll loop:

  · **Source image** — reads the `@thecanvas` slot's selector from
    `/active_state` and, on change, re-fetches via `/resource_image`.
    Keeps the viewer showing whatever Krita layer the user picked.

  · **Generated image** — reads `last_pasted` from `/active_state`
    and, on a new timestamp, pushes the entry onto
    `board.generatedStack`.  It is NOT auto-displayed — the user
    clicks a thumbnail in the panel's "Generated" section to preview
    it.  See pg_state.js's SOURCE vs PREVIEW vs GENERATED STACK.

Viewer state (drag, resize, ratio) is preserved across source
refreshes.  The generated stack is entirely client-local; it
survives source refreshes but is lost on page reload (the daemon
doesn't republish pastes).
"""

AUTOSYNC_JS = r"""
import { board, applyCanvasImage, pushGeneratedToStack } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

const POLL_INTERVAL_MS      = 500;
const PLAYGROUND_ID         = "outpaint";
const SLOT_ID               = "@thecanvas";
const DAEMON_DOWN_WARN      = 3;
const REREGISTER_ON_MISSING = true;

let _lastSelectorKey = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;
let _daemonDownStreak = 0;
let _lastReregisterAt = 0;

// Track the highest paste timestamp we've seen so a repeated poll
// for the same event does not re-add it to the stack.  Uses
// timestamp rather than hash: two genuinely-distinct outpaints of
// the same source could produce the same output bytes (deterministic
// seed), and their hashes would collide.  Timestamps never do.
//
// The first observation after bootstrap records the high-water mark
// without pushing — that swallows a stale paste that pre-dates this
// playground's startup.
let _lastPasteTs = -1;
let _lastPasteBootstrapDone = false;

function _selectorKey(sel) {
  if (!sel || typeof sel !== "object") return "";
  return (sel.source || "active") + ":" + (sel.layer_name || "");
}

export async function fetchSelectedLayer(selector) {
  flashStatus("refreshing layer\u2026", "");
  const spec = {
    source:     selector.source || "active",
    layer_name: selector.layer_name || null,
  };
  const img = await fetchResourceImage(spec);
  if (!img || !img.success || !img.data_url) {
    flashStatus(
      "layer refresh failed: " + ((img && img.message) || "unknown"),
      "bad",
    );
    return;
  }
  applyCanvasImage(
    img.hash || ("layer_" + Date.now()),
    img.data_url, img.width, img.height,
  );
  const label = spec.source === "layer"
    ? spec.layer_name
    : spec.source;
  flashStatus("layer \u2192 " + label, "ok");
}

async function _reregisterIfMissing() {
  if (!REREGISTER_ON_MISSING) return;
  const now = Date.now();
  if (now - _lastReregisterAt < 2000) return;
  _lastReregisterAt = now;
  try {
    await fetch("/playground_reregister", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
  } catch (_) {}
}

function _maybeStackPaste(lastPasted) {
  if (!_lastPasteBootstrapDone) {
    // First observation: record the high-water mark without pushing.
    // Any paste older than this is from before this playground
    // started and should not pollute the stack.  See module
    // docstring.
    _lastPasteBootstrapDone = true;
    if (lastPasted && typeof lastPasted.ts === "number") {
      _lastPasteTs = lastPasted.ts;
    }
    return;
  }
  if (!lastPasted || !lastPasted.b64) return;
  const ts = Number(lastPasted.ts) || 0;
  if (ts <= _lastPasteTs) return;
  _lastPasteTs = ts;

  pushGeneratedToStack({
    id:      "gen_" + ts,
    hash:    lastPasted.hash || "",
    b64:     lastPasted.b64,
    dataUrl: "data:image/png;base64," + lastPasted.b64,
    width:   Number(lastPasted.width)  || 0,
    height:  Number(lastPasted.height) || 0,
    ts:      ts,
  });
  flashStatus(
    "outpaint result stored \u2014 "
    + (lastPasted.width || "?") + "\u00d7"
    + (lastPasted.height || "?")
    + " (click thumbnail to preview)",
    "ok",
  );
}

async function _poll() {
  _timer = null;
  if (_running) return;
  _running = true;
  try {
    const resp = await fetchActiveState();
    if (!resp || resp.daemon_reachable === false) {
      _daemonDownStreak += 1;
      if (_daemonDownStreak === DAEMON_DOWN_WARN) {
        flashStatus(
          "daemon unreachable \u2014 playground will exit",
          "bad",
        );
      }
      return;
    }
    _daemonDownStreak = 0;

    // ---- Generated-image channel ----
    // Runs before the slot logic so a fresh outpaint result is
    // stacked even on the same poll as a source update.
    _maybeStackPaste(resp.last_pasted);

    // ---- Source-image channel (unchanged) ----
    const playgrounds = (resp.data && resp.data.playgrounds) || [];
    const entry = playgrounds.find(p => p.id === PLAYGROUND_ID);
    if (!entry) {
      flashStatus("not registered with daemon \u2014 re-registering", "warn");
      await _reregisterIfMissing();
      return;
    }
    const slots = entry.layer_slots || [];
    const slot = slots.find(s => s.id === SLOT_ID);
    if (!slot) {
      flashStatus(
        "slot " + SLOT_ID + " missing from registered entry",
        "warn",
      );
      await _reregisterIfMissing();
      return;
    }
    const selector = slot.selector || { source: "active", layer_name: null };
    const key = _selectorKey(selector);
    if (!_bootstrapped) {
      _bootstrapped = true;
      _lastSelectorKey = key;
      if (!board.sourceImageEl) {
        await fetchSelectedLayer(selector);
      }
      return;
    }
    if (key === _lastSelectorKey) return;
    _lastSelectorKey = key;
    await fetchSelectedLayer(selector);
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
    if (_timer !== null) { clearTimeout(_timer); _timer = null; }
    if (!_running) _poll();
  });
  _timer = setTimeout(_poll, 200);
}
"""