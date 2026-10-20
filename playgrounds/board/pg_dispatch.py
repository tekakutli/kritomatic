"""
pg_dispatch.py — draw() and the canvas event listeners.

ES module.  Exports two functions:

    draw          repaint the canvas and sync the panel's per-card
                  fields (`_syncCardFields`)
    updateStatus  refresh the status line at the bottom of the panel

Four drag modes, mutually exclusive:

    dragCanvas   pan the view (drag on empty space)
    dragFile     move the card (drag on the card's body)
    dragFile     resize the card (drag on a corner; mode="resize")
    dragFile     rotate the card (drag on the rotate handle;
                 mode="rotate")

MOUSEMOVE FAST PATH
===================
`draw()` runs only when the canvas is actually going to change:
during a card drag, during a canvas pan, on wheel zoom, on mouseup
after a drag, and on every model mutation (through the panel).

Pure hover — the mouse moving over the canvas with no button held —
only updates the CSS cursor style.  `_hoverCursorAt` (in pg_core)
is a single linear pass; `_setCursor` writes `style.cursor` only
when the value actually differs.

FIX HISTORY
===========
The import list in this file used to name `itemHitTest`, which
pg_core.js does not export — pg_core exports `fileHitTest`.  ES
modules reject a `import { x } from "y"` when `x` isn't in `y`'s
exports, so the browser refused to hand this module to `_boot.js`,
blanking the canvas.  The name was unused (`fileHitTest` is what
every handler actually calls); it has been removed.
"""

DISPATCH_JS = r"""
import {
  canvas, ctx, dpr, view, board, s2b, bringToTop,
  rotateHandleHitTest, fileCornerHitTest, fileHitTest,
  CORNER_SIGNS, _rotateOffset, _resizeCursorFor, _hoverCursorAt,
  resizeCardFromCorner, removeFile, arrangeGrid, fitAll,
} from "./pg_core.js";
import { drawBoardView } from "./pg_view_board.js";
import { _syncCardFields } from "./pg_panel_fields.js";
import { syncFileList } from "./pg_panel_lists.js";
import {
  refreshFromKrita, openAllMissing, activateFile, projectSelectedOntoOverlaps,
} from "./pg_krita.js";
import {
  pgkitNotifyEntities, pgkitNotifyLocal, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

let _statusTimer = null;

const DRAG_THRESHOLD = 3;
const state = {
  dragCanvas: null,
  dragFile:   null,
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

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;

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

  _setCursor(_hoverCursorAt(sx, sy));
});

window.addEventListener("mouseup", () => {
  const hadDrag = !!(state.dragFile || state.dragCanvas);
  state.dragFile = null; state.dragCanvas = null;
  _setCursor("default");
  if (hadDrag) { pgkitNotifyAll(); draw(); }
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
  else if (k === "delete" || k === "backspace") {
    if (board.selectedIdx >= 0) { e.preventDefault(); removeFile(board.selectedIdx); syncFileList(); draw(); }
  }
});

canvas.style.cursor = "default";
"""
