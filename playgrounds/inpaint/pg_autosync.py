"""@thecanvas auto-sync.

Polls the daemon's `comfy_status` every 500 ms while the tab is
visible and, whenever the `@thecanvas` resource spec changes — the
user picked a new source or layer in the docker's Playgrounds
section, in the Resources section (fallback path), or loaded a
different workflow — re-fetches the corresponding pixels and swaps in
the returned image via `applyCanvasImage`.

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
  3. `{source: "active"}` — fetch the composite of the active
     document.

The trigger is a spec change (`active` → `layer X`, `layer X` →
`layer Y`, etc.), not a composite change.  Generation does NOT trigger
a refresh; only an explicit `@thecanvas` re-selection does.  (The
inpaint playground no longer generates; the docker does.  This poller
is purely about the *displayed* canvas, which tracks `@thecanvas`.)

Pause on tab-hide: `document.visibilityState` is checked before every
reschedule.  A hidden tab does not poll at all.  On visibility-change
back to `"visible"`, the pending timer is cancelled and an immediate
poll runs, so re-focusing the tab updates instantly rather than
waiting for the next tick.

Boot-time behavior: on the first poll, if `@thecanvas` is non-null and
the playground has no image loaded yet, fetch and display it, then
schedule one sync so the inpaint server's cache is populated before
the first Run click.  That sync does NOT write to Krita scratch
layers — post-Slice-B, the inpaint server is a pure in-memory cache
and the daemon reads from it via `/canvas_b64` / `/selection_b64` at
prepare time.  If the user already picked or imported an image, leave
it alone.

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
   "instant" bottoms out around 100 ms. */
const POLL_INTERVAL_MS = 500;

const PLAYGROUND_ID = "inpaint";
const SLOT_ID       = "@thecanvas";

let _lastSpec = null;
let _bootstrapped = false;
let _running = false;
let _timer = null;
let _warnedNoWorkflow = false;
let _warnedBackendDown = false;

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
  // Registry first, config fallback, then implicit {source:"active"}.
  // Returning null means "no @thecanvas slot exists" — the poller
  // warns once and stays quiet.
  const fromPlayground = _specFromPlayground(data);
  if (fromPlayground) return fromPlayground;
  const fromConfig = _specFromConfig(data);
  if (fromConfig) return fromConfig;
  // The registry entry exists but no selector was set and no config
  // fell through — treat as "not configured yet", not "use active".
  // (A registered playground with a `@thecanvas` slot and no spec is
  // a transient pre-first-pick state; letting it default to
  // `active` would silently overwrite whatever the user was last
  // viewing.)
  const playgrounds = (data && data.playgrounds) || [];
  const registered = playgrounds.some(p => p && p.id === PLAYGROUND_ID);
  if (registered) return null;
  // Not registered and no config: no @thecanvas exists at all.
  return null;
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
  const wasEmpty = !board.imageHash;
  applyCanvasImage(
    img.hash || ("thecanvas_" + Date.now()),
    img.data_url,
    img.width,
    img.height,
  );
  if (wasEmpty) {
    // First canvas arrival this session.  Schedule one sync so the
    // inpaint server's in-memory cache is populated before the
    // first Run click.  The sync no longer writes to Krita scratch
    // layers — the server just caches the canvas + selection, and
    // the daemon reads them back via `/canvas_b64` and
    // `/selection_b64` at prepare time.
    scheduleSync();
  }
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

    if (!_bootstrapped) {
      _bootstrapped = true;
      _lastSpec = specHash;

      if (spec !== null && !board.imageHash) {
        await _fetchAndSet(spec);
      } else if (spec === null && !board.imageHash && !_warnedNoWorkflow) {
        // No workflow loaded (or the workflow does not declare
        // @thecanvas).  Without one, `@thecanvas` has no layer to
        // resolve to, so the canvas stays empty until the docker (or
        // CLI) loads a workflow.  Flash once; the next poll after a
        // workflow load will fire _fetchAndSet normally.
        _warnedNoWorkflow = true;
        flashStatus(
          "autosync: no workflow loaded \u2014 load one in the docker "
            + "to pull @thecanvas",
          "warn",
        );
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