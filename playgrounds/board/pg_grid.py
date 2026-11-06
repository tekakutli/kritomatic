"""
pg_grid.py — axis-aligned split lines that divide a source file into cells.

ES module.  A grid is a per-file, doc-space arrangement of vertical and
horizontal interior split lines.  It is orthogonal to the free crop
tool: the two can coexist on the same file, but the grid claims events
only when grid mode is on for the selected file.

Exports:

    gridByPath, gridState     the model
    drawGridForFile(file)     called from pg_view_board per card
    isGridModeOn()            true iff the selected file has grid mode
    gridLineHitTest(sx, sy)   { which, index } | null
    handleGridMouseDown       return true if consumed
    handleGridMouseMove       same
    handleGridMouseUp         same
    gridCursorAt              hover cursor, or null
    handleGridRightClick      right-click delete
    toggleGridMode            "Grid split" button
    addVLine, addHLine        "+ V line" / "+ H line" buttons
    createCellCrops           "Create cell crops" button
    syncGridPanel             enable/disable and relabel the four
                              buttons, and rebuild the cell list
    tryFocusCellAt            returns the targetDoc of a materialised
                              cell hit under the cursor, or null

STORE
=====
`gridByPath` is a Map from file.path to `{vx: number[], hy: number[],
cells: [{x1, y1, x2, y2, targetDoc}, ...]}`.

`vx` and `hy` hold *interior* line positions only — the implicit
boundaries at 0 and docWidth/docHeight are always there.

`cells` is a snapshot of the materialised exports: each entry records
the doc-space rectangle of one cell and the Krita document created
from it.  Cells are frozen — dragging a line does not move them —
because they represent "a file I have already created from this
region".  Re-running "Create cell crops" replaces the set.

INTERACTIVITY
=============
Grid editing is scoped to the selected file, and within that file
only while "grid mode" is on.  When grid mode is on:

    · left-drag on a line         move the line, clamped to neighbours
    · right-click on a line       delete the line
    · left-click elsewhere        the normal card / pan handler runs

MATERIALISED CELLS
==================
`createCellCrops` records the Krita document returned by the server
for each successfully created cell, and exits grid mode.  Materialised
cells render with a subtle cyan tint on the canvas, are always
visible regardless of selection, appear in a compact cell list under
the grid buttons (one row per cell with a **◎ Focus** button), and
respond to a clean click on their body by activating their Krita
document via `pg_dispatch._tryFocusAt` → `/krita/activate`.

CELL AFFINES
============
A cell is the doc-space rectangle between two adjacent V-boundaries
and two adjacent H-boundaries.  Target dims are the rounded extent;
affine maps source → target with no rotation:

    w = round(x2 - x1),  h = round(y2 - y1)
    a = w / (x2 - x1),   d = h / (y2 - y1)
    e = -a * x1,         f = -d * y1
"""

