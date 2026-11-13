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
panel reading; the terminal write is the durable record.  This is the
only place in this file that bypasses `flashStatus` for an error,
because it is the only place with a persistent DOM element designed
to hold that error text.
"""

PANEL_BINDINGS_JS = r"""
import { board, setImage, clearImage, _scheduleDraw,
         view, _applyRotation, _rotateImage } from "./pg_state.js";
import {
  setMasks, addMaskFromB64,
  selectedMask, selectedMaskIdx,
  replaceMaskSelection,
} from "./pg_masks.js";
import { buildMaskFromContextRectB64, projectCropDims } from "./pg_inpaint.js";
import { computeMaskBBox } from "./pg_pixels.js";
import { syncMaskList } from "./pg_panel_lists.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import { uploadImageFile, fetchStatus, runInpaint, runInpaintViaInterceptor,
         interruptInpaint } from "./pg_comfy_client.js";
import {
  checkKritaStatus, importKritaMasks,
} from "./pg_krita_client.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ============================================================
   Result-compositing tuning
   ============================================================
   Inset, in pixels, applied inward to the mask's rotated
   rectangle when compositing the returned image over the
   previous original.  Discards the returned image's antialiased
   edge transition band, which would otherwise show through as a
   hairline seam at the diamond boundary.

   Tune up (5, 7) if the seam persists — the transition width
   depends on PIL's rotate quality on the server plus Canvas's
   imageSmoothingQuality on the client, and can vary.

   Tune down (2, 1) if a strip of un-inpainted original becomes
   visible at the boundary — unlikely, since the inpaint region
   is normally well inside the diamond and the inset lives in
   the band between the inpaint and the diamond edge where the
   original and returned are visually identical.

   Not applied at rotation 0: no diamond edge, no antialiased
   transition, no reason to trim. */

