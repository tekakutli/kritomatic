"""Mask stack: creation, selection.

Mirror of inpaint's `pg_masks.js` — every mutation bumps
`_masksVersion` (see that playground's docstring for the full
rationale) so the image-layer draw sig catches mutations that
move no other field.  `applyCanvasImage` also invalidates each
mask's caches so tints rebuild against the new image frame.

The mask stack's contents are always BOUNDARY-SIZED (matching
`board.imageWidth × board.imageHeight`).  Converting them to
full-canvas-sized for sync is done in `pg_sync.js` — see that
module's docstring."""

MASKS_JS = r"""
import { board, _scheduleDraw, _bumpMasksVersion } from "./pg_state.js";

function _makeMaskEntry(b64) {
  const img = new Image();
  const entry = {
    b64, img, w: 0, h: 0, loaded: false,
    _pixels: null, _tinted: null, _tintedKey: null, _bbox: null,
    points: [],
    textPrompt: "",
  };
  img.onload = () => {
    entry.w = img.width;
    entry.h = img.height;
    entry.loaded = true;
    _bumpMasksVersion();
    _scheduleDraw();
  };
  img.onerror = () => {
    _bumpMasksVersion();
    _scheduleDraw();
  };
  img.src = "data:image/png;base64," + b64;
  return entry;
}

export function setMasks(b64List) {
  board.masks = b64List.map(b64 => _makeMaskEntry(b64));
  board.selectedMaskIndices = b64List.length > 0 ? [0] : [];
  board.hoveredMaskIdx = -1;
  _bumpMasksVersion();
  _scheduleDraw();
}

export function addMaskFromB64(b64) {
  board.masks.push(_makeMaskEntry(b64));
  _bumpMasksVersion();
  _scheduleDraw();
  return board.masks.length - 1;
}

export function replaceMaskAt(idx, b64) {
  if (idx < 0 || idx >= board.masks.length) return;
  board.masks[idx] = _makeMaskEntry(b64);
  board.hoveredMaskIdx = -1;
  _bumpMasksVersion();
  _scheduleDraw();
}

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