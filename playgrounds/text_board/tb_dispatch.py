"""
tb_dispatch.py — draw() and the canvas event listeners.

ES module.  Exports `draw`; the module keeps `updateStatus` local
because it's only called from this file's own `draw` and wheel
handler.

Two drag modes, mutually exclusive:

    dragCanvas   pan the view
    dragItem     move a card

Dragging a card moves it off its document position; a subsequent
Refresh snaps it back, because `_rebaseAllItems` recomputes board
positions from the document slot on every merge.  That is the
intended behaviour: the document is the source of truth for layout,
and manual drags are a scratchpad on top of it.

MOUSEMOVE FAST PATH
===================
`draw()` runs only when the canvas is actually going to change.
Pure hover — the mouse moving over the canvas with no button held —
updates the cursor readout (pure DOM) and the CSS cursor style
(a CSS property, not a canvas drawing), and nothing else.

CURSOR STYLE — SET ONLY ON CHANGE
=================================
`canvas.style.cursor = X` triggers a style recalc even when X equals
the current value, and the hover handler fires on every mousemove.
`_setCursor()` tracks the last value in JS and only writes when it
changes.  The hit test behind the decision is `_anyItemHitTest` —
boolean, no allocation, no z-sort.

DYNAMIC IMPORTS
===============
Several handlers reach for `refreshFromKrita`, `arrangeGrid`,
`fitAll`, `openAllClosed`, and `activateItemDocument`.  A static
import of any of those would introduce a cycle — `tb_krita` imports
`draw` from this file, `tb_core` imports nothing, but `tb_dispatch`
and `tb_krita` both chain through `tb_panel_fields` and
`tb_panel_lists`, and a boot-time ordering mistake there causes a
Temporal Dead Zone on `draw`.  The dynamic `import(...)` calls defer
resolution until after the boot module has run.
"""

DISPATCH_JS = r"""
import {
  canvas, ctx, dpr, view, board, s2b, itemHitTest, _anyItemHitTest,
  bringToTop,
} from "./tb_core.js";
import { drawBoardView } from "./tb_view.js";
import { _syncEditor } from "./tb_panel_fields.js";
import { syncItemList, updateCursorReadout, clearCursorReadout } from "./tb_panel_lists.js";
import { pgkitNotifyEntities, pgkitNotifyLocal } from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

let _statusTimer = null;

const DRAG_THRESHOLD = 3;
const state = {
  dragCanvas: null,
  dragItem:   null,
  mouse:      { sx: 0, sy: 0, inside: false },
};

let _lastCursor = "default";
function _setCursor(c) {
  if (c === _lastCursor) return;
  canvas.style.cursor = c;
  _lastCursor = c;
}

export function draw() {
  const cw = window.innerWidth;
  const ch = window.innerHeight;

  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  drawBoardView();

  const M = 6;
  ctx.strokeStyle = "#1e2836";
  ctx.lineWidth = 0.8;
  ctx.strokeRect(M + 0.5, M + 0.5, cw - 2 * M - 1, ch - 2 * M - 1);

  updateStatus();
}

function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;
  if (_statusTimer) return;

  const total   = board.items.length;
  const formats = new Set(board.items.map(it => it.formatSig)).size;
  const docs    = new Set(board.items.map(it => it.documentPath)).size;

  const parts = [
    total + " item" + (total === 1 ? "" : "s"),
    formats + " format" + (formats === 1 ? "" : "s"),
    docs + " doc" + (docs === 1 ? "" : "s"),
    "zoom " + (view.zoom * 100).toFixed(0) + "%",
  ];
  const text = parts.join("  \u00b7  ");
  if (el.textContent !== text) el.textContent = text;
  if (el.className !== "") el.className = "";
}

/* ==========================================================================
   CANVAS EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  const hit = itemHitTest(sx, sy);
  if (hit >= 0) {
    board.selectedIdx = hit;
    bringToTop(hit);
    const it = board.items[hit];
    state.dragItem = {
      idx: hit, startSx: sx, startSy: sy,
      startBx: it.bx, startBy: it.by, moved: false,
    };
    _setCursor("grabbing");
    syncItemList();
    _syncEditor();
    draw();
    return;
  }

  board.selectedIdx = -1;
  state.dragCanvas = {
    startSx: sx, startSy: sy,
    startPanX: view.panX, startPanY: view.panY,
  };
  _setCursor("grabbing");
  syncItemList();
  _syncEditor();
  draw();
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  state.mouse.sx = sx; state.mouse.sy = sy;
  state.mouse.inside = (sx >= 0 && sy >= 0 &&
                        sx < window.innerWidth && sy < window.innerHeight);

  if (state.mouse.inside) updateCursorReadout(sx, sy);
  else                    clearCursorReadout();

  if (state.dragItem) {
    const di = state.dragItem;
    const dx = sx - di.startSx;
    const dy = sy - di.startSy;
    if (!di.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) di.moved = true;
    if (di.moved) {
      const it = board.items[di.idx];
      it.bx = di.startBx + dx / view.zoom;
      it.by = di.startBy + dy / view.zoom;
    }
    draw();
    return;
  }

  if (state.dragCanvas) {
    const dc = state.dragCanvas;
    view.panX = dc.startPanX + (sx - dc.startSx);
    view.panY = dc.startPanY + (sy - dc.startSy);
    draw();
    return;
  }

  _setCursor(_anyItemHitTest(sx, sy) ? "grab" : "default");
});

window.addEventListener("mouseup", () => {
  const hadDrag = !!(state.dragItem || state.dragCanvas);
  state.dragItem   = null;
  state.dragCanvas = null;
  _setCursor("default");
  if (hadDrag) {
    pgkitNotifyEntities();
    pgkitNotifyLocal("view.panX");
    pgkitNotifyLocal("view.panY");
    draw();
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
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
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const hit = itemHitTest(sx, sy);
  if (hit >= 0) {
    const it = board.items[hit];
    import("./tb_krita.js").then(m => m.activateItemDocument(it)).catch(() => {});
    flashStatus("activating " + (it.documentName || "document"), "ok");
  }
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const k = e.key.toLowerCase();
  if (k === "r") { e.preventDefault(); import("./tb_krita.js").then(m => m.refreshFromKrita()).catch(() => {}); }
  else if (k === "g") { e.preventDefault(); import("./tb_core.js").then(m => { m.arrangeGrid(); draw(); }).catch(() => {}); }
  else if (k === "f") { e.preventDefault(); board._autoFitDone = true; import("./tb_core.js").then(m => m.fitAll()).catch(() => {}); }
  else if (k === "o") { e.preventDefault(); import("./tb_krita.js").then(m => m.openAllClosed()).catch(() => {}); }
  else if (k === "w") {
    e.preventDefault();
    board.showWordBoxes = !board.showWordBoxes;
    const cb = document.getElementById("wordBoxToggle");
    if (cb) cb.checked = board.showWordBoxes;
    pgkitNotifyLocal("board.showWordBoxes");
    draw();
  }
});

canvas.style.cursor = "default";
"""
