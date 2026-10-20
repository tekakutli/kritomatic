"""
bx_dispatch.py — draw() and the canvas / window event listeners.

The orchestrator.  draw() paints in one pass:

    1. paper fill
    2. drawFloorView() — steps, walls, boxes, handles, ghost, status
    3. sheet frame and registration marks

The canvas listeners (mousedown, mousemove, mouseup, contextmenu,
dblclick, wheel) each hold one branch per mode, and the branch order
is the priority:

    middle-button pan  >  Space-held left pan  >  handle-drag  >
    draw-box-place  >  body-drag  >  select

Nothing here decides what a click means beyond that priority; every
decision is delegated to the module that owns it.

Space-to-pan
------------
The Space key arms a pan mode borrowed from the cable playground.
Its state lives in bx_core's `spaceHeld` flag; the pointer state it
produces is the existing `panning` variable, shared with the middle-
button drag.  Only the trigger differs.

    keydown Space (target not an input)   →  spaceHeld = true
    mousedown left while spaceHeld        →  panning = {...}
    mousemove while panning               →  view.tx / view.ty shift
    mouseup                               →  panning = null
    keyup Space                           →  spaceHeld = false

Space-pan sits at the top of the mousedown priority so that, while
Space is held, no handle can be grabbed, no draw-box gesture can
start, and no box can be moved.  Releasing Space restores normal
interaction.  The Space keydown is preventDefault'ed so that a
button with focus does not also activate when Space is pressed.

Escape handling note for the popover
------------------------------------
bx_panel installs its own capture-phase Escape listener to close the
help popover without also disarming draw-box mode.  Nothing here
needs to change for that: the capture listener fires before this
file's bubble listener on the same window.

Handle dispatch and draw-box mode
---------------------------------
The handle branch runs before the draw-box branch on every left
mousedown.  That order is what lets the user exit draw-box mode by
clicking a handle on the box they just placed.  Arming the mode
clears any selection, so at the moment of arming hitTestHandle
returns null and the mousedown falls through to the place-or-draw
gesture.  But placing a box inside the mode selects it — mouseup
calls setSelected — and thereafter the box's handles are drawn, and
clicking one of them exits the mode and dispatches the matching drag
in the same gesture.  Without this ordering the handle click would
be swallowed by the draw-box branch and start a new box instead.

Handle dispatch, by hitTestHandle's type string:

    "rotate"         startRotate        — spin the box about its centre
    "corner"         startResizeCorner  — resize both axes, pinning
                                          the diagonally opposite corner
    "edge"           startResize        — resize one axis, pinning
                                          the opposite edge

Place vs. drag-draw
-------------------
Inside draw-box mode, a mousedown over the canvas starts
`pendingPlace` — a small state object recording the down point in
both screen and world coordinates.  The mousemove handler watches
the distance from that point:

    • below DRAG_CANCEL pixels, the state stays in "click" mode and no
      drag is started
    • once past DRAG_CANCEL, the state flips to "drag" mode, and every
      subsequent mousemove updates the cursor's world position on the
      state (`dragWx`, `dragWy`)

On mouseup the two modes commit differently:

    • click mode: a box of the current W×H×Rot preset, centred on the
      down point
    • drag mode: an axis-aligned box whose rectangle is the diagonal
      from the down point to the up point; rotation is forced to zero
      because two arbitrary points do not uniquely define a rotated
      rectangle

A drag whose rectangle is thinner than MIN_BOX_MM in either axis
falls back to click mode — a jittery click is still a click.

Translation
-----------
updateStatus is the only place in this module that writes
user-facing prose.  Every message it emits — the panning banner, the
six drag/draw banners, the collision warning, the hover and cursor
readouts — comes from T() in bx_i18n.py.  The place-gesture flash
messages on mouseup also route through T().
"""


