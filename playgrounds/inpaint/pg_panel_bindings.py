"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground:
  - Load image (file, drop; autosync from @thecanvas)
  - Load masks (import from Krita, pick a PNG file)
  - Select / delete masks
  - Rotate the playground view (single scrub input)
  - Materialise the context rectangle as a mask
  - Manual "Sync now" (re-push state)
  - ComfyUI / Krita status checks

Not present: Inpaint button, Cancel button, prompt field, interceptor
routing.  Generation is triggered by the docker's Run button; the
playground's job is to push state into Krita scratch layers so that
run finds the right canvas / mask / context rectangle.

SYNC TRIGGERS
=============
Every user-facing state mutation below calls `scheduleSync()` after
updating the model.  Image loads and mask imports go through the
debounced path; rotation changes bypass the debounce (handled in
`pg_panel_scrubs.js`).  Autosync from @thecanvas does NOT schedule a
push — pushing back what was just pulled would be wasteful.

MANUAL @thecanvas REFRESH
=========================
`_refreshCanvasAction` uses `applyCanvasImage`, which preserves the
mask stack and the context rectangle when the new image's dims
match.  (A full-dims mismatch clears the context rect, since its
coords referred to the old dims, but masks keep their pixel data.)"""

PANEL_BINDINGS_JS = r"""
import { board, setImage, clearImage, applyCanvasImage,
         _scheduleDraw, view, _applyRotation } from "./pg_state.js";
import {
  setMasks, addMaskFromB64,
  selectedMask, selectedMaskIdx,
  replaceMaskSelection,
} from "./pg_masks.js";
import { buildMaskFromContextRectB64 } from "./pg_inpaint.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { scheduleSync, syncNow } from "./pg_sync.js";
import { uploadImageFile, fetchStatus } from "./pg_comfy_client.js";
import {
  checkKritaStatus, importKritaMasks, fetchActiveState,
  fetchResourceImage,
} from "./pg_krita_client.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ============================================================
   File picking / drop
   ============================================================ */

function _showDropOverlay(on) {
  const el = document.getElementById("dropOverlay");
  if (el) el.classList.toggle("visible", on);
}

function _installDropOverlay() {
  if (document.getElementById("dropOverlay")) return;
  const el = document.createElement("div");
  el.id = "dropOverlay";
  el.textContent = "Drop an image";
  document.body.appendChild(el);
}

async function _handleDroppedFile(file) {
  flashStatus("uploading " + file.name + "\u2026", "");
  try {
    const info = await uploadImageFile(file);
    const dataUrl = await _fileToDataUrl(file);
    setImage(info.hash, dataUrl, info.width, info.height);
    flashStatus("image loaded: " + file.name, "ok");
    syncPanelFields();
    syncMaskList();
    pgkitNotifyAll();
    scheduleSync();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  } catch (e) {
    flashStatus("upload failed: " + e.message, "bad");
  }
}

function _fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(new Error("could not read file"));
    reader.readAsDataURL(file);
  });
}

function _pickImageAction() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/*";
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (file) _handleDroppedFile(file);
  });
  input.click();
}

function _pickMaskAction() {
  const input = document.createElement("input");
  input.type = "file";
  input.accept = "image/png,image/*";
  input.addEventListener("change", () => {
    const file = input.files && input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const dataUrl = String(reader.result || "");
      const b64 = dataUrl.replace(/^data:image\/[^;]+;base64,/, "");
      const newIdx = addMaskFromB64(b64);
      board.selectedMaskIndices = [newIdx];
      syncMaskList();
      syncPanelFields();
      pgkitNotifyAll();
      scheduleSync();
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    };
    reader.onerror = () => flashStatus("could not read mask file", "bad");
    reader.readAsDataURL(file);
  });
  input.click();
}

function _clearImageAction() {
  clearImage();
  const rotEl = document.getElementById("viewRotation");
  if (rotEl) rotEl.value = "0";
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  scheduleSync();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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
    "pickImageBtn", "clearImageBtn", "pickMaskBtn",
    "kritaImportMasksBtn",
    "kritaCheckBtn",
    "maskFromRectBtn",
    "refreshCanvasBtn",
    "syncNowBtn",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.pickImageBtn)   el.pickImageBtn.addEventListener("click", _pickImageAction);
  if (el.clearImageBtn)  el.clearImageBtn.addEventListener("click", _clearImageAction);
  if (el.pickMaskBtn)    el.pickMaskBtn.addEventListener("click", _pickMaskAction);
  if (el.kritaImportMasksBtn) el.kritaImportMasksBtn.addEventListener("click", _kritaImportMasksAction);
  if (el.kritaCheckBtn)  el.kritaCheckBtn.addEventListener("click", _kritaStatusRefresh);
  if (el.maskFromRectBtn) el.maskFromRectBtn.addEventListener("click", _maskFromRectAction);
  if (el.refreshCanvasBtn) el.refreshCanvasBtn.addEventListener("click", _refreshCanvasAction);
  if (el.syncNowBtn) el.syncNowBtn.addEventListener("click", syncNow);

  const statusEl = document.getElementById("statusLine");
  if (statusEl) {
    statusEl.style.cursor = "pointer";
    statusEl.title = "Click to re-check ComfyUI";
    statusEl.addEventListener("click", _checkStatusAction);
  }

  _installDropOverlay();
  let _dragDepth = 0;
  window.addEventListener("dragenter", () => {
    _dragDepth += 1;
    if (_dragDepth > 0) _showDropOverlay(true);
  });
  window.addEventListener("dragleave", () => {
    _dragDepth -= 1;
    if (_dragDepth <= 0) _showDropOverlay(false);
  });
  window.addEventListener("dragover", (e) => e.preventDefault());
  window.addEventListener("drop", (e) => {
    e.preventDefault();
    _dragDepth = 0;
    _showDropOverlay(false);
    const file = e.dataTransfer && e.dataTransfer.files
                 && e.dataTransfer.files[0];
    if (file && file.type.startsWith("image/")) _handleDroppedFile(file);
  });
}

/* ============================================================
   Actions
   ============================================================ */

pgkitRegisterAction("action.clearImage", {
  label: "Clear image", scope: "global", run: _clearImageAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.refreshCanvas", {
  label: "Refresh @thecanvas", scope: "global", run: _refreshCanvasAction,
  meta: { variant: "plain",
          description: "Re-fetch the current @thecanvas from Krita." },
});
pgkitRegisterAction("action.syncNow", {
  label: "Sync now", scope: "global", run: syncNow,
  meta: { variant: "plain",
          description: "Re-push the current state into Krita scratch layers." },
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
  (async () => {
    await _hydrateSharedSelection();
    if (board.imageHash) syncNow();
  })();
}
"""