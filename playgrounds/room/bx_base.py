"""
bx_base.py — palette, sheet constants, textures, DOM basics, primitives.

The lowest layer of the boxes playground's ES-module split: nothing
here depends on any other project module.  Everything else depends
on this one.

Contents
--------
    • FONT constants — MONO / SANS / @font-face CSS, sourced from
      `playground_fonts.py` so the panel, canvas, PNG export, and
      instructions popover all use the same typeface.

    • canvas / ctx / env — the 2d context and a mutable `env.dpr`
      holding the current device-pixel-ratio.  `env.dpr` replaces the
      old top-level `let dpr` (a binding that cannot be reassigned
      from another module).  `resize()` in bx_core.js updates
      `env.dpr`.

    • PALETTE — the dark-theme colour system.  Same palette the
      cable playground uses; the six semantic slots (ink, paper,
      step, transit, highlight + soft variants) are read by every
      renderer.

    • SHEET CONSTANTS — dimension-tick, handle-radius, zoom, split
      constants shared by every renderer.

    • PANEL STATE — `panelState.hovered` / `panelState.lastActivity`.

    • SCREEN TEXTURES — four cached pattern canvases and their
      cached `CanvasPattern` objects.  Both caches matter: the
      source canvas is expensive to build (one `arc` per pixel of
      the tile), and the `CanvasPattern` wrapper is expensive to
      allocate on every draw call, which is what the previous
      `_screenPat` did.

    • SHARED PRIMITIVES — `_roundRect`, `_drawValuePill`,
      `_drawValuePillColored`, `drawSnapCrosshair`.

Dependency direction
--------------------
Nothing in this file imports from another project module.  The
`ctx` and `env` singletons live here because `_screenPat` and every
primitive need them, and because putting them anywhere else
creates the cycle `bx_core → bx_view → bx_core` at module-evaluation
time (the earlier concatenation model had no such problem because
everything was in one scope).  This matches the pattern
`cone/pg_state.py` and `board/pg_core.py` use: extract to the
leaf, then all other modules import from the leaf.
"""

import json

from playground_fonts import (
    FONT_FAMILY_MONO,
    FONT_FAMILY_SANS,
    FONT_FACE_CSS as _FACE_CSS,
)


BASE_JS = (
    # ---- font exports (from playground_fonts) ------------------------------
    '/* ==== playground_fonts.py — centralised font stacks ==== */\n'
    'export const FONT_MONO = ' + json.dumps(FONT_FAMILY_MONO) + ';\n'
    'export const FONT_SANS = ' + json.dumps(FONT_FAMILY_SANS) + ';\n'
    'export const FONT_FACE_CSS = ' + json.dumps(_FACE_CSS) + ';\n'
    + r"""

/* ==========================================================================
   CANVAS / CONTEXT / DEVICE-PIXEL-RATIO
   ==========================================================================
   `canvas` and `ctx` are singletons — the page has exactly one
   <canvas> and one 2d context.

   `env.dpr` replaces the old top-level `let dpr`.  A top-level `let`
   cannot be reassigned from another module in the ES-module model
   (the import-side binding is read-only), so any mutable state that
   crosses module boundaries is wrapped in a `const` object.  Same
   pattern `cone/pg_state.py` uses for `coneState`, `SHAPE_STATE`,
   and `env`. */

export const canvas = document.getElementById("c");
export const ctx    = canvas.getContext("2d");
export const env    = { dpr: window.devicePixelRatio || 1 };

/* ==========================================================================
   PALETTE
   ==========================================================================
   Light ink on a deep, cool paper.  The polarity of the light theme
   is inverted without touching the sheet idiom: what used to be black
   ink is now cool grey, what used to be white paper is now near-black,
   and the three semantic accents are pushed into an electric register
   so they carry the visual weight that, on white paper, the ink alone
   used to carry.

     ink        — structural stroke for every wall, dimension, grid,
                  ruler, and sheet frame
     paper      — the canvas, and the knockout halo under every stroke
     paperTint  — the wall-band background; one step deeper than paper
     step       — magenta; step risers, dimensions, invalid accents
     transit    — cyan; valid boxes and cross-view markers
     highlight  — yellow; cursor, snap, selection */

export const PALETTE = {
  /* ---- structural ink ---- */
  ink:          "#dce6f2",
  inkSoft:      "rgba(220, 230, 242, 0.55)",
  inkFaint:     "rgba(220, 230, 242, 0.18)",
  inkGuide:     "rgba(220, 230, 242, 0.28)",

  /* ---- paper ---- */
  paper:        "#0a0e14",
  paperTint:    "#060a10",

  /* ---- electric accents ---- */
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
   SHEET CONSTANTS
   ========================================================================== */

export const SHEET_MARGIN       = 6;
export const RIBBON_BAND_H      = 22;
export const FLOOR_PAD          = 60;
export const MARGIN_TICK_LEN    = 5;
export const REG_CROSS_R        = 9;

/* Zoom bounds.  MIN_ZOOM_FACTOR is relative to the fit scale so a
   large room in a small window can zoom out below the hard floor;
   MAX_ZOOM is absolute. */
export const MIN_ZOOM_FACTOR    = 0.20;
export const MAX_ZOOM           = 8.0;

/* Handle geometry.  See bx_core.rotateHandleWorldPos for the
   reasoning behind the offset being in screen pixels. */
export const CORNER_HIT              = 11;
export const EDGE_HIT                = 10;
export const DRAG_CANCEL             = 6;
export const MIN_BOX_MM              = 10;
export const ROTATE_HANDLE_OFFSET_PX = 32;
export const ROTATE_HANDLE_R         = 9;

/* ==========================================================================
   PANEL FADE STATE
   ==========================================================================
   Written by installPanelFade (in bx_panel_bindings.js), read by
   updatePanelOpacity (also there). */

export const panelState = {
  hovered:      false,
  lastActivity: 0,
};

/* ==========================================================================
   SCREEN TEXTURES
   ==========================================================================
   Four cached pattern canvases and, in a second cache, their
   constructed CanvasPattern objects.

   The first cache (`_SCREEN_PATTERNS`) is the source canvas — the
   tile that the browser uses to repeat the pattern.  Rebuilding it
   is expensive (one `arc` / `lineTo` per mark), so it's built once
   per kind.

   The second cache (`_SCREEN_PAT_OBJECTS`) holds the result of
   `ctx.createPattern(source, "repeat")`.  That call also has a
   non-zero cost, and in a per-frame draw path it was being run for
   every step face every frame — one allocation per face per frame
   where a single cached object would do.  The cache collapses that
   to one allocation per kind, total.

   Patterns are context-agnostic in the Canvas 2D spec: a
   CanvasPattern created from one 2D context's `createPattern` can
   be assigned to any other context's `fillStyle`.  That's what
   lets the static-layer offscreen canvas (bx_dispatch._ensureStaticLayer)
   share the same pattern objects as the main canvas without a
   second bake.

   Four kinds:

     crossHatch    the hover highlight on every wall
     voidHatch     the wall band behind the chunks
     planInterior  the floor plan interior fill
     stepHatch     the light tint over every step footprint in the
                   plan band; bx_view.drawFloorView paints the step
                   faces with this. */

const _SCREEN_PATTERNS = Object.create(null);
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
