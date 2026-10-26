"""Per-frame panel sync: image info, mask count, inpaint button
enablement, cancel button enablement, context-rectangle checkbox and
hint visibility, status line.

The function is called once per frame from `pg_dispatch.draw()`.
Every DOM write is guarded against the current DOM value so a
steady-state frame touches nothing."""

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
    kritaImportBtn:  document.getElementById("kritaImportBtn"),
    kritaImportMasksBtn: document.getElementById("kritaImportMasksBtn"),
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
    if (board.running) {
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
  const promptReady = !!board.inpaintPrompt.trim();
  const canInpaint = hasImage && maskReady && promptReady && !busy;

  if (e.inpaintBtn) {
    e.inpaintBtn.textContent = busy ? "Inpainting\u2026" : "Inpaint";
    e.inpaintBtn.classList.toggle("busy", busy);
    e.inpaintBtn.disabled = !canInpaint;
    if (!hasImage)              e.inpaintBtn.title = "Load an image first";
    else if (!maskReady)        e.inpaintBtn.title = "Select a single mask to inpaint";
    else if (!promptReady)      e.inpaintBtn.title = "Enter an inpaint prompt";
    else                        e.inpaintBtn.title = "Run the inpaint workflow";
  }
  if (e.cancelBtn) e.cancelBtn.disabled = !busy;

  if (e.pickImageBtn) e.pickImageBtn.disabled = busy;
  if (e.clearImageBtn) e.clearImageBtn.disabled = busy;
  if (e.pickMaskBtn) e.pickMaskBtn.disabled = busy;
  if (e.kritaImportBtn) e.kritaImportBtn.disabled = busy;
  if (e.kritaImportMasksBtn) e.kritaImportMasksBtn.disabled = busy;

  if (e.inpaintEnabled) e.inpaintEnabled.disabled = busy;
  if (e.inpaintPrompt) e.inpaintPrompt.disabled = busy;
}
"""