const SEAM_INSET_PX = 3;

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
   Inpaint payload builder
   ============================================================
   Two states, selected by `view.rotation`:

   Un-rotated (theta = 0)
   ----------------------
   Send the original, un-rotated image at (W × H), the full mask
   composited over black, and the context mask (full white when no
   context rectangle is set; the user's rect when it is).
   `outputRotation = 0`.  This is the current Mode A behaviour, and
   is what Mode B has always effectively done at rotation 0 (the
   rotation math is an identity at theta = 0).

   Rotated (theta != 0)
   --------------------
   Send a rotated payload.  The crop region is derived from
   `projectCropDims`: base (W × H) unless the mask (intersected
   with the context rect, or unclipped when inpaint is disabled)
   extends beyond the base crop, in which case it expands
   symmetrically until the mask is covered.

   Mode A (no context rect):
     canvas    = rotated original, cropped to (cropW × cropH)
     selection = rotated mask, cropped to (cropW × cropH)
     context   = full white on (cropW × cropH)
     outputRotation = -theta

   Mode B (context rect):
     canvas    = same
     selection = rotated mask, cropped, intersected with the
                 context rect at its shifted position
     context   = user's rect on (cropW × cropH) at its shifted
                 position
     outputRotation = -theta

   Both modes end up with the same `outputRotation`; the difference
   is only how the context mask is built and whether the selection
   is clipped by the context rect before being sent.

   The context rect position in the expanded crop frame is
   (cropW - W)/2 + c.x for x, same for y.  Derivation: base frame's
   (c.x, c.y) is at (cropW - W)/2 + c.x in the expanded frame
   because expansion shifts the frame's top-left by
   ((cropW - W)/2, (cropH - H)/2) in the negative direction.

   `outputRotation = -view.rotation` tells the daemon to rotate the
   result back before pasting.  The client center-crops the returned
   (cropW × cropH) image back to (W × H) via `_setOriginalFromB64`
   and applies `_buildKeepMask` on the (W × H) composite to mask
   the non-content corners — same logic as before, just at the
   expanded crop dims. */

function _buildInpaintPayload(mask, maskBBoxOrig) {
  const W = board.imageWidth, H = board.imageHeight;
  const c = board.inpaintContext;
  const theta = view.rotation;

  // ---- Un-rotated path: theta = 0 ----
  // Same for both modes; only the context mask differs.
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

  // Canvas: rotate original to W' × H', crop to the (possibly
  // expanded) region.
  const rotImg = _rotateImage(board.originalImageEl, theta);
  const canvasCv = document.createElement("canvas");
  canvasCv.width = cropW; canvasCv.height = cropH;
  const canvasCtx = canvasCv.getContext("2d");
  canvasCtx.imageSmoothingEnabled = true;
  canvasCtx.imageSmoothingQuality = "high";
  canvasCtx.drawImage(rotImg, -cropX, -cropY);
  const canvasB64 = canvasCv.toDataURL("image/png").replace(
    /^data:image\/png;base64,/, "");

  // Selection: rotated mask, cropped to the same expanded region.
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
    // Mode B: intersect with user's context rect at its position in
    // the expanded crop frame.
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
    // Mode A: whole crop frame is context.
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
   Result compositing — see the module docstring for the full
   rationale.  Two functions:

     _buildKeepMask     geometry-only alpha mask of the returned
                        image's content region (diamond at 45°,
                        polygon for other angles, full frame at 0°),
                        drawn at (cropW × cropH) dims so the expanded
                        case produces a larger diamond, inset by
                        SEAM_INSET_PX to discard the returned image's
                        antialiased edge band.

     _setOriginalFromB64 composites the returned image, masked to
                        the keep region, over the previous original.
                        Where the mask is transparent, the previous
                        original shows through — recovering the
                        corners that ComfyUI's LoadImage turned black.

   `rotationDeg` is the display rotation that was live when the
   payload was built.  It is not `view.rotation` at the moment the
   response arrives; see "Rotation-capture timing" in the module
   docstring. */

function _buildKeepMask(W, H, rotationDeg, cropW, cropH) {
  // Reproduce, as an alpha mask, the region of the returned image
  // that has real content.  The returned image is the sent image
  // un-rotated by the server; the sent image is the (cropW × cropH)
  // crop of the rotated original.  Composing the two transforms, the
  // content region of the returned (center-cropped back to W × H) is
  // the (cropW × cropH) rect rotated by -rotationDeg around the
  // centre, intersected with the (W × H) frame itself.
  //
  // Base case (cropW=cropH=W): the classic diamond.  Expanded case:
  // a larger diamond that covers more of W × H, up to full white when
  // the crop is big enough to include the whole rotated frame.
  //
  // Everything outside that region is black: ComfyUI's LoadImage reads
  // the sent PNG's alpha as RGB, so the transparent corners the client
  // produced are rendered black by the time SaveImage writes the
  // result.
  //
  // `destination-in` uses the SOURCE'S ALPHA to multiply the
  // destination's alpha.  The mask therefore MUST be transparent
  // outside the content region, not black — an opaque black
  // background would be a no-op (alpha 1 × alpha 1 = alpha 1) and the
  // returned image's black corners would paint over the original
  // unchanged.  The canvas is transparent by default; only the white
  // rotated rect needs drawing.
  //
  // Seam inset: draw the white rect INSET by SEAM_INSET_PX on each
  // side.  That discards the returned image's antialiased transition
  // band (a few pixels of dark mixture-of-content-and-black along the
  // content boundary, produced by PIL's rotate on the server and by
  // drawImage's smoothQuality on the client).  Keeping those at full
  // opacity in the composite produces a hairline seam.  Discarding
  // them lets the original show through instead — invisible, because
  // inside the content region the original and the returned image
  // are essentially identical.
  //
  // Rotation 0: the rect IS the frame, there is no diamond edge and
  // no antialiased transition to discard.  Any inset would just trim a
  // strip of freshly-inpainted content from the canvas edges, so no
  // inset is applied in that case.
  const mask = document.createElement("canvas");
  mask.width = W;
  mask.height = H;
  const mctx = mask.getContext("2d");
  mctx.fillStyle = "#fff";
  mctx.save();
  mctx.translate(W / 2, H / 2);
  mctx.rotate(-rotationDeg * Math.PI / 180);
  mctx.translate(-W / 2, -H / 2);

  const inset = (rotationDeg % 360 === 0) ? 0 : SEAM_INSET_PX;
  mctx.fillRect(
    W / 2 - cropW / 2 + inset,
    H / 2 - cropH / 2 + inset,
    cropW - 2 * inset,
    cropH - 2 * inset,
  );

  mctx.restore();
  return mask;
}

function _setOriginalFromB64(b64, rotationDeg) {
  // `rotationDeg` is the display rotation that was active when this
  // inpaint was submitted.  It determines the diamond-shaped content
  // region of the returned image (see _buildKeepMask).  Passing it in
  // rather than reading `view.rotation` here matters because the user
  // can rotate the view during the 20–60 s wait for the server; the
  // returned image was computed with the rotation that was live when
  // the payload was sent, not whatever it is now.
  const dataUrl = "data:image/png;base64," + b64;
  const img = new Image();
  img.onload = () => {
    const W = board.originalWidth;
    const H = board.originalHeight;
    const srcW = img.naturalWidth;
    const srcH = img.naturalHeight;

    if (board.originalImageEl && W > 0 && H > 0
        && srcW >= W && srcH >= H) {
      // Unified path for both base (srcW === W) and expanded
      // (srcW > W) inpaints:
      //   1. draw original underneath
      //   2. center-crop the returned image to (W × H) — for base
      //      this is a no-op; for expanded this pulls out the
      //      original content the wider crop captured
      //   3. mask the center-crop to the content region implied by
      //      the returned dims, then composite over the original
      //
      // Base case: center-crop is the whole returned image, mask is
      // the base diamond, composite is exactly the previous
      // behaviour.  Expanded case: center-crop discards the black
      // margins, mask is a larger diamond that covers more of the
      // non-inpainted content, composite replaces more but is
      // visually identical there.
      const composite = document.createElement("canvas");
      composite.width = W;
      composite.height = H;
      const cctx = composite.getContext("2d");
      cctx.imageSmoothingEnabled = true;
      cctx.imageSmoothingQuality = "high";

      // 1. Original underneath.
      cctx.drawImage(board.originalImageEl, 0, 0, W, H);

      // 2. Masked returned, center-cropped to (W × H).
      const cropX = Math.round((srcW - W) / 2);
      const cropY = Math.round((srcH - H) / 2);

      const masked = document.createElement("canvas");
      masked.width = W;
      masked.height = H;
      const mctx = masked.getContext("2d");
      mctx.imageSmoothingEnabled = true;
      mctx.imageSmoothingQuality = "high";
      // Draw the returned image at the negative offset so its W × H
      // center lands at (0, 0) of `masked`.
      mctx.drawImage(img, -cropX, -cropY, srcW, srcH);
      mctx.globalCompositeOperation = "destination-in";
      mctx.drawImage(_buildKeepMask(W, H, rotationDeg, srcW, srcH), 0, 0);
      mctx.globalCompositeOperation = "source-over";

      // 3. Masked result on top.
      cctx.drawImage(masked, 0, 0);

      board.originalImageEl  = composite;
      board.originalDataUrl  = composite.toDataURL("image/png");
      board.imageEl          = composite;
      board.imageDataUrl     = board.originalDataUrl;
    } else {
      // Fallback: no prior original, or the returned dims are smaller
      // than W × H (unexpected).  Use the result as-is.
      board.originalImageEl  = img;
      board.originalWidth    = img.naturalWidth;
      board.originalHeight   = img.naturalHeight;
      board.originalDataUrl  = dataUrl;
      board.imageEl          = img;
      board.imageWidth       = img.naturalWidth;
      board.imageHeight      = img.naturalHeight;
      board.imageDataUrl     = dataUrl;
    }

    board.imageHash = "inpaint_" + Date.now();
    _applyRotation(view.rotation);
  };
  img.src = dataUrl;
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

  // Mask bbox in original-frame coords, used by `_buildInpaintPayload`
  // to decide whether the crop needs to expand.  Cached on the mask
  // entry, so this is O(1) after the first call for a given mask.
  const maskBBox = computeMaskBBox(idx);

  // Doc-sized payloads plus the matching un-rotation the daemon
  // needs before pasting — see _buildInpaintPayload above.
  const payload = _buildInpaintPayload(mask, maskBBox);
  // Capture the rotation the payload was built with.  The returned
  // image is un-rotated by exactly `-payload.outputRotation`, so its
  // black corners correspond to that rotation.  A scrub during the
  // 20–60 s poll must not desync the mask from the returned image.
  const inpaintRotation = view.rotation;

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

  // Composite the result over the previous original, masked to the
  // content region that rotation produced — see the module docstring
  // and _setOriginalFromB64 for the full rationale.
  _setOriginalFromB64(result.image_b64, inpaintRotation);

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
   ============================================================
   The `preferSelectionMaskChildren: true` flag tells the daemon:
   for every image-bearing layer in the selection, do NOT read
   its alpha channel; instead, expand it to its first
   `selectionmask` child.  Image layers with no selectionmask
   child are skipped entirely (no mask emitted for them).

   Explicitly-selected mask layers (transparencymask,
   selectionmask) still pass through unchanged, so selecting a
   mask directly keeps working.

   Multimask's identically-named button does not pass this flag,
   so its behaviour (read image layers via alpha) is unchanged. */

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
}
"""