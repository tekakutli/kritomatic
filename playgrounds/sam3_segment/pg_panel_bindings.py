PANEL_BINDINGS_JS = r"""
import {
  board, setImage, clearImage, clearPoints, setMasks, setSelectedMask,
} from "./pg_core.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { uploadImageFile, runSegment, interruptSegmentation, fetchStatus }
  from "./pg_comfy_client.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll, pgkitNotifyLocal,
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
   Install buttons
   ============================================================ */

export function installPanelBindings() {
  const runBtn      = document.getElementById("runBtn");
  const cancelBtn   = document.getElementById("cancelBtn");
  const clearPts    = document.getElementById("clearPointsBtn");
  const pickBtn     = document.getElementById("pickImageBtn");
  const clearImgBtn = document.getElementById("clearImageBtn");

  if (runBtn)      runBtn.addEventListener("click", _runAction);
  if (cancelBtn)   cancelBtn.addEventListener("click", _cancelAction);
  if (clearPts)    clearPts.addEventListener("click", _clearPointsAction);
  if (pickBtn)     pickBtn.addEventListener("click", _pickImageAction);
  if (clearImgBtn) clearImgBtn.addEventListener("click", _clearImageAction);

  // Status line is also the "re-check ComfyUI" button. flashStatus
  // overwrites its text, but the listener lives on the element, so a
  // click anywhere on the visible text re-runs the check.
  const statusEl = document.getElementById("statusLine");
  if (statusEl) {
    statusEl.style.cursor = "pointer";
    statusEl.title = "Click to re-check ComfyUI";
    statusEl.addEventListener("click", _checkStatusAction);
  }

  // drop overlay handlers
  _installDropOverlay();
  let _dragDepth = 0;
  window.addEventListener("dragenter", (e) => {
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
   Register actions
   ============================================================ */

pgkitRegisterAction("action.run", {
  label: "Run", scope: "global", run: _runAction,
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

/* ============================================================
   Register masks level
   ============================================================ */

pgkitRegisterLevel("masks", {
  label: "Masks",
  items: () => board.masks.map((m, i) => ({
    name:  "mask." + i,
    label: "Mask " + (i + 1),
    extra: { size: m.w + "\u00d7" + m.h },
  })),
  selected: () => board.selectedMaskIdx >= 0
    ? "mask." + board.selectedMaskIdx : null,
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
    setSelectedMask(idx);
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
    if (board.selectedMaskIdx === idx)
      board.selectedMaskIdx = board.masks.length > 0 ? 0 : -1;
    else if (board.selectedMaskIdx > idx)
      board.selectedMaskIdx -= 1;
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
    setSelectedMask(idx);
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
