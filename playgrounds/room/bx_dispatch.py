"""
bx_dispatch.py — draw() and the canvas event listeners.

The orchestrator.  draw() is the public entry point; internally it
coalesces multiple calls in the same frame and hands off to _paint().

Paint pipeline
--------------
_paint() runs in three phases:

    1.  Static-layer blit.  The walls, steps, plan hatches, sheet
        frame, and column markers never change during a box drag,
        hover, or panel edit — only on pan or zoom.  They are baked
        once into an offscreen canvas keyed on
        `(cw, ch, dpr, zoom, panX, panY)`, and blitted with a single
        `drawImage` in every frame thereafter.  See
        `_ensureStaticLayer`.

    2.  Dynamic scene.  Boxes, labels, invalid chips, handles, and
        the ghost.  Every element here depends on the model, the
        selection, or the cursor; drawn every paint.

    3.  Panel sync.  `updatePanelOpacity`, `_syncSelectionInputs`,
        `_boxSyncInspector`, `updateTitleBlock`, `updateStatus`.

Hover fast path — at the call site, not inside _paint
------------------------------------------------------
A pure hover mousemove (cursor moving over the canvas with no drag
in flight, no draw-box ghost following it, and no box entering or
leaving under the cursor) does not need a canvas repaint.  Only the
status line — which carries the live coordinate readout — needs to
update.

That check lives in the mousemove handler, not in `_paint`.  The
previous version put it inside `_paint` as a `needsCanvas` heuristic
that compared a tracked snapshot of a handful of state fields
(`selectedIdx`, `hoveredBox`, item count, view transform, active
gestures) against the current state.  That heuristic had a critical
blind spot: it did not track the *model* — box width, height,
position, rotation, name.  A remote patch that changed `b.w`
therefore triggered `draw()` correctly, but `_paint` compared the
tracked fields, saw no difference, skipped the canvas phases, and
only updated the status text.  The user saw no visual change until
they clicked somewhere, which changed a tracked field and forced a
real repaint.

Moving the decision to mousemove fixes that cleanly.  The handler
is the only place where "nothing material changed" is a safe
conclusion, because it is the only place where the current event is
*known* to be a pure hover (no gesture branch was taken, and the
hover detector has already run).  Every other `draw()` caller has
a specific reason to repaint, and now gets one unconditionally.

rAF coalescing
--------------
`draw()` is called from many places: panel sync, event handlers,
mutation helpers.  Two calls in the same frame coalesce into one
paint.  `drawNow()` is the escape hatch for a caller that truly
needs the pixels before returning; nothing in the current tree
does, so it exists only for callers that want to force a
synchronous paint.

The priority chain in the canvas listeners is unchanged:

    middle-button pan  >  Space-held left pan  >  handle-drag  >
    draw-box-place  >  body-drag  >  select

Nothing here decides what a click means beyond that priority; every
decision is delegated to the module that owns it.

STAGE 1 OPTIMIZATIONS (retained)
================================
    CURSOR SET ONLY ON CHANGE `_setCursor` compares the new value
                              to the last one written and only
                              assigns when it differs.

    STATUS TEXT WRITE-GUARD   `updateStatus` compares textContent
                              and className before assigning.

    TITLE BLOCK WRITE-GUARD   `updateTitleBlock` (in bx_panel_fields)
                              does the same.

    INSPECTOR DISPLAY-GUARD   `_boxSyncInspector` (in bx_panel_fields)
                              only writes style.display when it
                              differs.

Space-to-pan
------------
Holding Space arms pan mode; a left-press while armed starts a pan
drag that ends on mouseup.  `state.spaceHeld` (in bx_core) is the
flag.  Releasing Space resets it, and a window blur also resets it
so a Space pressed while focus was elsewhere does not leave a stuck
pan-armed state.

Escape handling priority
------------------------
Escape unwinds in priority order: an armed draw mode is the
outermost thing on the interaction stack, so it is what Escape
disarms first; if no mode is armed, Escape clears the selection.
(The help popover installs its own capture-phase Escape listener
that fires before this one — see bx_panel_bindings.)

Static imports
--------------
`zoomAt`, `callDraw`, `doSave` are static imports.  There is no
cycle from bx_dispatch back to bx_core, so the dynamic form was
unnecessary — every wheel tick and every Ctrl+S save was paying a
dynamic-import microtask that a static binding makes free.  The
remaining dynamic imports (`import("./bx_panel_bindings.js")` for
`_afterSelectChange`, `_flashPlacedOk`, etc.) are kept because
bx_panel_bindings imports `draw` from this file — that cycle is
real and the dynamic form defers the resolution until after both
modules have evaluated.
"""

