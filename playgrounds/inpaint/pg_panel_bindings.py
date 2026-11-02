"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground:
  - Load image (file, drop, import from Krita)
  - Load masks (import from Krita, pick a PNG file)
  - Select / delete masks
  - Rotate the playground view (slider)
  - Inpaint
  - Cancel
  - ComfyUI / Krita status checks

Not present (moved to sam3_segment or deferred to v2): Segment All,
Refine, mask prompt, region painting, points, undo/redo, export to
Krita."""

PANEL_BINDINGS_JS = r"""
import { board, setImage, clearImage, _scheduleDraw,
         view, _applyRotation, _rotateImage } from "./pg_state.js";
import {
  setMasks, addMaskFromB64,
  selectedMask, selectedMaskIdx,
  replaceMaskSelection,
} from "./pg_masks.js";
import {
  buildContextMaskB64, buildRotatedContextMaskB64,
  resizeMaskB64ToCanvas,
} from "./pg_inpaint.js";
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
  // clearImage resets view.rotation to 0; sync the widgets so the
  // user does not see a stale angle on a cleared canvas.
  const rotEl = document.getElementById("viewRotation");
  if (rotEl) rotEl.value = "0";
  const rotNumEl = document.getElementById("viewRotationNum");
  if (rotNumEl) rotNumEl.value = "0";
  syncPanelFields();
  syncMaskList();
  pgkitNotifyAll();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Inpaint payload builders
   ============================================================
   The daemon works in the *rotated* frame — that is the frame the
   user is looking at when they click Inpaint.  Since the board now
   stores the ORIGINAL, un-rotated raster (rotation is display-only),
   the inpaint action must materialise the rotated canvas, rotated
   selection mask, and rotated context mask once per run.

   All three blobs must come out at the SAME pixel dimensions (the
   axis-aligned bounding box of the rotated rectangle) so the
   daemon's crop, mask decode, and output rotation all line up.

   `outputRotation: -view.rotation` tells the daemon how to rotate
   the composite back to the original orientation before it returns
   it — the caller then `_setOriginalFromB64`s that composite and
   the display transform re-rotates it for the user's current view.

   Cropping: when `board.inpaintEnabled === true`, the rotated
   context mask has a white rectangle; the daemon finds its
   axis-aligned bbox and crops canvas + selection to it.  When
   false, the rotated context mask is full white, so the bbox is the
   full rotated canvas and no crop happens.  The context rectangle's
   user-visible size and position are the ONLY thing that determine
   this crop — the rotation slider cannot affect it because
   `board.inpaintContext` lives in original-image coords and is
   untouched by `_applyRotation`. */

function _rotateCanvasWithBlack(canvas0, angle) {
  const W = canvas0.width, H = canvas0.height;
  const rad = (angle % 360) * Math.PI / 180;
  const cos = Math.abs(Math.cos(rad));
  const sin = Math.abs(Math.sin(rad));
  const newW = Math.max(1, Math.round(W * cos + H * sin));
  const newH = Math.max(1, Math.round(W * sin + H * cos));
  const rc = document.createElement("canvas");
  rc.width = newW; rc.height = newH;
  const cx = rc.getContext("2d");
  cx.imageSmoothingEnabled = true;
  cx.imageSmoothingQuality = "high";
  cx.fillStyle = "#000";
  cx.fillRect(0, 0, newW, newH);
  cx.translate(newW / 2, newH / 2);
  cx.rotate(rad);
  cx.drawImage(canvas0, -W / 2, -H / 2);
  return rc;
}

function _canvasB64ForInpaint() {
  const angle = view.rotation;
  if (!angle || angle % 360 === 0) {
    return board.imageDataUrl.replace(/^data:image\/png;base64,/, "");
  }
  const rc = _rotateImage(board.originalImageEl, angle);
  return rc.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

/* Produce the selection mask as an opaque grayscale PNG at the same
   rotated dims as the canvas.  Mask value is `R * A / 255` — same
   convention `_maskToGrayscaleCanvas` used to enforce before this
   file stopped pre-rotating masks.  Steps:

     1. Composite mask.img onto a black canvas sized to the
        ORIGINAL image dims.  This converts RGBA-with-mask-in-R-or-A
        into an unambiguous opaque grayscale raster: black where
        the mask is 0, the mask value in all three channels
        elsewhere.
     2. If `view.rotation` is non-zero, rotate that raster into a
        fresh canvas at the rotated bbox dims, black-filled so the
        rotation's corner regions decode as not-selected. */

function _selectionB64ForInpaint(mask) {
  if (!mask || !mask.img) return mask.b64;
  const targetW = board.imageWidth;
  const targetH = board.imageHeight;

  const base = document.createElement("canvas");
  base.width = targetW;
  base.height = targetH;
  const bx = base.getContext("2d");
  bx.imageSmoothingEnabled = true;
  bx.imageSmoothingQuality = "high";
  bx.fillStyle = "#000";
  bx.fillRect(0, 0, targetW, targetH);
  bx.drawImage(mask.img, 0, 0, targetW, targetH);

  const angle = view.rotation;
  const out = (!angle || angle % 360 === 0)
    ? base
    : _rotateCanvasWithBlack(base, angle);

  return out.toDataURL("image/png").replace(/^data:image\/png;base64,/, "");
}

/* ============================================================
   Inpaint
   ============================================================ */

function _setOriginalFromB64(b64) {
  // The composite comes from Krita in its ORIGINAL orientation.
  // It becomes the new source; the display transform re-rotates it
  // for the user's current view.  Because rotation is display-only
  // now, `board.imageEl` / `imageWidth` / `imageHeight` /
  // `imageDataUrl` must be set directly here — `_applyRotation`
  // no longer derives them.
  const dataUrl = "data:image/png;base64," + b64;
  const img = new Image();
  img.onload = () => {
    board.originalImageEl = img;
    board.originalWidth   = img.naturalWidth;
    board.originalHeight  = img.naturalHeight;
    board.originalDataUrl = dataUrl;
    board.imageEl         = img;
    board.imageWidth      = img.naturalWidth;
    board.imageHeight     = img.naturalHeight;
    board.imageDataUrl    = dataUrl;
    board.imageHash       = "inpaint_" + Date.now();
    _applyRotation(view.rotation);
  };
  img.src = dataUrl;
}

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

  // All three blobs are built in the *rotated* frame, matching
  // what the user sees and what the daemon expects.  The rotated
  // context mask lets the daemon crop to the user's rectangle —
  // and only that rectangle, since rotation itself never touches
  // `board.inpaintContext`.
  const canvasB64    = _canvasB64ForInpaint();
  const selectionB64 = _selectionB64ForInpaint(mask);
  const contextB64   = buildRotatedContextMaskB64(view.rotation);

  flashStatus("inpainting\u2026", "");

  const payload = await runInpaint({
    canvasB64,
    selectionB64,
    contextB64,
    prompt: board.inpaintPrompt,
    outputRotation: -view.rotation,
  });
  if (!payload) return;

  // The composite comes from Krita in its ORIGINAL orientation.
  // Load it as the new original; the display transform re-rotates
  // for display, so the user's view stays in the frame they chose.
  _setOriginalFromB64(payload.image_b64);

  const statusCls = (payload.paste_ok === false) ? "warn" : "ok";
  const statusMsg = payload.message
    || ("inpainted in " + payload.timing.toFixed(2) + "s");
  flashStatus(statusMsg, statusCls);

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