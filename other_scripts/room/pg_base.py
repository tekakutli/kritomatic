"""
pg_base.py — palette, constants, textures, primitives, and the shared
extended view state.

The lowest layer of cable_live's twelve-module split: everything that
other modules read but do not own.  Nothing here depends on any other
live module; everything else depends on this one.

Contents
--------
    • PALETTE               — the dark-theme colour system
    • SHEET CONSTANTS       — dimension-tick, balloon, zoom, split
                              constants shared by every renderer
    • CABLE_STROKE          — cable width recipes (halo / ink pairs)
    • EXTENDED VIEW STATE   — the fields this half of the split adds to
                              pg_core's viewFloor / viewWall / state /
                              mouse, plus session-scoped NOTES and the
                              panel-fade flags
    • SCREEN TEXTURES       — the three hatch patterns the sheet uses
    • SHARED DRAWING HELPERS — the atoms: rounded rect, value pill,
                              coloured variant, snap crosshair
    • GRID LABEL / PILL     — the ruled pill that terminates a grid
                              line in either view

The extended view state lives here because it is the one place every
module reads from and no module exclusively owns.  It extends the state
pg_core.py declares; the rules that constrain it (zoom clamp, pan
arithmetic) live in pg_navigation.py, and the palette that colours it
lives above.

viewWall carries two independent scale factors:

    zoom     uniform — applied to both axes.  Adjusted by the plain
             wheel over the wall band, and by Shift+wheel over the
             floor band (which is isotropic and has no X-only mode).
    zoomX    horizontal-only — applied to viewWall.scaleX alone.
             Adjusted by Shift+wheel over the wall band.  Dilation
             leaves every vertical dimension untouched, which is what
             you want when a narrow wall's cable geometry is packed
             too tightly to read but the height scale is fine.

zoomX = 1 is a no-op; the two compose, so a uniform zoom of 1.5 with
an X-stretch of 2.0 gives scaleX a factor of 3.0 and scaleY a factor
of 1.5.  The wall-band reset (Home, or the panel's zoom-reset button)
zeroes both.
"""