DISPATCH_JS = r"""
import {
  canvas, ctx, env, PALETTE, panelState,
} from "./bx_base.js";
import {
  board, view, state,
  b2s, s2b, w2sFloor, s2wFloor,
  boxCorners, boxEdgeMidpoints, boxIsInvalid, boxOverlapsWalls,
  selectedItem, hitTestBox, hitTestHandle,
  invalidateBox, callDraw, zoomAt, doSave,
  getBoxParams,
} from "./bx_core.js";
import { drawStaticScene, drawDynamicScene } from "./bx_view.js";
import {
  startRotate, updateRotate, startResize, updateResize,
  startResizeCorner, updateResizeCorner, moveBoxSafely,
} from "./bx_edit.js";
import {
  _syncSelectionInputs, _boxSyncInspector, updateTitleBlock,
} from "./bx_panel_fields.js";
import { updatePanelOpacity } from "./bx_panel_bindings.js";
import { T } from "./bx_i18n.js";

/* ==========================================================================
   STAGE 1: CURSOR SET-ONLY-ON-CHANGE
   ========================================================================== */

let _lastCursor = "default";

function _setCursor(c) {
  if (c === _lastCursor) return;
  canvas.style.cursor = c;
  _lastCursor = c;
}

/* ==========================================================================
   STATIC LAYER — bake once, blit every frame
   ==========================================================================
   The static scene (paper, plan hatches, steps, walls, columns,
   sheet frame) is baked into an offscreen canvas at device-pixel
   resolution.  The cache key includes everything the bake depends
   on: viewport size, DPR, and the view transform.  A pan or a zoom
   invalidates the cache; every other frame reuses it.

   The offscreen canvas draws to its own context at
   `setTransform(dpr, ...)` so its pixel grid matches the main
   canvas's; `ctx.drawImage(sc, 0, 0, cw, ch)` then blits it 1:1
   under the main context's matching transform.  No resampling, no
   blur.

   The static scene's `_screenPat` calls go to `ctx` (the main
   canvas).  Canvas 2D patterns are context-agnostic: a pattern
   created from one context's `createPattern` can be assigned to
   any other context's `fillStyle`.  So the offscreen bake shares
   the same pattern objects as the main canvas with no second bake
   and no double allocation. */

let _staticCanvas = null;
let _staticKey    = null;

function _ensureStaticLayer(cw, ch) {
  const key = cw + "|" + ch + "|" + env.dpr + "|"
            + view.zoom + "|" + view.panX + "|" + view.panY;
  if (_staticKey === key && _staticCanvas) return _staticCanvas;

  if (!_staticCanvas) _staticCanvas = document.createElement("canvas");
  const bw = Math.round(cw * env.dpr);
  const bh = Math.round(ch * env.dpr);
  if (_staticCanvas.width  !== bw) _staticCanvas.width  = bw;
  if (_staticCanvas.height !== bh) _staticCanvas.height = bh;

  const sctx = _staticCanvas.getContext("2d");
  sctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);
  sctx.clearRect(0, 0, cw, ch);

  drawStaticScene(sctx);

  _staticKey = key;
  return _staticCanvas;
}

/* ==========================================================================
   DRAW — rAF-coalesced scheduler
   ==========================================================================
   Two draws in the same frame coalesce into one paint.  The first
   call in a frame schedules the rAF callback; the callback paints
   once and clears the flag, so a subsequent call in the same frame
   schedules a fresh callback for the *next* frame. */

let _drawScheduled = false;

export function draw() {
  if (_drawScheduled) return;
  _drawScheduled = true;
  requestAnimationFrame(() => {
    _drawScheduled = false;
    _paint();
  });
}

/* Escape hatch — synchronous paint.  Currently unused; provided so
   a caller that must read the canvas before returning (e.g. a
   hypothetical PNG-export flow) has an obvious way. */
export function drawNow() {
  _drawScheduled = false;
  _paint();
}

/* ==========================================================================
   PAINT — the actual per-frame work
   ==========================================================================
   Always paints.  The one case where a full repaint is unnecessary
   — a pure hover mousemove with no other state change — is short-
   circuited at the *call site* in the mousemove handler, where the
   decision is safe.  See the module docstring for why the decision
   does not live here. */

function _paint() {
  const cw = window.innerWidth, ch = window.innerHeight;
  ctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);

  /* Phase 1: blit the cached static layer. */
  const sc = _ensureStaticLayer(cw, ch);
  ctx.drawImage(sc, 0, 0, cw, ch);

  /* Phase 2: dynamic scene on top. */
  drawDynamicScene(ctx);

  /* Phase 3: panel sync.  Each function is equality-guarded
     internally; the cost is a handful of string compares. */
  updatePanelOpacity();
  _syncSelectionInputs();
  _boxSyncInspector();
  updateTitleBlock();
  updateStatus();
}

/* The mousemove fast path runs this subset — panel fade state and
   the live coordinate readout, both of which track the cursor and
   must update on every move.  The other panel syncs only react to
   model or selection changes, so a pure hover leaves them
   untouched; if any of them *did* change, the mutation path called
   `draw()` which ran the full `_paint()`. */
function _syncPanelFastPath() {
  updatePanelOpacity();
  updateStatus();
}

/* ==========================================================================
   STATUS LINE
   ========================================================================== */

function updateStatus() {
  const el = document.getElementById("status");
  if (!el) return;

  let text = "";
  let cls  = "";

  if (state.panning) {
    text = T("stPanningSpace");
  } else if (state.handleDrag) {
    const b = state.handleDrag.b;
    const verb = state.handleDrag.type === "rotate"        ? T("stVerbRotating")
               : state.handleDrag.type === "resize-corner" ? T("stVerbResizingBoth")
               :                                             T("stVerbResizing");
    text = T("stHandleDrag")(verb,
      b.x.toFixed(0), b.y.toFixed(0),
      b.w.toFixed(0), b.h.toFixed(0),
      b.rot.toFixed(0));
  } else if (state.drag) {
    const b = board.items[state.drag.index];
    if (b) {
      const inv = boxIsInvalid(b);
      text = T("stDragging")(inv,
        b.x.toFixed(0), b.y.toFixed(0), b.rot.toFixed(0));
      cls = inv ? "warn" : "";
    }
  } else if (state.pendingPlace && state.pendingPlace.drag) {
    const x0 = Math.min(state.pendingPlace.wx, state.pendingPlace.dragWx);
    const x1 = Math.max(state.pendingPlace.wx, state.pendingPlace.dragWx);
    const y0 = Math.min(state.pendingPlace.wy, state.pendingPlace.dragWy);
    const y1 = Math.max(state.pendingPlace.wy, state.pendingPlace.dragWy);
    const dw = x1 - x0, dh = y1 - y0;
    const inv = boxOverlapsWalls((x0 + x1) / 2, (y0 + y1) / 2, dw, dh, 0);
    text = T("stDrawing")(inv, dw.toFixed(0), dh.toFixed(0));
    cls = inv ? "warn" : "";
  } else if (!state.mouse.inside) {
    text = ""; cls = "";
  } else if (state.spaceHeld) {
    text = T("stSpaceHeld");
  } else if (state.dragBoxMode) {
    const [wx, wy] = s2b(state.mouse.sx, state.mouse.sy);
    const { w, h, rot } = getBoxParams();
    const bad = boxOverlapsWalls(wx, wy, w, h, rot);
    text = T("stDrawPreview")(wx.toFixed(0), wy.toFixed(0),
      w.toFixed(0), h.toFixed(0), rot.toFixed(0), bad);
    cls = bad ? "bad" : "ok";
  } else if (state.hoveredBox) {
    const b = state.hoveredBox;
    const inv = boxIsInvalid(b);
    text = T("stHover")(inv, b.name || T("boxUnnamed"),
      b.x.toFixed(0), b.y.toFixed(0), b.rot.toFixed(0));
    cls = inv ? "warn" : "";
  } else {
    const [wx, wy] = s2b(state.mouse.sx, state.mouse.sy);
    text = T("stCursor")(wx.toFixed(0), wy.toFixed(0));
  }

  if (el.textContent !== text) el.textContent = text;
  if (el.className   !== cls)  el.className   = cls;
}

/* ==========================================================================
   CANVAS EVENTS
   ========================================================================== */

canvas.addEventListener("mousedown", (e) => {
  if (document.activeElement && document.activeElement.blur) {
    document.activeElement.blur();
  }

  if (e.button === 1) {
    e.preventDefault();
    state.panning = { sx: e.clientX, sy: e.clientY,
                       tx: view.panX, ty: view.panY };
    updateCursor(state.mouse.sx, state.mouse.sy);
    return;
  }
  if (e.button !== 0) return;

  if (state.spaceHeld) {
    e.preventDefault();
    state.panning = { sx: e.clientX, sy: e.clientY,
                       tx: view.panX, ty: view.panY };
    updateCursor(state.mouse.sx, state.mouse.sy);
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const [wx, wy] = s2b(sx, sy);

  /* Handle FIRST, before the draw-box branch.  This is what lets
     the user click a handle on the box they just placed in draw-box
     mode and have the click grab the handle instead of starting a
     new box. */
  const h = hitTestHandle(sx, sy);
  if (h) {
    if (state.dragBoxMode) setDrawBoxMode(false);
    if (h.type === "rotate")      startRotate(selectedItem(), wx, wy);
    else if (h.type === "corner") startResizeCorner(selectedItem(), h.index);
    else                          startResize(selectedItem(), h.index);
    updateCursor(sx, sy);
    return;
  }

  if (state.dragBoxMode) {
    state.pendingPlace = {
      wx, wy,
      sx, sy,
      drag: false,
      dragWx: wx,
      dragWy: wy,
    };
    return;
  }

  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    const b = board.items.splice(idx, 1)[0];
    board.items.push(b);
    board.selectedIdx = board.items.length - 1;
    state.drag = { index: board.items.length - 1,
                   ox: b.x - wx, oy: b.y - wy };
    updateCursor(sx, sy);
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    return;
  }

  if (board.selectedIdx >= 0) {
    board.selectedIdx = -1;
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
  } else {
    draw();
  }
});

window.addEventListener("mousemove", (e) => {
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const inside = (sx >= 0 && sy >= 0 &&
                  sx < canvas.clientWidth && sy < canvas.clientHeight);

  if (state.panning) {
    view.panX = state.panning.tx + (e.clientX - state.panning.sx);
    view.panY = state.panning.ty + (e.clientY - state.panning.sy);
    draw(); return;
  }

  if (inside) { state.mouse.sx = sx; state.mouse.sy = sy; state.mouse.inside = true; }
  else        { state.mouse.inside = false; }

  if (state.handleDrag) {
    const [wx, wy] = s2b(sx, sy);
    if (state.handleDrag.type === "rotate") {
      updateRotate(wx, wy, e.shiftKey);
    } else if (state.handleDrag.type === "resize-corner") {
      updateResizeCorner(wx, wy);
    } else {
      updateResize(wx, wy);
    }
    updateCursor(sx, sy);
    draw(); return;
  }

  if (state.drag) {
    const [wx, wy] = s2b(sx, sy);
    const b = board.items[state.drag.index];
    moveBoxSafely(b, wx + state.drag.ox, wy + state.drag.oy);
    updateCursor(sx, sy);
    draw(); return;
  }

  /* Place-or-draw gesture.  May flip `drag: false → true` on this
     event; either way the ghost is following the cursor and needs
     a repaint. */
  if (state.pendingPlace) {
    if (!state.pendingPlace.drag) {
      const dx = sx - state.pendingPlace.sx, dy = sy - state.pendingPlace.sy;
      if (Math.hypot(dx, dy) > 6) {
        state.pendingPlace.drag = true;
      }
    }
    if (state.pendingPlace.drag) {
      const [wx, wy] = s2b(sx, sy);
      state.pendingPlace.dragWx = wx;
      state.pendingPlace.dragWy = wy;
    }
  }

  /* Capture the hover BEFORE updating it.  The fast path below
     depends on this being the previous frame's value. */
  const prevHoveredBox = state.hoveredBox;

  if (!state.dragBoxMode && inside) {
    const [wx, wy] = s2b(sx, sy);
    state.hoveredBox = null;
    const idx = hitTestBox(wx, wy);
    if (idx >= 0) state.hoveredBox = board.items[idx];
  } else {
    state.hoveredBox = null;
  }

  if (inside && !panelState.hovered && !state.drag && !state.handleDrag && !state.panning) {
    panelState.lastActivity = performance.now();
  }

  updateCursor(sx, sy);

  /* Fast-path decision, at the call site.  A pure hover — no draw-
     box ghost following the cursor, no pending place-or-draw, and
     no box entering or leaving under it — needs only the status
     readout and the panel fade state.  Everything else needs a
     full repaint. */
  const needsRepaint =
    !!state.pendingPlace ||
    state.dragBoxMode ||
    state.hoveredBox !== prevHoveredBox;

  if (needsRepaint) {
    draw();
  } else {
    _syncPanelFastPath();
  }
});

window.addEventListener("mouseup", (e) => {
  if (state.panning) {
    state.panning = null;
    updateCursor(state.mouse.sx, state.mouse.sy);
    return;
  }
  if (e.button !== 0) return;

  if (state.handleDrag) {
    state.handleDrag = null;
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    updateCursor(state.mouse.sx, state.mouse.sy);
    draw(); return;
  }
  if (state.drag) {
    state.drag = null;
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    updateCursor(state.mouse.sx, state.mouse.sy);
    draw(); return;
  }

  if (state.pendingPlace) {
    const p = state.pendingPlace;
    state.pendingPlace = null;

    let w, h, cx, cy, rot;

    if (p.drag) {
      const x0 = Math.min(p.wx, p.dragWx);
      const x1 = Math.max(p.wx, p.dragWx);
      const y0 = Math.min(p.wy, p.dragWy);
      const y1 = Math.max(p.wy, p.dragWy);
      const dw = x1 - x0;
      const dh = y1 - y0;

      if (dw < 10 || dh < 10) {
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
      import("./bx_panel_bindings.js").then(m => m._flashPlaceCollision()).catch(() => {});
    } else {
      state.boxCounter += 1;
      const b = { x: cx, y: cy, w, h, rot,
                  name: `Box ${state.boxCounter}` };
      board.items.push(b);
      board.selectedIdx = board.items.length - 1;
      import("./bx_panel_bindings.js").then(m => m._flashPlacedOk()).catch(() => {});
      import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    }
    draw();
  }
});

canvas.addEventListener("dblclick", (e) => {
  if (state.dragBoxMode) return;
  if (state.spaceHeld)   return;
  const rect = canvas.getBoundingClientRect();
  const [wx, wy] = s2b(e.clientX - rect.left, e.clientY - rect.top);
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    board.selectedIdx = idx;
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    const ni = document.getElementById("boxName");
    if (ni) { ni.focus(); ni.select(); }
  }
});

canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  if (state.dragBoxMode) return;
  if (state.spaceHeld)   return;
  const rect = canvas.getBoundingClientRect();
  const [wx, wy] = s2b(e.clientX - rect.left, e.clientY - rect.top);
  const idx = hitTestBox(wx, wy);
  if (idx >= 0) {
    const wasSel = idx === board.selectedIdx;
    if (board.items[idx] === state.hoveredBox) state.hoveredBox = null;
    board.items.splice(idx, 1);
    if (wasSel) board.selectedIdx = -1;
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    draw();
  }
});

canvas.addEventListener("wheel", (e) => {
  e.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const sx = e.clientX - rect.left, sy = e.clientY - rect.top;
  const factor = Math.exp(-e.deltaY * 0.0015);
  zoomAt(sx, sy, factor);
  callDraw();
}, { passive: false });

/* ==========================================================================
   KEYBOARD
   ========================================================================== */

window.addEventListener("keydown", (e) => {
  const t = e.target;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if (e.code === "Space" && !state.spaceHeld) {
    state.spaceHeld = true;
    if (state.mouse.inside && !state.panning) _setCursor("grab");
    e.preventDefault();
    return;
  }

  if ((e.ctrlKey || e.metaKey) && (e.key === "s" || e.key === "S")) {
    e.preventDefault();
    doSave(true);
    return;
  }

  if (e.key === "Escape") {
    if (state.dragBoxMode) {
      setDrawBoxMode(false);
    } else {
      board.selectedIdx = -1;
      import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    }
    return;
  }

  if (state.dragBoxMode) return;

  if (e.key === "r" || e.key === "R") {
    e.preventDefault();
    const deg = e.shiftKey ? 15 : 90;
    import("./bx_panel_bindings.js").then(m => m._rotateSelected(deg)).catch(() => {});
  } else if (e.key === "Delete" || e.key === "Backspace") {
    if (board.selectedIdx >= 0) {
      board.items.splice(board.selectedIdx, 1);
      board.selectedIdx = -1;
      import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
    }
  } else if (e.key === "0") {
    import("./bx_core.js").then(m => { m.fitView(); m.callDraw(); }).catch(() => {});
  }
});

window.addEventListener("keyup", (e) => {
  if (e.code === "Space") {
    state.spaceHeld = false;
    if (!state.panning) updateCursor(state.mouse.sx, state.mouse.sy);
  }
});

window.addEventListener("blur", () => {
  state.spaceHeld = false;
  if (state.panning) {
    state.panning = null;
    updateCursor(state.mouse.sx, state.mouse.sy);
  }
});

/* ==========================================================================
   CURSOR
   ========================================================================== */

export function updateCursor(sx, sy) {
  if (state.panning)              { _setCursor("grabbing"); return; }
  if (state.handleDrag || state.drag) { _setCursor("grabbing"); return; }
  if (state.spaceHeld)            { _setCursor("grab");     return; }
  if (state.dragBoxMode)          { _setCursor("crosshair"); return; }

  const h = hitTestHandle(sx, sy);
  if (h) {
    if (h.type === "rotate")      _setCursor("grab");
    else if (h.type === "corner") _setCursor("nwse-resize");
    else                          _setCursor("grab");
    return;
  }

  const [wx, wy] = s2b(sx, sy);
  if (hitTestBox(wx, wy) >= 0)    { _setCursor("move");     return; }
  _setCursor("crosshair");
}

/* ==========================================================================
   DRAW-BOX MODE
   ========================================================================== */

export function updateDrawBoxButton() {
  const el = document.getElementById("drawBoxBtn");
  if (el) {
    if (state.dragBoxMode) {
      el.classList.add("active");
      el.textContent = T("btnCancelDraw");
    } else {
      el.classList.remove("active");
      el.textContent = T("btnDrawBox");
    }
  }

  const ui = document.getElementById("ui");
  if (ui) {
    if (state.dragBoxMode) ui.classList.add("draw-mode");
    else                   ui.classList.remove("draw-mode");
  }
}

export function setDrawBoxMode(on) {
  state.dragBoxMode = !!on;
  state.pendingPlace = null;
  if (state.dragBoxMode) {
    board.selectedIdx = -1;
    state.hoveredBox  = null;
    import("./bx_panel_bindings.js").then(m => m._afterSelectChange()).catch(() => {});
  }
  updateDrawBoxButton();
  draw();
}
"""
