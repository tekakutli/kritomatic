PANEL_BINDINGS_JS = r"""
import {
  board, setImage, clearImage, clearPoints, setMasks,
  clearMaskSelection, replaceMaskSelection,
} from "./pg_core.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { uploadImageFile, runSegment, interruptSegmentation, fetchStatus }
  from "./pg_comfy_client.js";
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
   Actions
   ============================================================ */

async function _runAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;
  flashStatus("running\u2026", "");
  const payload = await runSegment();
  if (!payload) return;
  setMasks(payload.masks);
  flashStatus(
    "done: " + payload.masks.length + " mask(s) in "
    + payload.timing.toFixed(2) + "s", "ok",
  );
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _segmentAllAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;
  flashStatus("running (all)\u2026", "");
  const payload = await runSegment({ skipPoints: true });
  if (!payload) return;
  setMasks(payload.masks);
  flashStatus(
    "done: " + payload.masks.length + " mask(s) in "
    + payload.timing.toFixed(2) + "s", "ok",
  );
  syncMaskList();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _cancelAction() {
  if (!board.running) return;
  await interruptSegmentation();
  flashStatus("cancel requested", "warn");
}

function _clearPointsAction() {
  clearPoints();
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _clearMasksAction() {
  setMasks([]);
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
  const parts = [];
  parts.push("ComfyUI \u2713");
  parts.push("SAM3 " + (s.has_sam3 ? "\u2713" : "\u2717"));
  parts.push("tooling " + (s.has_tooling ? "\u2713" : "\u2717"));
  flashStatus(parts.join(" \u00b7 "),
              s.has_sam3 && s.has_tooling ? "ok" : "warn");
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

function _sleep(ms) {
  return new Promise(r => setTimeout(r, ms));
}

async function _exportAction() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn"); return;
  }
  let count = 0;
  for (const i of indices) {
    const m = board.masks[i];
    if (!m || !m.b64) continue;
    _downloadDataUrl(
      "data:image/png;base64," + m.b64,
      "mask_" + String(i + 1).padStart(2, "0") + ".png",
    );
    count += 1;
    // Browsers throttle rapid sequential downloads; 120ms spacing
    // lets the "multiple downloads" prompt handle them cleanly.
    await _sleep(120);
  }
  flashStatus("exported " + count + " mask(s)", "ok");
}

async function _exportCombinedAction() {
  const indices = board.selectedMaskIndices.slice();
  if (indices.length === 0) {
    flashStatus("no masks selected", "warn"); return;
  }
  const first = board.masks[indices[0]];
  if (!first || !first.img) {
    flashStatus("masks not loaded yet", "warn"); return;
  }
  const W = first.img.width, H = first.img.height;

  const off = document.createElement("canvas");
  off.width = W; off.height = H;
  const octx = off.getContext("2d");
  octx.fillStyle = "black";
  octx.fillRect(0, 0, W, H);
  // Additive blending produces the union: black+black=black,
  // white+anything=white (clamped).
  octx.globalCompositeOperation = "lighter";
  for (const i of indices) {
    const m = board.masks[i];
    if (!m || !m.img) continue;
    octx.drawImage(m.img, 0, 0, W, H);
  }
  const dataUrl = off.toDataURL("image/png");
  _downloadDataUrl(dataUrl, "masks_combined.png");
  flashStatus("exported combined mask (" + indices.length + ")", "ok");
}

/* ============================================================
   Install buttons
   ============================================================ */

export function installPanelBindings() {
  const runBtn      = document.getElementById("runBtn");
  const segAllBtn   = document.getElementById("segmentAllBtn");
  const cancelBtn   = document.getElementById("cancelBtn");
  const clearPts    = document.getElementById("clearPointsBtn");
  const pickBtn     = document.getElementById("pickImageBtn");
  const clearImgBtn = document.getElementById("clearImageBtn");
  const exportBtn   = document.getElementById("exportBtn");
  const exportCombBtn = document.getElementById("exportCombinedBtn");

  if (runBtn)         runBtn.addEventListener("click", _runAction);
  if (segAllBtn)      segAllBtn.addEventListener("click", _segmentAllAction);
  if (cancelBtn)      cancelBtn.addEventListener("click", _cancelAction);
  if (clearPts)       clearPts.addEventListener("click", _clearPointsAction);
  if (pickBtn)        pickBtn.addEventListener("click", _pickImageAction);
  if (clearImgBtn)    clearImgBtn.addEventListener("click", _clearImageAction);
  if (exportBtn)      exportBtn.addEventListener("click", _exportAction);
  if (exportCombBtn)  exportCombBtn.addEventListener("click", _exportCombinedAction);

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
    if (file && file.type.startsWith("image/")) {
      _handleDroppedFile(file);
    }
  });
}

/* ============================================================
   Actions
   ============================================================ */

pgkitRegisterAction("action.run", {
  label: "Run", scope: "global", run: _runAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.segmentAll", {
  label: "Segment All", scope: "global", run: _segmentAllAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.cancel", {
  label: "Cancel", scope: "global", run: _cancelAction,
  meta: { variant: "danger" },
});
pgkitRegisterAction("action.clearPoints", {
  label: "Clear points", scope: "global", run: _clearPointsAction,
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
    if (typeof n !== "string") return;
    const prefix = "mask.";
    if (!n.startsWith(prefix)) return;
    const idx = parseInt(n.slice(prefix.length), 10);
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
    if (typeof n !== "string") return;
    const prefix = "mask.";
    if (!n.startsWith(prefix)) return;
    const idx = parseInt(n.slice(prefix.length), 10);
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
    const prefix = "mask.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < board.masks.length)) return false;
    replaceMaskSelection(idx);
    syncMaskList();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }
  return false;
}

/* ============================================================
   Boot hook
   ============================================================ */

export function samBootSync() {
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  _checkStatusAction();
}
"""
