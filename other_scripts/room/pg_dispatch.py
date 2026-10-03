"""
pg_dispatch.py — the draw() orchestrator and the canvas event
listeners.

The top of the interaction stack.  draw() runs every renderer in the
correct order and then calls the four panel refreshers.  The five
canvas listeners (mousedown, mousemove, mouseup, contextmenu, wheel)
each hold one branch per mode, and the branch order is the priority:
pan > ruler > drawing > select/drag > nothing.

Nothing here does rendering itself; nothing here decides what a click
means beyond the mode priority.  Everything else is delegated to the
module that owns the concern.

draw() order
------------
    1. paper-fill the whole canvas
    2. clip to the floor band, run drawFloorView
    3. clip to the wall band, run drawWallView
    4. the wall→plan arrow field (from pg_arrows)
    5. the cross-view ghost marker (only during a Shift-held draw)
    6. the hover callout, on top of everything
    7. the divider rule and the section ribbon
    8. the plan-band scale bar
    9. the sheet frame, margin ticks, registration marks
   10. status / buttons / title block / ruler + cable inspector sync

Two of the items are positioned for z-order, not for ownership:

    • The wall→plan arrow field is drawn after both bands because it
      crosses between them; it is the one drawing pass that does not
      belong to either band's clip.

    • The divider rule, the section ribbon, and the plan-band scale
      bar are drawn AFTER the arrow field.  Every one of them carries
      a paper-coloured backing plate — the rule is a solid ink fill,
      the ribbon draws a paper rectangle behind each caption, and the
      scale bar draws a paper rectangle behind its two-tone bar — so
      drawing them last is what keeps their text legible when an
      arrow crosses the plan band's top-right corner or the divider.
      Drawing them earlier left them partially hidden behind the
      arrow strokes.

Wheel handling
--------------
Over the floor band, the wheel does a uniform zoom about the cursor.

Over the wall band, the wheel splits:

    plain wheel       uniform zoom about the cursor
    Shift+wheel       horizontal-only stretch about the cursor — the
                      strip dilates in u alone, every vertical
                      dimension is untouched.  This is the wall-only
                      "spread a packed wall out sideways" gesture.

The Shift split exists because the wall band is anisotropic — it has
two independent scale factors — while the floor band is isotropic and
has no X-only case to expose.

View reset
----------
Home resets both views to their base fit: uniform zoom, X stretch,
and pan all at once.  It routes through pg_navigation's resetZoomPan,
which is also what the panel's zoom-reset button calls, so the two
surfaces never drift.  The key is Home specifically: 0 was a poor
choice — it collides with numeric entry on AZERTY and QWERTZ, and it
is easy to hit while typing into a panel field.
"""


