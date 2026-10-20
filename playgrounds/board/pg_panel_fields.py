"""
pg_panel_fields.py — per-card field semantics.

ES module.  Exports:

    selectedFile       return the currently-selected card, or null
    _syncCardFields    push the selected card's scale and rotation
                       into the two scrub inputs (equality-guarded)

Two per-card scrub fields follow the selection: **Scale** and
**Rotation**.  Both are disabled with no card selected, and reflect
the selected card's own values otherwise.

`_syncCardFields` is called once per `draw()` frame.  Each write is
equality-guarded so the caret doesn't jump when the user is typing
into a field mid-drag.  This is the same pattern the pair and
text_board playgrounds use for their selection-following fields.
"""

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
