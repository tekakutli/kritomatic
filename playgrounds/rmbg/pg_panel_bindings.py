"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground (buttons / UI):
  - Import masks from Krita
  - Refresh @thecanvas
  - Rebind target document
  - Select / delete masks
  - Materialise the context rectangle as a mask
  - ComfyUI / Krita status checks

Programmatic operations exposed via `pgkitRegisterAction`:
  - `action.refreshCanvas`     re-fetch @thecanvas
  - `action.rebindDoc`         re-pin target document
  - `action.syncNow`           force an immediate state push
  - `action.checkStatus`       ComfyUI preflight
  - `action.kritaCheck`        daemon reachability
  - `action.kritaImportMasks`  import masks from Krita
  - `action.maskFromRect`      materialise context rect as mask
  - `action.selectMask`        select a mask by index
  - `action.deleteMask`        delete a mask by index

NO ROTATION SCRUB
=================
No rotation slider, no scrub input.  The context-rectangle
checkbox lives in the panel and is wired here.

BOUNDARY-AWARE MASK IMPORT
==========================
`importKritaMasks` returns FULL-canvas-sized masks from Krita
(the layer's native dims).  We crop each one to the current
boundary before adding it to the stack, so the mask pixel data
matches `board.imageWidth/Height` — which is what
`pg_pixels.js` and `pg_view_image.js` expect.

When the boundary isn't known yet (canvas hasn't loaded), the
import is deferred; the check inside `_kritaImportMasksAction`
flashes a warning telling the user to load an image first.

BOOT GATE + HYDRATION
=====================
Same as inpaint — `inpaintBootSync` sets the boot sync gate
BEFORE scheduling anything else, so autosync's first
`scheduleSync()` waits for hydration.  Mask hydration here
additionally waits for `board.boundary.w > 0` (via a short
poll) so the imported `theselection` layer can be cropped to
the boundary."""

PANEL_BINDINGS_JS = r"""
import { board } from "./pg_state.js";
import {
  addMaskFromB64, replaceMaskSelection,
} from "./pg_masks.js";
import { buildMaskFromContextRectB64, ensureDefaultContext } from "./pg_context.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { scheduleSync, syncNow, setBootSyncGate } from "./pg_sync.js";
import { fetchStatus } from "./pg_comfy_client.js";
import {
  checkKritaStatus, importKritaMasks, fetchActiveState,
} from "./pg_krita_client.js";
import {
  resolveCanvasSpec, fetchAndSetCanvas,
  applyPushedCanvas, applyPastedCanvas,
} from "./pg_autosync.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitRegisterField,
  pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ============================================================
   Helpers
   ============================================================ */

function _cropMaskToBoundaryAsync(fullB64) {
  return new Promise((resolve) => {
    if (!board.boundary.w || !board.boundary.h) {
      resolve(null);
      return;
    }
    const img = new Image();
    img.onload = () => {
      try {
        const c = document.createElement("canvas");
        c.width  = board.boundary.w;
        c.height = board.boundary.h;
        const cx = c.getContext("2d");
        cx.drawImage(
          img,
          board.boundary.x, board.boundary.y,
          board.boundary.w, board.boundary.h,
          0, 0, board.boundary.w, board.boundary.h,
        );
        resolve(c.toDataURL("image/png")
          .replace(/^data:image\/png;base64,/, ""));
      } catch (e) {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = "data:image/png;base64," + fullB64;
  });
}

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
  const spec = resolveCanvasSpec(state.data || null);
  if (!spec) {
    flashStatus(
      "@thecanvas spec not set \u2014 pick a source in the docker",
      "warn",
    );
    return;
  }
  await fetchAndSetCanvas(spec);
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
      flashStatus("rebind failed: " + (data.message || "unknown"), "bad");
      return;
    }
    flashStatus(data.message || "rebound", "ok");
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
  if (!board.contextEnabled) {
    flashStatus("Enable the context rectangle first", "warn");
    return;
  }
  const c = board.context;
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

async function _waitForBoundary(timeoutMs) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (board.boundary.w > 0 && board.boundary.h > 0) return true;
    await new Promise(r => setTimeout(r, 100));
  }
  return false;
}

