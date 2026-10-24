"""The mask stack: creation, selection.

Every mutation calls `_scheduleDraw()`.  There is no per-mask
metadata — inpaint masks are just a base64 PNG plus selection state.
`setImage` and `clearImage` (in `pg_state.js`) reset the array
directly to keep `pg_state` a leaf."""

MASKS_JS = r"""
import { board, _scheduleDraw } from "./pg_state.js";

/* ============================================================
   Mask entries
   ============================================================ */

function _makeMaskEntry(b64) {
  const img = new Image();
  const entry = {
    b64, img, w: 0, h: 0, loaded: false,
    _pixels: null, _tinted: null, _tintedKey: null,
  };
  img.onload = () => {
    entry.w = img.width; entry.h = img.height; entry.loaded = true;
    _scheduleDraw();
  };
  img.src = "data:image/png;base64," + b64;
  return entry;
}

export function setMasks(b64List) {
  board.masks = b64List.map(b64 => _makeMaskEntry(b64));
  board.selectedMaskIndices = b64List.length > 0 ? [0] : [];
  board.hoveredMaskIdx = -1;
  _scheduleDraw();
}

/* Append one mask to the existing stack. Used by the "import masks
   from Krita" flow and the "pick mask file" flow. Returns the new
   index so a caller can select it. */
export function addMaskFromB64(b64) {
  board.masks.push(_makeMaskEntry(b64));
  _scheduleDraw();
  return board.masks.length - 1;
}

export function replaceMaskAt(idx, b64) {
  if (idx < 0 || idx >= board.masks.length) return;
  board.masks[idx] = _makeMaskEntry(b64);
  board.hoveredMaskIdx = -1;
  _scheduleDraw();
}

/* ============================================================
   Selection
   ============================================================ */

export function isMaskSelected(idx) {
  return board.selectedMaskIndices.indexOf(idx) >= 0;
}

export function toggleMaskSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  const pos = board.selectedMaskIndices.indexOf(idx);
  if (pos >= 0) board.selectedMaskIndices.splice(pos, 1);
  else          board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function addMaskToSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  if (board.selectedMaskIndices.indexOf(idx) >= 0) return;
  board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function replaceMaskSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) {
    board.selectedMaskIndices = [];
  } else {
    board.selectedMaskIndices = [idx];
  }
  _scheduleDraw();
}

export function clearMaskSelection() {
  board.selectedMaskIndices = [];
  _scheduleDraw();
}

export function selectAllMasks() {
  board.selectedMaskIndices = board.masks.map((_, i) => i);
  _scheduleDraw();
}

export function selectedMaskIdx() {
  return board.selectedMaskIndices.length === 1
    ? board.selectedMaskIndices[0] : -1;
}

export function selectedMask() {
  const idx = selectedMaskIdx();
  return idx >= 0 ? board.masks[idx] : null;
}
"""
