PANEL_BINDINGS_JS = r"""
import {
  board, setImage, clearImage, setMasks,
  snapshotRefine, snapshotSegmentAll, pushOp, popOp,
  restoreMasks, replaceMaskAt, selectedMask, selectedMaskIdx,
  clearPointsOnSelectedMask, clearStagingPoints,
  clearSelectedMaskRegion, compositeRefineWithRegion,
  clearMaskSelection, replaceMaskSelection,
} from "./pg_core.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { uploadImageFile, runSegmentAll, runRefine,
         interruptSegmentation, fetchStatus }
  from "./pg_comfy_client.js";
import { checkKritaStatus, applyToKrita, importFromKrita }
  from "./pg_krita_client.js";
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

/* ============================================================
   Segment All
   ============================================================ */

export async function runSegmentAllAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;

  const snapshot = snapshotSegmentAll();
  flashStatus("detecting\u2026", "");

  const payload = await runSegmentAll();
  if (!payload) return;

  pushOp(snapshot);
  setMasks(payload.masks);
  board.stagingPoints = [];

  flashStatus(
    "detected " + payload.masks.length + " object(s) in "
    + payload.timing.toFixed(2) + "s", "ok",
  );
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Refine (region-constrained when a region exists)
   ============================================================ */

async function _unionMasksB64(b64List) {
  if (b64List.length === 0) return null;
  if (b64List.length === 1) return b64List[0];
  const imgs = await Promise.all(b64List.map(b64 => new Promise((res, rej) => {
    const i = new Image();
    i.onload = () => res(i);
    i.onerror = rej;
    i.src = "data:image/png;base64," + b64;
  })));
  const W = imgs[0].width, H = imgs[0].height;
  const c = document.createElement("canvas");
  c.width = W; c.height = H;
  const cx = c.getContext("2d");
  cx.globalCompositeOperation = "lighter";
  for (const im of imgs) cx.drawImage(im, 0, 0, W, H);
  return c.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

export async function runRefineAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;

  const maskIdx = selectedMaskIdx();
  if (maskIdx < 0) {
    flashStatus("Select a single mask to refine", "warn");
    return;
  }
  const mask = board.masks[maskIdx];
  if (!mask || !mask.loaded) {
    flashStatus("Mask not loaded yet", "warn");
    return;
  }

  const snapshot = snapshotRefine(maskIdx);
  const modelLabel = board.refineModel.toUpperCase();
  const regionSuffix = mask.regionCanvas ? " (region)" : "";
  flashStatus(
    "refining mask #" + (maskIdx + 1)
    + " via " + modelLabel + regionSuffix + "\u2026", "");

  const payload = await runRefine(maskIdx);
  if (!payload) return;

  pushOp(snapshot);

  try {
    let combinedB64 = await _unionMasksB64(payload.masks);
    if (!combinedB64) {
      flashStatus("refine returned no mask", "warn");
      popOp();
      return;
    }

    if (mask.regionCanvas) {
      try {
        combinedB64 = await compositeRefineWithRegion(
          snapshot.prevB64, combinedB64, mask.regionCanvas,
        );
      } catch (e) {
        flashStatus(
          "region composite failed: " + e.message
          + " (used full refine)", "warn");
      }
    }

    replaceMaskAt(maskIdx, combinedB64, mask.points);
    const backend = payload.backend || modelLabel.toLowerCase();
    flashStatus(
      "refined mask #" + (maskIdx + 1)
      + " via " + backend
      + (mask.regionCanvas ? " (region)" : "")
      + " in " + payload.timing.toFixed(2) + "s", "ok",
    );
  } catch (e) {
    flashStatus("refine failed: " + e.message, "bad");
    popOp();
  }

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Undo
   ============================================================ */

export async function undoAction() {
  if (board.running) return;
  const op = popOp();
  if (!op) { flashStatus("nothing to undo", "warn"); return; }

  if (op.type === 'refine') {
    replaceMaskAt(op.maskIdx, op.prevB64);
    flashStatus("undid mask #" + (op.maskIdx + 1) + " edit", "ok");
  } else if (op.type === 'segmentAll') {
    restoreMasks(op.prevMasks, op.prevSelection);
    if (op.prevStagingPoints) {
      board.stagingPoints = op.prevStagingPoints.map(p =>
        ({ x: p.x, y: p.y, isPositive: p.isPositive }));
    }
    flashStatus("undid Segment All", "ok");
  }

  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Misc actions
   ============================================================ */

async function _cancelAction() {
  if (!board.running) return;
  await interruptSegmentation();
  flashStatus("cancel requested", "warn");
}

function _clearPointsAction() {
  const changed = selectedMask()
    ? clearPointsOnSelectedMask()
    : clearStagingPoints();
  if (!changed) return;
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearRegionAction() {
  if (!clearSelectedMaskRegion()) return;
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearMasksAction() {
  setMasks([]);
  board.opLog = [];
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearImageAction() {
  clearImage();
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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

async function _checkStatusAction() {
  const s = await fetchStatus();
  if (!s.reachable) {
    flashStatus("ComfyUI unreachable: " + (s.message || ""), "bad");
    return;
  }
  const parts = [
    "ComfyUI \u2713",
    "SAM3 " + (s.has_sam3 ? "\u2713" : "\u2717"),
    "SAM2 " + (s.has_sam2 ? "\u2713" : "\u2717"),
  ];
  flashStatus(parts.join(" \u00b7 "),
              s.has_sam3 && s.has_sam2 ? "ok" : "warn");
}

/* ============================================================
   Export
   ============================================================ */

function _downloadDataUrl(dataUrl, filename) {
  const a = document.createElement("a");
  a.href = dataUrl;
  a.download = filename;
  a.style.display = "none";
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
}

function _sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function _exportAction() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) { flashStatus("no masks selected", "warn"); return; }
  let count = 0;
  for (const i of indices) {
    const m = board.masks[i];
    if (!m || !m.b64) continue;
    _downloadDataUrl(
      "data:image/png;base64," + m.b64,
      "mask_" + String(i + 1).padStart(2, "0") + ".png",
    );
    count += 1;
    await _sleep(120);
  }
  flashStatus("exported " + count + " mask(s)", "ok");
}

async function _exportCombinedAction() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) { flashStatus("no masks selected", "warn"); return; }
  const first = board.masks[indices[0]];
  if (!first || !first.img) { flashStatus("masks not loaded yet", "warn"); return; }
  const W = first.img.width, H = first.img.height;
  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  octx.fillStyle = "black";
  octx.fillRect(0, 0, W, H);
  octx.globalCompositeOperation = "lighter";
  for (const i of indices) {
    const m = board.masks[i];
    if (!m || !m.img) continue;
    octx.drawImage(m.img, 0, 0, W, H);
  }
  _downloadDataUrl(off.toDataURL("image/png"), "masks_combined.png");
  flashStatus("exported combined (" + indices.length + ")", "ok");
}

/* ============================================================
   Export to Krita
   ============================================================ */

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

function _kritaGetTargetLayer() {
  const el = document.getElementById("kritaTargetLayer");
  return el ? (el.value || "").trim() : "";
}

async function _kritaExportSelection() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn");
    return;
  }
  const b64s = indices.map(i => board.masks[i].b64);
  const unionB64 = await _unionMasksB64(b64s);
  if (!unionB64) {
    flashStatus("could not combine masks", "bad");
    return;
  }
  flashStatus("sending selection to Krita\u2026", "");
  const res = await applyToKrita({
    maskB64: unionB64,
    mode: "selection",
    selMode: "replace",
  });
  if (res.success) {
    flashStatus("selection applied to Krita", "ok");
    _kritaMarkReachable();
  } else {
    flashStatus("failed: " + (res.message || "unknown"), "bad");
    if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
      _kritaMarkUnreachable(res.message);
    }
  }
}

async function _kritaExportTransparency() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn");
    return;
  }
  const b64s = indices.map(i => board.masks[i].b64);
  const unionB64 = await _unionMasksB64(b64s);
  if (!unionB64) {
    flashStatus("could not combine masks", "bad");
    return;
  }
  flashStatus("sending transparency mask to Krita\u2026", "");
  const res = await applyToKrita({
    maskB64: unionB64,
    mode: "transparency",
    layerName: _kritaGetTargetLayer(),
  });
  if (res.success) {
    const d = res.data || {};
    const target = d.layer_name ? (" on " + d.layer_name) : "";
    flashStatus("transparency mask applied" + target, "ok");
    _kritaMarkReachable();
  } else {
    flashStatus("failed: " + (res.message || "unknown"), "bad");
    if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
      _kritaMarkUnreachable(res.message);
    }
  }
}

/* ============================================================
   Import from Krita
   ============================================================ */

async function _kritaImportAction() {
  flashStatus("importing from Krita\u2026", "");
  try {
    const res = await importFromKrita({});
    if (!res.success) {
      flashStatus("import failed: " + (res.message || "unknown"), "bad");
      if (/unreachable|connect|refused|timeout/i.test(res.message || "")) {
        _kritaMarkUnreachable(res.message);
      }
      return;
    }
    setImage(res.hash, res.data_url, res.width, res.height);
    flashStatus(
      "imported from Krita: " + (res.filename || "") +
      " (" + res.width + "\u00d7" + res.height + ")", "ok",
    );
    _kritaMarkReachable();
    syncPanelFields();
    syncMaskList();
    pgkitNotifyAll();
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
    "runBtn", "segmentAllBtn", "cancelBtn", "undoBtn",
    "clearPointsBtn", "clearRegionBtn", "pickImageBtn", "clearImageBtn",
    "kritaImportBtn",
    "exportBtn", "exportCombinedBtn",
    "kritaCheckBtn", "kritaSelectionBtn", "kritaTransparencyBtn",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.runBtn)        el.runBtn.addEventListener("click", runRefineAction);
  if (el.segmentAllBtn) el.segmentAllBtn.addEventListener("click", runSegmentAllAction);
  if (el.cancelBtn)     el.cancelBtn.addEventListener("click", _cancelAction);
  if (el.undoBtn)       el.undoBtn.addEventListener("click", undoAction);
  if (el.clearPointsBtn) el.clearPointsBtn.addEventListener("click", _clearPointsAction);
  if (el.clearRegionBtn) el.clearRegionBtn.addEventListener("click", _clearRegionAction);
  if (el.pickImageBtn)  el.pickImageBtn.addEventListener("click", _pickImageAction);
  if (el.clearImageBtn) el.clearImageBtn.addEventListener("click", _clearImageAction);
  if (el.kritaImportBtn) el.kritaImportBtn.addEventListener("click", _kritaImportAction);
  if (el.exportBtn)     el.exportBtn.addEventListener("click", _exportAction);
  if (el.exportCombinedBtn) el.exportCombinedBtn.addEventListener("click", _exportCombinedAction);

  if (el.kritaCheckBtn)        el.kritaCheckBtn.addEventListener("click", _kritaStatusRefresh);
  if (el.kritaSelectionBtn)    el.kritaSelectionBtn.addEventListener("click", _kritaExportSelection);
  if (el.kritaTransparencyBtn) el.kritaTransparencyBtn.addEventListener("click", _kritaExportTransparency);

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

pgkitRegisterAction("action.run", {
  label: "Run", scope: "global", run: runRefineAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.segmentAll", {
  label: "Segment All", scope: "global", run: runSegmentAllAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.undo", {
  label: "Undo", scope: "global", run: undoAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.cancel", {
  label: "Cancel", scope: "global", run: _cancelAction,
  meta: { variant: "danger" },
});
pgkitRegisterAction("action.clearPoints", {
  label: "Clear points", scope: "global", run: _clearPointsAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.clearRegion", {
  label: "Clear region", scope: "global", run: _clearRegionAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.clearMasks", {
  label: "Clear masks", scope: "global", run: _clearMasksAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.clearImage", {
  label: "Clear image", scope: "global", run: _clearImageAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.checkStatus", {
  label: "Check connection", scope: "global", run: _checkStatusAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.exportSelected", {
  label: "Export selected", scope: "global", run: _exportAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.exportCombined", {
  label: "Export combined", scope: "global", run: _exportCombinedAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.kritaCheck", {
  label: "Check Krita", scope: "global", run: _kritaStatusRefresh,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaImport", {
  label: "Import from Krita", scope: "global", run: _kritaImportAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.kritaSelection", {
  label: "Set as selection", scope: "global", run: _kritaExportSelection,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.kritaTransparency", {
  label: "Add transparency mask", scope: "global", run: _kritaExportTransparency,
  meta: { variant: "primary" },
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
             selected: board.selectedMaskIndices.indexOf(i) >= 0,
             points: (m.points || []).length,
             hasRegion: !!m.regionCanvas },
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
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }
  return false;
}

export function samBootSync() {
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  _checkStatusAction();
  _kritaStatusRefresh();
}
"""
