"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground:
  - Load image (file, drop, import from Krita)
  - Load masks (import from Krita, pick a PNG file)
  - Select / delete masks
  - Inpaint
  - Cancel
  - ComfyUI / Krita status checks

Not present (moved to sam3_segment or deferred to v2): Segment All,
Refine, mask prompt, region painting, points, undo/redo, export to
Krita."""

PANEL_BINDINGS_JS = r"""
import { board, setImage, clearImage, _scheduleDraw } from "./pg_state.js";
import {
  setMasks, addMaskFromB64,
  selectedMask, selectedMaskIdx,
  replaceMaskSelection,
} from "./pg_masks.js";
import { buildContextMaskB64, resizeMaskB64ToCanvas } from "./pg_inpaint.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { uploadImageFile, fetchStatus, runInpaint, interruptInpaint }
  from "./pg_comfy_client.js";
import {
  checkKritaStatus, importFromKrita, importKritaMasks,
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
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    };
    reader.onerror = () => flashStatus("could not read mask file", "bad");
    reader.readAsDataURL(file);
  });
  input.click();
}

function _clearImageAction() {
  clearImage();
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Inpaint
   ============================================================ */

async function _inpaintAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;

  const idx = selectedMaskIdx();
  if (idx < 0) { flashStatus("Select a mask to inpaint", "warn"); return; }
  if (!board.inpaintPrompt.trim()) {
    flashStatus("Enter an inpaint prompt", "warn"); return;
  }

  const mask = board.masks[idx];
  if (!mask || !mask.loaded) {
    flashStatus("Mask not loaded", "warn"); return;
  }

  // The three pixel inputs. Canvas and selection masks are the same
  // for every run; the context mask is either the rectangle (if
  // inpainting is enabled) or the full canvas.
  const canvasB64    = board.imageDataUrl.replace(/^data:image\/png;base64,/, "");
  const selectionB64 = resizeMaskB64ToCanvas(mask.b64, mask.img);
  const contextB64   = buildContextMaskB64();

  flashStatus("inpainting\u2026", "");

  const payload = await runInpaint({
    canvasB64,
    selectionB64,
    contextB64,
    prompt: board.inpaintPrompt,
  });
  if (!payload) return;

  // Replace the canvas with the result. Mask stack, view transform,
  // and inpaint context rect are preserved so the user can iterate
  // (adjust mask, adjust rect, run again).
  board.imageDataUrl = "data:image/png;base64," + payload.image_b64;
  const img = new Image();
  img.onload = () => { board.imageEl = img; _scheduleDraw(); };
  img.src = board.imageDataUrl;

  flashStatus("inpainted in " + payload.timing.toFixed(2) + "s", "ok");
  syncPanelFields();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

async function _cancelAction() {
  if (!board.running) return;
  await interruptInpaint();
  flashStatus("cancel requested", "warn");
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

async function _kritaImportMasksAction() {
  flashStatus("importing masks from Krita\u2026", "");
  try {
    const res = await importKritaMasks({});
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
    "inpaintBtn", "cancelBtn",
    "pickImageBtn", "clearImageBtn", "pickMaskBtn",
    "kritaImportBtn", "kritaImportMasksBtn",
    "kritaCheckBtn",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.inpaintBtn)   el.inpaintBtn.addEventListener("click", _inpaintAction);
  if (el.cancelBtn)    el.cancelBtn.addEventListener("click", _cancelAction);
  if (el.pickImageBtn) el.pickImageBtn.addEventListener("click", _pickImageAction);
  if (el.clearImageBtn) el.clearImageBtn.addEventListener("click", _clearImageAction);
  if (el.pickMaskBtn)  el.pickMaskBtn.addEventListener("click", _pickMaskAction);
  if (el.kritaImportBtn) el.kritaImportBtn.addEventListener("click", _kritaImportAction);
  if (el.kritaImportMasksBtn) el.kritaImportMasksBtn.addEventListener("click", _kritaImportMasksAction);
  if (el.kritaCheckBtn) el.kritaCheckBtn.addEventListener("click", _kritaStatusRefresh);

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

pgkitRegisterAction("action.inpaint", {
  label: "Inpaint", scope: "global", run: _inpaintAction,
  meta: { variant: "primary",
          description: "Run the inpaint workflow with the current " +
                       "canvas, selected mask, and context rectangle." },
});
pgkitRegisterAction("action.cancel", {
  label: "Cancel", scope: "global", run: _cancelAction,
  meta: { variant: "danger" },
});
pgkitRegisterAction("action.clearImage", {
  label: "Clear image", scope: "global", run: _clearImageAction,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.checkStatus", {
  label: "Check connection", scope: "global", run: _checkStatusAction,
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
pgkitRegisterAction("action.kritaImportMasks", {
  label: "Import masks from Krita", scope: "global",
  run: _kritaImportMasksAction,
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

export function inpaintBootSync() {
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  _checkStatusAction();
  _kritaStatusRefresh();
}
"""
