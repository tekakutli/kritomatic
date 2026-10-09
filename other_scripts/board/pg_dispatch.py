"""
pg_dispatch.py — draw() and the canvas event listeners.

Four drag modes, mutually exclusive:

    dragCanvas   pan the view (drag on empty space)
    dragFile     move the card (drag on the card's body)
    dragFile     resize the card (drag on a corner; mode="resize")
    dragFile     rotate the card (drag on the rotate handle;
                 mode="rotate")

The rotate handle is offered only on the selected card and sits
above its top edge.  During a rotate drag, holding Shift snaps the
rotation to 15° increments.  During a resize drag, holding Shift
preserves the card's aspect ratio as it was at drag start.

Keyboard: R refresh, G grid, F fit, O open-all-missing, P project
overlaps, H toggle panel.
"""

DISPATCH_JS = r"""
const DRAG_THRESHOLD = 3;

const state = {
  dragCanvas: null,
  dragFile:   null,
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
  _syncCardFields();
}

function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;
  if (_statusTimer) return;
  const opens  = board.files.filter(f => f.open).length;
  const closed = board.files.filter(f => !f.open && !f.missing).length;
  const miss   = board.files.filter(f => f.missing).length;
  const parts  = [
    opens + " open",
    closed + " closed",
  ];
  if (miss) parts.push(miss + " missing");
  parts.push("zoom " + (view.zoom * 100).toFixed(0) + "%");
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

  // 1) Rotate handle — offered only on the selected card.
  if (rotateHandleHitTest(sx, sy)) {
    const f = board.files[board.selectedIdx];
    state.dragFile = {
      idx:      board.selectedIdx,
      mode:     "rotate",
      startSx:  sx,
      startSy:  sy,
      centerX:  f.x + f.w / 2,
      centerY:  f.y + f.h / 2,
      startRotation: f.rotation,
      moved:    false,
    };
    canvas.style.cursor = "grabbing";
    draw();
    return;
  }

  // 2) Corner handle — takes priority over the body.
  const cornerHit = fileCornerHitTest(sx, sy);
  if (cornerHit) {
    board.selectedIdx = cornerHit.idx;
    bringToTop(cornerHit.idx);
    const f = board.files[cornerHit.idx];
    f.userSized = true;

    // Anchor: the diagonally-opposite corner in world space.  Its
    // offset from the centre, in the card's unrotated frame, is
    // (−dsx · w/2, −dsy · h/2); rotate that offset by the card's
    // rotation to get the world offset.
    const [dsx, dsy] = CORNER_SIGNS[cornerHit.corner];
    const cx = f.x + f.w / 2;
    const cy = f.y + f.h / 2;
    const [aox, aoy] = _rotateOffset(
      -dsx * f.w / 2,
      -dsy * f.h / 2,
      f.rotation
    );

    state.dragFile = {
      idx:         cornerHit.idx,
      mode:        "resize",
      corner:      cornerHit.corner,
      anchorX:     cx + aox,
      anchorY:     cy + aoy,
      startAspect: f.h / f.w,
      startSx:     sx,
      startSy:     sy,
      moved:       false,
    };
    canvas.style.cursor = _resizeCursorFor(cornerHit.corner);
    syncFileList();
    draw();
    return;
  }

  // 3) Card body — move.
  const hit = fileHitTest(sx, sy);
  if (hit >= 0) {
    board.selectedIdx = hit;
    bringToTop(hit);
    const f = board.files[hit];
    state.dragFile = {
      idx:        hit,
      mode:       "move",
      startSx:    sx,
      startSy:    sy,
      startFileX: f.x,
      startFileY: f.y,
      moved:      false,
    };
    canvas.style.cursor = "grabbing";
    syncFileList();
    draw();
    return;
  }

  // 4) Empty canvas — pan.
  board.selectedIdx = -1;
  state.dragCanvas = {
    startSx:   sx,
    startSy:   sy,
    startPanX: view.panX,
    startPanY: view.panY,
  };
  canvas.style.cursor = "grabbing";
  syncFileList();
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

  if (state.dragFile) {
    const df = state.dragFile;
    const dx = sx - df.startSx;
    const dy = sy - df.startSy;
    if (!df.moved && Math.hypot(dx, dy) > DRAG_THRESHOLD) df.moved = true;
    if (df.moved) {
      const f = board.files[df.idx];
      if (df.mode === "resize") {
        const [bx, by] = s2b(sx, sy);
        resizeCardFromCorner(f, df.corner, bx, by,
                             df.anchorX, df.anchorY,
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
  } else if (state.dragCanvas) {
    const dc = state.dragCanvas;
    view.panX = dc.startPanX + (sx - dc.startSx);
    view.panY = dc.startPanY + (sy - dc.startSy);
  } else {
    // Idle: pick the cursor that matches what a mousedown here would
    // start.  Rotate handle over corner over body over empty.
    if (rotateHandleHitTest(sx, sy)) {
      canvas.style.cursor = "grab";
    } else {
      const cornerHit = fileCornerHitTest(sx, sy);
      if (cornerHit) {
        canvas.style.cursor = _resizeCursorFor(cornerHit.corner);
      } else if (fileHitTest(sx, sy) >= 0) {
        canvas.style.cursor = "grab";
      } else {
        canvas.style.cursor = "default";
      }
    }
  }

  draw();
});

window.addEventListener("mouseup", () => {
  state.dragFile   = null;
  state.dragCanvas = null;
  canvas.style.cursor = "default";
  draw();
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mx = e.clientX - rect.left;
  const my = e.clientY - rect.top;
  const [bx, by] = s2b(mx, my);

  const factor  = e.deltaY < 0 ? 1.10 : 1 / 1.10;
  view.zoom = Math.max(0.05, Math.min(8, view.zoom * factor));

  view.panX = mx - bx * view.zoom;
  view.panY = my - by * view.zoom;

  draw();
}, { passive: false });

canvas.addEventListener("dblclick", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left;
  const sy = e.clientY - rect.top;
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
    if (board.selectedIdx >= 0) {
      e.preventDefault();
      removeFile(board.selectedIdx);
    }
  }
});

canvas.style.cursor = "default";
"""
