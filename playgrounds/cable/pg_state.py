"""
pg_state.py — leaf state module for the cable playground.

Everything mutable or shared that other modules read or write lives
here.  The module imports nothing from the project's own modules
except the generated `pg_geometry.js`.  Every other module imports
from this one.

WHY A STATE OBJECT FOR MUTABLE BINDINGS
=======================================
ES module `let` exports are read-only from the import side.  The
cable playground had a dozen such bindings that were reassigned from
multiple files; each now lives as a property of a `const` object:

    env, state, panelState, registryState, junctionGraph

plus the `const` collections `anchors`, `NOTES`, `mouse`, `viewFloor`,
`viewWall`, `layout`, `router`.

DRAW HOOK AND AFTER-CHANGE HOOK
===============================
`setDraw(fn)` is called once from the boot module with the project's
`draw`.  `callDraw()` is the only safe way for any other module to
ask for a repaint.

`setAfterChange(fn)` mirrors that for bulk mutations;
`notifyChanged()` runs whatever handler the boot module injected.

`_segAtOriginalU`
=================
Maps an absolute strip `u` back to a segment index, respecting focus-
mode remapping.  Lives here rather than in `pg_view_wall.js` so
`pg_snap.js` can call it without importing `pg_view_wall.js`, which
would create the cycle pg_edit → pg_snap → pg_view_wall →
pg_view_sheet → pg_edit.

`w2sFloor` / `w2sWall`
======================
The world-to-screen and screen-to-world transforms.  The naming
matches the concatenation model's convention (`w2s*` for world-
space, `s2w*` for screen-space), because every consuming module
imports under those names.

`GEOMETRY` — RE-EXPORTED
========================
`pg_geometry.js` is generated at build time by `cable_html.py`'s
`_build_module_sources(geom)` from the `room_walls.json` dict.  It
exports exactly one symbol — `GEOMETRY` — which every renderer reads
for bounds, cut height, step polygons, column boxes, and the
unfolded-wall segment table.

`pg_state.js` imports `GEOMETRY` for its own uses (step helpers,
polygon tests) and re-exports it so every consumer can say

    import { GEOMETRY } from "./pg_state.js";

without also having to remember the separate `pg_geometry.js` path.
That one-line re-export is what the linter was flagging: eleven
modules import `GEOMETRY` from `pg_state.js`, and without the
re-export, `pg_state.js` only consumed the symbol rather than
publishing it.

`state.trueCables`
==================
`state.trueCables` is initialized to `[]` here, not lazily created by
`recomputeTrueCables`, because the export entry point
(`pg_export_entry.js`) reads `state.trueCables.length` before any
cable has ever been created.  Without the initializer, opening the
export dialog on a fresh board throws.
"""
import json

from playground_fonts import (
    FONT_FAMILY_MONO,
    FONT_FAMILY_SANS,
    FONT_FACE_CSS,
)


