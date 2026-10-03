"""
bx_base.py — palette, sheet constants, textures, panel-fade state.

The lowest layer of the boxes playground's JS split.  Nothing here
depends on any other live module; everything else depends on this one.

Palette semantics (dark theme, borrowed verbatim from cable_playground's
pg_base.py):

    ink        structural — walls, dimensions, sheet frame
    paper      the canvas; the knockout halo under every stroke
    step       magenta — step risers, and here also the "invalid ghost"
    transit    cyan — the primary interactive accent (valid boxes)
    highlight  yellow — cursor, snap, selection

Two boxes-specific accents are new:

    valid box         transit (cyan) — matches the cable project's
                      cross-view slot: an element that "belongs" here
                      and is well-behaved
    overlapping box   step (magenta) — a caution, not an error
    invalid box       inkSoft (grey dashed) — disabled
    selected box      highlight (yellow) — the active element

Step-hatch and wall-hatch patterns are cached pattern canvases,
created on first use and reused for the lifetime of the page.  The
same pattern cache idiom the cable project uses.

Handle geometry
---------------
Four interaction constants set how the selected box's handles behave.
The first two are hit radii, unchanged from earlier revisions; the
last two place the floating rotation handle:

    CORNER_HIT, EDGE_HIT    half-width, in screen px, of the hit box
                            around a corner or edge handle
    ROTATE_HANDLE_OFFSET_PX how far above the box's top edge (in
                            SCREEN px) the rotation handle's centre
                            sits
    ROTATE_HANDLE_R         the rotation handle's drawn radius, in
                            screen px

The rotation offset is deliberately in SCREEN pixels, not world mm —
the handle must sit the same distance from the box at every zoom
level.  rotateHandleWorldPos converts it to mm at the current scale.
"""


BASE_JS = r"""
/* ==========================================================================
   PALETTE
   ========================================================================== */

const PALETTE = {
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
   SHEET CONSTANTS
   ========================================================================== */

const SHEET_MARGIN     = 6;
const RIBBON_BAND_H    = 22;
const FLOOR_PAD        = 60;
const MARGIN_TICK_LEN  = 5;
const REG_CROSS_R      = 9;

/* Zoom bounds.  There is no fixed minimum scale: the effective
   minimum is a fraction of the fit scale, which fitView() sets from
   the room's bounding box and the current viewport.  A fixed minimum
   (the earlier design used MIN_ZOOM = 0.20 px/mm) is wrong because
   the fit scale for a large room in a small window can be well below
   that: the first wheel-down event would jump the view IN to the
   fixed floor, and every subsequent wheel-down would be clamped
   there.  Making the floor relative to the fit means zoom-out always
   has slack to spare, however the room and viewport are sized. */
const MIN_ZOOM_FACTOR  = 0.20;   // zoom out to 1/5 of the fit scale
const MAX_ZOOM         = 8.0;

const CORNER_HIT       = 11;
const EDGE_HIT         = 10;
const DRAG_CANCEL      = 6;
const MIN_BOX_MM       = 10;

/* Rotation-handle geometry.  See the module docstring for why the
   offset is in screen pixels rather than world millimetres. */
const ROTATE_HANDLE_OFFSET_PX = 32;
const ROTATE_HANDLE_R         = 9;

/* ==========================================================================
   PANEL-FADE STATE
   ========================================================================== */

let panelHovered      = false;
let panelLastActivity = 0;

/* ==========================================================================
   SCREEN TEXTURES
   ==========================================================================
   Two cached pattern canvases:

     stepHatch   the light-blue step footprint.  On the light theme this
                 was a solid tint; on dark, a sparse diagonal hatch at
                 medium alpha reads as "surface, not wall" without
                 competing with the boxes.
     wallFill    a very faint hatch behind the walls, so the wall fill
                 reads as a material rather than a solid blob.  At this
                 alpha it is nearly invisible and mostly serves to keep
                 the wall's silhouette from looking like a printed bar.

   The pattern canvases are transparent — the caller's fillRect or
   path.fill() picks up whatever is beneath.  This differs from the
   cable project's patterns, which carried their own paper background;
   here the walls sit on top of a paper canvas and we do not want to
   repaint the paper behind every hatch tile. */

const _SCREEN_PATTERNS = Object.create(null);

function _screenPattern(kind) {
  if (_SCREEN_PATTERNS[kind]) return _SCREEN_PATTERNS[kind];
  const make = (size, draw) => {
    const pc = document.createElement("canvas");
    pc.width = pc.height = size;
    const p = pc.getContext("2d");
    p.lineCap = "round";
    draw(p, size);
    return pc;
  };
  const defs = {
    stepHatch: [12, (p, s) => {
      p.strokeStyle = "rgba(220, 230, 242, 0.16)";
      p.lineWidth = 0.9;
      p.beginPath();
      p.moveTo(-1, s + 1); p.lineTo(s + 1, -1);
      p.stroke();
    }],
    wallFill: [14, (p, s) => {
      p.strokeStyle = "rgba(220, 230, 242, 0.06)";
      p.lineWidth = 0.6;
      p.beginPath();
      p.moveTo(s + 1, -1); p.lineTo(-1, s + 1);
      p.stroke();
    }],
  };
  const [size, draw] = defs[kind];
  _SCREEN_PATTERNS[kind] = make(size, draw);
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

function _drawValuePill(cx, cy, text, accent) {
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
"""
