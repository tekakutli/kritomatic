"""Per-frame panel sync: image info, mask count, inpaint button
enablement, cancel button enablement, mask-from-rect enablement,
context-rectangle checkbox and hint visibility, interceptor routing
enablement, status line.

The function is called once per frame from `pg_dispatch.draw()`.
Every DOM write is guarded against the current DOM value so a
steady-state frame touches nothing.

The prompt field is *not* part of the inpaint button's enablement
gate: an empty playground prompt is served from the daemon's shared
state (the docker / web panel's `comfy_apply_config` params), with the
workflow's own `Primitive*` default as the last resort.  See
`inpaint_server.py`'s PROMPT RESOLUTION section."""

PANEL_FIELDS_JS = r"""
import { board } from "./pg_state.js";
import { selectedMask, selectedMaskIdx } from "./pg_masks.js";

let _els = null;

function _get() {
  if (_els) return _els;
  _els = {
    imageInfo:       document.getElementById("imageInfo"),
    maskCount:       document.getElementById("maskCount"),
    statusLine:      document.getElementById("statusLine"),
    inpaintEnabled:  document.getElementById("inpaintEnabled"),
    inpaintRectHint: document.getElementById("inpaintRectHint"),
    inpaintPrompt:   document.getElementById("inpaintPrompt"),
    inpaintBtn:      document.getElementById("inpaintBtn"),
    cancelBtn:       document.getElementById("cancelBtn"),
    pickImageBtn:    document.getElementById("pickImageBtn"),
    clearImageBtn:   document.getElementById("clearImageBtn"),
    pickMaskBtn:     document.getElementById("pickMaskBtn"),
    kritaImportMasksBtn: document.getElementById("kritaImportMasksBtn"),
    maskFromRectBtn: document.getElementById("maskFromRectBtn"),
  };
  return _els;
}

export function syncPanelFields() {
  const e = _get();

  if (e.imageInfo) {
    let text;
    if (!board.imageHash) {
      text = "No image loaded \u2014 drop one on the canvas";
    } else {
      text = board.imageWidth + " \u00d7 " + board.imageHeight
           + "   [" + board.imageHash.slice(0, 8) + "\u2026]";
    }
    if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
  }

  if (e.maskCount) {
    const t = board.selectedMaskIndices.length
            + " / " + board.masks.length;
    if (e.maskCount.textContent !== t) e.maskCount.textContent = t;
  }

  if (e.inpaintEnabled && document.activeElement !== e.inpaintEnabled) {
    e.inpaintEnabled.checked = !!board.inpaintEnabled;
  }
  if (e.inpaintRectHint) {
    const want = board.inpaintEnabled ? "block" : "none";
    if (e.inpaintRectHint.style.display !== want) {
      e.inpaintRectHint.style.display = want;
    }
  }

  if (e.statusLine) {
    let text, cls = "";
    if (board.running && board.currentInterceptTaskId) {
      text = "waiting for interceptor\u2026"; cls = "warn";
    } else if (board.running) {
      text = "inpainting\u2026"; cls = "ok";
    } else if (board.lastTiming != null) {
      text = "last: " + board.lastTiming.toFixed(2) + "s"; cls = "ok";
    } else {
      text = "idle";
    }
    if (e.statusLine.textContent !== text) e.statusLine.textContent = text;
    e.statusLine.className = cls;
  }

  const busy = board.running;
  const hasImage = !!board.imageHash;
  const selCount = board.selectedMaskIndices.length;
  const m = selectedMaskIdx() >= 0 ? board.masks[selectedMaskIdx()] : null;
  const maskReady = selCount === 1 && m && m.loaded;
  // Prompt is optional: an empty playground prompt is served from the
  // daemon's shared state (docker / web panel), with the workflow's
  // own Primitive* default as the last resort.  Only image + a single
  // loaded mask are required to enable Run.
  const canInpaint = hasImage && maskReady && !busy;

  if (e.inpaintBtn) {
    e.inpaintBtn.textContent = busy
      ? (board.currentInterceptTaskId ? "Waiting\u2026"
                                      : "Inpainting\u2026")
      : "Inpaint";
    e.inpaintBtn.classList.toggle("busy", busy);
    e.inpaintBtn.disabled = !canInpaint;
    if (!hasImage)         e.inpaintBtn.title = "Load an image first";
    else if (!maskReady)   e.inpaintBtn.title = "Select a single mask to inpaint";
    else                   e.inpaintBtn.title =
      "Run the inpaint workflow (blank prompt = use docker / web panel prompt)";
  }
  if (e.cancelBtn) e.cancelBtn.disabled = !busy;

  // maskFromRectBtn: needs an image, a drawn rect, and inpaint mode
  // active — otherwise the button has nothing meaningful to turn
  // into a mask (buildContextMaskB64 would either fail or return
  // the "whole canvas is the context" white mask).
  const hasRect = !!(board.inpaintContext
                     && board.inpaintContext.w > 0
                     && board.inpaintContext.h > 0);
  if (e.maskFromRectBtn) {
    e.maskFromRectBtn.disabled =
      !hasImage || !board.inpaintEnabled || !hasRect || busy;
    if (!hasImage)                  e.maskFromRectBtn.title =
      "Load an image first";
    else if (!board.inpaintEnabled) e.maskFromRectBtn.title =
      "Enable the context rectangle first";
    else if (!hasRect)              e.maskFromRectBtn.title =
      "Draw the context rectangle first";
    else                            e.maskFromRectBtn.title =
      "Create a new mask from the current context rectangle (white = inside, black = outside). The new mask is placed at the top of the mask stack and selected.";
  }

  if (e.pickImageBtn) e.pickImageBtn.disabled = busy;
  if (e.clearImageBtn) e.clearImageBtn.disabled = busy;
  if (e.pickMaskBtn) e.pickMaskBtn.disabled = busy;
  if (e.kritaImportMasksBtn) e.kritaImportMasksBtn.disabled = busy;

  if (e.inpaintEnabled) e.inpaintEnabled.disabled = busy;
  if (e.inpaintPrompt) e.inpaintPrompt.disabled = busy;
  const interceptEnabledEl = document.getElementById("interceptEnabled");
  if (interceptEnabledEl) interceptEnabledEl.disabled = busy;
  const interceptUrlEl = document.getElementById("interceptUrl");
  if (interceptUrlEl) interceptUrlEl.disabled = busy;
}
"""