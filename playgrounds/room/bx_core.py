"""
bx_core.py — model, storage, view state, transforms.

ES module.  This is the second layer of the split, importing from
`bx_base.js` (palette, canvas, env) and defining the model:

    * view transform (b2s / s2b)
    * board state (placedBoxes, selectedBox, hoveredBox, state object)
    * geometry helpers (pointInPolygon, segsIntersect, boxCorners,
      boxEdgeMidpoints, pointInFace, polygonOverlapsFace,
      polygonsOverlap, boxOverlapsWalls, boxIsInvalid)
    * handles (rotateHandleWorldPos / rotateHandleScreenPos — the
      single source of truth shared by the hit test and the renderer)
    * transforms (w2sFloor / s2wFloor via the view object, which
      itself is derived from view.zoom / view.panX / view.panY)
    * layout (fitView, resize, zoomAt)
    * persistence (serialize, applyLoaded, saveToServer,
      loadFromServer, autoload, doSave, downloadBoxes,
      consolePrintBoxList, printBoxList, saveToLocalStorage,
      loadFromLocalStorage, clearAll)
    * callbacks (notifyChanged, setDraw)

The old concatenation model had these as top-level `let` variables
scattered across the file: `placedBoxes`, `selectedBox`,
`hoveredBox`, `drag`, `handleDrag`, `pendingPlace`, `panning`,
`boxCounter`, `drawBoxMode`, `spaceHeld`, `mouse`, `view`.  ES
modules forbid cross-module reassignment of `let`, so all of them
move into a `const state` object (or the `board` / `view` objects,
which are the same `const`-object pattern):

    view         { zoom, panX, panY, fitScale } — properties mutable
    board        { items, selectedIdx, ... }
    state        { drag, handleDrag, pendingPlace, panning,
                   dragBoxMode, spaceHeld, hoveredBox,
                   boxCounter, mouse, drawing }
    env          { dpr } — from bx_base

`boxCounter` is the auto-name counter.  `drawing` is the in-progress
drag-draw state; `pendingPlace` is the click-to-place state, kept
separate because their lifetimes differ (`pendingPlace` is one
press-to-release; `drawing` was the analogue in the concat version
and is not needed here — box drag-draw is a single press-drag-
release gesture, not a multi-click tool).

Persistence
-----------
`room_boxes.json` is fetched at boot; every save also writes to
localStorage.  The two places that depend on the draw hook —
`fitView` when there is no content, and `doSave`'s feedback path —
call `callDraw()` (which routes to the injected draw function)
rather than a bare `draw()`.  In the ES-module model, `draw` is not
in this file's scope; `callDraw()` is the only safe way to ask for
a repaint.

DRAW REFERENCE
==============
`resize()` and `fitView()` call a `_draw` injected at boot via
`_setDraw(draw)`.  A static import of `draw` from `bx_dispatch.js`
would create a cycle (bx_dispatch imports from bx_core) — the same
indirection board/pair/cone use.

GEOMETRY IMPORT
===============
`bx_geometry.js` is the generated module that carries the room
geometry dict under the name `GEOMETRY`.  Every function in this
file that reads `GEOMETRY.bounds`, `GEOMETRY.faces`, or
`GEOMETRY.stepFaces` (via `boxOverlapsWalls` and `fitView` here;
other files add `GEOMETRY.cutZ`, `GEOMETRY.stepFaces`) needs
GEOMETRY in its own module scope — the concatenation model relied
on a page-level `const`, which the ES-module model does not have.
The import below is what puts it here.
"""

