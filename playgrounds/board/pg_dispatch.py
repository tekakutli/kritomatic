"""
pg_dispatch.py — draw() and the canvas event listeners.

ES module.  Exports two functions:

    draw          repaint the canvas and sync the panel's per-card
                  fields (`_syncCardFields`) and the grid buttons /
                  cell list (`syncGridPanel`)
    updateStatus  refresh the status line at the bottom of the panel

Drag modes, mutually exclusive:
    dragCanvas   pan the view
    dragFile     move / resize / rotate a card
    crop drag    one of four free-crop modes; pg_crops
    grid drag    move a split line; pg_grid

FOCUS — TWO COMPLEMENTARY SURFACES
==================================
Focusing a document in Krita is available two ways:

    1.  The panel.  Every file row, crop row, and grid-cell row
        carries a ◎ Focus button that POSTs /krita/activate for the
        relevant document (source file, or the crop/cell's exported
        target).  Always visible, always working, no gesture to
        remember.

    2.  The canvas.  A clean click (mousedown and mouseup within
        CLICK_FOCUS_THRESHOLD pixels, no drag, no handle grab) on a
        *materialised* free crop or grid cell focuses that crop/
        cell's exported Krita document.  This is the same action as
        the row's ◎ button — direct, spatial, and faster when the
        user's attention is already on the canvas.

A click is only treated as a focus gesture when it lands on a
materialised region (one with a recorded `targetDoc`).  Clicks on
plain card area, on unexported crops/cells, on handles, on grid
lines, or with any drag motion do not fire the focus action — they
do whatever they normally would.

Double-click on any card calls activateFile, matching the standard
file-explorer "open" gesture.  That path is the only way to focus a
*closed* source document (activateFile opens it first, then the new
view is already the active one); the panel row's ◎ does the same
thing when the row is a file.

FIX HISTORY
===========
The import list used to name `itemHitTest`, which pg_core.js does
not export — pg_core exports `fileHitTest`.  That failure blanked
the canvas.  Removed.
"""