STATE_JS = (
    f'export const FONT_MONO = {json.dumps(FONT_FAMILY_MONO)};\n'
    f'export const FONT_SANS = {json.dumps(FONT_FAMILY_SANS)};\n'
    f'export const FONT_FACE_CSS = {json.dumps(FONT_FACE_CSS)};\n'
    + r"""

import { GEOMETRY } from "./pg_geometry.js";
export { GEOMETRY };

/* ==========================================================================
   THE STATIC ROOM MODEL
   ========================================================================== */

export const WALL_HEIGHT = GEOMETRY.unfoldedWall.height;
export const WALL        = GEOMETRY.unfoldedWall;
export const STATE_FILE  = "room_layout.json";
export const ANCHOR_NEARBY_PX = 14;

/* ==========================================================================
   PALETTE
   ========================================================================== */

export const PALETTE = {
  ink:          "#dce6f2",
  inkSoft:      "rgba(220, 230, 242, 0.55)",
  inkFaint:     "rgba(220, 230, 242, 0.18)",
  inkGuide:     "rgba(220, 230, 242, 0.28)",

  paper:        "#0a0e14",
  paperTint:    "#060a10",

  step:         "#ff2bd6",
  stepSoft:     "rgba(255, 43, 214, 0.75)",
  stepFaint:    "rgba(255, 43, 214, 0.40)",

  transit:      "#00e5ff",
  transitSoft:  "rgba(0, 229, 255, 0.80)",
  transitFaint: "rgba(0, 229, 255, 0.42)",

  highlight:    "#ffe600",
  highlightBg:  "rgba(255, 230, 0, 0.16)",
  highlightSft: "rgba(255, 230, 0, 0.60)",
};

/* ==========================================================================
   SHEET / NAVIGATION CONSTANTS
   ========================================================================== */

export const SHEET_MARGIN  = 6;
export const RIBBON_BAND_H = 22;
export const FLOOR_PAD     = 60;
export const MARGIN_TICK_LEN = 5;
export const REG_CROSS_R   = 9;

export const MIN_ZOOM = 0.02;
export const MAX_ZOOM = 8.0;
export const ZOOM_STEP = 1.10;

export const WALL_SIDE_CHAN = 40;
export const SPLIT_MIN_FRAC = 0.35;
export const SPLIT_MAX_FRAC = 0.65;

/* ==========================================================================
   RULER / DIMENSION CONSTANTS
   ========================================================================== */

export const DIM_TICK_LEN   = 4;
export const DIM_ROW_H      = 16;
export const DIM_EXT_OVER   = 2;
export const DIM_EXT_GAP    = 2;
export const RULER_OFF_PX   = 26;
export const RULER_SNAP_PX  = 14;

/* ==========================================================================
   CABLE STROKE WIDTHS
   ========================================================================== */

export const CABLE_STROKE = {
  plain:    { halo: 4.5, ink: 1.4 },
  sibling:  { halo: 5.5, ink: 1.8 },
  selected: { outerHalo: 10, outerInk: 5.5,
              innerPaper: 4,  innerInk: 2 },
};

export const BALLOON_R = 8;
export const BALLOON_LEADER = 12;
export const BALLOON_MIN_SEP = 18;

/* ==========================================================================
   CANVAS / CONTEXT / DPR
   ========================================================================== */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export const env    = { dpr: window.devicePixelRatio || 1 };

/* ==========================================================================
   LAYOUT / VIEW STATE
   ========================================================================== */

export const layout = { floorH: 0, dividerY: 0, wallY: 0, wallH: 0 };

export const viewFloor = { scale: 1, tx: 0, ty: 0, zoom: 1, panX: 0, panY: 0 };
export const viewWall  = {
  scaleX: 1, scaleY: 1, tx: 0, ty: 0, stripTopY: 0,
  zoom: 1, zoomX: 1, panX: 0, panY: 0,
};

export const router = {
  ESCAPE_DROP: 30,
  leftBaseX:   0,
  rightBaseX:  0,
};

/* ---- world-to-screen (plan view) ---- */
export function w2sFloor(x, y) {
  return [x * viewFloor.scale + viewFloor.tx,
          -y * viewFloor.scale + viewFloor.ty];
}
/* ---- screen-to-world (plan view) ---- */
export function s2wFloor(sx, sy) {
  return [(sx - viewFloor.tx) / viewFloor.scale,
          -(sy - viewFloor.ty) / viewFloor.scale];
}
/* ---- world-to-screen (wall strip) ---- */
export function w2sWall(u, v) {
  return [u * viewWall.scaleX + viewWall.tx,
          -v * viewWall.scaleY + viewWall.ty];
}
/* ---- screen-to-world (wall strip) ---- */
export function s2wWall(sx, sy) {
  return [(sx - viewWall.tx) / viewWall.scaleX,
          -(sy - viewWall.ty) / viewWall.scaleY];
}

export function whichView(sy) {
  if (sy < layout.dividerY) return "floor";
  if (sy >= layout.wallY)   return "wall";
  return null;
}

/* ==========================================================================
   MUTABLE GLOBAL STATE
   ==========================================================================
   `trueCables` is initialized here because pg_export_entry.js reads
   `state.trueCables.length` before any cable has ever been created
   (on a fresh board, before the first refresh/save cycle). */

export const state = {
  floorGrids:    [],
  wallGrids:     [],
  floorCables:   [],
  wallCables:    [],
  trueCables:    [],

  selectedCable: null,
  selectedVertex: null,

  dragGrid:     null,
  dragVertex:   null,
  pan:          null,
  hoveredRoute: null,

  drawing:        null,
  drawingPreview: null,

  routeCache:   [],

  snapEnabled:  false,

  focusSeg:     -1,
  focusSavedU:  null,

  spaceHeld:    false,

  ruler: {
    active:            false,
    pending:           null,
    measures:          [],
    hoverPt:           null,
    hoverIdx:          -1,
    selected:          -1,
    axisPref:          null,
    ignoreSteps:       false,
    pendingJumpedStep: false,
    _hasLeftStepEdge:  false,
    _lastDStep:        Infinity,
    pendingEnd:        null,
    pendingAxis:       null,
    input: {
      mode:          null,
      targetMeasure: null,
    },
  },
};

export const mouse = {
  sx: 0, sy: 0, view: null, inside: false, alt: false, shift: false,
};

/* ==========================================================================
   PANEL / MOUSE-IDLE STATE
   ========================================================================== */

export const panelState = {
  hovered:      false,
  lastActivity: 0,
};

/* ==========================================================================
   ANCHORS + JUNCTION GRAPH
   ========================================================================== */

export const anchors = new Map();
export const registryState = { idCounter: 1 };

export const junctionGraph = {
  JUNCTIONS:            [],
  JUNCTION_OF_TERMINAL: null,
  SEGMENT_ADJ:          [],
  ADJ_DETAIL:           null,
};

/* ==========================================================================
   NOTES
   ========================================================================== */

export const NOTES = [];

/* ==========================================================================
   STEP HELPERS
   ========================================================================== */

export function segTop(s) {
  return (s && typeof s.z_hi === "number") ? s.z_hi : WALL_HEIGHT;
}
export function segBottom(s) {
  return (s && typeof s.z_lo === "number") ? s.z_lo : 0;
}
export function isStepFace(s) {
  return !!(s && s.kind === "step");
}

/* ==========================================================================
   POINT HELPERS
   ========================================================================== */

export function pointInPolygon(p, poly) {
  const x = p[0], y = p[1];
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    if (((yi > y) !== (yj > y)) &&
        (x < (xj - xi) * (y - yi) / (yj - yi) + xi)) inside = !inside;
  }
  return inside;
}

export function pointToSegmentDist(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  if (len2 < 1e-9) return Math.hypot(px - ax, py - ay);
  let t = ((px - ax) * dx + (py - ay) * dy) / len2;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

export function polylineHit(wx, wy, pts, tol) {
  for (let i = 0; i < pts.length - 1; i++) {
    if (pointToSegmentDist(wx, wy,
        pts[i][0], pts[i][1], pts[i+1][0], pts[i+1][1]) < tol) return true;
  }
  return false;
}

export function pointOnStep(x, y) {
  const p = [x, y];
  for (const face of GEOMETRY.stepFaces) {
    if (!pointInPolygon(p, face.outer)) continue;
    let inHole = false;
    for (const h of face.holes) {
      if (pointInPolygon(p, h)) { inHole = true; break; }
    }
    if (!inHole) return true;
  }
  return false;
}

export function fmtCm(v_mm) {
  const cm = v_mm / 10;
  const r  = Math.round(cm * 10) / 10;
  return Number.isInteger(r) ? String(r) : r.toFixed(1);
}

/* ==========================================================================
   FOCUS-MODE ORIGINAL-U LOOKUP
   ==========================================================================
   Maps an absolute strip u back to a segment index, respecting focus
   mode.  Callers: pg_view_wall.js's pill placement, pg_snap.js's
   proximity test.  Kept in this leaf module so pg_snap can call it
   without importing pg_view_wall. */

export function _segAtOriginalU(u) {
  if (state.focusSavedU) {
    for (const [segIdx, saved] of state.focusSavedU) {
      if (saved.u0 <= u && u <= saved.u1) return segIdx;
    }
    return -1;
  }
  for (let i = 0; i < WALL.segments.length; i++) {
    const s = WALL.segments[i];
    if (s.u0 <= u && u <= s.u1) return i;
  }
  return -1;
}

/* ==========================================================================
   DRAW HOOK
   ========================================================================== */

let _drawRef = null;
export function setDraw(fn) { _drawRef = fn; }
export function callDraw()  { if (_drawRef) _drawRef(); }

/* ==========================================================================
   AFTER-CHANGE HOOK
   ========================================================================== */

let _afterChange = null;
export function setAfterChange(fn) { _afterChange = fn; }
export function notifyChanged()    { if (_afterChange) _afterChange(); }

/* ==========================================================================
   SCREEN TEXTURES
   ========================================================================== */

const _SCREEN_PATTERNS    = Object.create(null);
const _SCREEN_PAT_OBJECTS = Object.create(null);

function _screenPattern(kind) {
  if (_SCREEN_PATTERNS[kind]) return _SCREEN_PATTERNS[kind];
  const make = (size, color, weight, draw) => {
    const pc = document.createElement("canvas");
    pc.width = pc.height = size;
    const p = pc.getContext("2d");
    p.fillStyle   = PALETTE.paper;
    p.fillRect(0, 0, size, size);
    p.strokeStyle = color;
    p.lineWidth   = weight;
    p.lineCap     = "round";
    draw(p, size);
    return pc;
  };
  const defs = {
    crossHatch: [11, "rgba(220, 230, 242, 0.72)", 0.6, (p, s) => {
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.moveTo(-1, -1);    p.lineTo(s + 1, s + 1);
      p.stroke();
    }],
    voidHatch: [12, "rgba(220, 230, 242, 0.72)", 0.6, (p, s) => {
      p.beginPath();
      p.moveTo(s + 1, -1); p.lineTo(-1, s + 1);
      p.stroke();
    }],
    planInterior: [22, "rgba(220, 230, 242, 0.11)", 0.35, (p, s) => {
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.stroke();
    }],
    stepHatch: [12, "rgba(220, 230, 242, 0.16)", 0.9, (p, s) => {
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.stroke();
    }],
  };
  const [size, color, weight, draw] = defs[kind];
  _SCREEN_PATTERNS[kind] = make(size, color, weight, draw);
  return _SCREEN_PATTERNS[kind];
}

export function _screenPat(kind) {
  let p = _SCREEN_PAT_OBJECTS[kind];
  if (!p) {
    p = ctx.createPattern(_screenPattern(kind), "repeat");
    _SCREEN_PAT_OBJECTS[kind] = p;
  }
  return p;
}

/* ==========================================================================
   SHARED DRAWING HELPERS
   ========================================================================== */

export function _roundRect(x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
  ctx.lineTo(x + w, y + h - r);
  ctx.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
  ctx.lineTo(x + r, y + h);
  ctx.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
  ctx.lineTo(x, y + r);
  ctx.arc(x + r, y + r, r, Math.PI, Math.PI * 3 / 2);
  ctx.closePath();
}

export function _drawValuePill(cx, cy, text, accent) {
  ctx.save();
  ctx.font = "600 10px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const w = ctx.measureText(text).width + 10;
  const h = 13;
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(cx - w / 2, cy - h / 2, w, h);
  ctx.strokeStyle = accent || PALETTE.ink;
  ctx.lineWidth = 0.9;
  ctx.strokeRect(cx - w / 2 + 0.4, cy - h / 2 + 0.4, w - 0.8, h - 0.8);
  ctx.fillStyle = accent || PALETTE.ink;
  ctx.fillText(text, cx, cy + 0.5);
  ctx.restore();
}

export function _drawValuePillColored(cx, cy, text, color) {
  ctx.save();
  ctx.font = "600 10px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const w = ctx.measureText(text).width + 10;
  const h = 13;
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(cx - w / 2, cy - h / 2, w, h);
  ctx.strokeStyle = color;
  ctx.lineWidth = 0.8;
  ctx.strokeRect(cx - w / 2 + 0.4, cy - h / 2 + 0.4, w - 0.8, h - 0.8);
  ctx.fillStyle = color;
  ctx.fillText(text, cx, cy + 0.5);
  ctx.restore();
}

export function drawSnapCrosshair(sx, sy) {
  ctx.save();
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 0.9;
  const r = 12, g = 3;
  ctx.beginPath();
  ctx.moveTo(sx - r, sy); ctx.lineTo(sx - g, sy);
  ctx.moveTo(sx + g, sy); ctx.lineTo(sx + r, sy);
  ctx.moveTo(sx, sy - r); ctx.lineTo(sx, sy - g);
  ctx.moveTo(sx, sy + g); ctx.lineTo(sx, sy + r);
  ctx.stroke();
  ctx.restore();
}
"""
)
