"""
bx_core.py — model, storage, view state, transforms.

The middle layer.  Declares:

    • the placed-box list, the selection, the drag state, the ghost
      state, and the world/screen transforms
    • the draw-box mode flag and its three helpers (setDrawBoxMode,
      toggleDrawBoxMode, updateDrawBoxButton) — the mode is a model-
      level fact, not a UI fact, so it lives here next to the rest
      of the interaction state, not in the panel
    • the Space-to-pan flag (spaceHeld) — same reasoning: the pointer
      behaviour and the cursor both read it
    • the collision, geometry, and hit-test primitives every other
      module reads
    • the persistence layer — serialize, normalize, save, load

Everything else in the JS bundle depends on bx_base and this module;
this module depends only on bx_base and bx_i18n.

Translation
-----------
Every user-facing string this module writes — the two draw-button
labels, the four save/print flash messages, and the load confirmation
— comes from T() in bx_i18n.py.  See those functions below.

Handles
-------
A selected box exposes three kinds of handle, all hit-tested by
hitTestHandle:

    rotate    a single floating circle above the box's top edge,
              drawn by bx_view.drawHandles.  Its position is computed
              by rotateHandleWorldPos / rotateHandleScreenPos in this
              module, so the hit test and the renderer share one
              source of truth.

    corner    four squares at the box's own corners.  A corner drag
              resizes BOTH axes at once, pinning the diagonally
              opposite corner.

    edge      four squares at the box's edge midpoints.  An edge drag
              resizes ONE axis, pinning the opposite edge.

The rotate handle is hit-tested FIRST, with a slightly larger radius
than the corner handles', so that near the top of the box a cursor
in the small gap between the two always resolves to rotate.

Draw-box mode and the panel
---------------------------
Arming or disarming draw-box mode is the single moment at which the
panel's GUIDES section — the W / H / Rot fields and the rotation
buttons — appears or disappears.  updateDrawBoxButton is the sink for
both the button's own active state and the #ui.draw-mode class the
CSS hides GUIDES with, so there is exactly one place that reads the
mode and writes it to the panel.

The inputs' VALUES are not part of this contract: the mode toggle
hides them via CSS but does not touch them, so the defaults the user
last set survive every toggle without any save/restore code.

Space-to-pan
------------
Holding Space arms pan mode; a left-press while armed starts a pan
drag that ends on mouseup.  The flag lives here rather than in the
panel because two independent things read it:

    • the mousedown dispatch (mousedown with Space held pans instead
      of interacting with a box),
    • updateCursor (Space held → open-hand cursor).

The pan state itself is the existing `panning` variable, which is
already shared with the middle-button drag; only the trigger differs.
"""


