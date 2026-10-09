"""
pg_dispatch.py — draw() and the canvas event listeners.

Four drag modes, mutually exclusive:

    dragCanvas   pan the whole view (drag on empty space)
    dragCard     move one card on the board (drag on a card)
    (wheel)      zoom about the cursor
    (dblclick)   activate the clicked document in Krita

A click on a card that does not cross the drag threshold selects the
card.  A card that is already selected and re-clicked also activates
the matching document in Krita — the same action double-click does.
This makes selection and activation both single-action gestures while
still allowing drag-move.
"""

DISPATCH_JS = r"""
const DRAG_THRESHOLD = 3;

const state = {
  dragCanvas: null,   // {startSx, startSy, startPanX, startPanY}
  dragCard:   null,   // {idx, startSx, startSy, startDocX, startDocY, moved}
  mouse:      { sx: 0, sy: 0, inside: false },
};

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
  const parts = [];
  parts.push(board.docs.length + " doc" + (board.docs.length === 1 ? "" : "s"));
  parts.push("zoom " + (view.zoom * 100).toFixed(0) + "%");
  if (board.selectedIdx >= 0 && board.selectedIdx < board.docs.length) {
    parts.push(board.docs[board.selectedIdx].name);
  }
  el.textContent = parts.join("  ·  ");
  el.className = "";
}

/* ==========================================================================
   EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (e.button !== 0) return;
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;

  const hit = docHitTest(sx, sy);
  if (hit >= 0) {
    const wasSelected = (board.selectedIdx === hit);
    board.selectedIdx = hit;

    const d = board.docs[hit];
    state.dragCard = {
      idx:        hit,
      startSx:    sx,
      startSy:    sy,
      startDocX:  d.x,
      startDocY:  d.y,
      moved:      false,
      wasSelected: wasSelected,
    };
    canvas.style.cursor = "grabbing";
    syncDocList();
    draw();
    return;
  }

  // Empty canvas: begin pan.
  board.selectedIdx = -1;
  state.dragCanvas = {
    startSx:   sx,
    startSy:   sy,
    startPanX: view.panX,
    startPanY: view.panY,
  };
  canvas.style.cursor = "grabbing";
  syncDocList();
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

  if (state.dragCard) {
    const dd = state.dragCard;
    const dx = sx - dd.startSx;
    const dy = sy - dd.startSy;
    if (!dd.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) dd.moved = true;
    if (dd.moved) {
      const d = board.docs[dd.idx];
      d.x = dd.startDocX + dx / view.zoom;
      d.y = dd.startDocY + dy / view.zoom;
    }
  } else if (state.dragCanvas) {
    const dc = state.dragCanvas;
    view.panX = dc.startPanX + (sx - dc.startSx);
    view.panY = dc.startPanY + (sy - dc.startSy);
  }
  draw();
});

window.addEventListener("mouseup", () => {
  if (state.dragCard) {
    // A click (no drag) on an already-selected card acts as
    // "activate in Krita", so a single click both selects and
    // activates when the user is already looking at that card.
    if (!state.dragCard.moved && state.dragCard.wasSelected) {
      const d = board.docs[state.dragCard.idx];
      if (d) activateDocument(d);
    }
  }
  state.dragCard   = null;
  state.dragCanvas = null;
  canvas.style.cursor = "default";
  draw();
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;

  // Board point under cursor before the zoom change.
  const [bx, by] = s2b(mx, my);

  const factor  = e.deltaY < 0 ? 1.10 : 1 / 1.10;
  view.zoom = Math.max(0.05, Math.min(8, view.zoom * factor));

  // Keep (bx, by) under the cursor after the zoom change.
  view.panX = mx - bx * view.zoom;
  view.panY = my - by * view.zoom;

  draw();
}, { passive: false });

canvas.addEventListener("dblclick", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
  const hit = docHitTest(sx, sy);
  if (hit >= 0) activateDocument(board.docs[hit]);
});

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (e.ctrlKey || e.metaKey || e.altKey) return;

  const k = e.key.toLowerCase();
  if (k === "r") { e.preventDefault(); refreshFromKrita(); }
  else if (k === "g") { e.preventDefault(); arrangeGrid(); draw(); }
  else if (k === "f") { e.preventDefault(); fitAll(); }
  else if (k === "delete" || k === "backspace") {
    if (board.selectedIdx >= 0) {
      e.preventDefault();
      removeDoc(board.selectedIdx);
    }
  }
});

canvas.style.cursor = "default";
"""
