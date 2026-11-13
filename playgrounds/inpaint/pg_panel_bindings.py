"""Panel button handlers, pgkit actions, mask list levels, and the
boot-sync routine.

Operations exposed by this playground:
  - Load image (file, drop; autosync from @thecanvas)
  - Load masks (import from Krita, pick a PNG file)
  - Select / delete masks
  - Rotate the playground view (single scrub input: click-to-type,
    drag-to-scrub, Shift-snap)
  - Materialise the context rectangle as a mask
  - Inpaint (local, or routed via the interceptor playground when
    the "Route via interceptor" checkbox is on)
  - Cancel (local interrupt, or interceptor cancellation)
  - ComfyUI / Krita status checks

Not present: Segment All, Refine, mask prompt, region painting,
points, undo/redo, export to Krita.  Also: the old "Import image from
Krita" button is gone — `pg_autosync.js` pulls the current
`@thecanvas` in automatically when the daemon's config points at
one.

PASTE ≠ PLAYGROUND UPDATE
=========================
After `/comfy/inpaint` succeeds, the server has already pasted the
result into Krita (via `comfy_paste_image_b64`, un-rotated by
`output_rotation`).  The inpaint playground does NOT pull that
result back into its own canvas.  The playground's displayed image
is whatever `@thecanvas` resolves to at any moment; that is the
whole point of the autosync poller in `pg_autosync.js`.  Injecting
the inpaint output would make the canvas show something `@thecanvas`
does not point at.

So `_inpaintAction`, after the server returns, only flashes a
status line.  It never writes `board.originalImageEl`,
`board.imageEl`, or `board.imageDataUrl`.  If the user wants to see
the pasted result in the playground, they set `@thecanvas` to a
spec that resolves to it (e.g. `source: "active"`); autosync then
refreshes the canvas on the next spec change, exactly as for any
other `@thecanvas` update.

KRITA STATUS MIRRORING
======================
`#kritaStatus` is a PERSISTENT panel element: a small "✓ reachable"
/ "✗ not reachable" label next to the Check-Krita button.  Unlike
`flashStatus`, it does not auto-clear — the last-known state stays
visible until the next refresh.

`_kritaMarkReachable()` writes the ✓ form; `_kritaMarkUnreachable(
reason)` writes the ✗ form.  Because the ✗ state is exactly what a
developer watching the terminal would want to see, `_kritaMarkUnreachable`
ALSO mirrors `reason` to `console.error`, which pgkit/panel_core.js
routes to `POST ./__log`.  The persistent DOM write is for at-a-glance
panel reading; the terminal write is the durable record.

SHARED-PROMPT HYDRATION
=======================
`_hydrateSharedPrompt` pulls the daemon's shared-state prompt
(`_active_config["params"]["prompt"]`, the same value the docker and
web panel edit) into the playground's prompt field on boot, but only
when the playground's own prompt is empty.

SHARED-SELECTION HYDRATION
==========================
`_hydrateSharedSelection` pulls the mask the daemon's config points
at via `@theselection` into the playground's mask stack on boot, but
only when the stack is empty.
"""