DISPATCH_JS = r"""
import {
  canvas, ctx, dpr, view, board, s2b, bringToTop,
  _boardPointInCard,
  rotateHandleHitTest, fileCornerHitTest, fileHitTest,
  CORNER_SIGNS, _rotateOffset, _resizeCursorFor, _hoverCursorAt,
  resizeCardFromCorner, removeFile, arrangeGrid, fitAll,
} from "./pg_core.js";
import { drawBoardView } from "./pg_view_board.js";
import { _syncCardFields } from "./pg_panel_fields.js";
import { syncFileList } from "./pg_panel_lists.js";
import {
  refreshFromKrita, openAllMissing, activateFile, activateDocument,
  projectSelectedOntoOverlaps,
} from "./pg_krita.js";
import {
  pgkitNotifyEntities, pgkitNotifyLocal, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import {
  cropCursorAt, handleCropMouseDown, handleCropMouseMove,
  handleCropMouseUp, deleteSelectedCrop, cropState,
  createCropForSelectedFile, tryFocusCropAt,
} from "./pg_crops.js";
import {
  isGridModeOn, gridCursorAt,
  handleGridMouseDown, handleGridMouseMove, handleGridMouseUp,
  handleGridRightClick, syncGridPanel, tryFocusCellAt,
} from "./pg_grid.js";

let _statusTimer = null;

const DRAG_THRESHOLD         = 3;
const CLICK_FOCUS_THRESHOLD  = 4;

const state = {
  dragCanvas: null,
  dragFile:   null,
  mouseDown:  { sx: 0, sy: 0 },
  mouse:      { sx: 0, sy: 0, inside: false },
};

let _lastCursor = "default";
function _setCursor(c) {
  if (c === _lastCursor) return;
  canvas.style.cursor = c;
  _lastCursor = c;
}

export function draw() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawBoardView();
  const M = 6;
  ctx.strokeStyle = "#1e2836"; ctx.lineWidth = 0.8;
  ctx.strokeRect(M + 0.5, M + 0.5, cw - 2*M - 1, ch - 2*M - 1);
  updateStatus();
  _syncCardFields();
  syncGridPanel();
}

export function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;
  if (_statusTimer) return;
  const opens = board.files.filter(f => f.open).length;
  const closed = board.files.filter(f => !f.open && !f.missing).length;
  const miss = board.files.filter(f => f.missing).length;
  const parts = [opens + " open", closed + " closed"];
  if (miss) parts.push(miss + " missing");
  parts.push("zoom " + (view.zoom * 100).toFixed(0) + "%");
  const text = parts.join("  \u00b7  ");
  if (el.textContent !== text) el.textContent = text;
  if (el.className !== "") el.className = "";
}

function _short(path) {
  if (!path) return "";
  return String(path).split(/[\\\/]/).pop();
}

// Return the targetDoc of a materialised crop/cell under (sx, sy),
// or null.  Scoped to the currently-selected file.  Used only by
// the click-to-focus path in mouseup; the panel buttons hit the
// same helpers via the row-click wiring in pg_crops / pg_grid.
function _tryFocusAt(sx, sy) {
  const file = (board.selectedIdx >= 0
                && board.selectedIdx < board.files.length)
    ? board.files[board.selectedIdx]
    : null;
  if (!file) return null;
  if (!file.docWidth || !file.docHeight) return null;

  const [bx, by] = s2b(sx, sy);
  if (!_boardPointInCard(file, bx, by)) return null;

  const cropDoc = tryFocusCropAt(file, sx, sy);
  if (cropDoc) return cropDoc;

  return tryFocusCellAt(file, sx, sy);
}

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  state.mouseDown.sx = sx;
  state.mouseDown.sy = sy;

  if (handleGridMouseDown(sx, sy, e)) return;
  if (!isGridModeOn()) {
    if (handleCropMouseDown(sx, sy, e)) return;
  }

  if (rotateHandleHitTest(sx, sy)) {
    const f = board.files[board.selectedIdx];
    state.dragFile = {
      idx: board.selectedIdx, mode: "rotate",
      startSx: sx, startSy: sy,
      centerX: f.x + f.w / 2, centerY: f.y + f.h / 2,
      startRotation: f.rotation, moved: false,
    };
    _setCursor("grabbing"); draw(); return;
  }

  const cornerHit = fileCornerHitTest(sx, sy);
  if (cornerHit) {
    board.selectedIdx = cornerHit.idx;
    bringToTop(cornerHit.idx);
    const f = board.files[cornerHit.idx];
    f.userSized = true;
    const [dsx, dsy] = CORNER_SIGNS[cornerHit.corner];
    const cx = f.x + f.w / 2, cy = f.y + f.h / 2;
    const [aox, aoy] = _rotateOffset(-dsx * f.w/2, -dsy * f.h/2, f.rotation);
    state.dragFile = {
      idx: cornerHit.idx, mode: "resize", corner: cornerHit.corner,
      anchorX: cx + aox, anchorY: cy + aoy, startAspect: f.h / f.w,
      startSx: sx, startSy: sy, moved: false,
    };
    _setCursor(_resizeCursorFor(cornerHit.corner));
    syncFileList(); draw(); return;
  }

  const hit = fileHitTest(sx, sy);
  if (hit >= 0) {
    board.selectedIdx = hit;
    bringToTop(hit);
    const f = board.files[hit];
    state.dragFile = {
      idx: hit, mode: "move",
      startSx: sx, startSy: sy,
      startFileX: f.x, startFileY: f.y, moved: false,
    };
    _setCursor("grabbing"); syncFileList(); draw(); return;
  }

  board.selectedIdx = -1;
  state.dragCanvas = {
    startSx: sx, startSy: sy,
    startPanX: view.panX, startPanY: view.panY,
  };
  _setCursor("grabbing"); syncFileList(); draw();
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  state.mouse.sx = sx; state.mouse.sy = sy;
  state.mouse.inside = (sx >= 0 && sy >= 0 &&
                        sx < window.innerWidth && sy < window.innerHeight);

  if (handleGridMouseMove(sx, sy, e)) return;
  if (!isGridModeOn()) {
    if (handleCropMouseMove(sx, sy, e)) return;
  }

  if (state.dragFile) {
    const df = state.dragFile;
    const dx = sx - df.startSx, dy = sy - df.startSy;
    if (!df.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) df.moved = true;
    if (df.moved) {
      const f = board.files[df.idx];
      if (df.mode === "resize") {
        const [bx, by] = s2b(sx, sy);
        resizeCardFromCorner(f, df.corner, bx, by, df.anchorX, df.anchorY,
                             df.startAspect, e.shiftKey);
      } else if (df.mode === "rotate") {
        const [bx, by] = s2b(sx, sy);
        let angle = Math.atan2(by - df.centerY, bx - df.centerX) + Math.PI / 2;
        if (e.shiftKey) {
          const snap = 15 * Math.PI / 180;
          angle = Math.round(angle / snap) * snap;
        }
        f.rotation = angle;
      } else {
        f.x = df.startFileX + dx / view.zoom;
        f.y = df.startFileY + dy / view.zoom;
      }
    }
    draw(); return;
  }

  if (state.dragCanvas) {
    const dc = state.dragCanvas;
    view.panX = dc.startPanX + (sx - dc.startSx);
    view.panY = dc.startPanY + (sy - dc.startSy);
    draw(); return;
  }

  const gridCur = gridCursorAt(sx, sy);
  if (gridCur) { _setCursor(gridCur); return; }
  if (!isGridModeOn()) {
    const cropCur = cropCursorAt(sx, sy);
    if (cropCur) { _setCursor(cropCur); return; }
  }
  _setCursor(_hoverCursorAt(sx, sy));
});

window.addEventListener("mouseup", (e) => {
  // Capture drag state before the mouseup handlers clear it; the
  // focus check below needs to know which kind of interaction just
  // ended so it only fires on clean clicks.
  const cropDrag = cropState.drag;
  const cardDrag = state.dragFile;

  const hadGridDrag = handleGridMouseUp();
  handleCropMouseUp();

  const hadDrag = !!(state.dragFile || state.dragCanvas);
  state.dragFile = null;
  state.dragCanvas = null;
  _setCursor("default");
  if (hadDrag) { pgkitNotifyAll(); draw(); }

  // ---- Click-to-focus -----------------------------------------------------
  //
  // A click is a focus gesture when it landed on a materialised
  // free crop or grid cell with no drag motion.  Handle drags,
  // grid-line drags, card moves, and pan gestures are excluded by
  // the mode checks; pure drags are excluded by the distance check.
  if (e.button !== 0) return;
  if (hadGridDrag) return;
  if (cropDrag && cropDrag.mode !== "move") return;
  if (cardDrag && cardDrag.mode !== "move") return;

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const dx = sx - state.mouseDown.sx;
  const dy = sy - state.mouseDown.sy;
  if (Math.hypot(dx, dy) >= CLICK_FOCUS_THRESHOLD) return;

  const target = _tryFocusAt(sx, sy);
  if (!target) return;

  activateDocument(target).then(
    () => flashStatus("focused " + _short(target), "ok"),
    (err) => flashStatus("focus failed: " + err.message, "bad"),
  );
});

canvas.addEventListener("contextmenu", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  if (handleGridRightClick(sx, sy, e)) {
    e.preventDefault();
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left, my = e.clientY - rect.top;
  const [bx, by] = s2b(mx, my);
  const factor = e.deltaY < 0 ? 1.10 : 1 / 1.10;
  view.zoom = Math.max(0.05, Math.min(8, view.zoom * factor));
  view.panX = mx - bx * view.zoom;
  view.panY = my - by * view.zoom;
  pgkitNotifyLocal("view.zoom");
  pgkitNotifyLocal("view.panX");
  pgkitNotifyLocal("view.panY");
  draw();
}, { passive: false });

canvas.addEventListener("dblclick", (e) => {
  // Standard "open file" gesture: focus (or open-then-focus) the
  // source document of the card under the cursor.
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const hit = fileHitTest(sx, sy);
  if (hit >= 0) activateFile(board.files[hit]);
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;
  const k = e.key.toLowerCase();
  if (k === "r") { e.preventDefault(); refreshFromKrita(); }
  else if (k === "g") { e.preventDefault(); arrangeGrid(); draw(); }
  else if (k === "f") { e.preventDefault(); fitAll(); }
  else if (k === "o") { e.preventDefault(); openAllMissing(); }
  else if (k === "p") { e.preventDefault(); projectSelectedOntoOverlaps(); }
  else if (k === "c") { e.preventDefault(); createCropForSelectedFile(); }
  else if (k === "delete" || k === "backspace") {
    if (cropState.selectedIdx >= 0) {
      e.preventDefault();
      deleteSelectedCrop();
    } else if (board.selectedIdx >= 0) {
      e.preventDefault();
      removeFile(board.selectedIdx);
      syncFileList();
      draw();
    }
  }
});

canvas.style.cursor = "default";
"""