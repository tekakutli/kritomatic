"""Per-frame panel sync: image info, mask count, mask-from-rect
enablement, context-rectangle checkbox and hint visibility, sync
status line.

Called once per frame from `pg_dispatch.draw()`.  Every DOM write is
guarded against the current DOM value so a steady-state frame touches
nothing.

The prompt field is gone.  The status line shows sync state instead of
inpaint state: `Synced HH:MM:SS · rotation N° · mask K/N · context on/off`,
or `Syncing…` / `Sync failed: …` / `No mask selected · workflow will
not inpaint`."""

PANEL_FIELDS_JS = r"""
import { board, view } from "./pg_state.js";
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
    syncNowBtn:      document.getElementById("syncNowBtn"),
    pickImageBtn:    document.getElementById("pickImageBtn"),
    clearImageBtn:   document.getElementById("clearImageBtn"),
    pickMaskBtn:     document.getElementById("pickMaskBtn"),
    kritaImportMasksBtn: document.getElementById("kritaImportMasksBtn"),
    maskFromRectBtn: document.getElementById("maskFromRectBtn"),
  };
  return _els;
}

function _fmtTime(ts) {
  try {
    return new Date(ts).toTimeString().slice(0, 8);
  } catch (_) {
    return "—";
  }
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
    if (board.syncing) {
      text = "syncing\u2026"; cls = "ok";
    } else if (board.syncError) {
      text = "sync failed: " + board.syncError; cls = "bad";
    } else if (board.lastSync) {
      const maskLabel = board.selectedMaskIndices.length === 1
        ? String(board.selectedMaskIndices[0] + 1)
        : (board.selectedMaskIndices.length > 1
           ? board.selectedMaskIndices.length + " selected"
           : "none");
      const ctxLabel = board.inpaintEnabled ? "on" : "off";
      const rot = (view.rotation || 0).toFixed(1);
      text = "synced " + _fmtTime(board.lastSync)
           + " · rotation " + rot + "\u00b0"
           + " · mask " + maskLabel
           + " · context " + ctxLabel;
      cls = "ok";
    } else if (!board.imageHash) {
      text = "idle"; cls = "";
    } else if (board.selectedMaskIndices.length === 0) {
      text = "No mask selected · workflow will not inpaint"; cls = "warn";
    } else {
      text = "not yet synced"; cls = "warn";
    }
    if (e.statusLine.textContent !== text) e.statusLine.textContent = text;
    e.statusLine.className = cls;
  }

  const busy = board.syncing;

  // maskFromRectBtn: needs image, active rect, and checkbox on.
  const hasRect = !!(board.inpaintContext
                     && board.inpaintContext.w > 0
                     && board.inpaintContext.h > 0);
  if (e.maskFromRectBtn) {
    e.maskFromRectBtn.disabled =
      !board.imageHash || !board.inpaintEnabled || !hasRect || busy;
    if (!board.imageHash)          e.maskFromRectBtn.title = "Load an image first";
    else if (!board.inpaintEnabled) e.maskFromRectBtn.title = "Enable the context rectangle first";
    else if (!hasRect)              e.maskFromRectBtn.title = "Draw the context rectangle first";
    else                            e.maskFromRectBtn.title = "Create a new mask from the current context rectangle.";
  }

  if (e.pickImageBtn) e.pickImageBtn.disabled = busy;
  if (e.clearImageBtn) e.clearImageBtn.disabled = busy;
  if (e.pickMaskBtn) e.pickMaskBtn.disabled = busy;
  if (e.kritaImportMasksBtn) e.kritaImportMasksBtn.disabled = busy;
  if (e.syncNowBtn) e.syncNowBtn.disabled = busy;

  if (e.inpaintEnabled) e.inpaintEnabled.disabled = busy;
}
"""