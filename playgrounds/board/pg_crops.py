"""
pg_crops.py — crop regions on the board.

ES module.  A crop is a rotated rectangle in *source-document pixel
space*, attached to one card.  It stays glued to the same region of
the document when the card is moved, resized, or rotated on the
board, because every render and every hit test converts through the
card's doc↔board transform.

Exports:

    crops, cropState           the model
    drawCropsForFile(file)     called from pg_view_board per card
    handleCropMouseDown        returns true if it consumed the event
    handleCropMouseMove        same
    handleCropMouseUp          same
    cropCursorAt               the CSS cursor to show, or null
    createCropForSelectedFile  "New crop"
    createCropFile             "Create crop file" (POSTs to server)
    deleteSelectedCrop         used by the Delete key
    syncCropList               rebuilds the panel's crop list,
                               grouped by source file

DEFAULT CROP
============
`createCropForSelectedFile` requires a selected file whose document
dimensions are known (which is to say, it must have been refreshed).
The default crop is a centred rectangle at 50% of the document's
width and height, rotation 0.

HIT TESTING IS SCOPED TO THE SELECTED FILE
==========================================
`cropHitTest` and the handle hit tests only consider crops whose
`srcPath` matches `board.files[board.selectedIdx].path`.  Clicking
a crop selects both the crop and its source file, so the rule is
self-consistent: once you've selected the crop you want, you can
drag it; click anywhere else and it deselects.

CROP LIST IS GROUPED BY SOURCE
==============================
The panel's crop list renders one group per source file, in the
same order as the Files list below, with the source's colour swatch
and name as the group header.  This makes it possible to tell at a
glance which file a crop belongs to.  The header of the currently-
selected file is highlighted (same cyan as a selected row); clicking
any header selects that file on the board.

A crop whose source file is no longer on the board is pruned at the
top of `syncCropList` — there is no way to keep a crop that has no
file to belong to, and leaving an orphan would produce a group
header for a file that isn't in the Files list.

AFFINE CONVENTION
=================
The (a..f) matrix passed to /krita/crop_to_file follows the same
convention as `pg_core.computeProjectionRegions`:

    tx = a*sx + c*sy + e
    ty = b*sx + d*sy + f

For a crop at doc centre (cx, cy), dimensions (w, h), rotation θ,
the straightened crop maps to a w × h target by rotating the whole
source by -θ around the crop centre and translating to target
centre:

    a =  cos θ        b = -sin θ
    c =  sin θ        d =  cos θ
    e = -cos θ * cx - sin θ * cy + w/2
    f =  sin θ * cx - cos θ * cy + h/2

The crop's local "up" edge midpoint lands at (w/2, 0) in the target
(top-centre), and its local "right" edge midpoint lands at (w, h/2)
(right-centre), for any θ.

PASTE SUCCESS IS CHECKED EXPLICITLY
===================================
`/krita/crop_to_file` returns `{success, successful, failed,
results, target_document}` — the same shape as the batch-paste
response, so `success` is the *transport* success, not the
*command* success.  `createCropFile` therefore checks
`successful > 0` (there is exactly one transform in a crop), and
surfaces the first per-command error message when it is 0.
Without this, a failed paste would still show "created" on the
status line, which is misleading given that the user just watched
Krita open an empty document.
"""

