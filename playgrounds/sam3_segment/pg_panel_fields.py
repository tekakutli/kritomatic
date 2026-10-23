PANEL_FIELDS_JS = r"""
import { board } from "./pg_core.js";

let _els = null;

function _get() {
  if (_els) return _els;
  _els = {
    imageInfo:  document.getElementById("imageInfo"),
    pointsInfo: document.getElementById("pointsInfo"),
    statusLine: document.getElementById("statusLine"),
    runBtn:     document.getElementById("runBtn"),
    cancelBtn:  document.getElementById("cancelBtn"),
  };
  return _els;
}

export function syncPanelFields() {
  const e = _get();
  if (e.imageInfo) {
    let text;
    if (!board.imageHash) {
      text = "No image loaded — drop one on the canvas";
    } else {
      text = (board.imageDataUrl ? "" : "")  // filename already set by pick/drop
           + board.imageWidth + " \u00d7 " + board.imageHeight
           + "   [" + board.imageHash.slice(0, 8) + "\u2026]";
    }
    if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
  }
  if (e.pointsInfo) {
    const p = board.positive.length, n = board.negative.length;
    const text = p + " positive \u00b7 " + n + " negative";
    if (e.pointsInfo.textContent !== text) e.pointsInfo.textContent = text;
  }
  if (e.statusLine) {
    let text, cls = "";
    if (board.running) {
      text = "running\u2026"; cls = "ok";
    } else if (board.lastTiming != null) {
      text = "last run: " + board.lastTiming.toFixed(2) + "s \u00b7 "
           + board.masks.length + " mask(s)"; cls = "ok";
    } else {
      text = "idle";
    }
    if (e.statusLine.textContent !== text) e.statusLine.textContent = text;
    e.statusLine.className = cls;
  }
  if (e.runBtn) {
    e.runBtn.textContent = board.running ? "Running\u2026" : "Run";
    e.runBtn.classList.toggle("busy", board.running);
    e.runBtn.disabled = !board.imageHash || board.running;
  }
  if (e.cancelBtn) {
    e.cancelBtn.disabled = !board.running;
  }
}
"""
