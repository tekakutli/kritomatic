"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground:
  - Load image (file, drop, import from Krita)
  - Load masks (import from Krita, pick a PNG file)
  - Select / delete masks
  - Rotate the playground view (slider)
  - Materialise the context rectangle as a mask
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
import { buildMaskFromContextRectB64 } from "./pg_inpaint.js";
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
   Mask from context rectangle
   ============================================================
   Materialises the current context rectangle as a new mask
   entry in the stack.

   The mask is built via `buildMaskFromContextRectB64`, which
   stores the rect *inverse-rotated* by `view.rotation` around
   the image centre.  Every mask is drawn under the view's
   rotation transform, so the inverse rotation cancels on
   display and the mask lands on screen exactly where the
   axis-aligned context rectangle appears.  Storing the plain
   axis-aligned rect (as `buildContextMaskB64` does) would make
   the mask appear rotated on screen, off-position relative to
   the visible context rect.

   Only meaningful when the rectangle is enabled: when
   `inpaintEnabled === false`, there is no rectangle to convert.
   The button is disabled in that state; the check below is the
   belt-and-braces version. */

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
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ============================================================
   Inpaint payload builders
   ============================================================
   Two modes, selected by `board.inpaintEnabled`.

   Mode A — no context rectangle (inpaintEnabled === false)
   -------------------------------------------------------
   Send the original, un-rotated image, the full mask, and a
   full-white context. The workflow inpaints the mask region of
   the whole image. `outputRotation = 0` because nothing needs to
   be un-rotated — the mask already lives in original-image
   coords.

   Mode B — context rectangle enabled
   ----------------------------------
   The rectangle is fixed in *screen* coords: it does not rotate
   with the view, it stays where the user dragged it. The image
   under it is the *rotated* image, so the content under the
   rectangle is a diamond of image pixels (the un-rotated image
   rotated by `view.rotation` around its center).

   To reproduce what the user sees on screen, we send:

     thecanvas    = the rotated image, center-cropped to (W × H)
     theselection = the rotated mask, center-cropped to (W × H),
                    multiplied by a white rectangle at
                    (c.x, c.y, c.w, c.h) on black
     thecontext   = the same white rectangle at (c.x, c.y, c.w, c.h)

   The centered crop is what fits a (W' × H') rotated canvas into
   the doc's (W × H) layer transport. The rect position in
   rotated-canvas coords is (c.x − W/2 + W'/2, c.y − H/2 + H'/2);
   subtracting the crop offset ((W' − W)/2, (H' − H)/2) gives back
   (c.x, c.y) — so the rectangle lands at the same position in the
   crop frame as it does in the un-rotated image. Only the content
   behind it is rotated, which is exactly the mismatch the user
   reported in the diagnostic: "the context should be rotated,
   and the mask should be rotated mask ∩ rect."

   `outputRotation = -view.rotation` tells the daemon to rotate the
   result back before pasting, so the composite in the document is
   back in original orientation with the inpaint applied to the
   corresponding original-image region.

   Corner caveat: the (W × H) crop of the rotated canvas misses the
   four corners of the original image (they extend beyond the
   crop when rotated). After un-rotation the new layer has
   transparent corners. Composite over the still-present original
   layer looks correct (original shows through the transparency);
   only the new layer alone is incomplete. This is fine for small
   central rects and is the practical trade-off of fitting rotated
   content through the (W × H) layer transport. */

