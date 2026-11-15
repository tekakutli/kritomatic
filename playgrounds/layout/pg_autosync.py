"""Layer-slot autosync.

Polls the playground server's `/active_state` every 500 ms while the
tab is visible.  Finds our playground entry (`id == "layout"`), then
our slot within it (`id == "@thelayout"`), and re-fetches the
selected layer via `/resource_image` whenever the slot's selector
changes.

The selector is edited by the docker's "Playground Input" section;
the playground only reads it.  Bootstrap: on the first poll, if no
image is loaded yet, fetch the current selector's image so the
canvas starts populated.

The exported `fetchSelectedLayer(selector)` is reused by the manual
"Refresh @thelayout" button in `pg_panel.js`.

Self-heal on missing entry: if the playground's entry (or its slot)
is absent from `comfy_status.playgrounds`, the browser calls
`/playground_reregister` (single-shot, non-blocking).  The next poll
(500 ms later) sees the re-registered entry and resumes.

Daemon-down: when `/active_state` reports `daemon_reachable: false`,
the poller flashes a warning after a few consecutive failures.  The
playground *process* exits on its own after ~10s of daemon-down
(layout_playground.py's `_watch_daemon_and_exit`); the browser cannot
survive the server's death regardless.
"""

AUTOSYNC_JS = r"""
import { board, setImage } from "./pg_state.js";
import { fetchActiveState, fetchResourceImage } from "./pg_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

const POLL_INTERVAL_MS      = 500;
const PLAYGROUND_ID         = "layout";
const SLOT_ID               = "@thelayout";
const DAEMON_DOWN_WARN      = 3;
const REREGISTER_ON_MISSING = true;

let _lastSelectorKey = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;
let _daemonDownStreak = 0;
let _lastReregisterAt = 0;

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
  setImage(
    img.hash || ("layer_" + Date.now()),
    img.data_url, img.width, img.height,
  );
  const label = spec.source === "layer"
    ? spec.layer_name
    : spec.source;
  flashStatus("layer \u2192 " + label, "ok");
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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
      if (!board.imageHash) {
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