CORE_JS = r"""
import {
  ctx, env, canvas, PALETTE,
  MIN_ZOOM_FACTOR, MAX_ZOOM,
} from "./bx_base.js";
import { GEOMETRY } from "./bx_geometry.js";
import { pgkitNotifyAll } from "./pgkit/remote_client.js";

/* ==========================================================================
   VIEW
   ==========================================================================
   The view transform.  Same fields the old top-level `view` const
   held: zoom, panX, panY, plus `fitScale` (the reference for the
   zoom-out floor in `zoomAt`).

   The board-space → screen-space mapping is:

       screenX =  boardX * zoom + panX
       screenY = -boardY * zoom + panY

   The minus sign on the Y axis is what flips the room's plan-space
   +Y-up convention (from the CAD section) into the canvas's +Y-down
   pixel convention.  Every world-space point — walls, steps, boxes,
   handles, cables — is passed through these two functions; nothing
   else in the codebase applies a scale or a flip of its own. */

export const view = { zoom: 1, panX: 0, panY: 0, fitScale: 1 };

export function b2s(bx, by) {
  return [bx * view.zoom + view.panX, -by * view.zoom + view.panY];
}
export function s2b(sx, sy) {
  return [(sx - view.panX) / view.zoom, -(sy - view.panY) / view.zoom];
}

/* The board is a plain board-space coordinate system.  These two are
   the same shape as the cable playground's w2sFloor / s2wFloor, kept
   here as small aliases because the boxes renderer names the space
   "board" rather than "floor". */
export function w2sFloor(x, y) { return b2s(x, y); }
export function s2wFloor(sx, sy) { return s2b(sx, sy); }

/* ==========================================================================
   BOARD STATE
   ========================================================================== */

export const board = {
  items:        [],
  selectedIdx:  -1,
  gridGap:      24,
  defaultWidth: 320,
  nextZ:        1,
  palette: [
    { r: 255, g: 200, b:  90 },
    { r: 120, g: 220, b: 255 },
    { r: 255, g: 130, b: 210 },
    { r: 140, g: 225, b: 160 },
    { r: 180, g: 155, b: 255 },
    { r: 255, g: 165, b: 105 },
    { r: 110, g: 220, b: 210 },
    { r: 220, g: 230, b: 120 },
  ],
};

/* The state object collects every interaction-scoped value that the
   old concatenation model held as a top-level `let`.  All of these
   are mutated from at least two modules (event handlers, hit tests,
   renderer, panel), so they must live in a `const` object rather
   than a bare `let`. */
export const state = {
  dragBoxMode:  false,
  spaceHeld:    false,
  hoveredBox:   null,
  drag:         null,
  handleDrag:   null,
  pendingPlace: null,
  panning:      null,
  drawing:      null,
  boxCounter:   0,
  mouse:        { sx: 0, sy: 0, inside: false },
};

/* Compatibility aliases so existing code that names the field
   directly (e.g. `pendingPlace.drag`) reads naturally.  These
   forward accesses to `state.*` — they are not separate bindings. */
Object.defineProperties(window, {
  __boxesStateAlias: { value: state, configurable: true },
});

/* ==========================================================================
   GEOMETRY HELPERS — pure functions, no state
   ========================================================================== */

export function pointInPolygon(p, poly) {
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

export function segsIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
  const d1x = bx - ax, d1y = by - ay;
  const d2x = dx - cx, d2y = dy - cy;
  const denom = d1x * d2y - d1y * d2x;
  if (Math.abs(denom) < 1e-12) return false;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / denom;
  const s = ((cx - ax) * d1y - (cy - ay) * d1x) / denom;
  return t >= 0 && t <= 1 && s >= 0 && s <= 1;
}

export function pointInFace(p, face) {
  if (!pointInPolygon(p, face.outer)) return false;
  for (const h of face.holes) if (pointInPolygon(p, h)) return false;
  return true;
}

export function polygonOverlapsFace(poly, face) {
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

export function polygonsOverlap(p1, p2) {
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

export function boxCorners(cx, cy, w, h, rotDeg) {
  const r = rotDeg * Math.PI / 180;
  const c = Math.cos(r), s = Math.sin(r);
  const hw = w / 2, hh = h / 2;
  const off = [[-hw, -hh], [hw, -hh], [hw, hh], [-hw, hh]];
  return off.map(([dx, dy]) =>
    [cx + c * dx - s * dy, cy + s * dx + c * dy]);
}

export function boxEdgeMidpoints(cx, cy, w, h, rotDeg) {
  const c = boxCorners(cx, cy, w, h, rotDeg);
  return [
    [(c[0][0] + c[1][0]) / 2, (c[0][1] + c[1][1]) / 2],
    [(c[1][0] + c[2][0]) / 2, (c[1][1] + c[2][1]) / 2],
    [(c[2][0] + c[3][0]) / 2, (c[2][1] + c[3][1]) / 2],
    [(c[3][0] + c[0][0]) / 2, (c[3][1] + c[0][1]) / 2],
  ];
}

export function boxOverlapsWalls(cx, cy, w, h, rotDeg) {
  const corners = boxCorners(cx, cy, w, h, rotDeg);
  for (const face of GEOMETRY.faces) {
    if (polygonOverlapsFace(corners, face)) return true;
  }
  return false;
}

export function boxIsInvalid(b) {
  if (b._invalid === undefined) {
    b._invalid = boxOverlapsWalls(b.x, b.y, b.w, b.h, b.rot);
  }
  return b._invalid;
}
export function invalidateBox(b) { b._invalid = undefined; }

/* ==========================================================================
   ROTATION HANDLE POSITION
   ==========================================================================
   The rotation handle sits in the box's LOCAL frame at
   (0, h/2 + offMm) — above the top edge — where offMm is
   ROTATE_HANDLE_OFFSET_PX converted from screen px to world mm at the
   current scale.  Because the offset is in screen px, the handle sits
   the same distance from the box at every zoom level. */

export function rotateHandleWorldPos(b) {
  const offMm = 32 / Math.max(view.zoom * (b._scale || 1), 1e-9);
  const r = b.rot * Math.PI / 180;
  const localY = b.h / 2 + offMm;
  return [
    b.x - Math.sin(r) * localY,
    b.y + Math.cos(r) * localY,
  ];
}

export function rotateHandleScreenPos(b) {
  const [wx, wy] = rotateHandleWorldPos(b);
  return b2s(wx, wy);
}

/* ==========================================================================
   VIEW TRANSFORMS
   ========================================================================== */

export function fitView() {
  const b = GEOMETRY.bounds;
  const cw = canvas.clientWidth, ch = canvas.clientHeight;
  const bw = (b.maxX - b.minX) || 1;
  const bh = (b.maxY - b.minY) || 1;
  const pad = 60;
  view.zoom = Math.min((cw - 2 * pad) / bw, (ch - 2 * pad) / bh);
  const mx = (b.minX + b.maxX) / 2;
  const my = (b.minY + b.maxY) / 2;
  /* The Y-axis flip lives in b2s, so the room centre maps to the
     viewport centre with panY = ch/2 + my * zoom (NOT -my * zoom).
     Deriving it: screen(centre) = [mx*zoom + panX, -my*zoom + panY]
     = [cw/2, ch/2] gives panX = cw/2 - mx*zoom, panY = ch/2 +
     my*zoom. */
  view.panX = cw / 2 - mx * view.zoom;
  view.panY = ch / 2 + my * view.zoom;

  /* Reference for the zoom-out floor: MIN_ZOOM_FACTOR × this value.
     Recalculated every resize so the floor tracks the fit. */
  view.fitScale = view.zoom;
}

export function resize() {
  env.dpr = window.devicePixelRatio || 1;
  const cw = window.innerWidth, ch = window.innerHeight;
  canvas.width  = Math.round(cw * env.dpr);
  canvas.height = Math.round(ch * env.dpr);
  canvas.style.width  = cw + "px";
  canvas.style.height = ch + "px";
  ctx.setTransform(env.dpr, 0, 0, env.dpr, 0, 0);
  fitView();
  callDraw();
}
window.addEventListener("resize", resize);

export function zoomAt(sx, sy, factor) {
  const [wx, wy] = s2b(sx, sy);
  const fitRef   = view.fitScale || view.zoom;
  const lower    = Math.max(1e-6, fitRef * MIN_ZOOM_FACTOR);
  const newZoom  = Math.max(lower, Math.min(MAX_ZOOM, view.zoom * factor));
  if (newZoom === view.zoom) return;
  view.zoom = newZoom;
  /* Keep the world point (wx, wy) fixed under the cursor after the
     zoom.  Solving [wx*z + panX, -wy*z + panY] = [sx, sy] for the
     pans gives panX = sx - wx*z and panY = sy + wy*z. */
  view.panX = sx - wx * view.zoom;
  view.panY = sy + wy * view.zoom;
}

/* ==========================================================================
   PERSISTENCE
   ========================================================================== */

const BOXES_FILE = "room_boxes.json";

export function serializeBoxes() {
  return {
    version: 1,
    boxes: board.items.map(b => ({
      name: b.name || "",
      x: Math.round(b.x * 100) / 100,
      y: Math.round(b.y * 100) / 100,
      w: Math.round(b.w * 100) / 100,
      h: Math.round(b.h * 100) / 100,
      rot: Math.round(b.rot * 100) / 100,
    })),
  };
}

export function normalizeBoxes(data) {
  if (Array.isArray(data)) return data;
  if (data && Array.isArray(data.boxes)) return data.boxes;
  return [];
}

export function applyLoadedBoxes(list) {
  board.items = [];
  state.boxCounter = 0;
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
    board.items.push(b);
    const m = /^Box (\d+)$/.exec(b.name);
    if (m) state.boxCounter = Math.max(state.boxCounter, +m[1]);
  }
}

export function saveToLocalStorage() {
  try {
    localStorage.setItem("room_boxes", JSON.stringify(serializeBoxes()));
  } catch (e) { /* quota / private mode — ignore */ }
}

export function loadFromLocalStorage() {
  try {
    const s = localStorage.getItem("room_boxes");
    return s ? JSON.parse(s) : null;
  } catch (e) { return null; }
}

export async function saveToServer() {
  try {
    const r = await fetch("/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serializeBoxes()),
    });
    return r.ok;
  } catch (e) { return false; }
}

export async function loadFromServer() {
  try {
    const r = await fetch(BOXES_FILE, { cache: "no-store" });
    if (!r.ok) return null;
    const txt = await r.text();
    if (!txt.trim()) return null;
    return JSON.parse(txt);
  } catch (e) { return null; }
}

export function downloadBoxes() {
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

export async function doSave(showFeedback) {
  saveToLocalStorage();
  const ok = await saveToServer();
  const { flashStatus } = await import("./pgkit/panel_core.js");
  const { T } = await import("./bx_i18n.js");
  if (!ok) {
    downloadBoxes();
    if (showFeedback) flashStatus(T("msgDownloadedOk")(BOXES_FILE), "ok");
  } else if (showFeedback) {
    flashStatus(T("msgSavedOk")(BOXES_FILE), "ok");
  }
}

export function consolePrintBoxList() {
  if (!board.items.length) { console.log("(no boxes placed)"); return; }
  const nameW = Math.max(4, ...board.items.map(b => (b.name || "").length));
  const header = "Name".padEnd(nameW) + "  " +
                 "Width".padStart(10) + "  " + "Height".padStart(10);
  console.log(header);
  console.log("-".repeat(header.length));
  for (const b of board.items) {
    const name = (b.name || "").padEnd(nameW);
    const w = b.w.toFixed(2).padStart(10);
    const h = b.h.toFixed(2).padStart(10);
    console.log(`${name}  ${w}  ${h}`);
  }
}

export async function printBoxList() {
  const { flashStatus } = await import("./pgkit/panel_core.js");
  const { T } = await import("./bx_i18n.js");
  try {
    const r = await fetch("/print", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(serializeBoxes()),
    });
    if (r.ok) { flashStatus(T("msgPrintTerminal"), "ok"); return; }
    consolePrintBoxList();
    flashStatus(T("msgPrintConsole"), "ok");
  } catch (e) {
    consolePrintBoxList();
    flashStatus(T("msgPrintConsole"), "ok");
  }
}

export async function autoload() {
  const { flashStatus } = await import("./pgkit/panel_core.js");
  const { T } = await import("./bx_i18n.js");
  let data = await loadFromServer();
  let source = data ? "file" : null;
  if (!data) {
    data = loadFromLocalStorage();
    source = data ? "localStorage" : null;
  }
  if (data) {
    applyLoadedBoxes(normalizeBoxes(data));
    const n = board.items.length;
    const bad = board.items.filter(boxIsInvalid).length;
    if (n) flashStatus(T("msgLoaded")(n, source, bad), bad ? "warn" : "ok");
  }
  callDraw();
}

/* ==========================================================================
   HIT TESTS
   ========================================================================== */

export function hitTestBox(wx, wy) {
  for (let i = board.items.length - 1; i >= 0; i--) {
    const b = board.items[i];
    if (pointInPolygon([wx, wy], boxCorners(b.x, b.y, b.w, b.h, b.rot))) {
      return i;
    }
  }
  return -1;
}

/* Handle hit-testing priority, highest first:

     rotate    the floating circle above the top edge
     corner    the four box corners
     edge      the four edge midpoints

   The type string returned here is the same one bx_dispatch uses to
   decide which drag to start. */
export function hitTestHandle(sx, sy) {
  const selectedBox = selectedItem();
  if (!selectedBox) return null;
  const b = selectedBox;
  const corners = boxCorners(b.x, b.y, b.w, b.h, b.rot);
  const edges   = boxEdgeMidpoints(b.x, b.y, b.w, b.h, b.rot);

  const [rhx, rhy] = rotateHandleScreenPos(b);
  if (Math.hypot(sx - rhx, sy - rhy) <= 9 + 4) {
    return { type: "rotate" };
  }

  for (let i = 0; i < 4; i++) {
    const [px, py] = b2s(corners[i][0], corners[i][1]);
    if (Math.abs(sx - px) <= 11 && Math.abs(sy - py) <= 11) {
      return { type: "corner", index: i };
    }
  }
  for (let i = 0; i < 4; i++) {
    const [px, py] = b2s(edges[i][0], edges[i][1]);
    if (Math.abs(sx - px) <= 10 && Math.abs(sy - py) <= 10) {
      return { type: "edge", index: i };
    }
  }
  return null;
}

/* ==========================================================================
   UI INPUTS — ghost params
   ========================================================================== */

export function getBoxParams() {
  const w = parseFloat(document.getElementById("boxW").value) || 100;
  const h = parseFloat(document.getElementById("boxH").value) || 100;
  let rot = parseFloat(document.getElementById("boxRot").value) || 0;
  rot = ((rot % 360) + 360) % 360;
  return { w, h, rot };
}

export function setRotation(deg) {
  const v = ((deg % 360) + 360) % 360;
  document.getElementById("boxRot").value = v;
}

/* ==========================================================================
   SELECTION
   ========================================================================== */

export function selectedItem() {
  if (board.selectedIdx < 0 ||
      board.selectedIdx >= board.items.length) return null;
  return board.items[board.selectedIdx];
}

export function setSelected(b) {
  board.selectedIdx = (b == null) ? -1 : board.items.indexOf(b);
  // selection-change side effects (list, inspector) are performed
  // by the caller (bx_panel_bindings._selectAction).
}

/* ==========================================================================
   DRAW REFERENCE
   ==========================================================================
   The draw hook is injected at boot via _setDraw.  Without this
   indirection every function here that needs a repaint would import
   `draw` from `bx_dispatch.js`, which imports from this file. */

let _drawRef = null;
export function _setDraw(fn) { _drawRef = fn; }
export function callDraw() { if (_drawRef) _drawRef(); }

/* ==========================================================================
   STATUS — flashStatus
   ==========================================================================
   A local, self-contained flash-status function.  The pgkit panel
   has its own (pgkit/panel_core.js) but this one is used by
   internal helpers that run before the kit has been wired.
   The class names match the pgkit CSS exactly ("ok"/"warn"/"bad"). */

let statusTimer = null;
export function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.className = cls || "";
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => {
    statusTimer = null;
    callDraw();
  }, 1400);
}
"""
