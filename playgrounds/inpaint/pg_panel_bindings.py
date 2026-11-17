"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground (buttons / UI):
  - Import masks from Krita
  - Refresh @thecanvas
  - Rebind target document  (manual escape hatch for pin drift)
  - Select / delete masks
  - Rotate the playground view (single scrub input)
  - Materialise the context rectangle as a mask
  - ComfyUI / Krita status checks

Programmatic operations exposed via `pgkitRegisterAction` (usable
from `remote_cli`, scripts, other playgrounds over the hub):
  - `action.refreshCanvas`      re-fetch @thecanvas
  - `action.rebindDoc`          re-pin the target document
  - `action.syncNow`            force an immediate state push
                                (retries a failed delta because
                                `pg_sync.js` now advances its
                                change-detection trackers only on
                                success)
  - `action.checkStatus`        ComfyUI preflight
  - `action.kritaCheck`         daemon reachability
  - `action.kritaImportMasks`   import masks from Krita
  - `action.maskFromRect`       materialise context rect as mask
  - `action.selectMask` (row)   select a mask by index
  - `action.deleteMask` (row)   delete a mask by index

The `action.syncNow` primitive is the only way the "force a push"
affordance survives.  The in-panel "Sync now" button was removed
when the tracker-advance-on-success fix landed (see
`pg_sync.py`'s TRACKER ADVANCE ON SUCCESS ONLY): every real
trigger for a push is automatic, and the one case the button
should have rescued — a failed push leaving the client and server
diverged — is now recovered automatically by the next state
change.

Not present: Inpaint button, Cancel button, prompt field,
interceptor routing, pick-image / pick-mask / clear-image buttons,
drop overlay, "Sync now" button.  Generation is triggered by the
docker's Run button; the playground's job is to push state into the
inpaint server's cache (via `/sync`) so that run finds the right
canvas / mask / context rectangle.  The image is pulled exclusively
from the daemon (autosync + manual refresh), never from a local
file.

SYNC TRIGGERS
=============
Every user-facing state mutation below calls `scheduleSync()` after
updating the model.  Mask imports go through the debounced path;
rotation changes bypass the debounce (handled in
`pg_panel_scrubs.js`).  Autosync from @thecanvas does NOT schedule a
push on every poll — only on the first canvas arrival of the
session, so the inpaint server's cache is warm (canvas + selection)
before the first Run click.  See `pg_autosync.js`'s
first-canvas-arrival branch.

BOOT ORDER
==========
`inpaintBootSync` sets the boot sync gate (see `pg_sync.py`'s module
docstring, BOOT GATE) BEFORE it schedules anything else.  Every sync
that fires before hydration settles — including the one autosync
schedules on first-canvas-arrival — is deferred behind that gate, so
the inpaint server receives the hydrated (or definitively white-
fallback) `theselection` on the first sync, never an intermediate
white push.

MANUAL @thecanvas REFRESH
=========================
`_refreshCanvasAction` uses `applyCanvasImage`, which preserves the
mask stack and the context rectangle when the new image's dims
match.  (A full-dims mismatch clears the context rect, since its
coords referred to the old dims, but masks keep their pixel data.)

REBIND DOC
==========
`_rebindDocAction` POSTs to the playground's `/rebind_document`,
which clears the pinned target document and re-captures from the
daemon's activeDocument().  Use case: another playground
(controlnets Push-to-Krita) activated its own doc while inpaint was
open; user manually switched back to their real doc; clicking
`⟲ Rebind doc` re-pins inpaint to the doc they are now looking at.
See `inpaint_server.py`'s module docstring, PINNED TARGET DOCUMENT.
After rebinding, `_refreshCanvasAction` runs so the canvas reflects
the newly-pinned doc without a manual extra click."""

PANEL_BINDINGS_JS = r"""
import { board, applyCanvasImage,
         _scheduleDraw, view, _applyRotation } from "./pg_state.js";
import {
  setMasks, addMaskFromB64,
  selectedMask, selectedMaskIdx,
  replaceMaskSelection,
} from "./pg_masks.js";
import { buildMaskFromContextRectB64 } from "./pg_inpaint.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { scheduleSync, syncNow, setBootSyncGate } from "./pg_sync.js";
import { fetchStatus } from "./pg_comfy_client.js";
import {
  checkKritaStatus, importKritaMasks, fetchActiveState,
  fetchResourceImage,
} from "./pg_krita_client.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ============================================================
   Manual @thecanvas refresh
   ============================================================ */

async function _refreshCanvasAction() {
  flashStatus("refreshing @thecanvas\u2026", "");
  let state;
  try {
    state = await fetchActiveState();
  } catch (e) {
    flashStatus("@thecanvas refresh failed: " + e.message, "bad");
    return;
  }
  if (!state || !state.success) {
    flashStatus(
      "@thecanvas refresh failed: "
        + ((state && state.message) || "no active state"),
      "bad",
    );
    return;
  }
  const cfg = (state.data && state.data.config) || null;
  const res = (cfg && cfg.resources) || {};
  const spec = res["@thecanvas"];
  if (!spec || typeof spec !== "object") {
    flashStatus("@thecanvas not set in daemon config", "warn");
    return;
  }
  const normalized = {
    source: spec.source || "active",
    layer_name: spec.layer_name || null,
  };

  let img;
  try {
    img = await fetchResourceImage(normalized);
  } catch (e) {
    flashStatus("@thecanvas refresh failed: " + e.message, "bad");
    return;
  }
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

  const label = normalized.source === "layer"
    ? normalized.layer_name
    : normalized.source;
  flashStatus("@thecanvas \u2192 " + label, "ok");

  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Rebind target document
   ============================================================ */

async function _rebindDocAction() {
  flashStatus("rebinding target document\u2026", "");
  try {
    const res = await fetch("/rebind_document", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    const data = await res.json();
    if (!data.success) {
      flashStatus(
        "rebind failed: " + (data.message || "unknown"),
        "bad",
      );
      return;
    }
    flashStatus(data.message || "rebound", "ok");
    // The pin changed; refresh the canvas so the new target's
    // composite shows up immediately without a second click.
    await _refreshCanvasAction();
  } catch (e) {
    flashStatus("rebind failed: " + e.message, "bad");
  }
}

/* ============================================================
   Mask from context rectangle
   ============================================================ */

function _maskFromRectAction() {
  if (!board.imageHash) {
    flashStatus("Load an image first", "warn");
    return;
  }
  if (!board.inpaintEnabled) {
    flashStatus("Enable the context rectangle first", "warn");
    return;
  }
  const c = board.inpaintContext;
  if (!c || c.w <= 0 || c.h <= 0) {
    flashStatus("Draw the context rectangle first", "warn");
    return;
  }

  const b64 = buildMaskFromContextRectB64();
  if (!b64) {
    flashStatus("Could not build mask", "bad");
    return;
  }

  const idx = addMaskFromB64(b64);
  board.selectedMaskIndices = [idx];

  flashStatus("created mask " + (idx + 1) + " from context rectangle", "ok");
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  scheduleSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Shared-selection hydration
   ============================================================ */

async function _hydrateSharedSelection() {
  if (board.masks.length > 0) return;

  let state;
  try {
    state = await fetchActiveState();
  } catch (_) {
    return;
  }
  if (!state || !state.success) return;
  const data = state.data || {};
  const cfg = data.config;
  if (!cfg || typeof cfg !== "object") return;
  const resources = cfg.resources || {};
  const spec = resources["@theselection"];
  if (!spec || typeof spec !== "object") return;
  if (spec.source !== "layer") return;
  const layerName = spec.layer_name;
  if (!layerName) return;

  let res;
  try {
    res = await importKritaMasks({ layerNames: [layerName] });
  } catch (_) {
    return;
  }
  if (!res || !res.success) return;
  const masks = res.masks || [];
  if (masks.length === 0) return;

  const addedIndices = [];
  for (const m of masks) {
    if (m && m.mask_b64) addedIndices.push(addMaskFromB64(m.mask_b64));
  }
  if (addedIndices.length === 0) return;

  board.selectedMaskIndices = [addedIndices[0]];

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  flashStatus(
    "mask hydrated from @theselection ("
      + addedIndices.length + " layer(s))",
    ""
  );
}

/* ============================================================
   Status checks
   ============================================================ */

async function _checkStatusAction() {
  const s = await fetchStatus();
  if (!s.reachable) {
    flashStatus("ComfyUI unreachable: " + (s.message || ""), "bad");
    return;
  }
  flashStatus("ComfyUI \u2713", "ok");
}

function _kritaMarkReachable() {
  const el = document.getElementById("kritaStatus");
  if (!el) return;
  el.textContent = "\u2713 reachable";
  el.className = "ok";
  el.title = "Kritomatic daemon at localhost:12346";
}

function _kritaMarkUnreachable(reason) {
  const el = document.getElementById("kritaStatus");
  if (!el) return;
  el.textContent = "\u2717 " + (reason || "not reachable");
  el.className = "bad";
  el.title = "Start Krita with the Kritomatic Daemon plugin enabled";
  try {
    console.error("[inpaint] krita:", reason || "not reachable");
  } catch (_) {}
}

async function _kritaStatusRefresh() {
  const s = await checkKritaStatus();
  if (s.reachable) _kritaMarkReachable();
  else             _kritaMarkUnreachable(s.message || "not reachable");
}

/* ============================================================
   Import masks from Krita
   ============================================================ */

async function _kritaImportMasksAction() {
  flashStatus("importing masks from Krita\u2026", "");
  try {
    const res = await importKritaMasks({ preferSelectionMaskChildren: true });
    if (!res.success) {
      flashStatus("import failed: " + (res.message || "unknown"), "bad");
      if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
        _kritaMarkUnreachable(res.message);
      }
      return;
    }

    const masks = res.masks || [];
    const skipped = res.skipped || [];
    const selectionPath = res.selection_path || "unknown";

    if (masks.length === 0) {
      const reason = skipped.length
        ? " (" + skipped.map(s => s.name + ": " + s.reason).join("; ") + ")"
        : "";
      flashStatus("no masks to import" + reason, "warn");
      _kritaMarkReachable();
      return;
    }

    let dimWarn = "";
    if (board.imageWidth && board.imageHeight) {
      const mism = masks.find(m =>
        m.width !== board.imageWidth || m.height !== board.imageHeight);
      if (mism) {
        dimWarn = " \u2014 mask '" + mism.name + "' is "
                  + mism.width + "\u00d7" + mism.height
                  + ", image is "
                  + board.imageWidth + "\u00d7" + board.imageHeight
                  + " (will scale)";
      }
    }

    const startIdx = board.masks.length;
    for (const m of masks) {
      addMaskFromB64(m.mask_b64);
    }
    board.selectedMaskIndices = masks.map((_, i) => startIdx + i);

    const skippedMsg = skipped.length
      ? " (" + skipped.length + " skipped)"
      : "";
    const multiMsg = (selectionPath === "active_only" && masks.length === 1)
      ? " (active layer only; multi-select unavailable)"
      : "";
    flashStatus(
      "imported " + masks.length + " mask(s)" + skippedMsg + multiMsg + dimWarn,
      dimWarn || multiMsg ? "warn" : "ok"
    );
    _kritaMarkReachable();

    syncMaskList();
    syncPanelFields();
    pgkitNotifyAll();
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("import failed: " + e.message, "bad");
  }
}

/* ============================================================
   Install buttons
   ============================================================ */

export function installPanelBindings() {
  const el = {};
  const ids = [
    "kritaImportMasksBtn",
    "kritaCheckBtn",
    "rebindDocBtn",
    "maskFromRectBtn",
    "refreshCanvasBtn",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.kritaImportMasksBtn) el.kritaImportMasksBtn.addEventListener("click", _kritaImportMasksAction);
  if (el.kritaCheckBtn)  el.kritaCheckBtn.addEventListener("click", _kritaStatusRefresh);
  if (el.rebindDocBtn)   el.rebindDocBtn.addEventListener("click", _rebindDocAction);
  if (el.maskFromRectBtn) el.maskFromRectBtn.addEventListener("click", _maskFromRectAction);
  if (el.refreshCanvasBtn) el.refreshCanvasBtn.addEventListener("click", _refreshCanvasAction);

  const statusEl = document.getElementById("statusLine");
  if (statusEl) {
    statusEl.style.cursor = "pointer";
    statusEl.title = "Click to re-check ComfyUI";
    statusEl.addEventListener("click", _checkStatusAction);
  }
}

/* ============================================================
   Actions
   ============================================================ */

pgkitRegisterAction("action.refreshCanvas", {
  label: "Refresh @thecanvas", scope: "global", run: _refreshCanvasAction,
  meta: { variant: "plain",
          description: "Re-fetch the current @thecanvas from Krita." },
});
pgkitRegisterAction("action.rebindDoc", {
  label: "Rebind doc", scope: "global", run: _rebindDocAction,
  meta: { variant: "plain",
          description: "Re-pin inpaint to the currently active Krita document." },
});
pgkitRegisterAction("action.syncNow", {
  label: "Sync now", scope: "global", run: syncNow,
  meta: { variant: "plain",
          description: "Force an immediate state push to the inpaint "
                     + "server.  Retries any previously-failed delta "
                     + "because the change-detection trackers advance "
                     + "only on success." },
});
pgkitRegisterAction("action.checkStatus", {
  label: "Check connection", scope: "global", run: _checkStatusAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaCheck", {
  label: "Check Krita", scope: "global", run: _kritaStatusRefresh,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaImportMasks", {
  label: "Import masks from Krita", scope: "global",
  run: _kritaImportMasksAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.maskFromRect", {
  label: "Mask from context rectangle", scope: "global",
  run: _maskFromRectAction,
  meta: { variant: "plain" },
});

/* ============================================================
   Masks level + row actions
   ============================================================ */

pgkitRegisterLevel("masks", {
  label: "Masks",
  items: () => board.masks.map((m, i) => ({
    name:  "mask." + i,
    label: "Mask " + (i + 1),
    extra: { size: m.w + "\u00d7" + m.h,
             selected: board.selectedMaskIndices.indexOf(i) >= 0 },
  })),
  selected: () => board.selectedMaskIndices.length > 0
    ? "mask." + board.selectedMaskIndices[0] : null,
  parent: () => null,
});

pgkitRegisterAction("action.selectMask", {
  label: "Select", scope: "row", level: "masks",
  appliesTo: () => true,
  run: (payload) => {
    const n = payload?.target?.name;
    if (typeof n !== "string" || !n.startsWith("mask.")) return;
    const idx = parseInt(n.slice(5), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return;
    replaceMaskSelection(idx);
    syncMaskList();
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.deleteMask", {
  label: "\u00d7", scope: "row", level: "masks",
  appliesTo: () => true,
  run: (payload) => {
    const n = payload?.target?.name;
    if (typeof n !== "string" || !n.startsWith("mask.")) return;
    const idx = parseInt(n.slice(5), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return;
    board.masks.splice(idx, 1);
    const next = [];
    for (const i of board.selectedMaskIndices) {
      if (i < idx) next.push(i);
      else if (i > idx) next.push(i - 1);
    }
    board.selectedMaskIndices = next;
    if (board.hoveredMaskIdx === idx) board.hoveredMaskIdx = -1;
    else if (board.hoveredMaskIdx > idx) board.hoveredMaskIdx -= 1;
    syncMaskList();
    pgkitNotifyAll();
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { variant: "danger" },
});

/* ============================================================
   pgkitHandleSelect
   ============================================================ */

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;
  if (level === "masks") {
    if (!name.startsWith("mask.")) return false;
    const idx = parseInt(name.slice(5), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return false;
    replaceMaskSelection(idx);
    syncMaskList();
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }
  return false;
}

export function inpaintBootSync() {
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  _checkStatusAction();
  _kritaStatusRefresh();

  // Boot sync gate.  Set BEFORE the hydration IIFE and BEFORE any
  // sync scheduling, so autosync's first-canvas-arrival
  // `scheduleSync()` (fired later, on its own timer) is deferred
  // behind hydration.  See `pg_sync.py`'s module docstring,
  // BOOT GATE.
  //
  // `.catch(() => {})` here is a safety net: `_hydrateSharedSelection`
  // already catches its own errors and returns, but a future edit
  // could introduce a throw that would otherwise leave the gate
  // forever-pending and freeze syncs.  `setBootSyncGate` attaches its
  // own `.catch` too, so the double-safety is intentional.
  const hydration = _hydrateSharedSelection().catch(() => {});
  setBootSyncGate(hydration);

  (async () => {
    await hydration;
    if (board.imageHash) syncNow();
  })();
}
"""