function _buildInpaintPayload(mask) {
  const W = board.imageWidth, H = board.imageHeight;
  const c = board.inpaintContext;
  const theta = view.rotation;

  // ---- Mode A: no context rectangle ----
  if (!board.inpaintEnabled) {
    const canvasB64 = board.imageDataUrl.replace(
      /^data:image\/png;base64,/, "");

    // Selection = full mask, composited over black.
    const selCanvas = document.createElement("canvas");
    selCanvas.width = W; selCanvas.height = H;
    const scx = selCanvas.getContext("2d");
    scx.imageSmoothingEnabled = true;
    scx.imageSmoothingQuality = "high";
    scx.fillStyle = "#000";
    scx.fillRect(0, 0, W, H);
    if (mask && mask.img) scx.drawImage(mask.img, 0, 0, W, H);
    const selectionB64 = selCanvas.toDataURL("image/png").replace(
      /^data:image\/png;base64,/, "");

    // Context = full white (no crop).
    const ctxCanvas = document.createElement("canvas");
    ctxCanvas.width = W; ctxCanvas.height = H;
    const ccx = ctxCanvas.getContext("2d");
    ccx.fillStyle = "#fff";
    ccx.fillRect(0, 0, W, H);
    const contextB64 = ctxCanvas.toDataURL("image/png").replace(
      /^data:image\/png;base64,/, "");

    return { canvasB64, selectionB64, contextB64, outputRotation: 0 };
  }

  // ---- Mode B: context rectangle enabled ----
  // Rotate the original image into a (W' × H') frame, then
  // center-crop back to (W × H).
  const rotImg = _rotateImage(board.originalImageEl, theta);
  const cropXImg = Math.round((rotImg.width  - W) / 2);
  const cropYImg = Math.round((rotImg.height - H) / 2);

  const canvasCv = document.createElement("canvas");
  canvasCv.width = W; canvasCv.height = H;
  const canvasCtx = canvasCv.getContext("2d");
  canvasCtx.imageSmoothingEnabled = true;
  canvasCtx.imageSmoothingQuality = "high";
  canvasCtx.drawImage(rotImg, -cropXImg, -cropYImg);
  const canvasB64 = canvasCv.toDataURL("image/png").replace(
    /^data:image\/png;base64,/, "");

  // Mask: build at (W × H) first so mask.img is normalized to
  // canvas dims even if it was imported at a different size.
  // Then rotate → (W' × H') → center-crop to (W × H).
  const maskBase = document.createElement("canvas");
  maskBase.width = W; maskBase.height = H;
  const mbx = maskBase.getContext("2d");
  mbx.imageSmoothingEnabled = true;
  mbx.imageSmoothingQuality = "high";
  mbx.fillStyle = "#000";
  mbx.fillRect(0, 0, W, H);
  if (mask && mask.img) mbx.drawImage(mask.img, 0, 0, W, H);

  const rotMask = _rotateImage(maskBase, theta);
  const cropXMask = Math.round((rotMask.width  - W) / 2);
  const cropYMask = Math.round((rotMask.height - H) / 2);

  const maskCv = document.createElement("canvas");
  maskCv.width = W; maskCv.height = H;
  const maskCtx = maskCv.getContext("2d");
  maskCtx.imageSmoothingEnabled = true;
  maskCtx.imageSmoothingQuality = "high";
  maskCtx.fillStyle = "#000";
  maskCtx.fillRect(0, 0, W, H);
  maskCtx.drawImage(rotMask, -cropXMask, -cropYMask);

  // Intersect with the fixed rect. The rect stays axis-aligned
  // at (c.x, c.y) in the crop frame; only the mask content behind
  // it is rotated. `multiply` keeps the rotated mask values
  // inside the rect and zeros them outside — mask ∩ rect.
  const rectMask = document.createElement("canvas");
  rectMask.width = W; rectMask.height = H;
  const rmx = rectMask.getContext("2d");
  rmx.fillStyle = "#000";
  rmx.fillRect(0, 0, W, H);
  rmx.fillStyle = "#fff";
  rmx.fillRect(c.x, c.y, c.w, c.h);

  maskCtx.globalCompositeOperation = "multiply";
  maskCtx.drawImage(rectMask, 0, 0);
  maskCtx.globalCompositeOperation = "source-over";

  const selectionB64 = maskCv.toDataURL("image/png").replace(
    /^data:image\/png;base64,/, "");

  // Context: white rect at (c.x, c.y, c.w, c.h) on black.
  const ctxCanvas = document.createElement("canvas");
  ctxCanvas.width = W; ctxCanvas.height = H;
  const ccx = ctxCanvas.getContext("2d");
  ccx.fillStyle = "#000";
  ccx.fillRect(0, 0, W, H);
  ccx.fillStyle = "#fff";
  ccx.fillRect(c.x, c.y, c.w, c.h);
  const contextB64 = ctxCanvas.toDataURL("image/png").replace(
    /^data:image\/png;base64,/, "");

  return {
    canvasB64,
    selectionB64,
    contextB64,
    outputRotation: theta ? -theta : 0,
  };
}

/* ============================================================
   Inpaint
   ============================================================ */

function _setOriginalFromB64(b64) {
  // The composite comes from Krita in its ORIGINAL orientation.
  // It becomes the new source; the display transform re-rotates
  // it for the user's current view. Because rotation is
  // display-only, `board.imageEl` / `imageWidth` / `imageHeight`
  // / `imageDataUrl` must be set directly here —
  // `_applyRotation` no longer derives them.
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

  // Doc-sized payloads plus the matching un-rotation the daemon
  // needs before pasting — see _buildInpaintPayload above.
  const payload = _buildInpaintPayload(mask);

  flashStatus("inpainting\u2026", "");

  const result = await runInpaint({
    canvasB64: payload.canvasB64,
    selectionB64: payload.selectionB64,
    contextB64: payload.contextB64,
    prompt: board.inpaintPrompt,
    outputRotation: payload.outputRotation,
  });
  if (!result) return;

  // The composite comes from Krita in its ORIGINAL orientation.
  // Load it as the new original; the display transform re-rotates
  // for display, so the user's view stays in the frame they chose.
  _setOriginalFromB64(result.image_b64);

  const statusCls = (result.paste_ok === false) ? "warn" : "ok";
  const statusMsg = result.message
    || ("inpainted in " + result.timing.toFixed(2) + "s");
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
    "maskFromRectBtn",
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
  if (el.maskFromRectBtn) el.maskFromRectBtn.addEventListener("click", _maskFromRectAction);

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