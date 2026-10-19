PG_PANEL_FIELDS_JS = r"""
import { board } from "./pg_core.js";

export function selectedFile() {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) return null;
  return board.files[board.selectedIdx];
}

export function _syncCardFields() {
  const scaleEl = document.getElementById("cardScaleVal");
  const rotEl = document.getElementById("cardRotVal");
  const f = selectedFile();

  if (!f) {
    if (scaleEl) {
      scaleEl.disabled = true;
      if (document.activeElement !== scaleEl && scaleEl.value !== "\u2014") scaleEl.value = "\u2014";
    }
    if (rotEl) {
      rotEl.disabled = true;
      if (document.activeElement !== rotEl && rotEl.value !== "\u2014") rotEl.value = "\u2014";
    }
    return;
  }

  if (scaleEl) {
    scaleEl.disabled = false;
    if (document.activeElement !== scaleEl) {
      const denom = board.defaultWidth > 0 ? board.defaultWidth : 1;
      const target = (f.w / denom).toFixed(2);
      if (scaleEl.value !== target) scaleEl.value = target;
    }
  }
  if (rotEl) {
    rotEl.disabled = false;
    if (document.activeElement !== rotEl) {
      const target = (f.rotation * 180 / Math.PI).toFixed(1);
      if (rotEl.value !== target) rotEl.value = target;
    }
  }
}
"""
