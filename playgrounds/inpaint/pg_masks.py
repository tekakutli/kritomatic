"""The mask stack: creation, selection.

Every mutation calls `_scheduleDraw()`.  There is no per-mask
metadata — inpaint masks are just a base64 PNG plus selection state.
`setImage` and `clearImage` (in `pg_state.js`) reset the array
directly to keep `pg_state` a leaf.

MASKS VERSION BUMPS
===================
`_bumpMasksVersion()` (from `pg_state.js`) is called at every point
where a mask's *visible contribution* changes without moving any
other field that appears in the image-layer draw sig:

    · `img.onload` — `loaded` flips false→true, w/h become real.
      The mask first becomes drawable here.  `masks.length` was
      already incremented when the entry was pushed; `loaded` is not
      in the sig (only the counter is).
    · `setMasks(b64List)` — entry identity changes; length and
      selection are unchanged in the common refresh case.
    · `replaceMaskAt(idx, b64)` — same shape, same-length replace.

`addMaskFromB64` bumps indirectly (`masks.length` in the sig
changes), and `delete` (via `splice` in `pg_panel_lists.js` /
`pg_panel_bindings.js`) bumps via `masks.length`.  Both are covered
by the length term, so no explicit call is needed there — but the
counter is incremented anyway for defence in depth.

Deleting a mask shifts every later mask's `maskColor(i)` (the tint
colour is a function of index).  That is caught by `masks.length`
in the sig, which changes on any delete.

`applyCanvasImage` invalidates every mask's `_pixels`/`_tinted`
caches; it bumps the counter inline in `pg_state.js`.  That is what
makes the tinted overlay rebuild against the new image frame even
when `masks.length`, selection, and hover are unchanged."""

MASKS_JS = r"""
import {
  board, view, _scheduleDraw, _rotateMask, _bumpMasksVersion,
} from "./pg_state.js";

/* ============================================================
   Mask entries
   ============================================================ */

function _makeMaskEntry(b64) {
  const img = new Image();
  const entry = {
    b64, img, w: 0, h: 0, loaded: false,
    rotatedCanvas: null, rotatedW: 0, rotatedH: 0,
    _pixels: null, _tinted: null, _tintedKey: null, _bbox: null,
  };
  img.onload = () => {
    entry.w = img.width;
    entry.h = img.height;
    entry.loaded = true;
    // Rotation state at the moment the mask loads; if the user
    // rotates after this point, `_applyRotation` re-renders the
    // image layer and the tinted overlay rebuilds via the usual
    // per-frame draw path.
    _rotateMask(entry, view.rotation);
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

/* Append one mask to the existing stack. Used by the "import masks
   from Krita" flow and the "pick mask file" flow. Returns the new
   index so a caller can select it. */
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