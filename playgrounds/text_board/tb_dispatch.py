"""
tb_dispatch.py — draw() and the canvas event listeners.

Two drag modes, mutually exclusive:

    dragCanvas   pan the view
    dragItem     move a card

Dragging a card moves it off its document position; a subsequent
Refresh snaps it back, because `_rebaseAllItems` recomputes board
positions from the document slot on every merge.  That is the
intended behaviour: the document is the source of truth for layout,
and manual drags are a scratchpad on top of it.

The cursor readout is updated on every mousemove: it maps the pointer
to a document and reports the document-space coordinates.

Keyboard: R refresh, G grid, F fit, W toggle word boxes, O open all
picked files, H toggle panel.

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
changes.  The hit test behind the decision is `_anyItemHitTest` (see
tb_core.py) — boolean, no allocation, no z-sort.
"""

DISPATCH_JS = r"""
const DRAG_THRESHOLD = 3;

const state = {
  dragCanvas: null,
  dragItem:   null,
  mouse:      { sx: 0, sy: 0, inside: false },
};

/* Cursor state tracked here so `canvas.style.cursor` is only written
   when it actually changes.  The default on startup is "default". */
let _lastCursor = "default";

function _setCursor(c) {
  if (c === _lastCursor) return;
  canvas.style.cursor = c;
  _lastCursor = c;
}

/* ==========================================================================
   DRAW
   ========================================================================== */

function draw() {
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
  const docs    = _documentSlots.size;
  const closed  = closedWantedPaths().length;

  const parts = [
    total + " item" + (total === 1 ? "" : "s"),
    formats + " format" + (formats === 1 ? "" : "s"),
    docs + " doc" + (docs === 1 ? "" : "s"),
    "zoom " + (view.zoom * 100).toFixed(0) + "%",
  ];
  if (closed > 0) {
    parts.push(closed + " closed file" + (closed === 1 ? "" : "s"));
  }
  const text = parts.join("  \u00b7  ");
  if (el.textContent !== text) el.textContent = text;
  const cls = closed > 0 ? "warn" : "";
  if (el.className !== cls) el.className = cls;
}

/* ==========================================================================
   EVENTS
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
      idx:       hit,
      startSx:   sx,
      startSy:   sy,
      startBx:   it.bx,
      startBy:   it.by,
      moved:     false,
    };
    _setCursor("grabbing");
    syncItemList();
    _syncEditor();
    draw();
    return;
  }

  board.selectedIdx = -1;
  state.dragCanvas = {
    startSx:   sx,
    startSy:   sy,
    startPanX: view.panX,
    startPanY: view.panY,
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
  state.mouse.sx = sx;
  state.mouse.sy = sy;
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

  // Pure hover: only the CSS cursor style can change.  `_anyItemHitTest`
  // is a boolean scan with no allocation and no z-sort; `_setCursor`
  // only writes `style.cursor` when the value actually differs from
  // the previous frame.  No `draw()` — the canvas is pixel-identical.
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
    activateItemDocument(it);
    flashStatus("activating " + (it.documentName || "document"), "ok");
  }
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
            t.tagName === "SELECT")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const k = e.key.toLowerCase();
  if (k === "r") { e.preventDefault(); refreshFromKrita(); }
  else if (k === "g") { e.preventDefault(); arrangeGrid(); draw(); }
  else if (k === "f") {
    e.preventDefault();
    board._autoFitDone = true;
    fitAll();
  }
  else if (k === "o") { e.preventDefault(); openAllClosed(); }
  else if (k === "w") {
    e.preventDefault();
    board.showWordBoxes = !board.showWordBoxes;
    const cb = document.getElementById("wordBoxToggle");
    if (cb) cb.checked = board.showWordBoxes;
    pgkitNotifyLocal("board.showWordBoxes");
    draw();
  }
});
"""