CROPS_JS = r"""
import {
  board, ctx, b2s, s2b, _rotateOffset, bringToTop,
} from "./pg_core.js";
import { draw } from "./pg_dispatch.js";
import { syncFileList } from "./pg_panel_lists.js";
import { flashStatus } from "./pgkit/panel_core.js";

// ============================================================================
//  State
// ============================================================================

export const crops = [];
export const cropState = {
  selectedIdx: -1,
  drag: null,
  _mouseSx: 0,
  _mouseSy: 0,
};

const DRAG_THRESHOLD = 3;
const CORNER_HIT     = 8;
const EDGE_HIT       = 7;
const ROTATE_HIT     = 10;
const ROTATE_OFFSET  = 24;
const MIN_DOC_DIM    = 20;

const CORNER_SIGNS = [[-1, -1], [1, -1], [1, 1], [-1, 1]];
const EDGE_SIGNS   = [[0, -1], [1, 0], [0, 1], [-1, 0]];

// ============================================================================
//  Lookups and transforms
// ============================================================================

function _fileByPath(path) {
  for (let i = 0; i < board.files.length; i++) {
    if (board.files[i].path === path) return board.files[i];
  }
  return null;
}

function _selectedFile() {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) return null;
  return board.files[board.selectedIdx];
}

function _fileCenter(file) {
  return [file.x + file.w / 2, file.y + file.h / 2];
}

export function docToBoard(file, dx, dy) {
  if (!file.docWidth || !file.docHeight) return [0, 0];
  const ux = (dx / file.docWidth  - 0.5) * file.w;
  const uy = (dy / file.docHeight - 0.5) * file.h;
  const [rx, ry] = _rotateOffset(ux, uy, file.rotation);
  const [fcx, fcy] = _fileCenter(file);
  return [fcx + rx, fcy + ry];
}

export function boardToDoc(file, bx, by) {
  if (!file.docWidth || !file.docHeight) return [0, 0];
  const [fcx, fcy] = _fileCenter(file);
  const [ux, uy] = _rotateOffset(bx - fcx, by - fcy, -file.rotation);
  return [
    (ux / file.w + 0.5) * file.docWidth,
    (uy / file.h + 0.5) * file.docHeight,
  ];
}

// ============================================================================
//  Crop geometry (doc space / screen space)
// ============================================================================

function _cropCornerDoc(crop, corner) {
  const [sx, sy] = CORNER_SIGNS[corner];
  const [ox, oy] = _rotateOffset(
    sx * crop.w / 2, sy * crop.h / 2, crop.rotation
  );
  return [crop.cx + ox, crop.cy + oy];
}

function _cropEdgeDoc(crop, edge) {
  const [sx, sy] = EDGE_SIGNS[edge];
  const [ox, oy] = _rotateOffset(
    sx * crop.w / 2, sy * crop.h / 2, crop.rotation
  );
  return [crop.cx + ox, crop.cy + oy];
}

function cropCornerScreen(file, crop, corner) {
  const [dx, dy] = _cropCornerDoc(crop, corner);
  return b2s(...docToBoard(file, dx, dy));
}

function cropEdgeScreen(file, crop, edge) {
  const [dx, dy] = _cropEdgeDoc(crop, edge);
  return b2s(...docToBoard(file, dx, dy));
}

function cropCenterScreen(file, crop) {
  return b2s(...docToBoard(file, crop.cx, crop.cy));
}

function cropRotateHandleScreen(file, crop) {
  const [csx, csy] = cropCenterScreen(file, crop);
  const [esx, esy] = cropEdgeScreen(file, crop, 0);  // top
  const dx = esx - csx, dy = esy - csy;
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return [csx, csy - ROTATE_OFFSET];
  return [
    esx + (dx / len) * ROTATE_OFFSET,
    esy + (dy / len) * ROTATE_OFFSET,
  ];
}

// ============================================================================
//  Hit testing (scoped to the selected file)
// ============================================================================

function _activeCrop() {
  const sel = _selectedFile();
  if (!sel) return null;
  if (cropState.selectedIdx < 0 || cropState.selectedIdx >= crops.length) return null;
  const crop = crops[cropState.selectedIdx];
  if (crop.srcPath !== sel.path) return null;
  return { crop, file: sel };
}

export function cropHitTest(sx, sy) {
  const sel = _selectedFile();
  if (!sel) return -1;
  const [bx, by] = s2b(sx, sy);
  for (let i = crops.length - 1; i >= 0; i--) {
    const crop = crops[i];
    if (crop.srcPath !== sel.path) continue;
    const [dx, dy] = boardToDoc(sel, bx, by);
    const [rx, ry] = _rotateOffset(
      dx - crop.cx, dy - crop.cy, -crop.rotation
    );
    if (Math.abs(rx) <= crop.w / 2 && Math.abs(ry) <= crop.h / 2) return i;
  }
  return -1;
}

function _cornerHit() {
  const a = _activeCrop();
  if (!a) return null;
  for (let c = 0; c < 4; c++) {
    const [hx, hy] = cropCornerScreen(a.file, a.crop, c);
    if (Math.hypot(cropState._mouseSx - hx, cropState._mouseSy - hy) <= CORNER_HIT) {
      return { corner: c };
    }
  }
  return null;
}

function _edgeHit() {
  const a = _activeCrop();
  if (!a) return null;
  for (let e = 0; e < 4; e++) {
    const [hx, hy] = cropEdgeScreen(a.file, a.crop, e);
    if (Math.hypot(cropState._mouseSx - hx, cropState._mouseSy - hy) <= EDGE_HIT) {
      return { edge: e };
    }
  }
  return null;
}

function _rotateHit() {
  const a = _activeCrop();
  if (!a) return false;
  const [hx, hy] = cropRotateHandleScreen(a.file, a.crop);
  return Math.hypot(cropState._mouseSx - hx, cropState._mouseSy - hy) <= ROTATE_HIT;
}

export function cropCursorAt(sx, sy) {
  cropState._mouseSx = sx; cropState._mouseSy = sy;
  if (_rotateHit()) return "grab";
  if (_cornerHit()) return "nwse-resize";
  if (_edgeHit())   return "move";
  if (cropHitTest(sx, sy) >= 0) return "move";
  return null;
}

// ============================================================================
//  Drawing
// ============================================================================

export function drawCropsForFile(file) {
  const sel = _selectedFile();
  for (let i = 0; i < crops.length; i++) {
    const crop = crops[i];
    if (crop.srcPath !== file.path) continue;
    const isActive = (i === cropState.selectedIdx) && (sel === file);
    _drawCrop(file, crop, isActive);
  }
}

function _drawCrop(file, crop, isActive) {
  if (!file.docWidth || !file.docHeight) return;

  // Crop quad in screen space
  const pts = [];
  for (let c = 0; c < 4; c++) {
    const [dx, dy] = _cropCornerDoc(crop, c);
    pts.push(b2s(...docToBoard(file, dx, dy)));
  }

  // File quad in screen space (for the scrim)
  const [fcx, fcy] = _fileCenter(file);
  const filePts = [];
  for (const [sx, sy] of CORNER_SIGNS) {
    const [ox, oy] = _rotateOffset(sx * file.w / 2, sy * file.h / 2, file.rotation);
    filePts.push(b2s(fcx + ox, fcy + oy));
  }

  // Scrim: file's quad minus crop's quad, via even-odd fill
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(filePts[0][0], filePts[0][1]);
  for (let c = 1; c < 4; c++) ctx.lineTo(filePts[c][0], filePts[c][1]);
  ctx.closePath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let c = 1; c < 4; c++) ctx.lineTo(pts[c][0], pts[c][1]);
  ctx.closePath();
  ctx.fillStyle = "rgba(0, 0, 0, 0.55)";
  ctx.fill("evenodd");
  ctx.restore();

  // Crop border
  ctx.save();
  ctx.beginPath();
  ctx.moveTo(pts[0][0], pts[0][1]);
  for (let c = 1; c < 4; c++) ctx.lineTo(pts[c][0], pts[c][1]);
  ctx.closePath();
  ctx.strokeStyle = isActive
    ? "rgba(0, 229, 255, 1.00)"
    : "rgba(0, 229, 255, 0.55)";
  ctx.lineWidth = isActive ? 2.2 : 1.4;
  ctx.setLineDash(isActive ? [] : [6, 4]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();

  if (!isActive) return;

  // Corner handles
  for (let c = 0; c < 4; c++) {
    const [hx, hy] = pts[c];
    ctx.beginPath();
    ctx.arc(hx, hy, 5, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0, 229, 255, 0.95)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.4;
    ctx.stroke();
  }

  // Edge handles
  for (let e = 0; e < 4; e++) {
    const [hx, hy] = cropEdgeScreen(file, crop, e);
    ctx.beginPath();
    ctx.arc(hx, hy, 4, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(0, 229, 255, 0.75)";
    ctx.fill();
    ctx.strokeStyle = "#0a0e14";
    ctx.lineWidth = 1.2;
    ctx.stroke();
  }

  // Rotate handle + tether
  const [rhx, rhy] = cropRotateHandleScreen(file, crop);
  const [tesx, tesy] = cropEdgeScreen(file, crop, 0);
  ctx.beginPath();
  ctx.moveTo(tesx, tesy);
  ctx.lineTo(rhx, rhy);
  ctx.strokeStyle = "rgba(0, 229, 255, 0.55)";
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.beginPath();
  ctx.arc(rhx, rhy, 6, 0, Math.PI * 2);
  ctx.fillStyle = "rgba(140, 225, 255, 1.0)";
  ctx.fill();
  ctx.strokeStyle = "#0a0e14";
  ctx.lineWidth = 1.4;
  ctx.stroke();
}

// ============================================================================
//  Mouse handlers — return true if the crop consumed the event
// ============================================================================

export function handleCropMouseDown(sx, sy, e) {
  cropState._mouseSx = sx; cropState._mouseSy = sy;

  // Rotate handle
  if (_rotateHit()) {
    const a = _activeCrop();
    const [csx, csy] = cropCenterScreen(a.file, a.crop);
    cropState.drag = {
      mode: "rotate",
      startSx: sx, startSy: sy,
      centerSx: csx, centerSy: csy,
      fileRotation: a.file.rotation,
      moved: false,
    };
    return true;
  }

  // Corner handle
  const ch = _cornerHit();
  if (ch) {
    const a = _activeCrop();
    const [ax, ay] = _cropCornerDoc(a.crop, (ch.corner + 2) % 4);
    cropState.drag = {
      mode: "resize-corner",
      corner: ch.corner,
      anchorX: ax, anchorY: ay,
      startSx: sx, startSy: sy,
      moved: false,
    };
    return true;
  }

  // Edge handle
  const eh = _edgeHit();
  if (eh) {
    const a = _activeCrop();
    const [ax, ay] = _cropEdgeDoc(a.crop, (eh.edge + 2) % 4);
    cropState.drag = {
      mode: "resize-edge",
      edge: eh.edge,
      anchorX: ax, anchorY: ay,
      startW: a.crop.w, startH: a.crop.h,
      startSx: sx, startSy: sy,
      moved: false,
    };
    return true;
  }

  // Body → select + move
  const hit = cropHitTest(sx, sy);
  if (hit >= 0) {
    cropState.selectedIdx = hit;
    const crop = crops[hit];
    const file = _fileByPath(crop.srcPath);
    if (file) {
      for (let i = 0; i < board.files.length; i++) {
        if (board.files[i] === file) { board.selectedIdx = i; break; }
      }
    }
    cropState.drag = {
      mode: "move",
      startSx: sx, startSy: sy,
      startCx: crop.cx, startCy: crop.cy,
      moved: false,
    };
    syncCropList();
    draw();
    return true;
  }

  // Click elsewhere → deselect crop, let the rest of the handler run
  if (cropState.selectedIdx >= 0) {
    cropState.selectedIdx = -1;
    syncCropList();
    draw();
  }
  return false;
}

export function handleCropMouseMove(sx, sy, e) {
  const drag = cropState.drag;
  if (!drag) return false;
  const a = _activeCrop();
  if (!a) { cropState.drag = null; return false; }
  const { crop, file } = a;

  const dx = sx - drag.startSx, dy = sy - drag.startSy;
  if (!drag.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) drag.moved = true;
  if (!drag.moved) return true;

  if (drag.mode === "move") {
    const [dsx0, dsy0] = boardToDoc(file, ...s2b(drag.startSx, drag.startSy));
    const [dsx1, dsy1] = boardToDoc(file, ...s2b(sx, sy));
    crop.cx = drag.startCx + (dsx1 - dsx0);
    crop.cy = drag.startCy + (dsy1 - dsy0);

  } else if (drag.mode === "rotate") {
    const mx = sx - drag.centerSx, my = sy - drag.centerSy;
    let upAngle = Math.atan2(mx, -my);
    let target = upAngle - drag.fileRotation;
    if (e.shiftKey) {
      const snap = 15 * Math.PI / 180;
      target = Math.round(target / snap) * snap;
    }
    crop.rotation = target;

  } else if (drag.mode === "resize-corner") {
    const [mx, my] = boardToDoc(file, ...s2b(sx, sy));
    const [lx, ly] = _rotateOffset(
      mx - drag.anchorX, my - drag.anchorY, -crop.rotation
    );
    const [sxs, sys] = CORNER_SIGNS[drag.corner];
    let newW = Math.max(MIN_DOC_DIM, sxs * lx);
    let newH = Math.max(MIN_DOC_DIM, sys * ly);
    const [dcx, dcy] = _rotateOffset(
      sxs * newW / 2, sys * newH / 2, crop.rotation
    );
    crop.cx = drag.anchorX + dcx;
    crop.cy = drag.anchorY + dcy;
    crop.w = newW;
    crop.h = newH;

  } else if (drag.mode === "resize-edge") {
    const [mx, my] = boardToDoc(file, ...s2b(sx, sy));
    const [lx, ly] = _rotateOffset(
      mx - drag.anchorX, my - drag.anchorY, -crop.rotation
    );
    const [sxs, sys] = EDGE_SIGNS[drag.edge];
    let newW = drag.startW;
    let newH = drag.startH;
    if (sxs !== 0) newW = Math.max(MIN_DOC_DIM, sxs * lx);
    if (sys !== 0) newH = Math.max(MIN_DOC_DIM, sys * ly);
    const [dcx, dcy] = _rotateOffset(
      sxs * newW / 2, sys * newH / 2, crop.rotation
    );
    crop.cx = drag.anchorX + dcx;
    crop.cy = drag.anchorY + dcy;
    crop.w = newW;
    crop.h = newH;
  }

  draw();
  return true;
}

export function handleCropMouseUp(e) {
  const had = !!cropState.drag;
  cropState.drag = null;
  if (had) draw();
  return had;
}

// ============================================================================
//  Actions
// ============================================================================

export function createCropForSelectedFile() {
  const file = _selectedFile();
  if (!file) { flashStatus("select a file first", "warn"); return; }
  if (!file.docWidth || !file.docHeight) {
    flashStatus("file dimensions unknown — refresh first", "warn");
    return;
  }
  crops.push({
    id: "crop-" + Date.now() + "-" + Math.floor(Math.random() * 1000),
    srcPath: file.path,
    cx: file.docWidth / 2,
    cy: file.docHeight / 2,
    w: file.docWidth * 0.5,
    h: file.docHeight * 0.5,
    rotation: 0,
  });
  cropState.selectedIdx = crops.length - 1;
  syncCropList();
  draw();
  flashStatus("new crop on " + file.name, "ok");
}

export async function createCropFile() {
  const a = _activeCrop();
  if (!a) { flashStatus("no crop selected", "warn"); return; }
  const { crop, file } = a;

  const w = Math.max(1, Math.round(crop.w));
  const h = Math.max(1, Math.round(crop.h));
  const theta = crop.rotation;
  const cosT = Math.cos(theta), sinT = Math.sin(theta);
  const cx = crop.cx, cy = crop.cy;

  const a_ = cosT;
  const b_ = -sinT;
  const c_ = sinT;
  const d_ = cosT;
  const e_ = -cosT * cx - sinT * cy + w / 2;
  const f_ = sinT * cx - cosT * cy + h / 2;

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "creating crop file (" + w + "×" + h + ")…";
    statusEl.className = "";
  }

  let payload;
  try {
    const res = await fetch("/krita/crop_to_file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        source_path: file.path,
        width: w, height: h,
        a: a_, b: b_, c: c_, d: d_, e: e_, f: f_,
      }),
    });
    payload = await res.json();
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = "crop failed: " + err.message;
      statusEl.className = "bad";
    }
    return;
  }

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) || "unknown";
    if (statusEl) {
      statusEl.textContent = "crop failed: " + msg;
      statusEl.className = "bad";
    }
    return;
  }

  // `paste_transforms` returns success=True at the transport level even
  // when the individual command failed.  There is exactly one transform
  // in a crop, so a `successful === 0` here means the paste did not run
  // — most commonly because the target document name we sent did not
  // match anything on the daemon side.  Surface the daemon's own
  // message instead of a misleading "created".
  const ok  = payload.successful || 0;
  const bad = payload.failed     || 0;
  if (ok === 0) {
    let firstFailMsg = "";
    for (const r of (payload.results || [])) {
      if (r.status === "error") { firstFailMsg = r.message || ""; break; }
    }
    if (statusEl) {
      statusEl.textContent = "crop paste failed: " +
        (firstFailMsg || (bad ? "unknown daemon error" : "no result"));
      statusEl.className = "bad";
    }
    return;
  }

  flashStatus("created " + w + "×" + h + " crop", "ok");
}

export function deleteSelectedCrop() {
  if (cropState.selectedIdx < 0) return false;
  crops.splice(cropState.selectedIdx, 1);
  cropState.selectedIdx = -1;
  syncCropList();
  draw();
  return true;
}

// ============================================================================
//  Crop list panel — grouped by source file
// ============================================================================

export function syncCropList() {
  // Prune orphaned crops (source card no longer on the board)
  for (let ci = crops.length - 1; ci >= 0; ci--) {
    if (!_fileByPath(crops[ci].srcPath)) {
      crops.splice(ci, 1);
      if (cropState.selectedIdx === ci) cropState.selectedIdx = -1;
      else if (cropState.selectedIdx > ci) cropState.selectedIdx -= 1;
    }
  }

  const list = document.getElementById("cropList");
  if (!list) return;
  list.innerHTML = "";

  if (crops.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no crops — select a file, click + New crop";
    list.appendChild(e);
    return;
  }

  // Group crops by source path.  Order groups by board.files order,
  // so the crop list matches the Files list below.  Within a group,
  // crops stay in creation order (their order in `crops`).
  const groups = new Map();  // srcPath -> array of indices into crops
  for (let ci = 0; ci < crops.length; ci++) {
    const sp = crops[ci].srcPath;
    if (!groups.has(sp)) groups.set(sp, []);
    groups.get(sp).push(ci);
  }
  const order = [];
  for (const f of board.files) {
    if (groups.has(f.path)) order.push(f.path);
  }

  const selFile = _selectedFile();

  for (const srcPath of order) {
    const file = _fileByPath(srcPath);
    const indices = groups.get(srcPath);
    const isSelFile = selFile && selFile.path === srcPath;
    const hue = file.color || board.palette[0];

    // ---- Group header ------------------------------------------------
    const header = document.createElement("div");
    header.className = "cropGroup" + (isSelFile ? " selected" : "");
    header.title = srcPath + " — click to select this file on the board";

    const sw = document.createElement("span");
    sw.className = "fileSwatch";
    sw.style.background = "rgba(" + hue.r + ", " + hue.g + ", " + hue.b
                        + ", " + (isSelFile ? 0.95 : 0.55) + ")";
    sw.style.border = "1px solid rgba(" + hue.r + ", " + hue.g + ", " + hue.b
                    + ", " + (isSelFile ? 1.0 : 0.85) + ")";
    header.appendChild(sw);

    const lbl = document.createElement("span");
    lbl.className = "cropGroupLabel";
    lbl.textContent = file.name;
    header.appendChild(lbl);

    const cnt = document.createElement("span");
    cnt.className = "cropGroupCount";
    cnt.textContent = String(indices.length);
    header.appendChild(cnt);

    header.addEventListener("click", () => {
      for (let k = 0; k < board.files.length; k++) {
        if (board.files[k] === file) {
          board.selectedIdx = k;
          bringToTop(k);
          break;
        }
      }
      syncFileList();
      syncCropList();
      draw();
    });

    list.appendChild(header);

    // ---- Crop rows under this header --------------------------------
    for (const ci of indices) {
      const crop = crops[ci];
      const isSel = ci === cropState.selectedIdx;
      const row = document.createElement("div");
      row.className = "cropRow" + (isSel ? " selected" : "");
      row.title = "source: " + crop.srcPath;

      const lb2 = document.createElement("span");
      lb2.className = "cropLabel";
      lb2.textContent = crop.id;
      row.appendChild(lb2);

      const dm = document.createElement("span");
      dm.className = "cropDims";
      let dimsText = Math.round(crop.w) + "×" + Math.round(crop.h);
      const deg = crop.rotation * 180 / Math.PI;
      if (Math.abs(deg) > 0.5) dimsText += "  " + Math.round(deg) + "°";
      dm.textContent = dimsText;
      row.appendChild(dm);

      const del = document.createElement("button");
      del.className = "fileDel";
      del.textContent = "\u00D7";
      del.title = "Delete this crop";
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        crops.splice(ci, 1);
        if (cropState.selectedIdx === ci) cropState.selectedIdx = -1;
        else if (cropState.selectedIdx > ci) cropState.selectedIdx -= 1;
        syncCropList();
        draw();
      });
      row.appendChild(del);

      row.addEventListener("click", () => {
        cropState.selectedIdx = ci;
        for (let k = 0; k < board.files.length; k++) {
          if (board.files[k] === file) {
            board.selectedIdx = k;
            break;
          }
        }
        syncCropList();
        draw();
      });

      list.appendChild(row);
    }
  }
}
"""