DISPATCH_JS = r"""
/* ==========================================================================
   MAIN DRAW
   ========================================================================== */

function draw() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(0, 0, cw, ch);

  drawFloorView();
  drawSheetFrame(cw, ch);

  updatePanelOpacity();
}

/* ==========================================================================
   EVENT HANDLERS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  /* Middle-button drag pans; the same `panning` state and mousemove
     branch handle both this and the Space-held left drag below. */
  if (e.button === 1) {
    e.preventDefault();
    panning = { sx: e.clientX, sy: e.clientY,
                tx: view.tx, ty: view.ty };
    updateCursor(mouse.sx, mouse.sy);
    return;
  }
  if (e.button !== 0) return;

  /* Space-held left drag pans too.  This sits above every other
     branch on purpose: while Space is held, a left press must never
     grab a handle, start a draw-box gesture, or move a box.  The
     user is explicitly asking to move the view. */
  if (spaceHeld) {
    e.preventDefault();
    panning = { sx: e.clientX, sy: e.clientY,
                tx: view.tx, ty: view.ty };
    updateCursor(mouse.sx, mouse.sy);
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const [wx, wy] = s2w(sx, sy);

  /* Handle FIRST, before the draw-box branch.  This is what lets the
     user click a handle on the box they just placed in draw-box mode
     and have the click grab the handle instead of starting a new
     box.  If nothing is selected (the state right after arming the
     mode), hitTestHandle returns null and the mousedown falls
     through to the draw-box branch below, which is the desired
     behaviour. */
  const h = hitTestHandle(sx, sy);
  if (h) {
    if (drawBoxMode) setDrawBoxMode(false);
    if (h.type === "rotate")      startRotate(selectedBox, wx, wy);
    else if (h.type === "corner") startResizeCorner(selectedBox, h.index);
    else                          startResize(selectedBox, h.index);
    updateCursor(sx, sy);
    return;
  }

  /* Draw-box mode owns the whole canvas.  Existing box bodies are
     not interactive while it is armed: a left mousedown on a box
     starts a place-or-draw gesture rather than a move drag, which is
     what lets a box be drawn over an existing one. */
  if (drawBoxMode) {
    pendingPlace = {
      wx, wy,
      sx, sy,
      drag: false,
      dragWx: wx,
      dragWy: wy,
    };
    return;
  }

  /* Normal interaction: body → empty floor. */
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    const b = placedBoxes.splice(idx, 1)[0];
    placedBoxes.push(b);
    setSelected(b);
    drag = { index: placedBoxes.length - 1,
             ox: b.x - wx, oy: b.y - wy };
    updateCursor(sx, sy);
    return;
  }

  /* Empty floor.  Nothing to drag, nothing to place — the click
     simply deselects whatever was selected. */
  if (selectedBox) setSelected(null);
  else            draw();
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const inside = (sx >= 0 && sy >= 0 &&
                  sx < canvas.clientWidth && sy < canvas.clientHeight);

  /* The panning branch is shared by the middle-button drag and the
     Space-held left drag.  Whichever started the pan, this is how it
     moves. */
  if (panning) {
    view.tx = panning.tx + (e.clientX - panning.sx);
    view.ty = panning.ty + (e.clientY - panning.sy);
    draw(); return;
  }

  if (inside) { mouse.sx = sx; mouse.sy = sy; mouse.inside = true; }
  else        { mouse.inside = false; }

  if (handleDrag) {
    const [wx, wy] = s2w(sx, sy);
    if (handleDrag.type === "rotate") {
      updateRotate(wx, wy, e.shiftKey);
    } else if (handleDrag.type === "resize-corner") {
      updateResizeCorner(wx, wy);
    } else {
      updateResize(wx, wy);
    }
    updateCursor(sx, sy);
    draw(); return;
  }

  if (drag) {
    const [wx, wy] = s2w(sx, sy);
    const b = placedBoxes[drag.index];
    moveBoxSafely(b, wx + drag.ox, wy + drag.oy);
    updateCursor(sx, sy);
    draw(); return;
  }

  /* Place-or-draw gesture.  Flip into drag mode the first time the
     cursor crosses the threshold; from then on, keep tracking the
     cursor's world position.  Below the threshold, nothing happens —
     the state stays in click mode. */
  if (pendingPlace) {
    if (!pendingPlace.drag) {
      const dx = sx - pendingPlace.sx, dy = sy - pendingPlace.sy;
      if (Math.hypot(dx, dy) > DRAG_CANCEL) {
        pendingPlace.drag = true;
      }
    }
    if (pendingPlace.drag) {
      const [wx, wy] = s2w(sx, sy);
      pendingPlace.dragWx = wx;
      pendingPlace.dragWy = wy;
    }
  }

  /* Hover detection is skipped in draw-box mode: the cursor is not
     "hovering" a box, it is over a potential new box.  This is what
     lets the ghost follow the cursor even when the cursor is on top
     of an existing box. */
  if (!drawBoxMode && inside) {
    const [wx, wy] = s2w(sx, sy);
    hoveredBox = null;
    const idx = hitTestBox(wx, wy);
    if (idx >= 0) hoveredBox = placedBoxes[idx];
  } else {
    hoveredBox = null;
  }

  if (inside && !panelHovered && !drag && !handleDrag && !panning) {
    panelLastActivity = performance.now();
  }

  updateCursor(sx, sy);
  draw();
});

window.addEventListener("mouseup", (e) => {
  /* The panning check runs first and is not gated on button number:
     a middle-button drag and a Space-held left drag both end here,
     and both must clear the state.  Before this check was unified,
     the Space-held pan (button 0) fell through to the handleDrag /
     drag / pendingPlace branches — none of which were ever set — and
     left `panning` stuck. */
  if (panning) {
    panning = null;
    updateCursor(mouse.sx, mouse.sy);
    return;
  }
  if (e.button !== 0) return;

  if (handleDrag) {
    handleDrag = null;
    syncSelectionInputs();
    updateCursor(mouse.sx, mouse.sy);
    draw(); return;
  }
  if (drag) {
    drag = null;
    syncSelectionInputs();
    updateCursor(mouse.sx, mouse.sy);
    draw(); return;
  }

  if (pendingPlace) {
    const p = pendingPlace;
    pendingPlace = null;

    /* Two commit paths: drag-draw (an axis-aligned rectangle whose
       two corners are the down point and the current cursor world
       position) and click-place (a preset W×H×Rot at the down point).
       A drag whose rectangle collapses to below MIN_BOX_MM in either
       axis is treated as a click — a jittery release is still a
       release. */
    let w, h, cx, cy, rot;

    if (p.drag) {
      const x0 = Math.min(p.wx, p.dragWx);
      const x1 = Math.max(p.wx, p.dragWx);
      const y0 = Math.min(p.wy, p.dragWy);
      const y1 = Math.max(p.wy, p.dragWy);
      const dw = x1 - x0;
      const dh = y1 - y0;

      if (dw < MIN_BOX_MM || dh < MIN_BOX_MM) {
        /* Degenerate drag — fall through to click-place. */
        const params = getBoxParams();
        w = params.w; h = params.h; rot = params.rot;
        cx = p.wx; cy = p.wy;
      } else {
        w = dw; h = dh; rot = 0;
        cx = (x0 + x1) / 2;
        cy = (y0 + y1) / 2;
      }
    } else {
      const params = getBoxParams();
      w = params.w; h = params.h; rot = params.rot;
      cx = p.wx; cy = p.wy;
    }

    if (boxOverlapsWalls(cx, cy, w, h, rot)) {
      flashStatus(T("msgPlaceCollision"), "bad");
    } else {
      boxCounter += 1;
      const b = { x: cx, y: cy, w, h, rot,
                  name: `Box ${boxCounter}` };
      placedBoxes.push(b);
      setSelected(b);
      flashStatus(T("msgPlacedOk"), "ok");
    }
    draw();
  }
});

canvas.addEventListener("dblclick", (e) => {
  if (drawBoxMode) return;
  if (spaceHeld)   return;    // Space is a pan gesture, not a select
  const rect = canvas.getBoundingClientRect();
  const [wx, wy] = s2w(e.clientX - rect.left, e.clientY - rect.top);
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    setSelected(placedBoxes[idx]);
    const ni = document.getElementById("boxName");
    if (ni) { ni.focus(); ni.select(); }
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (drawBoxMode) return;
  if (spaceHeld)   return;    // Space is a pan gesture, not a delete
  const rect = canvas.getBoundingClientRect();
  const [wx, wy] = s2w(e.clientX - rect.left, e.clientY - rect.top);
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    const wasSel = placedBoxes[idx] === selectedBox;
    if (placedBoxes[idx] === hoveredBox) hoveredBox = null;
    placedBoxes.splice(idx, 1);
    if (wasSel) setSelected(null);
    draw();
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const factor = Math.exp(-e.deltaY * 0.0015);
  zoomAt(sx, sy, factor);
  draw();
}, { passive: false });

/* ==========================================================================
   KEYBOARD
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  /* Space arms pan mode.  e.code rather than e.key: with Space,
     e.key is " " and that is fiddly to compare; e.code is "Space"
     and unambiguous.  preventDefault is required — a focused button
     activates on Space, and would fire its click even while the
     user is holding Space to pan. */
  if (e.code === "Space" && !spaceHeld) {
    spaceHeld = true;
    if (mouse.inside && !panning) canvas.style.cursor = "grab";
    e.preventDefault();
    return;
  }

  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    doSave(true);
    return;
  }

  /* Escape unwinds in priority order: an armed draw mode is the
     outermost thing on the interaction stack, so it is what
     Escape disarms first; if no mode is armed, Escape clears the
     selection.  (The help popover installs its own capture-phase
     Escape listener that fires before this one — see the module
     docstring.) */
  if (e.key === "Escape") {
    if (drawBoxMode) {
      setDrawBoxMode(false);
    } else {
      setSelected(null);
    }
    return;
  }

  if (drawBoxMode) return;   // the remaining shortcuts are mode-off only

  if (e.key === "r" || e.key === "R") {
    e.preventDefault();
    const deg = e.shiftKey ? 15 : 90;
    rotateSelection(deg);
    draw();
  } else if (e.key === "Delete" || e.key === "Backspace") {
    if (selectedBox) {
      const idx = placedBoxes.indexOf(selectedBox);
      if (idx >= 0) placedBoxes.splice(idx, 1);
      setSelected(null);
    }
  } else if (e.key === "0") {
    fitView();
    draw();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    spaceHeld = false;
    /* If a pan is in flight, leave it in flight: the mouseup that
       ends it will also refresh the cursor.  Otherwise refresh the
       cursor now, so the open-hand icon disappears the moment Space
       is released. */
    if (!panning) updateCursor(mouse.sx, mouse.sy);
  }
});

window.addEventListener("blur", () => {
  /* A Space pressed while the window had focus, followed by a click
     on another window, would otherwise leave `spaceHeld` stuck on
     for the next focus.  Reset both this and any in-flight pan. */
  spaceHeld = false;
  if (panning) {
    panning = null;
    updateCursor(mouse.sx, mouse.sy);
  }
});

/* ==========================================================================
   CURSOR + STATUS
   ========================================================================== */

function updateCursor(sx, sy) {
  /* Pan state first: it outranks every other cursor.  While a pan is
     in flight, the closed-hand cursor; while Space is merely held,
     the open hand.  Only after both of those, the mode / hover /
     handle cursors. */
  if (panning)                       { canvas.style.cursor = "grabbing"; return; }
  if (handleDrag || drag)            { canvas.style.cursor = "grabbing"; return; }
  if (spaceHeld)                     { canvas.style.cursor = "grab";     return; }
  if (drawBoxMode)                   { canvas.style.cursor = "crosshair"; return; }

  const h = hitTestHandle(sx, sy);
  if (h) {
    if (h.type === "rotate")      canvas.style.cursor = "grab";
    else if (h.type === "corner") canvas.style.cursor = "nwse-resize";
    else                          canvas.style.cursor = "grab";
    return;
  }

  const [wx, wy] = s2w(sx, sy);
  if (hitTestBox(wx, wy) >= 0)       { canvas.style.cursor = "move";     return; }
  canvas.style.cursor = "crosshair";
}

function updateStatus(validFlags) {
  const el = document.getElementById("status");
  if (!el || statusTimer) return;

  if (panning) {
    el.textContent = T("stPanningSpace");
    el.className = "";
    return;
  }
  if (handleDrag) {
    const b = handleDrag.b;
    const verb = handleDrag.type === "rotate"        ? T("stVerbRotating")
               : handleDrag.type === "resize-corner" ? T("stVerbResizingBoth")
               :                                       T("stVerbResizing");
    el.textContent = T("stHandleDrag")(
      verb,
      b.x.toFixed(0), b.y.toFixed(0),
      b.w.toFixed(0), b.h.toFixed(0),
      b.rot.toFixed(0));
    el.className = "";
    return;
  }
  if (drag) {
    const b = placedBoxes[drag.index];
    const inv = boxIsInvalid(b);
    el.textContent = T("stDragging")(
      inv,
      b.x.toFixed(0), b.y.toFixed(0), b.rot.toFixed(0));
    el.className = inv ? "warn" : "";
    return;
  }
  if (pendingPlace && pendingPlace.drag) {
    const x0 = Math.min(pendingPlace.wx, pendingPlace.dragWx);
    const x1 = Math.max(pendingPlace.wx, pendingPlace.dragWx);
    const y0 = Math.min(pendingPlace.wy, pendingPlace.dragWy);
    const y1 = Math.max(pendingPlace.wy, pendingPlace.dragWy);
    const dw = x1 - x0, dh = y1 - y0;
    const inv = boxOverlapsWalls((x0 + x1) / 2, (y0 + y1) / 2, dw, dh, 0);
    el.textContent = T("stDrawing")(inv, dw.toFixed(0), dh.toFixed(0));
    el.className = inv ? "warn" : "";
    return;
  }
  if (!mouse.inside) { el.textContent = ""; el.className = ""; return; }

  /* Space held (but no pan in flight): the user is one press away
     from panning.  Say so. */
  if (spaceHeld) {
    el.textContent = T("stSpaceHeld");
    el.className = "";
    return;
  }

  /* Draw mode: the cursor is a potential new box, not a hovering
     cursor.  Report the box that a click would place. */
  if (drawBoxMode) {
    const [wx, wy] = s2w(mouse.sx, mouse.sy);
    const { w, h, rot } = getBoxParams();
    const bad = boxOverlapsWalls(wx, wy, w, h, rot);
    el.textContent = T("stDrawPreview")(
      wx.toFixed(0), wy.toFixed(0),
      w.toFixed(0), h.toFixed(0), rot.toFixed(0), bad);
    el.className = bad ? "bad" : "ok";
    return;
  }

  if (hoveredBox) {
    const b = hoveredBox;
    const inv = boxIsInvalid(b);
    el.textContent = T("stHover")(
      inv,
      b.name || T("boxUnnamed"),
      b.x.toFixed(0), b.y.toFixed(0), b.rot.toFixed(0));
    el.className = inv ? "warn" : "";
    return;
  }

  const [wx, wy] = s2w(mouse.sx, mouse.sy);
  el.textContent = T("stCursor")(wx.toFixed(0), wy.toFixed(0));
  el.className = "";
}
"""