DISPATCH_JS = r"""
/* ==========================================================================
   MAIN DRAW DISPATCH
   ========================================================================== */

function draw() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(0, 0, cw, ch);

  ctx.save();
  ctx.beginPath(); ctx.rect(0, 0, cw, layout.floorH); ctx.clip();
  drawFloorView();
  ctx.restore();

  ctx.save();
  ctx.beginPath(); ctx.rect(0, layout.wallY, cw, layout.wallH); ctx.clip();
  drawWallView();
  ctx.restore();

  drawWallToPlanArrows();
  drawCrossViewGhosts();
  drawHoverCallout();

  /* The divider rule, the section ribbon, and the plan-band scale bar
     are drawn AFTER the arrow field.  Every one of them carries a
     paper-coloured backing plate — the rule is a solid ink fill, the
     ribbon draws a paper rectangle behind each caption, and the scale
     bar draws a paper rectangle behind its two-tone bar — so drawing
     them last is what keeps their text legible when an arrow crosses
     the plan band's top-right corner or the divider.  Drawing them
     earlier left them partially hidden behind the arrow strokes. */
  ctx.fillStyle = PALETTE.ink;
  ctx.fillRect(0, layout.dividerY,     cw, 1);
  ctx.fillRect(0, layout.dividerY + 2, cw, 1);
  drawSectionRibbon(cw);
  drawScaleBar();

  drawSheetFrame(cw, ch);

  updateStatus();
  updateDrawButton();
  updateRulerButton();
  updateTitleBlock();
  _rulerSyncMeasureInspector();
  _cableSyncInspector();
}

/* ==========================================================================
   EVENT HANDLERS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  /* Clicking the canvas takes focus off whatever panel field was
     last edited.  Without this, a global shortcut like Home is
     silently dead until the user clicks somewhere else — the
     keydown handler's own `if target is INPUT return` guard eats
     the key instead. */
  if (document.activeElement && document.activeElement.blur) {
    document.activeElement.blur();
  }

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const view = whichView(sy);

  if (e.button === 1) {
    if (!view) return;
    e.preventDefault();
    state.pan = { view, lastSx: sx, lastSy: sy };
    canvas.style.cursor = "grabbing";
    return;
  }

  if (e.button === 0 && spaceHeld) {
    if (!view) return;
    e.preventDefault();
    state.pan = { view, lastSx: sx, lastSy: sy };
    canvas.style.cursor = "grabbing";
    return;
  }

  if (e.button !== 0) return;
  if (!view) return;

  /* Ruler mode intercepts the left click before anything else.  The
     ruler is a floor-plan tool; a click in the wall strip while it is
     armed is ignored.

     Click flow, in order:

       1. If the canvas numeric input is open, the first click just
          closes it (the blur handler also runs, so this is idempotent).

       2. If a first point is placed, the click commits the measurement
          — at the locked pendingEnd if one is set, otherwise at the
          cursor-derived endpoint.

       3. If a completed measurement's pill is under the cursor, the
          click SELECTS it.  Selection populates the MEASURE section
          in the panel; it does not delete.

       4. Otherwise (empty canvas), the click deselects any selected
          measurement and places the first point of a new one. */
  if (state.ruler.active) {
    if (view !== "floor") return;   // ruler is a floor-plan tool

    if (_RULER_NUM_INPUT_EL &&
        _RULER_NUM_INPUT_EL.classList.contains("show")) {
      _rulerCloseNumericInput();
      return;
    }

    const [wx, wy] = s2wFloor(sx, sy);
    _rulerUpdateSkipState(wx, wy, e.shiftKey);
    const pt = rulerSnapPoint(wx, wy);

    /* (2) commit a pending measurement */
    if (state.ruler.pending) {
      const p = state.ruler.pending;
      let axis, r2;
      if (state.ruler.pendingEnd) {
        axis = state.ruler.pendingAxis
             || state.ruler.axisPref
             || "aligned";
        r2 = state.ruler.pendingEnd;
      } else {
        axis = _rulerPickAxis(p.x, p.y, pt.x, pt.y,
                              state.ruler.axisPref, e.altKey);
        r2 = _rulerResolveSecondPoint(p, pt, axis);
      }
      if (Math.hypot(r2.x - p.x, r2.y - p.y) < 1.0) { draw(); return; }
      state.ruler.measures.push({
        x0: p.x, y0: p.y,
        x1: r2.x, y1: r2.y,
        ox1: pt.x, oy1: pt.y,
        axis,
      });
      /* Auto-select the freshly committed measurement so its
         values appear immediately in the panel. */
      state.ruler.selected          = state.ruler.measures.length - 1;
      state.ruler.pending           = null;
      state.ruler.axisPref          = null;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      _rulerCloseNumericInput();
      draw();
      return;
    }

    /* (3) select a measurement by clicking its pill */
    const hi = _rulerHitMeasure(sx, sy);
    if (hi >= 0) {
      state.ruler.selected = hi;
      _rulerSyncMeasureInspector();
      draw();
      return;
    }

    /* (4) empty canvas: deselect and place first point */
    state.ruler.selected          = -1;
    state.ruler.pending           = pt;
    state.ruler.pendingJumpedStep = false;
    state.ruler.ignoreSteps       = false;
    state.ruler._hasLeftStepEdge  = false;
    state.ruler._lastDStep        = Infinity;
    state.ruler.pendingEnd        = null;
    state.ruler.pendingAxis       = null;
    _rulerCloseNumericInput();
    _rulerUpdateSkipState(wx, wy, e.shiftKey);
    draw();
    return;
  }

  if (drawing) {
    if (drawing.view === null) drawing.view = view;
    else if (drawing.view !== view) return;
    addDrawPoint(sx, sy, e.altKey);
    return;
  }

  if (view === "floor") {
    const g = nearestFloorGridLine(sx, sy);
    if (g) { state.dragGrid = { grid: g, view: "floor" }; return; }
    const v = nearestFloorVertex(sx, sy);
    if (v) {
      state.selectedCable = v.cable;
      state.selectedVertex = v;
      state.dragVertex = { ...v, view: "floor", corner: null };
      draw(); return;
    }
    const [wx, wy] = s2wFloor(sx, sy);
    const tol = 12 / viewFloor.scale;
    for (let i = state.floorCables.length - 1; i >= 0; i--) {
      const c = state.floorCables[i];
      if (drawing && drawing.baseCableId === c.id) continue;
      if (polylineHitView("floor", wx, wy, c, tol)) {
        state.selectedCable = c;
        state.selectedVertex = null;
        draw(); return;
      }
    }
    state.selectedCable = null; state.selectedVertex = null; draw();
  } else {
    const g = nearestWallGridLine(sx, sy);
    if (g) { state.dragGrid = { grid: g, view: "wall" }; return; }
    const v = nearestWallVertex(sx, sy);
    if (v) {
      state.selectedCable = v.cable;
      state.selectedVertex = v;
      const a = anchors.get(v.cable.anchorIds[v.index]);
      const corner = computeCornerInfo(a);
      state.dragVertex = { ...v, view: "wall", corner };
      draw(); return;
    }
    const [u, vv] = s2wWall(sx, sy);
    const tol = 12 / Math.min(viewWall.scaleX, viewWall.scaleY);
    for (let i = state.wallCables.length - 1; i >= 0; i--) {
      const c = state.wallCables[i];
      if (drawing && drawing.baseCableId === c.id) continue;
      if (polylineHitView("wall", u, vv, c, tol)) {
        state.selectedCable = c;
        state.selectedVertex = null;
        draw(); return;
      }
    }
    state.selectedCable = null; state.selectedVertex = null; draw();
  }
});

canvas.addEventListener("contextmenu", (e) => {
  /* In ruler mode the right-click is a two-stage cancel: drop the
     pending point if there is one, otherwise clear the selection and
     disarm the mode. */
  if (state.ruler.active) {
    e.preventDefault();
    if (state.ruler.pending) {
      state.ruler.pending           = null;
      state.ruler.axisPref          = null;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      _rulerCloseNumericInput();
    } else {
      state.ruler.active            = false;
      state.ruler.pendingJumpedStep = false;
      state.ruler.ignoreSteps       = false;
      state.ruler._hasLeftStepEdge  = false;
      state.ruler._lastDStep        = Infinity;
      state.ruler.pendingEnd        = null;
      state.ruler.pendingAxis       = null;
      state.ruler.selected          = -1;
      _rulerCloseNumericInput();
      updateRulerButton();
    }
    draw();
    return;
  }
  if (!drawing) return;
  e.preventDefault();
  if (drawing.anchorIds.length > 0) {
    drawing.anchorIds.pop();
    if (drawing.view === "wall" && drawing.anchorIds.length > 0) {
      const lastId = drawing.anchorIds[drawing.anchorIds.length - 1];
      const a = anchors.get(lastId);
      if (a && a.space === "wall-edge") enterFocus(a.segIdx);
    } else if (drawing.view === "wall") {
      exitFocus();
    }
    draw();
  }
});

/* Wheel zoom.  After changing the view transform, state.hoveredRoute
   is a reference to a route object from the previous solve — its
   coordinates are stale and drawing it would paint a hover at the
   old geometry.  We clear it, draw once to re-solve the routes and
   rebuild routeCache, then hit-test against the fresh cache and draw
   again if the hover resolved.  The cost is one extra solve per
   wheel tick, which the solver's bounded iteration count makes
   negligible for a discrete, user-paced event. */
canvas.addEventListener("wheel", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const view = whichView(sy);
  if (!view) return;
  e.preventDefault();
  const factor = e.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
  if (view === "floor") {
    zoomViewAt(viewFloor, s2wFloor, w2sFloor, sx, sy, factor);
  } else if (e.shiftKey) {
    /* Shift+wheel on the wall band = horizontal-only stretch of the
       unfolded strip.  The vertical axis is left alone, so a wall's
       cable geometry can be spread out sideways without every height
       dimension stretching with it.  The same modifier does not
       touch the floor band's wheel behaviour — Shift+wheel over the
       plan still does a uniform zoom. */
    stretchWallX(sx, factor);
  } else {
    zoomViewAt(viewWall,  s2wWall,  w2sWall,  sx, sy, factor);
  }
  state.hoveredRoute = null;
  draw();
  if (mouse.inside && !drawing && !state.ruler.active) {
    state.hoveredRoute = hitTestRoute(sx, sy);
    draw();
  }
}, { passive: false });

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  mouse.sx = sx; mouse.sy = sy;
  mouse.alt = e.altKey;
  mouse.shift = e.shiftKey;
  mouse.inside = (sx >= 0 && sy >= 0 &&
                  sx < window.innerWidth && sy < window.innerHeight);
  mouse.view = mouse.inside ? whichView(sy) : null;

  /* Panning: the hover reference from before the pan is stale, so
     clear it — otherwise the highlight sticks to old coordinates
     until the next mousemove re-hit-tests. */
  if (state.pan) {
    const dx = sx - state.pan.lastSx;
    const dy = sy - state.pan.lastSy;
    state.pan.lastSx = sx;
    state.pan.lastSy = sy;
    if (state.pan.view === "floor") panView(viewFloor, dx, dy);
    else                            panView(viewWall,  dx, dy);
    state.hoveredRoute = null;
    draw();
    return;
  }

  /* Ruler mode owns the pointer while armed. */
  if (state.ruler.active) {
    if (mouse.inside && mouse.view === "floor") {
      const [wx, wy] = s2wFloor(sx, sy);
      _rulerUpdateSkipState(wx, wy, e.shiftKey);
      state.ruler.hoverPt  = rulerSnapPoint(wx, wy);
      state.ruler.hoverIdx = state.ruler.pending
        ? -1
        : _rulerHitMeasure(sx, sy);
    } else {
      state.ruler.hoverPt  = null;
      state.ruler.hoverIdx = -1;
    }
    updateCursor();
    draw();
    return;
  }

  if (mouse.inside && !panelHovered && !drawing &&
      !state.dragGrid && !state.dragVertex) {
    panelLastActivity = performance.now();
  }
  updatePanelOpacity();

  if (state.dragGrid)   { handleGridDrag(sx, sy);   draw(); return; }
  if (state.dragVertex) { handleVertexDrag(sx, sy); draw(); return; }

  let hovered = null;
  if (!drawing && mouse.inside) hovered = hitTestRoute(sx, sy);
  state.hoveredRoute = hovered;

  if (drawing) {
    if (drawing.view === null) {
      drawingPreview = mouse.inside ? { sx, sy } : null;
    } else if (mouse.inside && mouse.view === drawing.view) {
      drawingPreview = { sx, sy };
    } else {
      drawingPreview = null;
    }
  }
  updateCursor();
  draw();
});

window.addEventListener("mouseup", (e) => {
  if (state.pan) {
    state.pan = null;
    updateCursor();
    return;
  }
  state.dragGrid = null;
  state.dragVertex = null;
  updateCursor();
  draw();
});

/* ==========================================================================
   KEYBOARD: NOTE, ZOOM RESET, SPACE-PAN
   ==========================================================================
   Registered on window (bubble phase — the ruler installs its own
   capture-phase listener and can stopPropagation before this runs).
   None of these use modifiers other than Space, so no collision with
   the ruler's Shift-prefixed keymap. */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if (e.code === "Space" && !spaceHeld) {
    spaceHeld = true;
    if (mouse.inside && !drawing) canvas.style.cursor = "grab";
    e.preventDefault();
    return;
  }

  /* View reset.  Home, not 0: Home is unambiguous, never
     intercepted by the browser for anything else, and unaffected by
     modifier state.  resetZoomPan clears uniform zoom, the
     horizontal wall stretch (viewWall.zoomX), and both pans — the
     wall band returns to the exact state it loads in.  The panel's
     zoom-reset button calls the same function, so the two surfaces
     never drift. */
  if (e.key === "Home") {
    resetZoomPan();
    e.preventDefault();
    return;
  }

  if (e.key === "n" || e.key === "N") {
    if (!mouse.inside || !mouse.view) return;
    if (drawing) return;
    e.preventDefault();
    addNote();
  }
});

window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    spaceHeld = false;
    if (!state.pan) updateCursor();
  }
});

window.addEventListener("blur", () => {
  spaceHeld = false;
  if (state.pan) {
    state.pan = null;
    updateCursor();
  }
});
"""