async function _hydrateSharedSelection() {
  if (board.masks.length > 0) return;
  if (!await _waitForBoundary(3000)) return;

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
    if (!m || !m.mask_b64) continue;
    const cropped = await _cropMaskToBoundaryAsync(m.mask_b64);
    if (cropped) {
      addedIndices.push(addMaskFromB64(cropped));
    }
  }
  if (addedIndices.length === 0) return;

  board.selectedMaskIndices = [addedIndices[0]];

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  flashStatus(
    "mask hydrated from @theselection ("
      + addedIndices.length + " layer(s), cropped to content)",
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
  if (!board.boundary.w || !board.boundary.h) {
    flashStatus("Load an image first (boundary not known yet)", "warn");
    return;
  }

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

    const startIdx = board.masks.length;
    let imported = 0;
    for (const m of masks) {
      const cropped = await _cropMaskToBoundaryAsync(m.mask_b64);
      if (!cropped) continue;
      addMaskFromB64(cropped);
      imported++;
    }

    if (imported === 0) {
      flashStatus("imported 0 masks (all cropped to nothing)", "warn");
      return;
    }

    board.selectedMaskIndices =
      Array.from({ length: imported }, (_, i) => startIdx + i);

    flashStatus(
      "imported " + imported + " mask(s) (cropped to content)",
      "ok"
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
   Field + push-channel registrations
   ============================================================ */

function _registerFields() {
  pgkitRegisterField("rmbg.contextEnabled", {
    get: () => !!board.contextEnabled,
    set: (v) => {
      board.contextEnabled = !!v;
      const el = document.getElementById("contextEnabled");
      if (el && document.activeElement !== el) el.checked = board.contextEnabled;
      if (board.contextEnabled) ensureDefaultContext();
      scheduleSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    },
    meta: { kind: "bool", label: "Context rectangle" },
  });

  pgkitRegisterField("masks.selectedIndex", {
    get: () => board.selectedMaskIndices.length > 0
      ? board.selectedMaskIndices[0] : -1,
    set: (v) => {
      const n = parseInt(v, 10);
      if (!isFinite(n)) return;
      if (n < 0 || n >= board.masks.length) {
        import("./pg_masks.js").then(m => m.clearMaskSelection()).catch(() => {});
      } else {
        import("./pg_masks.js").then(m => m.replaceMaskSelection(n)).catch(() => {});
      }
      scheduleSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    },
    meta: { kind: "number", label: "First selected mask" },
  });

  pgkitRegisterField("status.syncing", {
    get: () => !!board.syncing,
    set: () => {},
    meta: { kind: "bool", label: "Syncing", visible: false },
  });
}

function _registerCanvasPushedField() {
  pgkitRegisterField("canvas.pushed", {
    get: () => board.imageHash ? { hash: board.imageHash } : null,
    set: (v) => applyPushedCanvas(v),
    meta: { kind: "object", visible: false },
  });
  pgkitRegisterField("paste.applied", {
    get: () => null,
    set: (v) => applyPastedCanvas(v),
    meta: { kind: "object", visible: false },
  });
}

/* ============================================================
   Install buttons
   ============================================================ */

export function installPanelBindings() {
  _registerFields();
  _registerCanvasPushedField();

  const el = {};
  const ids = [
    "kritaImportMasksBtn",
    "kritaCheckBtn",
    "rebindDocBtn",
    "maskFromRectBtn",
    "refreshCanvasBtn",
    "contextEnabled",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.kritaImportMasksBtn) el.kritaImportMasksBtn.addEventListener("click", _kritaImportMasksAction);
  if (el.kritaCheckBtn)  el.kritaCheckBtn.addEventListener("click", _kritaStatusRefresh);
  if (el.rebindDocBtn)   el.rebindDocBtn.addEventListener("click", _rebindDocAction);
  if (el.maskFromRectBtn) el.maskFromRectBtn.addEventListener("click", _maskFromRectAction);
  if (el.refreshCanvasBtn) el.refreshCanvasBtn.addEventListener("click", _refreshCanvasAction);

  if (el.contextEnabled) {
    el.contextEnabled.checked = !!board.contextEnabled;
    el.contextEnabled.addEventListener("change", () => {
      board.contextEnabled = !!el.contextEnabled.checked;
      if (board.contextEnabled) ensureDefaultContext();
      pgkitNotifyAll();
      scheduleSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    });
  }

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
          description: "Re-pin rmbg to the currently active Krita document." },
});
pgkitRegisterAction("action.syncNow", {
  label: "Sync now", scope: "global", run: syncNow,
  meta: { variant: "plain",
          description: "Force an immediate state push.  Retries any "
                     + "previously-failed delta because the change-detection "
                     + "trackers advance only on success." },
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

/* ============================================================
   Boot
   ============================================================ */

export function inpaintBootSync() {
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  _checkStatusAction();
  _kritaStatusRefresh();

  const hydration = _hydrateSharedSelection().catch(() => {});
  setBootSyncGate(hydration);

  (async () => {
    await hydration;
    if (board.imageHash) syncNow();
  })();
}
"""