GRID_JS = r"""
import {
  board, canvas, ctx, b2s, s2b,
} from "./pg_core.js";
import { draw } from "./pg_dispatch.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { docToBoard, boardToDoc } from "./pg_crops.js";

// ============================================================================
//  State
// ============================================================================

export const gridByPath     = new Map();   // path -> { vx, hy, cells }
export const gridModeByPath = new Set();   // paths with grid mode on

export const gridState = {
  drag:  null,   // { which, index, startSx, startSy, startDocValue, moved }
  hover: null,   // { which, index }
};

const LINE_HIT       = 6;
const DRAG_THRESHOLD = 3;

// ============================================================================
//  Helpers
// ============================================================================

function _shortPath(p) {
  if (!p) return "";
  return String(p).split(/[\\\/]/).pop();
}

function _fileByPath(path) {
  for (const f of board.files) if (f.path === path) return f;
  return null;
}

function _selectedFile() {
  if (board.selectedIdx < 0 || board.selectedIdx >= board.files.length) return null;
  return board.files[board.selectedIdx];
}

export function isGridModeOn() {
  const f = _selectedFile();
  return f ? gridModeByPath.has(f.path) : false;
}

function _gridFor(file) {
  if (!file) return null;
  return gridByPath.get(file.path) || null;
}

function _boundariesX(file, grid) {
  return [0, ...grid.vx.slice().sort((a, b) => a - b), file.docWidth];
}
function _boundariesY(file, grid) {
  return [0, ...grid.hy.slice().sort((a, b) => a - b), file.docHeight];
}

// ============================================================================
//  Screen geometry
// ============================================================================

function _vLineScreen(file, docX) {
  return [
    b2s(...docToBoard(file, docX, 0)),
    b2s(...docToBoard(file, docX, file.docHeight)),
  ];
}
function _hLineScreen(file, docY) {
  return [
    b2s(...docToBoard(file, 0, docY)),
    b2s(...docToBoard(file, file.docWidth, docY)),
  ];
}

function _distToSegment(sx, sy, x1, y1, x2, y2) {
  const dx = x2 - x1, dy = y2 - y1;
  const len2 = dx * dx + dy * dy;
  if (len2 < 1e-9) return Math.hypot(sx - x1, sy - y1);
  let t = ((sx - x1) * dx + (sy - y1) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(sx - (x1 + t * dx), sy - (y1 + t * dy));
}

// ============================================================================
//  Hit testing
// ============================================================================

export function gridLineHitTest(sx, sy) {
  const file = _selectedFile();
  if (!file) return null;
  const grid = _gridFor(file);
  if (!grid) return null;
  if (!file.docWidth || !file.docHeight) return null;

  let best = null;
  for (let i = 0; i < grid.vx.length; i++) {
    const [p1, p2] = _vLineScreen(file, grid.vx[i]);
    const d = _distToSegment(sx, sy, p1[0], p1[1], p2[0], p2[1]);
    if (d <= LINE_HIT && (!best || d < best.dist)) {
      best = { which: "v", index: i, dist: d };
    }
  }
  for (let i = 0; i < grid.hy.length; i++) {
    const [p1, p2] = _hLineScreen(file, grid.hy[i]);
    const d = _distToSegment(sx, sy, p1[0], p1[1], p2[0], p2[1]);
    if (d <= LINE_HIT && (!best || d < best.dist)) {
      best = { which: "h", index: i, dist: d };
    }
  }
  return best ? { which: best.which, index: best.index } : null;
}

// ============================================================================
//  Cursor
// ============================================================================

export function gridCursorAt(sx, sy) {
  if (!isGridModeOn()) {
    gridState.hover = null;
    return null;
  }
  const hit = gridLineHitTest(sx, sy);
  gridState.hover = hit;
  if (!hit) return null;
  return hit.which === "v" ? "col-resize" : "row-resize";
}

// ============================================================================
//  Focus helper (used by pg_dispatch's clean-click path)
// ============================================================================

export function tryFocusCellAt(file, sx, sy) {
  if (!file || !file.docWidth || !file.docHeight) return null;
  const grid = _gridFor(file);
  if (!grid || !grid.cells || !grid.cells.length) return null;
  const [bx, by] = s2b(sx, sy);
  const [dx, dy] = boardToDoc(file, bx, by);
  for (let i = grid.cells.length - 1; i >= 0; i--) {
    const c = grid.cells[i];
    if (!c.targetDoc) continue;
    if (dx >= c.x1 && dx <= c.x2 && dy >= c.y1 && dy <= c.y2) {
      return c.targetDoc;
    }
  }
  return null;
}

// ============================================================================
//  Rendering
// ============================================================================

export function drawGridForFile(file) {
  const grid = _gridFor(file);
  if (!grid) return;
  if (!file.docWidth || !file.docHeight) return;

  // Materialised cell tints: rendered for ALL files, not just the
  // selected one, so users can see which cells they have exported.
  if (grid.cells && grid.cells.length) {
    for (const cell of grid.cells) {
      const p1 = b2s(...docToBoard(file, cell.x1, cell.y1));
      const p2 = b2s(...docToBoard(file, cell.x2, cell.y1));
      const p3 = b2s(...docToBoard(file, cell.x2, cell.y2));
      const p4 = b2s(...docToBoard(file, cell.x1, cell.y2));
      ctx.save();
      ctx.beginPath();
      ctx.moveTo(p1[0], p1[1]);
      ctx.lineTo(p2[0], p2[1]);
      ctx.lineTo(p3[0], p3[1]);
      ctx.lineTo(p4[0], p4[1]);
      ctx.closePath();
      ctx.fillStyle = "rgba(0, 229, 255, 0.08)";
      ctx.fill();
      ctx.restore();
    }
  }

  // Grid lines: only for the selected file (current edit state).
  const sel = _selectedFile();
  if (!sel || sel.path !== file.path) return;

  const modeOn = gridModeByPath.has(file.path);

  for (let i = 0; i < grid.vx.length; i++) {
    const [p1, p2] = _vLineScreen(file, grid.vx[i]);
    const isHover = gridState.hover && gridState.hover.which === "v"
                    && gridState.hover.index === i;
    const isDrag  = gridState.drag  && gridState.drag.which  === "v"
                    && gridState.drag.index === i;
    _drawLine(p1, p2, modeOn, isHover, isDrag);
  }
  for (let i = 0; i < grid.hy.length; i++) {
    const [p1, p2] = _hLineScreen(file, grid.hy[i]);
    const isHover = gridState.hover && gridState.hover.which === "h"
                    && gridState.hover.index === i;
    const isDrag  = gridState.drag  && gridState.drag.which  === "h"
                    && gridState.drag.index === i;
    _drawLine(p1, p2, modeOn, isHover, isDrag);
  }
}

function _drawLine(p1, p2, modeOn, isHover, isDrag) {
  const bright = isDrag ? 1.00 : (isHover ? 0.95 : (modeOn ? 0.85 : 0.45));
  const width  = isDrag ? 2.4  : (isHover ? 2.0  : 1.4);

  ctx.save();
  ctx.strokeStyle = "rgba(0, 229, 255, " + bright + ")";
  ctx.lineWidth = width;
  ctx.setLineDash(isDrag || isHover ? [] : [6, 4]);
  ctx.beginPath();
  ctx.moveTo(p1[0], p1[1]);
  ctx.lineTo(p2[0], p2[1]);
  ctx.stroke();
  ctx.setLineDash([]);
  ctx.restore();
}

// ============================================================================
//  Drag
// ============================================================================

export function handleGridMouseDown(sx, sy, e) {
  if (!isGridModeOn()) return false;
  const hit = gridLineHitTest(sx, sy);
  if (!hit) return false;
  const file = _selectedFile();
  if (!file) return false;
  const grid = _gridFor(file);
  if (!grid) return false;

  const startDoc = hit.which === "v" ? grid.vx[hit.index] : grid.hy[hit.index];
  gridState.drag = {
    which: hit.which, index: hit.index,
    startSx: sx, startSy: sy,
    startDocValue: startDoc,
    moved: false,
  };
  gridState.hover = hit;
  draw();
  return true;
}

export function handleGridMouseMove(sx, sy, e) {
  const drag = gridState.drag;
  if (!drag) return false;
  const file = _selectedFile();
  if (!file) return false;
  const grid = _gridFor(file);
  if (!grid) return false;

  const dx = sx - drag.startSx, dy = sy - drag.startSy;
  if (!drag.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) drag.moved = true;
  if (!drag.moved) return true;

  const [dsx0, dsy0] = boardToDoc(file, ...s2b(drag.startSx, drag.startSy));
  const [dsx1, dsy1] = boardToDoc(file, ...s2b(sx, sy));
  const deltaDoc = drag.which === "v" ? (dsx1 - dsx0) : (dsy1 - dsy0);

  const arr = drag.which === "v" ? grid.vx : grid.hy;
  const upper = drag.which === "v" ? file.docWidth : file.docHeight;
  const lo = drag.index > 0 ? arr[drag.index - 1] + 1 : 1;
  const hi = drag.index < arr.length - 1 ? arr[drag.index + 1] - 1 : upper - 1;
  let newVal = drag.startDocValue + deltaDoc;
  newVal = Math.max(lo, Math.min(hi, newVal));
  arr[drag.index] = newVal;

  draw();
  return true;
}

export function handleGridMouseUp(e) {
  const had = !!gridState.drag;
  gridState.drag = null;
  if (had) draw();
  return had;
}

// ============================================================================
//  Right-click delete
// ============================================================================

export function handleGridRightClick(sx, sy, e) {
  if (!isGridModeOn()) return false;
  const hit = gridLineHitTest(sx, sy);
  if (!hit) return false;
  const file = _selectedFile();
  if (!file) return false;
  const grid = _gridFor(file);
  if (!grid) return false;

  const arr = hit.which === "v" ? grid.vx : grid.hy;
  if (hit.index < 0 || hit.index >= arr.length) return false;
  arr.splice(hit.index, 1);
  gridState.hover = null;

  if (grid.vx.length === 0 && grid.hy.length === 0) {
    gridByPath.delete(file.path);
    gridModeByPath.delete(file.path);
  }

  syncGridPanel();
  draw();
  return true;
}

// ============================================================================
//  Buttons
// ============================================================================

export function toggleGridMode() {
  const file = _selectedFile();
  if (!file) { flashStatus("select a file first", "warn"); return; }
  if (!file.docWidth || !file.docHeight) {
    flashStatus("file dimensions unknown — refresh first", "warn");
    return;
  }
  if (gridModeByPath.has(file.path)) {
    gridModeByPath.delete(file.path);
    flashStatus("grid mode off", "ok");
  } else {
    if (!gridByPath.has(file.path)) {
      gridByPath.set(file.path, {
        vx: [file.docWidth  / 2],
        hy: [file.docHeight / 2],
        cells: [],
      });
    }
    gridModeByPath.add(file.path);
    flashStatus("grid mode on — drag lines, right-click to delete", "ok");
  }
  syncGridPanel();
  draw();
}

export function addVLine() {
  const file = _selectedFile();
  if (!file) { flashStatus("select a file first", "warn"); return; }
  if (!gridModeByPath.has(file.path)) {
    flashStatus("enable grid mode first", "warn"); return;
  }
  const grid = _gridFor(file);
  if (!grid) return;
  const xb = _boundariesX(file, grid);
  let bestI = 0, bestW = -1;
  for (let i = 0; i < xb.length - 1; i++) {
    const w = xb[i + 1] - xb[i];
    if (w > bestW) { bestW = w; bestI = i; }
  }
  grid.vx.push((xb[bestI] + xb[bestI + 1]) / 2);
  grid.vx.sort((a, b) => a - b);
  syncGridPanel();
  draw();
}

export function addHLine() {
  const file = _selectedFile();
  if (!file) { flashStatus("select a file first", "warn"); return; }
  if (!gridModeByPath.has(file.path)) {
    flashStatus("enable grid mode first", "warn"); return;
  }
  const grid = _gridFor(file);
  if (!grid) return;
  const yb = _boundariesY(file, grid);
  let bestI = 0, bestH = -1;
  for (let i = 0; i < yb.length - 1; i++) {
    const h = yb[i + 1] - yb[i];
    if (h > bestH) { bestH = h; bestI = i; }
  }
  grid.hy.push((yb[bestI] + yb[bestI + 1]) / 2);
  grid.hy.sort((a, b) => a - b);
  syncGridPanel();
  draw();
}

export async function createCellCrops() {
  const file = _selectedFile();
  if (!file) { flashStatus("select a file first", "warn"); return; }
  if (!file.docWidth || !file.docHeight) {
    flashStatus("file dimensions unknown — refresh first", "warn"); return;
  }
  const grid = _gridFor(file);
  if (!grid) { flashStatus("no grid — click Grid split first", "warn"); return; }
  if (grid.vx.length === 0 && grid.hy.length === 0) {
    flashStatus("grid has no splits — add at least one line", "warn"); return;
  }

  const xb = _boundariesX(file, grid);
  const yb = _boundariesY(file, grid);
  const cells = [];
  for (let i = 0; i < xb.length - 1; i++) {
    for (let j = 0; j < yb.length - 1; j++) {
      const x1 = xb[i], x2 = xb[i + 1];
      const y1 = yb[j], y2 = yb[j + 1];
      const w = Math.round(x2 - x1);
      const h = Math.round(y2 - y1);
      if (w < 1 || h < 1) continue;
      const a = w / (x2 - x1);
      const d = h / (y2 - y1);
      cells.push({
        x1, y1, x2, y2,
        width: w, height: h,
        a: a, b: 0, c: 0, d: d,
        e: -a * x1, f: -d * y1,
      });
    }
  }

  const statusEl = document.getElementById("status");
  if (statusEl) {
    statusEl.textContent = "creating " + cells.length + " cell crops…";
    statusEl.className = "";
  }

  let payload;
  try {
    const res = await fetch("/krita/crop_to_files", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source_path: file.path, cells: cells }),
    });
    payload = await res.json();
  } catch (err) {
    if (statusEl) {
      statusEl.textContent = "grid crop failed: " + err.message;
      statusEl.className = "bad";
    }
    return;
  }

  if (!payload || !payload.success) {
    const msg = (payload && payload.message) || "unknown";
    if (statusEl) {
      statusEl.textContent = "grid crop failed: " + msg;
      statusEl.className = "bad";
    }
    return;
  }

  const ok  = payload.successful || 0;
  const bad = payload.failed     || 0;
  let msg = "created " + ok + " cell crop" + (ok === 1 ? "" : "s");
  if (bad) {
    msg += " · " + bad + " failed";
    for (const r of (payload.results || [])) {
      if (!r.success && r.message) { msg += " — " + r.message; break; }
    }
  }
  flashStatus(msg, bad ? "bad" : "ok");

  // Record materialised cells so the panel's per-cell Focus button
  // and the canvas click-to-focus path both have something to work
  // with.  Cells without a target_document are skipped.
  const materialised = [];
  for (const r of (payload.results || [])) {
    if (!r.success) continue;
    const def = cells[r.index];
    if (!def) continue;
    if (!r.target_document) continue;
    materialised.push({
      x1: def.x1, y1: def.y1, x2: def.x2, y2: def.y2,
      targetDoc: r.target_document,
    });
  }
  grid.cells = materialised;

  // Exit grid mode so subsequent line clicks don't fight the panel.
  gridModeByPath.delete(file.path);

  syncGridPanel();
  draw();
}

// ============================================================================
//  Panel sync (buttons + cell list)
// ============================================================================

let _cellListSig = null;

export function syncGridPanel() {
  // Prune grids for files no longer on the board.
  for (const path of Array.from(gridByPath.keys())) {
    if (!_fileByPath(path)) {
      gridByPath.delete(path);
      gridModeByPath.delete(path);
    }
  }

  const file = _selectedFile();
  const on = file ? gridModeByPath.has(file.path) : false;
  const hasGrid = file ? gridByPath.has(file.path) : false;

  const splitBtn = document.getElementById("gridSplitBtn");
  if (splitBtn) {
    splitBtn.textContent = on ? "Exit grid" : "Grid split";
    splitBtn.classList.toggle("active", on);
  }
  const addV = document.getElementById("gridAddVBtn");
  if (addV) addV.disabled = !on;
  const addH = document.getElementById("gridAddHBtn");
  if (addH) addH.disabled = !on;
  const create = document.getElementById("gridCreateBtn");
  if (create) create.disabled = !hasGrid;

  _syncGridCellList(file);
}

function _syncGridCellList(file) {
  const list = document.getElementById("gridCellList");
  if (!list) return;

  const grid = file ? gridByPath.get(file.path) : null;
  const cells = grid && grid.cells ? grid.cells : [];

  const sig = file && file.path ? file.path + "|"
    + JSON.stringify(cells.map(c => [c.x1, c.y1, c.x2, c.y2, c.targetDoc]))
    : "";
  if (sig === _cellListSig) return;
  _cellListSig = sig;

  list.innerHTML = "";

  if (cells.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = file
      ? "no exported cells — click Create cell crops"
      : "no file selected";
    list.appendChild(e);
    return;
  }

  for (const cell of cells) {
    const row = document.createElement("div");
    row.className = "gridCellRow";
    row.title = cell.targetDoc || "";

    const lb = document.createElement("span");
    lb.className = "gridCellLabel";
    lb.textContent = Math.round(cell.x2 - cell.x1) + "×"
                   + Math.round(cell.y2 - cell.y1);
    row.appendChild(lb);

    const fbtn = document.createElement("button");
    fbtn.className = "cropFocus";
    fbtn.textContent = "\u25CE";
    fbtn.disabled = !cell.targetDoc;
    fbtn.title = cell.targetDoc
      ? "Focus " + _shortPath(cell.targetDoc) + " in Krita"
      : "Not exported";
    fbtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!cell.targetDoc) return;
      try {
        const m = await import("./pg_krita.js");
        await m.activateDocument(cell.targetDoc);
        flashStatus("focused " + _shortPath(cell.targetDoc), "ok");
      } catch (err) {
        flashStatus("focus failed: " + err.message, "bad");
      }
    });
    row.appendChild(fbtn);

    list.appendChild(row);
  }
}
"""