BASE_JS = r"""
/* ==========================================================================
   PALETTE
   ==========================================================================
   Light ink on a deep, cool paper.  The polarity of the light theme
   is inverted without touching the sheet idiom: what used to be black
   ink is now cool grey, what used to be white paper is now near-black,
   and the three semantic accents are pushed into an electric register
   so they carry the visual weight that, on white paper, the ink alone
   used to carry.

     • ink        — structural stroke for every cable, wall, dimension,
                    grid, ruler, anchor, and sheet frame
     • paper      — the canvas, and the knockout halo under every
                    stroke, and the fill of a wall chunk in the strip
     • paperTint  — the wall band.  In the light theme this sat one
                    step BELOW paper (#f4f4f4 under #ffffff) so a wall
                    chunk read as raised.  Here it sits one step DEEPER
                    than paper (#060a10 under #0a0e14) for the same
                    reason, in the same direction.
     • step       — magenta.  Step risers (z_hi dashes, dimension chain
                    accents, step-bridge bars)
     • transit    — cyan.  Anything crossing between the two views:
                    hop arcs, cross-view boundary dots, escape arrows,
                    the SAVE outline, the armed ruler button, the
                    ruler's numeric input, the measure inspector, and
                    a selected ruler measurement's value pill
     • highlight  — yellow.  Cursor, snap crosshair, merge-target ring,
                    live drawing preview, the live draw-button outline,
                    and a hovered / pending ruler measurement

   Every rgba variant keeps the same semantic slot as its solid; alpha
   values are chosen for a dark field where a 60 % ink reads like a
   40 % ink did on white. */
const PALETTE = {
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

const SHEET_MARGIN       = 6;
const RIBBON_BAND_H      = 22;
const BALLOON_R          = 9;
const BALLOON_MIN_SEP    = 16;
const BALLOON_LEADER     = 22;
const DIM_ROW_H          = 16;
const DIM_TICK_LEN       = 4;
const DIM_EXT_OVER       = 2;
const FLOOR_PAD          = 28;
const WALL_SIDE_CHAN     = 24;
const MARGIN_TICK_LEN    = 5;
const REG_CROSS_R        = 9;
const SPLIT_MIN_FRAC     = 0.35;
const SPLIT_MAX_FRAC     = 0.65;

const MIN_ZOOM           = 0.25;
const MAX_ZOOM           = 8.0;
const ZOOM_STEP          = 1.12;

/* Cable stroke recipes.  The unselected recipe is a two-pass halo+ink;
   the selected recipe is a four-pass doubled stroke: outer halo, thick
   ink ring, inner paper gap, thin ink core.  Draw order matters — each
   later stroke is NARROWER than the previous, so it overlays the centre
   of its predecessor rather than being covered by it.

   These widths set the cable's visual weight everywhere it renders —
   in the floor view, the wall view, and the hover highlight.  Halo to
   ink is held at roughly 2.2:1, the same proportion pg_export.py uses,
   so a screen cable and a printed cable of the same logical weight
   read as the same hand.  To make cables lighter or heavier, scale
   every number in one recipe by the same factor; changing ink alone
   would break the halo ratio and change how cables read over grid
   lines and dimension rules. */
const CABLE_STROKE = {
  plain:    { halo: 2.6, ink: 1.2 },
  sibling:  { halo: 3.0, ink: 1.5 },
  selected: { outerHalo: 5.4, outerInk: 4.0,
              innerPaper: 2.8, innerInk: 1.3 },
};

/* ==========================================================================
   EXTENDED VIEW STATE
   ==========================================================================
   The fields this half of the split adds to pg_core's viewFloor /
   viewWall / state / mouse.  Extending rather than redeclaring keeps
   pg_core untouched — the const bindings stay the ones pg_core made,
   and every later module just reaches into the same objects.

   viewWall.zoomX is the horizontal-only stretch factor: it feeds
   viewWall.scaleX and nothing else, so the unfolded strip can be
   dilated sideways without touching any vertical dimension.  It is
   applied alongside viewWall.zoom inside fitViews, so the two
   compose multiplicatively and both feed the router base Xs that the
   arrow field and the ruler read.

   Session-scoped NOTES and the panel-fade flags are declared here too:
   they belong to no single view, and every renderer that reads them
   already reads from this module's palette. */

viewFloor.zoom = 1;   viewFloor.panX = 0;   viewFloor.panY = 0;
viewWall.zoom  = 1;   viewWall.zoomX = 1;   viewWall.panX  = 0;
viewWall.panY  = 0;

state.pan = null;
let spaceHeld = false;

/* Modifier tracking.  pg_core defines `mouse` with `alt` only; we
   add `shift` here so the ruler can read the Shift-held state from
   draw() and drawRulers(), which have no event to consult. */
mouse.shift = false;

/* Session-scoped redline notes. */
const NOTES = [];

/* Panel-fade state. */
let panelHovered = false;
let panelLastActivity = 0;

/* ==========================================================================
   SCREEN TEXTURES
   ==========================================================================
   Pattern weights are retuned for a dark field.  On white paper a
   0.55 px stroke at 100 % ink read as a crisp mark; on a dark field
   the same mark blooms, so the hatch weights are lifted slightly
   and the alphas dropped so the three fields keep their
   light / mid / faint hierarchy.

     crossHatch    — the hover highlight on every wall.  Two
                     diagonals at 0.6 px / 0.72 alpha.  Reads as
                     the drafting symbol for "attention here", and
                     stays distinct from the two single-direction
                     diagonals already in play.  Its alpha and
                     weight are the two knobs that control how loud
                     the highlight is: below roughly 0.35 / 0.45 the
                     mark fades into the wall's paper fill on a
                     dark field and the hover stops registering; at
                     full ink it reads as a solid block rather than
                     a hatch.  0.72 / 0.6 is deliberately in
                     between — bright enough to be unmistakable
                     against the surrounding strip, still sparse
                     enough that the wall's own dimension rules and
                     z_hi dashes show through it.
     voidHatch     — the wall band behind the chunks.  Matches the
                     crossHatch brightness so the "no wall here"
                     symbology reads as strongly as the wall-under-
                     cursor highlight, instead of fading into the
                     strip.
     planInterior  — the floor plan interior fill.  The lightest of
                     the three: a texture, not a highlight. */

const _SCREEN_PATTERNS = Object.create(null);

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
  };
  const [size, color, weight, draw] = defs[kind];
  _SCREEN_PATTERNS[kind] = make(size, color, weight, draw);
  return _SCREEN_PATTERNS[kind];
}

function _screenPat(kind) {
  return ctx.createPattern(_screenPattern(kind), "repeat");
}

/* ==========================================================================
   SHARED DRAWING HELPERS
   ========================================================================== */

function _roundRect(x, y, w, h, r) {
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

function _drawValuePill(cx, cy, text) {
  ctx.save();
  ctx.font = "600 10px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const w = ctx.measureText(text).width + 10;
  const h = 13;
  ctx.fillStyle = PALETTE.paper;
  ctx.fillRect(cx - w / 2, cy - h / 2, w, h);
  ctx.strokeStyle = PALETTE.ink;
  ctx.lineWidth = 0.8;
  ctx.strokeRect(cx - w / 2 + 0.4, cy - h / 2 + 0.4, w - 0.8, h - 0.8);
  ctx.fillStyle = PALETTE.ink;
  ctx.fillText(text, cx, cy + 0.5);
  ctx.restore();
}

/* Value pill with an arbitrary ink colour.  The palette-driven
   _drawValuePill only inks in PALETTE.ink; the highlighted, selected,
   and pending states of a ruler want their own accent. */
function _drawValuePillColored(cx, cy, text, color) {
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

/* ==========================================================================
   SNAP CROSSHAIR
   ==========================================================================
   The small four-armed cross the drawing preview and the ruler's free-
   snap indicator both use.  It is the drafting idiom's mark for "the
   cursor is here and snapping is not active". */

function drawSnapCrosshair(sx, sy) {
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

/* ==========================================================================
   GRID LABEL / PILL
   ==========================================================================
   The ruled pill that terminates a grid line.  In the floor view a
   N–S line gets a number, an E–W line a letter; in the wall view a
   vertical line gets "u<n>" and a horizontal line "v<n>".  The same
   pill is used in both views, so it lives in the shared layer. */

function _gridLabel(g, allGrids) {
  if (g.type === "ns") {
    const sibs = allGrids.filter(x => x.type === "ns")
                        .sort((a, b) => a.pos - b.pos);
    const i = sibs.indexOf(g);
    return String(i + 1);
  }
  if (g.type === "ew") {
    const sibs = allGrids.filter(x => x.type === "ew")
                        .sort((a, b) => a.pos - b.pos);
    const i = sibs.indexOf(g);
    return String.fromCharCode(65 + (i % 26));
  }
  return "";
}

function _drawGridPill(sx, sy, label, active) {
  ctx.save();
  ctx.font = "700 10px " + FONT_MONO;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const textW = ctx.measureText(label).width;
  const pw = textW + 10;
  const ph = 14;
  _roundRect(sx - pw / 2, sy - ph / 2, pw, ph, 3);
  ctx.fillStyle = PALETTE.paper;
  ctx.fill();
  ctx.strokeStyle = active ? PALETTE.ink : PALETTE.inkSoft;
  ctx.lineWidth = 1.2;
  ctx.stroke();
  ctx.fillStyle = active ? PALETTE.ink : PALETTE.inkSoft;
  ctx.fillText(label, sx, sy + 0.5);
  ctx.restore();
}
"""
