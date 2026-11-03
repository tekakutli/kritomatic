"""The mask stack: creation, selection, per-mask points, per-mask
prompt, and staging points.

Every mutation calls `_scheduleDraw()`.  There is no `clearMasks` export
because nothing outside this file ever cleared the stack — `setMasks([])`
does the same thing and is what the panel's "Clear masks" button
actually invokes.  `setImage` and `clearImage` (in `pg_state.js`) reset
the array directly to keep `pg_state` a leaf."""

MASKS_JS = r"""
import { board, _scheduleDraw } from "./pg_state.js";

/* ============================================================
   Mask entries
   ============================================================ */

function _makeMaskEntry(b64, points, textPrompt, regionCanvas) {
  const img = new Image();
  const entry = {
    b64, img, w: 0, h: 0, loaded: false,
    points: points ? points.map(p => ({ x: p.x, y: p.y, isPositive: p.isPositive })) : [],
    textPrompt: textPrompt != null ? textPrompt : "",
    regionCanvas: regionCanvas || null,
    _regionEdgePoints: null,   // cached flat [x1,y1,x2,y2,...] of edge pixels
    _regionChains: null,       // cached traced polylines for even dot spacing
    _pixels: null, _tinted: null, _tintedKey: null, _bbox: null,
  };
  img.onload = () => {
    entry.w = img.width; entry.h = img.height; entry.loaded = true;
    _scheduleDraw();
  };
  img.src = "data:image/png;base64," + b64;
  return entry;
}

export function setMasks(b64List) {
  board.masks = b64List.map(b64 => _makeMaskEntry(b64, [], "", null));
  board.selectedMaskIndices = b64List.length > 0 ? [0] : [];
  board.hoveredMaskIdx = -1;
  board.lassoPoints = [];
  board.freehandPoints = [];
  board.freehandActive = false;
  board.freehandIsErase = false;
  board.regionStrokeActive = false;
  _scheduleDraw();
}

/* Append one mask to the existing stack. Used by the "import masks
   from Krita" flow, which layers imported masks on top of whatever
   is already present rather than replacing. Returns the new index so
   a caller can select it. */
export function addMaskFromB64(b64, options) {
  const opts = options || {};
  const entry = _makeMaskEntry(
    b64,
    opts.points || [],
    opts.textPrompt || "",
    opts.regionCanvas || null,
  );
  board.masks.push(entry);
  _scheduleDraw();
  return board.masks.length - 1;
}

export function replaceMaskAt(idx, b64, points) {
  if (idx < 0 || idx >= board.masks.length) return;
  const old = board.masks[idx];
  const prevPoints = (points !== undefined) ? points : old.points;
  board.masks[idx] = _makeMaskEntry(b64, prevPoints, old.textPrompt, old.regionCanvas);
  board.hoveredMaskIdx = -1;
  _scheduleDraw();
}

export function restoreMasks(maskSnapshots, selection) {
  board.masks = maskSnapshots.map(m =>
    _makeMaskEntry(m.b64, m.points, m.textPrompt, null));
  board.selectedMaskIndices = selection.slice();
  board.hoveredMaskIdx = -1;
  board.lassoPoints = [];
  board.freehandPoints = [];
  board.freehandActive = false;
  board.freehandIsErase = false;
  board.regionStrokeActive = false;
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
  board.lassoPoints = [];
  const pos = board.selectedMaskIndices.indexOf(idx);
  if (pos >= 0) board.selectedMaskIndices.splice(pos, 1);
  else          board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function addMaskToSelection(idx) {
  if (idx < 0 || idx >= board.masks.length) return;
  if (board.selectedMaskIndices.indexOf(idx) >= 0) return;
  board.lassoPoints = [];
  board.selectedMaskIndices.push(idx);
  board.selectedMaskIndices.sort((a, b) => a - b);
  _scheduleDraw();
}

export function replaceMaskSelection(idx) {
  board.lassoPoints = [];
  if (idx < 0 || idx >= board.masks.length) {
    board.selectedMaskIndices = [];
  } else {
    board.selectedMaskIndices = [idx];
  }
  _scheduleDraw();
}

export function clearMaskSelection() {
  board.lassoPoints = [];
  board.selectedMaskIndices = [];
  _scheduleDraw();
}

export function selectAllMasks() {
  board.lassoPoints = [];
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

/* ============================================================
   Staging points
   ============================================================ */

export function addStagingPoint(x, y, isPositive) {
  if (x < 0 || y < 0 || x >= board.imageWidth || y >= board.imageHeight) return false;
  board.stagingPoints.push({ x, y, isPositive });
  return true;
}

export function clearStagingPoints() {
  if (board.stagingPoints.length === 0) return false;
  board.stagingPoints = [];
  return true;
}

export function hitTestStagingPoint(ix, iy, tolerancePx) {
  const tol2 = tolerancePx * tolerancePx;
  for (let i = board.stagingPoints.length - 1; i >= 0; i--) {
    const p = board.stagingPoints[i];
    const dx = p.x - ix, dy = p.y - iy;
    if (dx * dx + dy * dy <= tol2) return i;
  }
  return -1;
}

export function removeStagingPoint(idx) {
  if (idx < 0 || idx >= board.stagingPoints.length) return false;
  board.stagingPoints.splice(idx, 1);
  return true;
}

/* ============================================================
   Per-mask points
   ============================================================ */

export function addPointToSelectedMask(x, y, isPositive) {
  const m = selectedMask();
  if (!m) return false;
  if (x < 0 || y < 0 || x >= board.imageWidth || y >= board.imageHeight) return false;
  m.points.push({ x, y, isPositive });
  return true;
}

export function clearPointsOnSelectedMask() {
  const m = selectedMask();
  if (!m) return false;
  if (m.points.length === 0) return false;
  m.points = [];
  return true;
}

export function hitTestPointOnSelectedMask(ix, iy, tolerancePx) {
  const m = selectedMask();
  if (!m) return -1;
  const tol2 = tolerancePx * tolerancePx;
  for (let i = m.points.length - 1; i >= 0; i--) {
    const p = m.points[i];
    const dx = p.x - ix, dy = p.y - iy;
    if (dx * dx + dy * dy <= tol2) return i;
  }
  return -1;
}

export function removePointFromSelectedMask(idx) {
  const m = selectedMask();
  if (!m) return false;
  if (idx < 0 || idx >= m.points.length) return false;
  m.points.splice(idx, 1);
  return true;
}

export function setMaskPrompt(idx, text) {
  if (idx < 0 || idx >= board.masks.length) return;
  board.masks[idx].textPrompt = String(text || "");
}
"""
