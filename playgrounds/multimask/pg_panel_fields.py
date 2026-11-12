"""Per-frame panel sync: image info, area count.  Equality-guarded so
a steady-state frame touches no DOM."""

PANEL_FIELDS_JS = r"""
import { board } from "./pg_state.js";

let _els = null;
function _get() {
  if (_els) return _els;
  _els = {
    imageInfo:   document.getElementById("imageInfo"),
    areaCount:   document.getElementById("areaCount"),
    statusLine:  document.getElementById("statusLine"),
  };
  return _els;
}

export function syncPanelFields() {
  const e = _get();

  if (e.imageInfo) {
    let text;
    if (!board.imageHash) {
      text = "No image loaded";
    } else {
      text = board.imageWidth + " \u00d7 " + board.imageHeight
           + "   [" + String(board.imageHash).slice(0, 8) + "\u2026]";
    }
    if (e.imageInfo.textContent !== text) e.imageInfo.textContent = text;
  }

  if (e.areaCount) {
    const t = String(board.areas.length);
    if (e.areaCount.textContent !== t) e.areaCount.textContent = t;
  }
}
"""