CORE_JS = r"""
/* ==========================================================================
   MODEL STATE
   ========================================================================== */

let placedBoxes = [];
let selectedBox = null;
let hoveredBox  = null;
let drag        = null;
let handleDrag  = null;
let pendingPlace = null;
let panning     = null;
let boxCounter  = 0;

/* Draw-box mode.  When true the canvas is exclusively the drawing
   surface: clicks and drags place or draw a box, and existing boxes
   are not interactive.  When false, normal select / move / resize
   applies.

   The flag is a model-level fact, not a UI fact — the pointer
   behaviour, the ghost preview, and the visibility of the panel's
   GUIDES section all read it — so it lives here next to `drag` and
   `pendingPlace` rather than in the panel.

   The three helpers below are the whole API:
     setDrawBoxMode(on)     set the mode explicitly
     toggleDrawBoxMode()    flip the mode
     updateDrawBoxButton()  push the mode into the panel (button state
                            and the #ui.draw-mode class that the CSS
                            uses to show or hide GUIDES)

   Arming the mode clears any selection and pending gesture, so the
   transition is always crisp.  Disarming it clears any pending
   gesture for the same reason.  The mode is never cleared by placing
   a box: it behaves like a stamp, not like a one-shot action. */
let drawBoxMode = false;

/* Space-to-pan.  Set true while the Space key is down; the mousedown
   dispatch uses it to start a pan instead of interacting with the
   scene, and updateCursor uses it to show the open-hand cursor.
   Reset on keyup and on window blur, so a Space pressed while the
   browser has focus elsewhere does not leave a stuck pan-armed
   state. */
let spaceHeld = false;

const mouse = { sx: 0, sy: 0, inside: false };
const view  = { scale: 1, tx: 0, ty: 0, fitScale: 1 };

function setDrawBoxMode(on) {
  drawBoxMode = !!on;
  pendingPlace = null;
  if (drawBoxMode) {
    /* Arming the mode deselects — the panel's conditional BOX
       inspector collapses, the canvas handles disappear, and the
       draw ghost takes over as the only cursor-following overlay. */
    selectedBox = null;
    hoveredBox  = null;
    if (typeof syncSelectionInputs === "function") syncSelectionInputs();
  }
  updateDrawBoxButton();
  draw();
}

function toggleDrawBoxMode() {
  setDrawBoxMode(!drawBoxMode);
}

/* Push the current mode into the panel.  Three things track
   drawBoxMode, all written from this one function so the sink is
   single:

     • the Draw box button's active state and label,
     • a #ui.draw-mode class on the panel root, which the CSS uses to
       show or hide the GUIDES section.

   The panel root class is what makes GUIDES contextual.  The
   inputs' values are untouched by any of this — see the module
   docstring.

   The button's two labels come from T() in bx_i18n.py. */
function updateDrawBoxButton() {
  const el = document.getElementById("drawBoxBtn");
  if (el) {
    if (drawBoxMode) {
      el.classList.add("active");
      el.textContent = T("btnCancelDraw");
    } else {
      el.classList.remove("active");
      el.textContent = T("btnDrawBox");
    }
  }

  const ui = document.getElementById("ui");
  if (ui) {
    if (drawBoxMode) ui.classList.add("draw-mode");
    else             ui.classList.remove("draw-mode");
  }
}

/* ==========================================================================
   GEOMETRY HELPERS — pure functions, no state
   ========================================================================== */

function pointInPolygon(p, poly) {
  const x = p[0], y = p[1];
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > y) !== (yj > y)) &&
        (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) {
      inside = !inside;
    }
  }
  return inside;
}

function segsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
  const d1x = bx - ax, d1y = by - ay;
  const d2x = dx - cx, d2y = dy - cy;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-12) return false;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / denom;
  const s = ((cx - ax) * d1y - (cy - ay) * d1x) / denom;
  return t >= 0 && t <= 1 && s >= 0 && s <= 1;
}

function pointInFace(p, face) {
  if (!pointInPolygon(p, face.outer)) return false;
  for (const h of face.holes) if (pointInPolygon(p, h)) return false;
  return true;
}

function polygonOverlapsFace(poly, face) {
  for (const p of poly) if (pointInFace(p, face)) return true;
  const rings = [face.outer, ...face.holes];
  for (const ring of rings) {
    for (const p of ring) if (pointInPolygon(p, poly)) return true;
  }
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i], b = poly[(i + 1) % poly.length];
    for (const ring of rings) {
      for (let j = 0; j < ring.length; j++) {
        const c = ring[j], d = ring[(j + 1) % ring.length];
        if (segsIntersect(a[0], a[1], b[0], b[1],
                          c[0], c[1], d[0], d[1])) return true;
      }
    }
  }
  return false;
}

function polygonsOverlap(p1, p2) {
  for (const p of p1) if (pointInPolygon(p, p2)) return true;
  for (const p of p2) if (pointInPolygon(p, p1)) return true;
  for (let i = 0; i < p1.length; i++) {
    const a = p1[i], b = p1[(i + 1) % p1.length];
    for (let j = 0; j < p2.length; j++) {
      const c = p2[j], d = p2[(j + 1) % p2.length];
      if (segsIntersect(a[0], a[1], b[0], b[1],
                        c[0], c[1], d[0], d[1])) return true;
    }
  }
  return false;
}

function boxCorners(cx, cy, w, h, rotDeg) {
  const r = rotDeg * Math.PI / 180;
  const c = Math.cos(r), s = Math.sin(r);
  const hw = w / 2, hh = h / 2;
  const off = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]];
  return off.map(([dx, dy]) =>
    [cx + c * dx - s * dy, cy + s * dx + c * dy]);
}

function boxEdgeMidpoints(cx, cy, w, h, rotDeg) {
  const c = boxCorners(cx, cy, w, h, rotDeg);
  return [
    [(c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2],
    [(c[1][0] + c[2][0]) / 2, (c[1][1] + c[2][1]) / 2],
    [(c[2][0] + c[3][0]) / 2, (c[2][1] + c[3][1]) / 2],
    [(c[3][0] + c[0][0]) / 2, (c[3][1] + c[0][1]) / 2],
  ];
}

/* ==========================================================================
   ROTATION HANDLE POSITION
   ==========================================================================
   The rotation handle sits in the box's LOCAL frame at
   (0, h/2 + offMm) — above the top edge — where offMm is
   ROTATE_HANDLE_OFFSET_PX converted from screen px to world mm at the
   current scale.  Because the offset is in screen px, the handle sits
   the same distance from the box at every zoom level, which is what
   every design tool does.

   rotateHandleWorldPos returns the handle in world mm.
   rotateHandleScreenPos wraps it with the view transform.  Both are
   used by hitTestHandle (below) and by bx_view.drawHandles, so the
   hit test and the renderer share one source of truth. */

function rotateHandleWorldPos(b) {
  const offMm = ROTATE_HANDLE_OFFSET_PX / Math.max(view.scale, 1e-9);
  const r = b.rot * Math.PI / 180;
  const localY = b.h / 2 + offMm;
  return [
    b.x - Math.sin(r) * localY,
    b.y + Math.cos(r) * localY,
  ];
}

function rotateHandleScreenPos(b) {
  const [wx, wy] = rotateHandleWorldPos(b);
  return w2s(wx, wy);
}

function boxOverlapsWalls(cx, cy, w, h, rotDeg) {
  const corners = boxCorners(cx, cy, w, h, rotDeg);
  for (const face of GEOMETRY.faces) {
    if (polygonOverlapsFace(corners, face)) return true;
  }
  return false;
}

function boxIsInvalid(b) {
  if (b._invalid === undefined) {
    b._invalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
  }
  return b._invalid;
}
function invalidateBox(b) { b._invalid = undefined; }

/* ==========================================================================
   VIEW TRANSFORMS
   ========================================================================== */

const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");
let dpr = window.devicePixelRatio || 1;

function w2s(wx, wy) {
  return [wx * view.scale + view.tx, -wy * view.scale + view.ty];
}
function s2w(sx, sy) {
  return [(sx - view.tx) / view.scale, -(sy - view.ty) / view.scale];
}

function fitView() {
  const b = GEOMETRY.bounds;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  const bw = (b.maxX - b.minX) || 1;
  const bh = (b.maxY - b.minY) || 1;
  const pad = FLOOR_PAD;
  view.scale = Math.min((cw - 2 * pad) / bw, (ch - 2 * pad) / bh);
  const mx = (b.minX + b.maxX) / 2;
  const my = (b.minY + b.maxY) / 2;
  view.tx = cw / 2 - mx * view.scale;
  view.ty = ch / 2 + my * view.scale;

  /* Remember the fit scale.  It is the reference for the zoom-out
     floor: zoomAt() clamps the scale to MIN_ZOOM_FACTOR × this
     value, so the floor is recalculated every time the viewport
     changes and never sits above the current fit. */
  view.fitScale = view.scale;
}

function resize() {
  dpr = window.devicePixelRatio || 1;
  canvas.width  = Math.round(window.innerWidth  * dpr);
  canvas.height = Math.round(window.innerHeight * dpr);
  canvas.style.width  = window.innerWidth  + "px";
  canvas.style.height = window.innerHeight + "px";
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  fitView();
  draw();
}
window.addEventListener("resize", resize);

function zoomAt(sx, sy, factor) {
  const [wx, wy] = s2w(sx, sy);
  const fitRef   = view.fitScale || view.scale;
  const lower    = Math.max(1e-6, fitRef * MIN_ZOOM_FACTOR);
  const newScale = Math.max(lower,
                            Math.min(MAX_ZOOM, view.scale * factor));
  if (newScale === view.scale) return;
  view.scale = newScale;
  view.tx = sx - wx * view.scale;
  view.ty = sy + wy * view.scale;
}

/* ==========================================================================
   PERSISTENCE
   ========================================================================== */

const BOXES_FILE = "room_boxes.json";

function serializeBoxes() {
  return {
    version: 1,
    boxes: placedBoxes.map(b => ({
      name: b.name || "",
      x: Math.round(b.x * 100) / 100,
      y: Math.round(b.y * 100) / 100,
      w: Math.round(b.w * 100) / 100,
      h: Math.round(b.h * 100) / 100,
      rot: Math.round(b.rot * 100) / 100,
    })),
  };
}

function normalizeBoxes(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.boxes)) return data.boxes;
  return [];
}

function applyLoadedBoxes(list) {
  placedBoxes = [];
  boxCounter = 0;
  for (const item of list) {
    if (!item || typeof item !== "object") continue;
    const w = +item.w, h = +item.h;
    if (!(w > 0) || !(h > 0)) continue;
    const b = {
      x: +item.x || 0,
      y: +item.y || 0,
      w, h,
      rot: (((+item.rot || 0) % 360) + 360) % 360,
      name: (typeof item.name === "string") ? item.name : "",
    };
    placedBoxes.push(b);
    const m = /^Box (\d+)$/.exec(b.name);
    if (m) boxCounter = Math.max(boxCounter, +m[1]);
  }
}

function saveToLocalStorage() {
  try {
    localStorage.setItem("room_boxes", JSON.stringify(serializeBoxes()));
  } catch (e) { /* quota / private mode — ignore */ }
}

function loadFromLocalStorage() {
  try {
    const s = localStorage.getItem("room_boxes");
    return s ? JSON.parse(s) : null;
  } catch (e) { return null; }
}

async function saveToServer() {
  try {
    const r = await fetch("/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serializeBoxes()),
    });
    return r.ok;
  } catch (e) { return false; }
}

async function loadFromServer() {
  try {
    const r = await fetch(BOXES_FILE, { cache: "no-store" });
    if (!r.ok) return null;
    const txt = await r.text();
    if (!txt.trim()) return null;
    return JSON.parse(txt);
  } catch (e) { return null; }
}

function downloadBoxes() {
  const blob = new Blob(
    [JSON.stringify(serializeBoxes(), null, 2)],
    { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = BOXES_FILE;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function doSave(showFeedback) {
  saveToLocalStorage();
  const ok = await saveToServer();
  if (!ok) {
    downloadBoxes();
    if (showFeedback)
      flashStatus(T("msgDownloadedOk")(BOXES_FILE), "ok");
  } else if (showFeedback) {
    flashStatus(T("msgSavedOk")(BOXES_FILE), "ok");
  }
}

function consolePrintBoxList() {
  if (!placedBoxes.length) {
    console.log("(no boxes placed)");
    return;
  }
  const nameW = Math.max(4, ...placedBoxes.map(b => (b.name || "").length));
  const header = "Name".padEnd(nameW) + "  " +
                 "Width".padStart(10) + "  " +
                 "Height".padStart(10);
  console.log(header);
  console.log("-".repeat(header.length));
  for (const b of placedBoxes) {
    const name = (b.name || "").padEnd(nameW);
    const w = b.w.toFixed(2).padStart(10);
    const h = b.h.toFixed(2).padStart(10);
    console.log(`${name}  ${w}  ${h}`);
  }
}

async function printBoxList() {
  try {
    const r = await fetch("/print", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serializeBoxes()),
    });
    if (r.ok) {
      flashStatus(T("msgPrintTerminal"), "ok");
      return;
    }
    consolePrintBoxList();
    flashStatus(T("msgPrintConsole"), "ok");
  } catch (e) {
    consolePrintBoxList();
    flashStatus(T("msgPrintConsole"), "ok");
  }
}

async function autoload() {
  let data = await loadFromServer();
  let source = data ? "file" : null;
  if (!data) {
    data = loadFromLocalStorage();
    source = data ? "localStorage" : null;
  }
  if (data) {
    applyLoadedBoxes(normalizeBoxes(data));
    const n = placedBoxes.length;
    const bad = placedBoxes.filter(boxIsInvalid).length;
    if (n) {
      flashStatus(T("msgLoaded")(n, source, bad), bad ? "warn" : "ok");
    }
  }
  draw();
}

/* ==========================================================================
   HIT TESTS
   ========================================================================== */

function hitTestBox(wx, wy) {
  for (let i = placedBoxes.length - 1; i >= 0; i--) {
    const b = placedBoxes[i];
    if (pointInPolygon([wx, wy], boxCorners(b.x, b.y, b.w, b.h, b.rot))) {
      return i;
    }
  }
  return -1;
}

/* Handle hit-testing priority, highest first:

     rotate    the floating circle above the top edge — checked first,
               with a slightly larger hit radius than its drawn one,
               so a cursor in the small gap between it and the top
               corners resolves to rotate
     corner    the four box corners
     edge      the four edge midpoints

   The type string returned here is the same one bx_dispatch uses to
   decide which drag to start; there is no name-to-behaviour mapping
   anywhere else. */
function hitTestHandle(sx, sy) {
  if (!selectedBox) return null;
  const b = selectedBox;
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);

  const [rhx, rhy] = rotateHandleScreenPos(b);
  if (Math.hypot(sx - rhx, sy - rhy) <= ROTATE_HANDLE_R + 4) {
    return { type: "rotate" };
  }

  for (let i = 0; i < 4; i++) {
    const [px, py] = w2s(corners[i][0], corners[i][1]);
    if (Math.abs(sx - px) <= CORNER_HIT && Math.abs(sy - py) <= CORNER_HIT) {
      return { type: "corner", index: i };
    }
  }
  for (let i = 0; i < 4; i++) {
    const [px, py] = w2s(edges[i][0], edges[i][1]);
    if (Math.abs(sx - px) <= EDGE_HIT && Math.abs(sy - py) <= EDGE_HIT) {
      return { type: "edge", index: i };
    }
  }
  return null;
}

/* ==========================================================================
   UI INPUTS — ghost params
   ========================================================================== */

function getBoxParams() {
  const w = parseFloat(document.getElementById("boxW").value) || 100;
  const h = parseFloat(document.getElementById("boxH").value) || 100;
  let rot = parseFloat(document.getElementById("boxRot").value) || 0;
  rot = ((rot % 360) + 360) % 360;
  return { w, h, rot };
}

function setRotation(deg) {
  const v = ((deg % 360) + 360) % 360;
  document.getElementById("boxRot").value = v;
}

/* ==========================================================================
   STATUS — flashStatus
   ========================================================================== */

let statusTimer = null;
function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.className = cls || "";
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => {
    statusTimer = null;
    draw();
  }, 1400);
}
"""
