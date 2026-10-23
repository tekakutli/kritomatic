PANEL_FIELDS_JS = r"""
import { board } from "./pg_core.js";

let _els = null;

function _get() {
  if (_els) return _els;
  _els = {
    imageInfo:   document.getElementById("imageInfo"),
    pointsInfo:  document.getElementById("pointsInfo"),
    maskCount:   document.getElementById("maskCount"),
    statusLine:  document.getElementById("statusLine"),
    runBtn:      document.getElementById("runBtn"),
    segAllBtn:   document.getElementById("segmentAllBtn"),
    cancelBtn:   document.getElementById("cancelBtn"),
    exportBtn:   document.getElementById("exportBtn"),
    exportCombBtn: document.getElementById("exportCombinedBtn"),
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

  if (e.pointsInfo) {
    const p = board.positive.length, n = board.negative.length;
    const text = p + " positive \u00b7 " + n + " negative";
    if (e.pointsInfo.textContent !== text) e.pointsInfo.textContent = text;
  }

  if (e.maskCount) {
    const t = board.selectedMaskIndices.length
            + " / " + board.masks.length;
    if (e.maskCount.textContent !== t) e.maskCount.textContent = t;
  }

  if (e.statusLine) {
    let text, cls = "";
    if (board.running) {
      text = "running\u2026"; cls = "ok";
    } else if (board.lastTiming != null) {
      text = "last run: " + board.lastTiming.toFixed(2) + "s \u00b7 "
           + board.masks.length + " mask(s)";
      if (board.selectedMaskIndices.length > 0
          && board.selectedMaskIndices.length !== board.masks.length) {
        text += " \u00b7 " + board.selectedMaskIndices.length + " selected";
      }
      cls = "ok";
    } else {
      text = "idle";
    }
    if (e.statusLine.textContent !== text) e.statusLine.textContent = text;
    e.statusLine.className = cls;
  }

  const busy = board.running;
  const hasImage = !!board.imageHash;
  const hasMasks = board.selectedMaskIndices.length > 0;

  if (e.runBtn) {
    e.runBtn.textContent = busy ? "Running\u2026" : "Run";
    e.runBtn.classList.toggle("busy", busy);
    e.runBtn.disabled = !hasImage || busy;
  }
  if (e.segAllBtn) {
    e.segAllBtn.textContent = busy ? "Running\u2026" : "Segment All";
    e.segAllBtn.classList.toggle("busy", busy);
    e.segAllBtn.disabled = !hasImage || busy;
  }
  if (e.cancelBtn) {
    e.cancelBtn.disabled = !busy;
  }
  if (e.exportBtn) {
    e.exportBtn.disabled = !hasMasks;
  }
  if (e.exportCombBtn) {
    e.exportCombBtn.disabled = !hasMasks;
  }
}
"""
