PANEL_FIELDS_JS = r"""
import { board, selectedMask, selectedMaskIdx } from "./pg_core.js";

let _els = null;

function _get() {
  if (_els) return _els;
  _els = {
    imageInfo:    document.getElementById("imageInfo"),
    pointsInfo:   document.getElementById("pointsInfo"),
    pointsLabel:  document.getElementById("pointsLabel"),
    maskPromptRow: document.getElementById("maskPromptRow"),
    maskPromptEl:  document.getElementById("maskPromptText"),
    maskCount:    document.getElementById("maskCount"),
    statusLine:   document.getElementById("statusLine"),
    runBtn:       document.getElementById("runBtn"),
    segAllBtn:    document.getElementById("segmentAllBtn"),
    cancelBtn:    document.getElementById("cancelBtn"),
    undoBtn:      document.getElementById("undoBtn"),
    clearPointsBtn: document.getElementById("clearPointsBtn"),
    exportBtn:    document.getElementById("exportBtn"),
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

  const selIdx = selectedMaskIdx();
  const m = selIdx >= 0 ? board.masks[selIdx] : null;

  if (e.pointsLabel) {
    let label;
    if (m)              label = "Mask " + (selIdx + 1) + " points";
    else if (board.masks.length === 0) label = "Staging points";
    else                label = "Staging points";
    if (e.pointsLabel.textContent !== label) e.pointsLabel.textContent = label;
  }

  if (e.pointsInfo) {
    let text;
    if (m) {
      const p = m.points.filter(pt =>  pt.isPositive).length;
      const n = m.points.filter(pt => !pt.isPositive).length;
      text = p + " positive \u00b7 " + n + " negative";
    } else {
      const p = board.stagingPoints.filter(pt =>  pt.isPositive).length;
      const n = board.stagingPoints.filter(pt => !pt.isPositive).length;
      text = p + " positive \u00b7 " + n + " negative";
    }
    if (e.pointsInfo.textContent !== text) e.pointsInfo.textContent = text;
  }

  // Mask prompt row: visible only when a mask is selected AND model is sam3
  if (e.maskPromptRow) {
    const show = m && board.refineModel === 'sam3';
    e.maskPromptRow.style.display = show ? "" : "none";
    if (show && e.maskPromptEl) {
      if (document.activeElement !== e.maskPromptEl) {
        const v = m.textPrompt || "";
        if (e.maskPromptEl.value !== v) e.maskPromptEl.value = v;
      }
    }
  }

  if (e.maskCount) {
    const t = board.selectedMaskIndices.length
            + " / " + board.masks.length;
    if (e.maskCount.textContent !== t) e.maskCount.textContent = t;
  }

  if (e.statusLine) {
    const flags = [];
    if (board.autoRun) flags.push("auto-run");
    const suffix = flags.length ? " \u00b7 " + flags.join(" \u00b7 ") : "";
    let text, cls = "";
    if (board.running) {
      text = "running\u2026" + suffix; cls = "ok";
    } else if (board.lastTiming != null) {
      text = "last: " + board.lastTiming.toFixed(2) + "s" + suffix;
      cls = "ok";
    } else {
      text = "idle" + suffix;
    }
    if (e.statusLine.textContent !== text) e.statusLine.textContent = text;
    e.statusLine.className = cls;
  }

  const busy = board.running;
  const hasImage = !!board.imageHash;
  const selCount = board.selectedMaskIndices.length;
  const canRefine = hasImage && !busy && selCount === 1;
  const canSegmentAll = hasImage && !busy;
  const canUndo = board.opLog.length > 0 && !busy;
  const hasMasks = selCount > 0;

  if (e.runBtn) {
    e.runBtn.textContent = busy ? "Running\u2026" : "Run";
    e.runBtn.classList.toggle("busy", busy);
    e.runBtn.disabled = !canRefine;
    e.runBtn.title = selCount === 0
      ? "Select a mask to refine, or use Segment All"
      : (selCount > 1
          ? "Select a single mask to refine"
          : "Refine mask #" + (selIdx + 1) + " via " + board.refineModel.toUpperCase());
  }
  if (e.segAllBtn) {
    e.segAllBtn.textContent = busy ? "Running\u2026" : "Segment All";
    e.segAllBtn.classList.toggle("busy", busy);
    e.segAllBtn.disabled = !canSegmentAll;
  }
  if (e.cancelBtn)     e.cancelBtn.disabled = !busy;
  if (e.undoBtn) {
    e.undoBtn.disabled = !canUndo;
    e.undoBtn.textContent = board.opLog.length > 0
      ? "Undo (" + board.opLog.length + ")"
      : "Undo";
  }
  if (e.clearPointsBtn) {
    const hasActivePoints = m
      ? m.points.length > 0
      : board.stagingPoints.length > 0;
    e.clearPointsBtn.disabled = !hasActivePoints;
  }
  if (e.exportBtn)     e.exportBtn.disabled = !hasMasks;
  if (e.exportCombBtn) e.exportCombBtn.disabled = !hasMasks;
}
"""