PANEL_BINDINGS_JS = r"""
import { board, setImage, clearImage, _scheduleDraw,
         view, _rotateImage } from "./pg_state.js";
import {
  setMasks, addMaskFromB64,
  selectedMask, selectedMaskIdx,
  replaceMaskSelection,
} from "./pg_masks.js";
import { buildMaskFromContextRectB64, projectCropDims } from "./pg_inpaint.js";
import { computeMaskBBox } from "./pg_pixels.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import {
  uploadImageFile, fetchStatus, runInpaint, runInpaintViaInterceptor,
  interruptInpaint, fetchCurrentPrompt,
} from "./pg_comfy_client.js";
import {
  checkKritaStatus, importKritaMasks, fetchActiveState,
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
  // clearImage resets view.rotation to 0; sync the widget so the
  // user does not see a stale angle on a cleared canvas.
  const rotEl = document.getElementById("viewRotation");
  if (rotEl) rotEl.value = "0";
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
   axis-aligned context rectangle appears. */

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
   Inpaint payload builder
   ============================================================
   Two states, selected by `view.rotation`:

   Un-rotated (theta = 0)
   ----------------------
   Send the original, un-rotated image at (W × H), the full mask
   composited over black, and the context mask (full white when no
   context rectangle is set; the user's rect when it is).
   `outputRotation = 0`.

   Rotated (theta != 0)
   --------------------
   Send a rotated payload.  The crop region is derived from
   `projectCropDims`: base (W × H) unless the mask (intersected
   with the context rect, or unclipped when inpaint is disabled)
   extends beyond the base crop, in which case it expands
   symmetrically until the mask is covered.

   `outputRotation = -view.rotation` tells the daemon to rotate the
   result back before pasting.  The server passes that value
   through to `comfy_paste_image_b64`, which runs
   `QTransform().rotate(output_rotation)` to un-rotate, then
   center-crops to the document's dims.  Result: the region lands
   at the right position in Krita, un-rotated. */

function _buildInpaintPayload(mask, maskBBoxOrig) {
  const W = board.imageWidth, H = board.imageHeight;
  const c = board.inpaintContext;
  const theta = view.rotation;

  // ---- Un-rotated path: theta = 0 ----
  if (!theta || theta % 360 === 0) {
    const canvasB64 = board.imageDataUrl.replace(
      /^data:image\/png;base64,/, "");

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

    let contextB64;
    if (!board.inpaintEnabled) {
      const ctxCanvas = document.createElement("canvas");
      ctxCanvas.width = W; ctxCanvas.height = H;
      const ccx = ctxCanvas.getContext("2d");
      ccx.fillStyle = "#fff";
      ccx.fillRect(0, 0, W, H);
      contextB64 = ctxCanvas.toDataURL("image/png").replace(
        /^data:image\/png;base64,/, "");
    } else {
      const ctxCanvas = document.createElement("canvas");
      ctxCanvas.width = W; ctxCanvas.height = H;
      const ccx = ctxCanvas.getContext("2d");
      ccx.fillStyle = "#000";
      ccx.fillRect(0, 0, W, H);
      ccx.fillStyle = "#fff";
      ccx.fillRect(c.x, c.y, c.w, c.h);
      contextB64 = ctxCanvas.toDataURL("image/png").replace(
        /^data:image\/png;base64,/, "");
    }

    return {
      canvasB64, selectionB64, contextB64,
      outputRotation: 0,
      cropW: W, cropH: H,
    };
  }

  // ---- Rotated path: theta != 0, both modes unified ----
  const rad = (theta % 360) * Math.PI / 180;
  const cosA = Math.abs(Math.cos(rad));
  const sinA = Math.abs(Math.sin(rad));
  const Wp = Math.max(1, Math.round(W * cosA + H * sinA));
  const Hp = Math.max(1, Math.round(W * sinA + H * cosA));

  const { cropW, cropH } = projectCropDims(
    W, H, maskBBoxOrig, theta, c, board.inpaintEnabled,
  );
  const cropX = Math.round((Wp - cropW) / 2);
  const cropY = Math.round((Hp - cropH) / 2);

  const rotImg = _rotateImage(board.originalImageEl, theta);
  const canvasCv = document.createElement("canvas");
  canvasCv.width = cropW; canvasCv.height = cropH;
  const canvasCtx = canvasCv.getContext("2d");
  canvasCtx.imageSmoothingEnabled = true;
  canvasCtx.imageSmoothingQuality = "high";
  canvasCtx.drawImage(rotImg, -cropX, -cropY);
  const canvasB64 = canvasCv.toDataURL("image/png").replace(
    /^data:image\/png;base64,/, "");

  const maskBase = document.createElement("canvas");
  maskBase.width = W; maskBase.height = H;
  const mbx = maskBase.getContext("2d");
  mbx.imageSmoothingEnabled = true;
  mbx.imageSmoothingQuality = "high";
  mbx.fillStyle = "#000";
  mbx.fillRect(0, 0, W, H);
  if (mask && mask.img) mbx.drawImage(mask.img, 0, 0, W, H);

  const rotMask = _rotateImage(maskBase, theta);
  const maskCv = document.createElement("canvas");
  maskCv.width = cropW; maskCv.height = cropH;
  const maskCtx = maskCv.getContext("2d");
  maskCtx.imageSmoothingEnabled = true;
  maskCtx.imageSmoothingQuality = "high";
  maskCtx.fillStyle = "#000";
  maskCtx.fillRect(0, 0, cropW, cropH);
  maskCtx.drawImage(rotMask, -cropX, -cropY);

  let contextB64;
  if (board.inpaintEnabled) {
    const ctxInCropX = (cropW - W) / 2 + c.x;
    const ctxInCropY = (cropH - H) / 2 + c.y;

    const rectMask = document.createElement("canvas");
    rectMask.width = cropW; rectMask.height = cropH;
    const rmx = rectMask.getContext("2d");
    rmx.fillStyle = "#000";
    rmx.fillRect(0, 0, cropW, cropH);
    rmx.fillStyle = "#fff";
    rmx.fillRect(ctxInCropX, ctxInCropY, c.w, c.h);

    maskCtx.globalCompositeOperation = "multiply";
    maskCtx.drawImage(rectMask, 0, 0);
    maskCtx.globalCompositeOperation = "source-over";

    const ctxCanvas = document.createElement("canvas");
    ctxCanvas.width = cropW; ctxCanvas.height = cropH;
    const ccx = ctxCanvas.getContext("2d");
    ccx.fillStyle = "#000";
    ccx.fillRect(0, 0, cropW, cropH);
    ccx.fillStyle = "#fff";
    ccx.fillRect(ctxInCropX, ctxInCropY, c.w, c.h);
    contextB64 = ctxCanvas.toDataURL("image/png").replace(
      /^data:image\/png;base64,/, "");
  } else {
    const ctxCanvas = document.createElement("canvas");
    ctxCanvas.width = cropW; ctxCanvas.height = cropH;
    const ccx = ctxCanvas.getContext("2d");
    ccx.fillStyle = "#fff";
    ccx.fillRect(0, 0, cropW, cropH);
    contextB64 = ctxCanvas.toDataURL("image/png").replace(
      /^data:image\/png;base64,/, "");
  }

  const selectionB64 = maskCv.toDataURL("image/png").replace(
    /^data:image\/png;base64,/, "");

  return {
    canvasB64, selectionB64, contextB64,
    outputRotation: -theta,
    cropW, cropH,
  };
}

/* ============================================================
   Shared-prompt hydration
   ============================================================ */

async function _hydrateSharedPrompt() {
  if (board.inpaintPrompt && board.inpaintPrompt.trim()) return;
  const r = await fetchCurrentPrompt();
  if (!r || !r.success) return;
  const shared = (r.prompt || "").trim();
  if (!shared) return;
  if (board.inpaintPrompt && board.inpaintPrompt.trim()) return;
  board.inpaintPrompt = shared;
  const el = document.getElementById("inpaintPrompt");
  if (el && document.activeElement !== el) el.value = shared;
  syncPanelFields();
  pgkitNotifyAll();
  flashStatus("prompt hydrated from shared state", "");
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
   Inpaint
   ============================================================ */

async function _inpaintAction() {
  if (!board.imageHash) { flashStatus("Load an image first", "warn"); return; }
  if (board.running) return;

  const idx = selectedMaskIdx();
  if (idx < 0) { flashStatus("Select a mask to inpaint", "warn"); return; }

  const mask = board.masks[idx];
  if (!mask || !mask.loaded) {
    flashStatus("Mask not loaded", "warn"); return;
  }

  const maskBBox = computeMaskBBox(idx);
  const payload = _buildInpaintPayload(mask, maskBBox);

  flashStatus("inpainting\u2026", "");

  let result;
  if (board.interceptEnabled) {
    const callbackUrl = new URL(
      "intercept_callback", window.location.href
    ).href;
    result = await runInpaintViaInterceptor({
      canvasB64:      payload.canvasB64,
      selectionB64:   payload.selectionB64,
      contextB64:     payload.contextB64,
      prompt:         board.inpaintPrompt,
      outputRotation: payload.outputRotation,
      interceptorUrl: board.interceptUrl,
      callbackUrl:    callbackUrl,
    });
  } else {
    result = await runInpaint({
      canvasB64:      payload.canvasB64,
      selectionB64:   payload.selectionB64,
      contextB64:     payload.contextB64,
      prompt:         board.inpaintPrompt,
      outputRotation: payload.outputRotation,
    });
  }
  if (!result) return;

  // The server already pasted the result into Krita, un-rotated.
  // Do NOT touch the playground canvas here: what the canvas shows
  // is whatever `@thecanvas` resolves to, and `pg_autosync.js`
  // refreshes it on `@thecanvas` spec changes.  Injecting the
  // inpaint output would make the canvas show something `@thecanvas`
  // does not point at.  The inpaint result is visible in Krita; the
  // playground just reports what happened.
  flashStatus(
    result.paste_ok
      ? ("inpainted + pasted to Krita: "
         + (result.paste_layer_name || "new layer")
         + " in " + result.timing.toFixed(2) + "s")
      : ("inpainted in " + result.timing.toFixed(2) + "s, "
         + "but Krita paste failed: "
         + (result.paste_message || "unknown")),
    result.paste_ok ? "ok" : "warn"
  );

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
  // Mirror to the terminal.  The persistent #kritaStatus element is
  // the panel's at-a-glance indicator; the terminal entry is the
  // durable record.  console.error is captured by
  // pgkit/panel_core.js's override and routed to POST ./__log.
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
    "kritaImportMasksBtn",
    "kritaCheckBtn",
    "maskFromRectBtn",
  ];
  for (const id of ids) el[id] = document.getElementById(id);

  if (el.inpaintBtn)   el.inpaintBtn.addEventListener("click", _inpaintAction);
  if (el.cancelBtn)    el.cancelBtn.addEventListener("click", _cancelAction);
  if (el.pickImageBtn) el.pickImageBtn.addEventListener("click", _pickImageAction);
  if (el.clearImageBtn) el.clearImageBtn.addEventListener("click", _clearImageAction);
  if (el.pickMaskBtn)  el.pickMaskBtn.addEventListener("click", _pickMaskAction);
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
  (async () => {
    await _hydrateSharedPrompt();
    await _hydrateSharedSelection();
  })();
}
"""
