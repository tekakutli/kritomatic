"""Per-frame panel sync: image info, mask count, mask-from-rect
enablement, context-rectangle checkbox and hint visibility, sync
status line.

Same pattern as inpaint minus rotation display.

`#statusLine` shows sync state: `Synced HH:MM:SS · mask K/N ·
context on/off`, or `Syncing…` / `Sync failed: …` / `No mask
selected · operates on the alpha-content region`."""

PANEL_FIELDS_JS = r"""
import { board, view } from "./pg_state.js";
import { selectedMaskIdx } from "./pg_masks.js";

let _els = null;

function _get() {
  if (_els) return _els;
  _els = {
    imageInfo:       document.getElementById("imageInfo"),
    maskCount:       document.getElementById("maskCount"),
    statusLine:      document.getElementById("statusLine"),
    contextEnabled:  document.getElementById("contextEnabled"),
    contextHint:     document.getElementById("contextHint"),
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
      text = "No image loaded \u2014 load a workflow in the docker to pull @thecanvas";
    } else if (board.boundary.w !== board.fullImageWidth
            || board.boundary.h !== board.fullImageHeight) {
      text = "Content " + board.boundary.w + " \u00d7 " + board.boundary.h
           + "   (of " + board.fullImageWidth + " \u00d7 " + board.fullImageHeight + ")"
           + "   [" + board.imageHash.slice(0, 8) + "\u2026]";
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

  if (e.contextEnabled && document.activeElement !== e.contextEnabled) {
    e.contextEnabled.checked = !!board.contextEnabled;
  }
  if (e.contextHint) {
    const want = board.contextEnabled ? "block" : "none";
    if (e.contextHint.style.display !== want) {
      e.contextHint.style.display = want;
    }
  }

  if (e.statusLine) {
    let text, cls = "";
    if (board.syncing) {
      text = "syncing\u2026"; cls = "ok";
    } else if (board.syncError) {
      text = "sync failed: " + board.syncError; cls = "bad";
    } else if (board.lastSync) {
      const idx = selectedMaskIdx();
      const maskLabel = idx >= 0
        ? String(idx + 1)
        : (board.selectedMaskIndices.length > 1
           ? board.selectedMaskIndices.length + " selected"
           : "none");
      const ctxLabel = board.contextEnabled ? "on" : "off";
      text = "synced " + _fmtTime(board.lastSync)
           + " · mask " + maskLabel
           + " · context " + ctxLabel;
      cls = "ok";
    } else if (!board.imageHash) {
      text = "idle"; cls = "";
    } else if (board.selectedMaskIndices.length === 0) {
      text = "No mask selected · operates on the alpha-content region"; cls = "warn";
    } else {
      text = "not yet synced"; cls = "warn";
    }
    if (e.statusLine.textContent !== text) e.statusLine.textContent = text;
    e.statusLine.className = cls;
  }

  const busy = board.syncing;

  const hasRect = !!(board.context
                     && board.context.w > 0
                     && board.context.h > 0);
  if (e.maskFromRectBtn) {
    e.maskFromRectBtn.disabled =
      !board.imageHash || !board.contextEnabled || !hasRect || busy;
    if (!board.imageHash)           e.maskFromRectBtn.title = "Load an image first";
    else if (!board.contextEnabled) e.maskFromRectBtn.title = "Enable the context rectangle first";
    else if (!hasRect)              e.maskFromRectBtn.title = "Draw the context rectangle first";
    else                            e.maskFromRectBtn.title = "Create a new mask from the current context rectangle.";
  }

  if (e.kritaImportMasksBtn) e.kritaImportMasksBtn.disabled = busy;
  if (e.contextEnabled)      e.contextEnabled.disabled = busy;
}
"""