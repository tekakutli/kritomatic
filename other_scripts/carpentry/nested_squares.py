"""
nested_squares.py — print sheet of a nested rotating-square cascade.

==============================================================================
WHAT THIS SHEET SHOWS
==============================================================================

Three nested rounded squares, each one governing a circle.

    1. OUTER SQUARE (side L, sharp corners) — shown as "outer jig"
       Rotates about the origin.  Its four corners trace a circle of
       radius R_outer = L / sqrt(2), shown as "rotation track".  A ring
       of tangential arrows around that radius depicts the motion.

    2. MIDDLE SQUARE (side s, corner radius c) — shown as "step 1"
       Rides in the corner of the outer jig, tangent to the two walls
       that meet there.  As the outer jig rotates, step 1's center is
       carried around the origin at radius (L - s) / sqrt(2).  Step 1
       also carries a small disk of radius e, shown as "pen disk".  That
       disk's far edge reaches

           r2 = (L - s) / sqrt(2) + e

       from the origin.  This is the "built jig" — a jig the process
       itself produces.

    3. INNERMOST SQUARE (same s and c as step 1) — shown as "step 2"
       Placed so that one of its far corners just touches the built jig.
       Its own pen disk of radius e reaches

           r1 = rho_inner + e

       where rho_inner is the distance of step 2's center from the
       origin.  r1 is shown as "target sweep"; L is the sheet's headline
       result.  The two named radii on the sheet are r1 and r2.

==============================================================================
FIT TEST — when the cascade is over-constrained
==============================================================================

The built jig is a circle of radius r2.  The outer jig is a square of
side L_side.  For the built jig to physically fit inside the outer jig,
the circle must sit inside the square — which requires

    r2 <= L_side / 2

Since L_side is itself a function of r2 (L_side = sqrt(2)*(r2 - e) + s),
we can substitute and rearrange to get a closed-form upper bound on r2
alone:

    r2 <= (s - sqrt(2)*e) / (2 - sqrt(2))

If r2 exceeds that bound, then r1 was chosen too large: the built jig
has grown past what the outer jig can hold, and the cascade is no longer
a valid construction.  This is the sheet's "warning situation".

The same condition can be re-expressed as an upper bound on r1 — the
largest r1 the cascade can tolerate before the built jig outgrows the
outer jig:

    r1 <= sqrt((r2_limit - c)^2 - a^2) - a + e

The formula strip reports the test on the right-hand side:

    * top-right cell:    the general closed-form test, with a one-line
                         explanation of what it checks
    * bottom-right cell: the test evaluated with the current numbers,
                         showing r2, its limit, r1, and its limit,
                         followed by a PASS / FAIL verdict

==============================================================================
IMPORTANT LAYOUT KNOBS  (legend cell spacing)
==============================================================================

Two knobs control every visible gap inside the legend.  They live in
Legend.__init__ and are entirely independent: changing one never
touches what the other controls.

    self.row_gap     — space BETWEEN cells, vertically.  Increase to
                       loosen the stack of rows (LEGEND ↔ outer jig ↔
                       rotation track ↔ step 1, etc.).  Also affects
                       cell height, and therefore the auto-sized font.

    self.swatch_gap  — space INSIDE a cell, between the swatch badge
                       and the text below it.  Increase to push the
                       symbol further from its label; decrease to weld
                       them together.  Independent of row_gap.

    (self.col_gap    — the horizontal counterpart of row_gap: space
                       BETWEEN the two columns.  Same idea, rotated 90°.)

Worked example — the values this sheet settled on:

    self.row_gap    = 0.02     # was 0.006 — open up the vertical
                               # gaps between cells without touching
                               # the swatch↔text pairing

    self.col_gap    = 0.04     # was 0.025 — open up the horizontal
                               # gap between the two columns

    self.swatch_gap = 0.01     # was 0.004 — the symbol now sits a
                               # small but visible distance above its
                               # label, still reading as a single unit

Guiding question the user asked, and the answer:

    "I want to change the margin size while controlling how far each
     symbol is from its legend."

        → margin size between cells:      row_gap, col_gap
        → distance symbol ↔ its label:    swatch_gap

==============================================================================
LEGEND NAMING
==============================================================================

The legend uses short functional names from the workshop vocabulary of
jigs, tracks, and steps:

    geometric name        | legend name
    ----------------------|--------------------
    outer square          | outer jig
    outer boundary        | rotation track
    middle square         | step 1
    middle circle         | built jig
    innermost square      | step 2
    inner circle          | target sweep
    central disks         | pen disk

Reasoning:

    * A "jig" is a constraint that confines motion.  The outer jig is
      the given frame; the built jig is a constraint the process itself
      produces.

    * A "track" is the path a moving thing is confined to.  The
      rotation track is what the outer jig's corners leave behind as
      the jig turns.

    * A "step" is one moving part in the process.  Step 1 rides the
      outer jig; step 2 rides the built jig.  The two steps share the
      bottom row of the legend grid, side by side, so the process
      order reads at a glance.

    * A "sweep" is the trace left by the moving part, and the "target
      sweep" is the one we are solving for.

    * A "pen disk" is the marking element carried at each step's
      center.  Its radius is e.

The formulas, ruler labels, and drawing annotations keep the geometric
names (r1, r2, R, L, s, c, e) so the sheet still reads as a single
self-consistent derivation.

==============================================================================
THE DERIVATION (why the numbers come out the way they do)
==============================================================================

Let a = s / 2 - c be the distance from a rounded square's center to the
center of any of its corner arcs.  A rounded square centered at distance
rho from the origin, with local +x pointing radially outward, has its two
far corner-arc centers at world distance

    sqrt((rho + a)^2 + a^2)

from the origin.  (The two near corner-arc centers are at
sqrt((rho - a)^2 + a^2).)  So its far corners lie on a circle of radius

    sqrt((rho + a)^2 + a^2) + c.

Given inputs:

    s   — width across flats of the rounded squares
    c   — corner radius of the rounded squares
    r1  — desired radius of the target sweep
    e   — radius of the pen disk carried by each step

the cascade is solved bottom-up:

    a         = s/2 - c
    rho_inner = r1 - e                          (pen disk just reaches r1)
    r2        = sqrt((rho_inner + a)^2 + a^2) + c
                                                (step 2's far corner
                                                 touches the built jig)
    L_side    = sqrt(2) * (r2 - e) + s          (step 1 corner-tucked in
                                                 the outer jig, its pen
                                                 disk reaching r2)
    R_outer   = L_side / sqrt(2)                (circumradius of outer jig)

==============================================================================
SHEET LAYOUT
==============================================================================

    +-----------------------------------------------------+---------------+
    |                                                     |    LEGEND     |
    |            DRAWING (single axes)                    |  ┌──┐  ┌──┐   |
    |                                                     |  │  │  │  │   |
    |     - outer jig (solid, one real pose + ghosts)     |  ││ │  ││ │   |
    |     - step 1    (solid thin, hatched fill)          |  ││ │  ││ │   |
    |     - step 2    (solid thin, dotted fill)           |  ││ │  ││ │   |
    |     - two circles: r2, r1                           |  └──┘  └──┘   |
    |     - pen disk  (hatched "xxx")                     |  ┌──┐  ┌──┐   |
    |     - rotation track arrows on R_outer              |  │  │  │  │   |
    |     - vertical scale ruler with r1 and r2 ticks     |  ││ │  ││ │   |
    |     - one side-dimension on the outer jig           |  ││ │  ││ │   |
    |                                                     |  └──┘  └──┘   |
    |                                                     |  ┌──┐  ┌──┐   |
    |                                                     |  │▓▓│  │▓▓│   |
    |                                                     |  │▓▓│  │▓▓│   |
    |                                                     |  │  │  │  │   |
    +-----------------------------------------------------+  └──┘  └──┘   |
    |  GENERAL CLOSED   |  FIT TEST                       |  ┌──┐  ┌──┐   |
    |  SOLVED           |  EVALUATION                     |  │▓▓│  │▓▓│   |
    +-------------------+---------------------------------+  └──┘  └──┘   |

    Header (title + rule) sits inside the top margin.  A single MARGIN
    constant keeps the sheet's outer whitespace uniform on all four
    sides — the title's top edge, the content box's bottom edge, and the
    content box's left/right edges all sit at the same distance from the
    paper edge.  Everything is monospace and black/white; layers are
    distinguished by line weight and hatch, never by hue.

    The formula strip is a 2x2 grid.  The LEFT column (widest) holds the
    two closed forms — general on top, solved on the bottom.  The RIGHT
    column (narrower) holds the fit test — general on top, evaluated
    with the current numbers on the bottom.  A dashed vertical rule
    separates the columns, and a dashed horizontal rule separates the
    rows.

    The legend is a grid of cells in TWO columns.  Each cell contains a
    swatch centered in a fixed-height slot at the top of the cell,
    followed by rotated text below it (the text reads top-to-bottom).

    Within every cell, the VALUE column (e.g. "L = 20.20 cm") is on the
    LEFT of the cell and the NAME column (e.g. "outer jig") is on the
    RIGHT.  Reading top-to-bottom, the value appears first, then the
    name.

    The legend grid, cell by cell:

                    RIGHT column  |  LEFT column
                ------------------+-----------------
        row 0:  LEGEND            |  built jig
        row 1:  outer jig         |  target sweep
        row 2:  rotation track    |  pen disk
        row 3:  step 1            |  step 2

    Step 1 and step 2 share the bottom row, side by side, so the two
    moving parts of the process read together.

    Swatch shapes match what they represent:

        * step 1 and step 2 use a HATCHED SQUARE — physically square in
          print, and filled with the same hatch as the corresponding
          shape in the drawing;
        * pen disk uses a HATCHED CIRCLE — physically circular in print,
          again matching its shape in the drawing;
        * outer jig uses a VERY THIN VERTICAL LINE — it is a boundary,
          so it gets a hairline stroke;
        * rotation track uses a VERTICAL ARROW, matching the direction
          the tangential arrows move in the drawing;
        * built jig and target sweep use VERTICAL LINES.

    The LEGEND header is sized dynamically so its rotated text fills
    its cell without spilling into the next column.  Its text is
    TOP-ALIGNED within the cell — the same visual anchor as the swatch
    in every other entry — so the header reads as part of the same
    vertical rhythm.

    Body-text auto-sizing
    ---------------------
    The NAME and VALUE font sizes are NOT hardcoded.  They are computed
    in __init__ from the cell geometry, using the longest NAME and
    longest VALUE that will actually appear on the sheet, minus a small
    physical margin on each side so the text never sits flush against
    the cell edges.  Two constraints are considered:

        vertical:   the rotated NAME (or VALUE) must fit inside the
                    cell's height, after the swatch slot, the
                    swatch-to-text gap, and the margin
        horizontal: the NAME's and VALUE's rotated line thicknesses
                    must fit side by side, after the margin

    The tighter of the two wins.  Because the vertical constraint is
    usually the binding one, the vertical reservations — the swatch
    slot, the gap, and the margin — are kept as tight as possible so
    that the text itself can grow.

    The swatch slot is DERIVED from the actual swatch size (the largest
    of the square side and the disk diameter), with only a 5% buffer.
    This means the slot reserves no more room than the swatch needs, and
    the text below it starts higher in the cell.  Because the swatches
    are small (0.36 in), the slot is small too, and the body text has
    more vertical room to grow into.

    The swatch sits above the text, separated by SWATCH_GAP (see the
    IMPORTANT LAYOUT KNOBS section) so they read as a single visual
    unit — badge plus label — with just enough space to feel composed
    rather than fused.

    The hatched square and hatched circle are calibrated in physical
    inches so they truly appear square / circular on paper.  They are
    deliberately modest in size — the sheet's focus is the text, and the
    swatches read as small badges rather than as headline shapes.

    Every swatch is drawn inside an equal-height slot, so all cells in a
    row start their text at the same y — no matter whether the swatch is
    a line, arrow, square, or circle.

==============================================================================
RULER LABELS — VALUE LEFT, NAME RIGHT
==============================================================================

The ruler spine separates two short columns:

    value (large)  |  spine  |  name (small)
    ─────────────  |  ────   |  ──────────
        8.21       |    ·    |  r2
          3        |    ·    |  r1
          0        |    ·    |

Values are right-aligned just left of the spine; names are left-aligned
just right of the spine.  Both sit at the tick's own y.  No stacking,
no shared column that could be misread as a single multi-line number.
The name column lives in the gap between the ruler and the drawing body,
so RULER_GAP must be wide enough to hold the longest name.

The two named radii on the sheet are r1 (target sweep) and r2 (built
jig).  Every other radius — the rotation track — is referred to by its
own distinct name (R_outer), and there is exactly one name per quantity.

All coordinates in the drawing are in centimetres, matching the inputs.
"""

import textwrap
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon, Circle, Rectangle, Ellipse
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D

# ----------------------------------------------------------------------------
# INPUTS
#
# The only four numbers the sheet needs.  Everything else is derived.
# ----------------------------------------------------------------------------
s  = 10.0   # rounded-square width across flats (cm)
c  = 1.0    # rounded-square corner radius (cm)
r1 = 3.0    # radius of the target sweep (cm)
e  = 1.0    # radius of the pen disk carried by each step (cm)

# ----------------------------------------------------------------------------
# DERIVED GEOMETRY
#
# See the module docstring for the full derivation.  All in cm.
# ----------------------------------------------------------------------------
a         = s/2 - c
rho_inner = r1 - e
r2        = np.sqrt((rho_inner + a)**2 + a**2) + c
L_side    = np.sqrt(2) * (r2 - e) + s
R_outer   = L_side / np.sqrt(2)

# ----------------------------------------------------------------------------
# FIT TEST
#
# The built jig (a circle of radius r2) must fit inside the outer jig (a
# square of side L_side).  A circle of radius r fits inside a square of
# side L iff r <= L/2.  Substituting L_side = sqrt(2)*(r2 - e) + s and
# rearranging gives a closed-form upper bound on r2 alone:
#
#     r2 <= (s - sqrt(2)*e) / (2 - sqrt(2))
#
# The same condition can be re-expressed as an upper bound on r1 — the
# largest r1 the cascade can tolerate before the built jig outgrows the
# outer jig.  Inverting the cascade at r2 = r2_fit_limit:
#
#     r1 <= sqrt((r2_limit - c)^2 - a^2) - a + e
#
# If r2 exceeds r2_fit_limit (equivalently, if r1 exceeds r1_fit_limit),
# the cascade is over-constrained and the sheet reports FAIL.
# ----------------------------------------------------------------------------
r2_fit_limit = (s - np.sqrt(2) * e) / (2.0 - np.sqrt(2))

_r1_discriminant = (r2_fit_limit - c)**2 - a**2
if _r1_discriminant >= 0.0:
    r1_fit_limit = float(np.sqrt(_r1_discriminant) - a + e)
else:
    r1_fit_limit = float("nan")

fit_ok     = bool(r2 <= r2_fit_limit)
fit_status = "PASS" if fit_ok else "FAIL"

# ----------------------------------------------------------------------------
# PALETTE
#
# Greyscale only.  INK is the drawing line, INK_SOFT is for helper text,
# INK_FAINT / GHOST are for ghosted poses and construction hints,
# ARROW_TONE is a middle grey so rotation-track arrows stay subordinate
# to the geometry they annotate.  PAPER is the background — never
# colored.
# ----------------------------------------------------------------------------
INK        = "#000000"
INK_SOFT   = "#555555"
INK_FAINT  = "#d0d0d0"
ARROW_TONE = "#909090"
GHOST      = "#bdbdbd"
PAPER      = "#ffffff"
FONT_MONO  = "DejaVu Sans Mono"

# ----------------------------------------------------------------------------
# LINE STYLES
#
# Two signature families:
#
#   Solid lines — the outer jig, the two steps, and the two reference
#   circles.  Weight distinguishes them: the outer jig is a boundary, so
#   it gets a very thin hairline stroke; the two steps are drawn a touch
#   heavier so their hatched interiors read cleanly through the boundary;
#   and the target sweep is heaviest of the three, since it is the sheet's
#   goal.
#
#   Dashed lines — reserved for helper geometry only (the built jig,
#   the faint radial guide, extension lines).  No solid entity shares a
#   dash pattern with a helper.
#
# The "disk" style is for the hatched pen disks.
# ----------------------------------------------------------------------------
STYLE = {
    "outer_sq":  dict(ls="-",                lw=0.5),
    "outer_cir": dict(ls="-",                lw=1.4),
    "mid_sq":    dict(ls="-",                lw=0.7),
    "mid_cir":   dict(ls=(0, (7, 3, 1, 3)),  lw=1.6),
    "inner_sq":  dict(ls="-",                lw=0.7),
    "inner_cir": dict(ls="-",                lw=2.2),
    "disk":      dict(ls="-",                lw=1.2),
}

GHOST_LW_FACTOR = 0.55      # ghost line weights are 55% of the real ones
HATCH_MID       = "\\"      # texture of step 1's interior
HATCH_INNER     = "."       # texture of step 2's interior
HATCH_DISK      = "xxx"     # texture of the pen disks

# ----------------------------------------------------------------------------
# ANGLES
#
# Every square is drawn in one "real" pose and one or more ghost poses.
# Ghosts help the eye read the motion without cluttering the sheet.
# ----------------------------------------------------------------------------
OUTER_ROT         = 20.0    # real outer-jig rotation (degrees)
OUTER_GHOST_ANGS  = (260.0,)
MID_GHOST_ANGS    = (260.0,)
INNER_REAL_ANG    = 200.0   # angle of step 2's center
INNER_GHOST_ANGS  = (140.0,)

# Ring of tangential arrows drawn just outside R_outer to signal the
# rotation track.
ROT_ARROW_COUNT   = 32
ROT_ARROW_LEN     = 1.15
ROT_ARROW_HEAD    = 13      # arrowhead "mutation scale" (pt)

# Vertical scale ruler — the value font is the "large" number on the
# left of the spine, the name font is the small label on the right of
# the spine.  RULER_NAME_RIGHT_OFFSET is how far right of the spine the
# name's left edge sits; the name column lives inside RULER_GAP, so
# RULER_GAP has to be wide enough to hold it.
RULER_FSIZE             = 24.0
RULER_NAME_FSIZE        = RULER_FSIZE * 0.62
RULER_MINOR             = 1.0         # minor tick every 1 cm
RULER_GAP               = 1.6         # gap between ruler and drawing body
RULER_PAD               = None        # computed once BODY_HALF is known
RULER_ANCHOR_OFFSET     = 1.6         # spine -> value right edge (cm)
RULER_NAME_RIGHT_OFFSET = 0.65        # spine -> name left edge (cm)
RULER_CHAR_W_FACTOR     = 0.62        # monospace char width / pt

# Side dimension on the outer jig.
SIDE_DIM_OFFSET = 1.4       # how far the dim line sits outside the jig

# ============================================================================
# LAYOUT  (all fractions of the figure; FIG_W/H in inches)
#
# A single MARGIN constant keeps the sheet's outer whitespace uniform on
# all four sides.  The title's top edge, the content box's bottom edge,
# and the content box's left/right edges all sit exactly MARGIN from the
# paper edge.  The header rule and the top of the content box are
# derived from the header block height plus a small gap below the rule.
# ============================================================================
FIG_W, FIG_H = 10.5, 11.5

MARGIN          = 0.045      # uniform whitespace on every side
HEADER_BLOCK_H  = 0.034      # title text block height
HEADER_TO_BODY  = 0.030      # gap between the header rule and the body

HEADER_L       = MARGIN
HEADER_R       = 1.0 - MARGIN
HEADER_TITLE_Y = 1.0 - MARGIN       # top of title text (va='top')
HEADER_RULE_Y  = HEADER_TITLE_Y - HEADER_BLOCK_H

CONTENT_L = MARGIN
CONTENT_R = 1.0 - MARGIN
CONTENT_T = HEADER_RULE_Y - HEADER_TO_BODY
CONTENT_B = MARGIN

# Column split — left column holds the drawing and the formula strip,
# right column holds the legend.
COL_SPLIT       = 0.800
GUTTER_LEGEND   = 0.020

# Left column: drawing (top), formula strip (bottom).
DRAW_L = CONTENT_L
DRAW_W = COL_SPLIT - DRAW_L - GUTTER_LEGEND

FORM_L = DRAW_L
FORM_W = DRAW_W

FORM_H        = 0.320
FORM_GAP      = 0.014
FORM_CELL_H   = (FORM_H - FORM_GAP) / 2.0

FORM_B        = CONTENT_B
FORM_T        = FORM_B + FORM_H

# Drawing sits above the formula strip.
GAP    = 0.014
DRAW_B = FORM_T + GAP
DRAW_H = CONTENT_T - DRAW_B

# Right column.
LEG_L = COL_SPLIT
LEG_W = CONTENT_R - LEG_L
LEG_B = CONTENT_B
LEG_H = CONTENT_T - CONTENT_B

# ============================================================================
# FORMULA STRINGS  (raw, unwrapped)
#
# The strip is a 2x2 grid.  Left column (widest): the closed forms
# (general on top, solved on the bottom).  Right column (narrower): the
# fit test (general on top, evaluated on the bottom).
#
# Only two named radii appear on the sheet — r1 (target sweep) and r2
# (built jig).  Every other radius has exactly one name.  The formula
# blocks keep the geometric symbols (r1, r2, R, L, s, c, e) so the
# derivation stays self-consistent with the ruler labels.
# ============================================================================
general_raw = (
    "GENERAL CLOSED FORM\n"
    "  a     = s/2 - c\n"
    "  r2    = sqrt((r1 - e + a)^2 + a^2) + c\n"
    "  R     = L / sqrt(2)\n"
    "  L     = sqrt(2) * (r2 - e) + s"
)

solved_raw = (
    f"SOLVED FOR  (r1, s, c, e) = "
    f"({r1:g}, {s:g}, {c:g}, {e:g}) cm\n"
    f"  a     = {s:g}/2 - {c:g}"
    f" = {a:.4f} cm\n"
    f"  r2    = sqrt(({r1:g}-{e:g}+{a:g})^2 + {a:g}^2) + {c:g}"
    f" = {r2:.4f} cm\n"
    f"  R     = {L_side:.4f}/sqrt(2)"
    f" = {R_outer:.4f} cm\n"
    f"  L     = sqrt(2)*({r2:.4f}-{e:g}) + {s:g}"
    f" = {L_side:.4f} cm"
)

# Fit test: general form and evaluated form.  The test block leads with
# a one-line plain-English description of what it checks, then the
# closed-form inequality.  The evaluation block shows both radii against
# their limits (r2 vs. its limit, r1 vs. its limit) and the PASS / FAIL
# verdict.
test_raw = (
    "FIT TEST\n"
    "  built jig must\n"
    "  fit in outer jig\n"
    "  r2 <= (s - √2 e)\n"
    "        / (2 - √2)"
)

eval_raw = (
    "EVALUATION (cm)\n"
    f"  r2    = {r2:.4f}\n"
    f"  r2max = {r2_fit_limit:.4f}\n"
    f"  r1    = {r1:.4f}\n"
    f"  r1max = {r1_fit_limit:.4f}\n"
    f"  status: {fit_status}"
)

# ============================================================================
# FORMULA WRAPPING
#
# The formula strip is a 2x2 grid: the two closed forms on the left,
# and the fit test plus its evaluation on the right.  We measure how
# many monospace characters fit in each column, then wrap each block
# accordingly.  Formula lines align their continuations under the first
# '='; header lines wrap greedily.  A hard cut is the last resort.
# ============================================================================
FORM_FSIZE   = 12.0
FORM_X_PAD   = 0.022
FORM_COL_SPLIT = 0.68       # left column takes 68% of the strip width;
                            # right column takes 32% (narrow, for the
                            # fit test)


def _mono_char_w_in(size_pt):
    """Approximate the width in inches of one monospace character."""
    return size_pt * 0.602 / 72.0


def _col_max_chars(col_frac, size_pt, pad_frac):
    """
    How many monospace characters fit inside a sub-column of the formula
    strip?  `col_frac` is the column's fraction of the strip width, and
    `pad_frac` is the padding on each side, also as a fraction of the
    strip width.
    """
    text_w_in = (col_frac - 2.0 * pad_frac) * FORM_W * FIG_W
    return max(8, int(text_w_in / _mono_char_w_in(size_pt)))


FORM_MAX_CHARS_L = _col_max_chars(FORM_COL_SPLIT, FORM_FSIZE, FORM_X_PAD)
FORM_MAX_CHARS_R = _col_max_chars(1.0 - FORM_COL_SPLIT, FORM_FSIZE, FORM_X_PAD)


def _wrap_formula_line(line, max_chars):
    """
    Wrap a single line of a formula block so it never exceeds `max_chars`.

    Formula lines (begin with two spaces and contain '=') break at
    whitespace and align their continuations under the first '='.
    Header lines wrap greedily.  A line whose first token is longer than
    the budget is hard-cut so nothing is ever cropped.
    """
    if len(line) <= max_chars:
        return [line]

    is_formula = line.startswith("  ") and "=" in line

    if not is_formula:
        # Greedy word wrap, no alignment.
        words = line.split()
        if not words:
            return [line]
        out = []
        cur = words[0]
        for w in words[1:]:
            trial = cur + " " + w
            if len(trial) <= max_chars:
                cur = trial
            else:
                out.append(cur)
                cur = w
        out.append(cur)
        return out

    # Formula line: break at whitespace, align continuations under '='.
    eq = line.find("=")
    align_col = min(eq, max(2, max_chars // 3))

    out = []
    remaining = line
    guard = 0
    while len(remaining) > max_chars and guard < 60:
        cut = remaining.rfind(" ", 0, max_chars + 1)
        if cut <= 0:
            cut = max_chars
        out.append(remaining[:cut].rstrip())
        rest = remaining[cut:].lstrip()
        remaining = " " * align_col + rest
        guard += 1
    out.append(remaining)
    return out


def wrap_formula_block(text, max_chars):
    """Apply `_wrap_formula_line` to every line of a multi-line block."""
    out = []
    for line in text.splitlines():
        out.extend(_wrap_formula_line(line, max_chars))
    return "\n".join(out)


general = wrap_formula_block(general_raw, FORM_MAX_CHARS_L)
solved  = wrap_formula_block(solved_raw,  FORM_MAX_CHARS_L)
test_s  = wrap_formula_block(test_raw,    FORM_MAX_CHARS_R)
eval_s  = wrap_formula_block(eval_raw,    FORM_MAX_CHARS_R)


# ============================================================================
# GEOMETRY
#
# All geometry functions operate in centimetres, on plain numpy arrays of
# (x, y) pairs, with the origin at the center of the whole cascade.
# ============================================================================

def rounded_square(s, c, n=64):
    """
    Return the CCW outline of a rounded square centered at the origin.

    Parameters
    ----------
    s : width across flats (side length as if the corners were sharp)
    c : corner radius, 0 <= c <= s/2
    n : samples per 90-degree corner arc

    The outline is built from four straight edges and four quarter-circle
    arcs, walked counter-clockwise starting at the top edge.  The four
    arc centers sit at (±a, ±a) where a = s/2 - c.
    """
    a_ = s/2 - c
    pts = [(-a_, s/2), (a_, s/2)]
    for t in np.linspace(np.pi/2, 0, n):
        pts.append((a_ + c*np.cos(t), a_ + c*np.sin(t)))
    pts.append((s/2, -a_))
    for t in np.linspace(0, -np.pi/2, n):
        pts.append((a_ + c*np.cos(t), -a_ + c*np.sin(t)))
    pts.append((-a_, -s/2))
    for t in np.linspace(-np.pi/2, -np.pi, n):
        pts.append((-a_ + c*np.cos(t), -a_ + c*np.sin(t)))
    pts.append((-s/2, a_))
    for t in np.linspace(np.pi, np.pi/2, n):
        pts.append((-a_ + c*np.cos(t), a_ + c*np.sin(t)))
    return np.array(pts)


def rot(pts, deg):
    """Rotate an (N, 2) point array by `deg` degrees about the origin."""
    th = np.radians(deg)
    Rm = np.array([[np.cos(th), -np.sin(th)],
                   [np.sin(th),  np.cos(th)]])
    return pts @ Rm.T


def place_corner_tucked_middle(outer_rot, L_side, s, c):
    """
    Place step 1 tucked into one corner of the outer jig.

    Step 1 is tangent to the two walls that meet at the outer jig's
    top-right corner (in the jig's local frame).  Its center sits at
    local coordinates (L/2 - s/2, L/2 - s/2), which rotates with the
    outer jig.

    Returns
    -------
    (pts, center) : polygon outline and center of step 1, both in world
                    coordinates.
    """
    local_center = np.array([L_side/2 - s/2, L_side/2 - s/2])
    theta = np.radians(outer_rot)
    Rm = np.array([[np.cos(theta), -np.sin(theta)],
                   [np.sin(theta),  np.cos(theta)]])
    center = Rm @ local_center
    pts = rot(rounded_square(s, c), outer_rot) + center
    return pts, center


def place_inner(angle_deg):
    """
    Place step 2 so one far corner touches the built jig.

    Step 2's center orbits the origin at radius rho_inner.  At every
    angle, the local +x axis points radially outward, so the two "far"
    corner arcs (local (a, a) and local (a, -a)) land on the built jig
    of radius r2 = sqrt((rho_inner + a)^2 + a^2) + c.

    Returns
    -------
    pts   : world-coordinate polygon outline
    center: world-coordinate center
    touch : list of two world points on r2 — the far-corner touch
            points, useful for marking tangency.
    """
    th = np.radians(angle_deg)
    cx = rho_inner * np.cos(th)
    cy = rho_inner * np.sin(th)
    pts = rot(rounded_square(s, c), angle_deg) + np.array([cx, cy])

    # The two far corner-arc centers, rotated into world space.
    arc_local = np.array([[a, a], [a, -a]])
    arc_world = rot(arc_local, angle_deg) + np.array([cx, cy])

    # Push each corner-arc center outward by c (the arc radius) to get
    # the actual far-corner point on the built jig.
    touch = []
    for ac in arc_world:
        d = np.hypot(ac[0], ac[1])
        tp = ac * (1.0 + c / d)
        touch.append(tp)
    return pts, (cx, cy), touch


# ============================================================================
# DRAW HELPERS
#
# Thin wrappers around matplotlib patches that keep line styles, z-order,
# and ink colour consistent across the sheet.
# ============================================================================

def ghost_square(ax, pts, key, hatch=None):
    """Draw a faint polygon (a 'ghost pose') using STYLE[key] at 55% weight."""
    st = STYLE[key]
    ax.add_patch(Polygon(pts, closed=True,
                         fill=(hatch is not None),
                         fc="none" if hatch else "none",
                         hatch=hatch, ec=GHOST,
                         lw=st["lw"] * GHOST_LW_FACTOR,
                         ls=st["ls"], zorder=1))


def real_square(ax, pts, key, hatch=None):
    """Draw a solid polygon (the highlighted pose) using STYLE[key]."""
    st = STYLE[key]
    ax.add_patch(Polygon(pts, closed=True,
                         fill=(hatch is not None),
                         fc="none" if hatch else "none",
                         hatch=hatch, ec=INK,
                         lw=st["lw"], ls=st["ls"], zorder=6))


def circle(ax, radius, key, zorder=3, tone=INK):
    """Draw a circle centered at the origin, in STYLE[key]."""
    st = STYLE[key]
    ax.add_patch(Circle((0, 0), radius, fill=False,
                        ec=tone, lw=st["lw"], ls=st["ls"], zorder=zorder))


def central_disk(ax, center, radius, ghost=False):
    """
    Draw one of the small hatched pen disks carried at a step's center.
    Ghost pen disks are lighter and thinner; the real one is at full
    weight.
    """
    st = STYLE["disk"]
    tone = GHOST if ghost else INK
    lw = st["lw"] * (GHOST_LW_FACTOR if ghost else 1.0)
    ax.add_patch(Circle(center, radius, fill=True, fc="none",
                        hatch=HATCH_DISK, ec=tone, lw=lw,
                        zorder=2 if ghost else 5))


def tangential_arrow(ax, radius, angle_deg, length,
                     color=ARROW_TONE, lw=1.6, head=ROT_ARROW_HEAD):
    """
    Draw a small arrow tangent to a circle of the given radius, starting
    at the given angle and pointing counter-clockwise.  Used to build the
    ring of rotation-track indicators around R_outer.
    """
    th = np.radians(angle_deg)
    x0 = radius * np.cos(th)
    y0 = radius * np.sin(th)
    tx = -np.sin(th); ty = np.cos(th)     # tangent direction at (x0, y0)
    x1 = x0 + length * tx; y1 = y0 + length * ty
    ax.annotate("",
                xy=(x1, y1), xytext=(x0, y0),
                arrowprops=dict(arrowstyle="->", color=color, lw=lw,
                                mutation_scale=head,
                                shrinkA=0, shrinkB=0),
                zorder=8)


def draw_vertical_scale_ruler(ax, x_pos, radii_labels,
                              name_right_offset=RULER_NAME_RIGHT_OFFSET,
                              minor_step=RULER_MINOR,
                              fsize=RULER_FSIZE,
                              name_fsize=RULER_NAME_FSIZE):
    """
    Draw a vertical ruler at x = x_pos.

    Parameters
    ----------
    x_pos              : data-x of the ruler spine
    radii_labels       : list of (radius, name, value) triples
                         radius — y position of the major tick
                         name   — small label drawn to the RIGHT of the
                                  spine ('' for none)
                         value  — large horizontal number drawn to the
                                  LEFT of the spine, at the tick's y
    name_right_offset  : how far RIGHT of the spine the name's left edge
                         sits.  The name column lives inside the gap
                         between the ruler and the drawing body, so this
                         plus the name's width must be <= RULER_GAP.
    minor_step         : spacing of minor ticks in cm
    fsize              : value font size (points)
    name_fsize         : name font size (points)

    Values are right-aligned at (x_pos - RULER_ANCHOR_OFFSET).  Names are
    left-aligned at (x_pos + name_right_offset).  Both sit at the tick's
    own y — no stacking.
    """
    R = max(r for r, _, _ in radii_labels)

    # Spine of the ruler
    ax.plot([x_pos, x_pos], [0, R], color=INK, lw=1.0, zorder=9)
    # Top and bottom end caps
    for yy in (0.0, R):
        ax.plot([x_pos - 0.55, x_pos + 0.55], [yy, yy],
                color=INK, lw=1.3, zorder=9)

    # Minor ticks (skip positions that coincide with a major tick)
    n_minor = int(R / minor_step)
    major_radii = [r for r, _, _ in radii_labels]
    for k in range(1, n_minor + 1):
        yk = k * minor_step
        if any(abs(yk - r) < 1e-6 for r in major_radii):
            continue
        ax.plot([x_pos - 0.20, x_pos], [yk, yk],
                color=INK, lw=0.6, zorder=9)

    # Major ticks + split labels
    val_anchor_x = x_pos - RULER_ANCHOR_OFFSET

    for r, name, value in radii_labels:
        ax.plot([x_pos - 0.40, x_pos + 0.40], [r, r],
                color=INK, lw=1.5, zorder=9)

        # Value: large, right-aligned, left of the spine, at tick's y
        ax.annotate(value,
                    xy=(x_pos - 0.55, r),
                    xytext=(val_anchor_x, r),
                    ha="right", va="center",
                    fontsize=fsize, family=FONT_MONO,
                    color=INK, zorder=9,
                    annotation_clip=False)

        # Name: small, left-aligned, right of the spine, at tick's y
        if name:
            ax.annotate(name,
                        xy=(x_pos + 0.55, r),
                        xytext=(x_pos + name_right_offset, r),
                        ha="left", va="center",
                        fontsize=name_fsize, family=FONT_MONO,
                        color=INK_SOFT, zorder=9,
                        annotation_clip=False)


def draw_side_dimension(ax, p1, p2, label,
                        offset=1.4, tick=0.55, label_gap=0.55,
                        fsize=RULER_FSIZE):
    """
    Draw a dimension line parallel to segment (p1, p2), offset outward
    from the origin, with perpendicular end ticks and a label rotated
    to match the segment.

    Used to annotate the outer jig's side length.
    """
    p1 = np.asarray(p1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    d = p2 - p1
    L = float(np.hypot(*d))
    if L < 1e-9:
        return
    u = d / L

    # Outward normal: pick the sign so the dim line moves away from
    # the origin.
    n = np.array([-u[1], u[0]])
    mid = 0.5 * (p1 + p2)
    if np.dot(mid, n) < 0:
        n = -n

    q1 = p1 + n * offset
    q2 = p2 + n * offset

    # Extension lines (dashed) from the jig's corners to the dim line.
    for p, q in ((p1, q1), (p2, q2)):
        ax.plot([p[0], q[0]], [p[1], q[1]],
                color=GHOST, lw=0.6, ls=(0, (2, 3)), zorder=8)

    # Dimension line itself.
    ax.plot([q1[0], q2[0]], [q1[1], q2[1]],
            color=INK, lw=1.0, zorder=9)

    # End ticks perpendicular to the dim line.
    for q in (q1, q2):
        ax.plot([q[0] - tick * n[0], q[0] + tick * n[0]],
                [q[1] - tick * n[1], q[1] + tick * n[1]],
                color=INK, lw=1.3, zorder=9)

    # Label, rotated with the segment but flipped to stay readable.
    ang = np.degrees(np.arctan2(u[1], u[0]))
    if ang > 90.0:
        ang -= 180.0
    elif ang < -90.0:
        ang += 180.0

    lm = 0.5 * (q1 + q2) + n * label_gap
    ax.text(lm[0], lm[1], label,
            rotation=ang, rotation_mode="anchor",
            ha="center", va="center",
            fontsize=fsize, family=FONT_MONO,
            color=INK, zorder=10,
            bbox=dict(boxstyle="square,pad=0.18",
                      fc=PAPER, ec="none"))


# ============================================================================
# FIGURE
# ============================================================================
fig = plt.figure(figsize=(FIG_W, FIG_H))
fig.patch.set_facecolor(PAPER)

# ---- header ----------------------------------------------------------------
fig.text(HEADER_L, HEADER_TITLE_Y,
         "NESTED ROTATING SQUARES  —  print sheet",
         fontsize=17, family=FONT_MONO, weight="bold",
         va="top", ha="left", color=INK)

fig.add_artist(Line2D([HEADER_L, HEADER_R],
                      [HEADER_RULE_Y, HEADER_RULE_Y],
                      transform=fig.transFigure,
                      color=INK, lw=1.0))

# ---- drawing axes ----------------------------------------------------------
ax = fig.add_axes([DRAW_L, DRAW_B, DRAW_W, DRAW_H])
ax.set_facecolor(PAPER)
ax.set_aspect('equal', adjustable='datalim')

# ---- legend axes -----------------------------------------------------------
lg = fig.add_axes([LEG_L, LEG_B, LEG_W, LEG_H])
lg.axis("off")
lg.set_xlim(0, 1); lg.set_ylim(0, 1)


# ============================================================================
# DRAWING
# ============================================================================
h = L_side / 2
base = np.array([[-h,-h],[h,-h],[h,h],[-h,h]])

# ---- ghost poses -----------------------------------------------------------
# One ghost of the outer jig, one of step 1 (hatched), and one of step 2
# (hatched).  All three at 55% line weight.  Step 1 and step 2 ghosts
# inherit the thin solid boundary of their real poses.
for a_ in OUTER_GHOST_ANGS:
    ghost_square(ax, rot(base, a_), "outer_sq")

for a_ in MID_GHOST_ANGS:
    pts_g, ctr_g = place_corner_tucked_middle(a_, L_side, s, c)
    ghost_square(ax, pts_g, "mid_sq", hatch=HATCH_MID)
    central_disk(ax, ctr_g, e, ghost=True)

for a_ in INNER_GHOST_ANGS:
    pts_g, ctr_g, _ = place_inner(a_)
    ghost_square(ax, pts_g, "inner_sq", hatch=HATCH_INNER)
    central_disk(ax, ctr_g, e, ghost=True)

# ---- the two reference circles --------------------------------------------
# r2 is the built jig reached by step 1's pen disk; r1 is the target
# sweep traced by step 2's pen disk.
circle(ax, r2, "mid_cir",   zorder=3)
circle(ax, r1, "inner_cir", zorder=4)

# ---- ring of tangential arrows at R_outer ---------------------------------
# Every arrow is tangent to the outer circle, hinting at the rotation
# track left by the outer jig's corners.
for th in np.linspace(0.0, 360.0, ROT_ARROW_COUNT, endpoint=False):
    tangential_arrow(ax, radius=R_outer, angle_deg=th,
                     length=ROT_ARROW_LEN)

# ---- real poses -----------------------------------------------------------
# Outer jig (solid, hairline thin).
sq_pts_outer = rot(base, OUTER_ROT)
real_square(ax, sq_pts_outer, "outer_sq")
draw_side_dimension(ax, sq_pts_outer[1], sq_pts_outer[2],
                    label=f"L = {L_side:.2f} cm",
                    offset=SIDE_DIM_OFFSET)

# Step 1, tucked in the outer jig's corner (solid, thin, hatched).
mid_pts, mid_ctr = place_corner_tucked_middle(OUTER_ROT, L_side, s, c)
real_square(ax, mid_pts, "mid_sq", hatch=HATCH_MID)
central_disk(ax, mid_ctr, e, ghost=False)
ax.plot(mid_ctr[0], mid_ctr[1], marker="o", ms=3.0,
        mfc=PAPER, mec=INK, mew=1.0, zorder=7)

# Step 2, far corner on r2 (solid, thin, hatched).
inner_pts, inner_ctr, inner_touch = place_inner(INNER_REAL_ANG)
real_square(ax, inner_pts, "inner_sq", hatch=HATCH_INNER)
central_disk(ax, inner_ctr, e, ghost=False)
ax.plot(inner_ctr[0], inner_ctr[1], marker="o", ms=3.0,
        mfc=PAPER, mec=INK, mew=1.0, zorder=7)

# Origin marker.
ax.plot(0, 0, marker="+", ms=12, mew=1.2, color=INK, zorder=11)


# ----------------------------------------------------------------------------
# DRAWING BODY EXTENT + RULER POSITION
#
# The ruler sits to the left of the drawing body.  RULER_PAD is computed
# from the longest ruler VALUE (names now live to the RIGHT of the spine,
# so they don't add to the left footprint), the font size, and the axes
# width in inches.  RULER_GAP must be wide enough to hold the name column
# that lives between the spine and the drawing body.
# ----------------------------------------------------------------------------
_cos = abs(np.cos(np.radians(OUTER_ROT)))
_sin = abs(np.sin(np.radians(OUTER_ROT)))
_sq_half   = (L_side / 2.0) * (_cos + _sin)
_ring_half = np.hypot(R_outer, ROT_ARROW_LEN)
DIM_EXTENT = SIDE_DIM_OFFSET + 0.55 + 0.55   # offset + tick + label gap
BODY_HALF  = max(_sq_half, _ring_half) + DIM_EXTENT

RULER_X_POS = -BODY_HALF - RULER_GAP

# ---- Ruler labels as (radius, name, value) triples -------------------------
_ruler_labels = [
    (0.0, "",   "0"),
    (r1,  "r1", f"{r1:g}"),
    (r2,  "r2", f"{r2:.2f}"),
]

# ---- Horizontal fit: only the value contributes on the left ----------------
_val_n     = max(len(v) for _, _, v in _ruler_labels)
_val_w_in  = _val_n * RULER_FSIZE * RULER_CHAR_W_FACTOR / 72.0
_ax_w_in    = DRAW_W * FIG_W
_data_per_in = (2.0 * BODY_HALF + RULER_GAP + 2.0) / _ax_w_in
RULER_PAD = RULER_ANCHOR_OFFSET + _val_w_in * _data_per_in + 0.50

cx_lo = RULER_X_POS - RULER_PAD
cx_hi =  BODY_HALF
cy_lo = -BODY_HALF
cy_hi =  BODY_HALF

A_box  = (DRAW_W * FIG_W) / (DRAW_H * FIG_H)
data_w = cx_hi - cx_lo
data_h = cy_hi - cy_lo

# Expand whichever axis needs it so the data box matches the axes box.
if data_w / data_h > A_box:
    new_h = data_w / A_box
    mid   = 0.5 * (cy_lo + cy_hi)
    cy_lo = mid - new_h / 2
    cy_hi = mid + new_h / 2
else:
    new_w = data_h * A_box
    mid   = 0.5 * (cx_lo + cx_hi)
    cx_lo = mid - new_w / 2
    cx_hi = mid + new_w / 2

ax.set_xlim(cx_lo, cx_hi)
ax.set_ylim(cy_lo, cy_hi)

# Faint radial guide along the +x axis.
ax.plot([RULER_X_POS, BODY_HALF * 0.95], [0, 0],
        color=GHOST, lw=0.6, ls=(0, (2, 3)), zorder=0)

draw_vertical_scale_ruler(
    ax,
    x_pos=RULER_X_POS,
    radii_labels=_ruler_labels,
)

ax.set_xticks([]); ax.set_yticks([])
for sp in ax.spines.values():
    sp.set_visible(True); sp.set_color(INK); sp.set_linewidth(0.9)


# ============================================================================
# FORMULA STRIP  (2x2 grid of cells)
#
# The strip is divided into four cells:
#
#   +----------------------+-------------------+
#   | GENERAL CLOSED FORM  | FIT TEST          |
#   |  ...                 |  ...              |
#   +----------------------+-------------------+
#   | SOLVED FOR ...       | EVALUATION (cm)   |
#   |  ...                 |  ...              |
#   +----------------------+-------------------+
#
# A vertical dashed rule separates the two columns, and a horizontal
# dashed rule separates the two rows.  The two closed forms live in the
# left column; the fit test and its pass/fail evaluation live in the
# narrower right column.
# ============================================================================
ax_form = fig.add_axes([FORM_L, FORM_B, FORM_W, FORM_H])
ax_form.set_facecolor(PAPER)
ax_form.set_xticks([]); ax_form.set_yticks([])
ax_form.set_xlim(0, 1); ax_form.set_ylim(0, 1)
for sp in ax_form.spines.values():
    sp.set_visible(True); sp.set_color(INK); sp.set_linewidth(0.9)

# Split the strip into two rows with a small gap.
form_gap_frac  = FORM_GAP / FORM_H
form_cell_frac = FORM_CELL_H / FORM_H

upper_center = (1.0 + form_cell_frac + form_gap_frac) / 2.0
lower_center = form_cell_frac / 2.0

# Vertical divider between the two columns.
ax_form.plot([FORM_COL_SPLIT, FORM_COL_SPLIT], [0.0, 1.0],
             color=INK_SOFT, lw=0.6, ls=(0, (2, 3)),
             transform=ax_form.transAxes, clip_on=False, zorder=1)

# Horizontal separator between the two rows.
ax_form.plot([0.0, 1.0],
             [form_cell_frac + form_gap_frac / 2.0] * 2,
             color=INK_SOFT, lw=0.6, ls=(0, (2, 3)),
             transform=ax_form.transAxes, clip_on=False, zorder=1)

# ---- Left column: closed forms --------------------------------------------
ax_form.text(FORM_X_PAD, upper_center, general,
             fontsize=FORM_FSIZE, family=FONT_MONO,
             va="center", ha="left", color=INK, linespacing=1.40,
             transform=ax_form.transAxes)
ax_form.text(FORM_X_PAD, lower_center, solved,
             fontsize=FORM_FSIZE, family=FONT_MONO,
             va="center", ha="left", color=INK, linespacing=1.40,
             transform=ax_form.transAxes)

# ---- Right column: fit test + evaluation ----------------------------------
ax_form.text(FORM_COL_SPLIT + FORM_X_PAD, upper_center, test_s,
             fontsize=FORM_FSIZE, family=FONT_MONO,
             va="center", ha="left", color=INK, linespacing=1.40,
             transform=ax_form.transAxes)
ax_form.text(FORM_COL_SPLIT + FORM_X_PAD, lower_center, eval_s,
             fontsize=FORM_FSIZE, family=FONT_MONO,
             va="center", ha="left", color=INK, linespacing=1.40,
             transform=ax_form.transAxes)


# ============================================================================
# LEGEND  (2-column grid of rotated cells)
#
# The legend is laid out as a grid of cells: N_COLS columns, and enough
# rows to hold every entry.  Each cell contains:
#
#     - a swatch centered in a fixed-height slot at the top of the cell;
#     - rotated text below the slot, separated from it by a very small
#       physical gap (SWATCH_GAP_IN) so swatch and text read as a single
#       visual unit;
#     - the text reads top-to-bottom, on the rotated baseline.
#
# Within every cell, the VALUE column (e.g. "L = 20.20 cm") is on the
# LEFT, and the NAME column (e.g. "outer jig") is on the RIGHT.  Reading
# top-to-bottom, the value appears first, then the name.
#
# The grid (see the module docstring for the full picture):
#
#             RIGHT column   |  LEFT column
#         -------------------+------------------
#  row 0:  LEGEND             |  built jig
#  row 1:  outer jig          |  target sweep
#  row 2:  rotation track     |  pen disk
#  row 3:  step 1             |  step 2
#
# Step 1 and step 2 share the bottom row, side by side, so the two
# moving parts of the process read together.
#
# IMPORTANT LAYOUT KNOBS  (see the module docstring for the fuller note):
#
#     row_gap     — space BETWEEN cells, vertically
#     col_gap     — space BETWEEN the two columns, horizontally
#     swatch_gap  — space INSIDE a cell, symbol ↔ its text label
#
# The first two control how loose or tight the grid feels.  The third
# controls how far each symbol sits from its label.  They are fully
# independent: touching one never moves the other.
#
# Body-text auto-sizing
# ---------------------
# The NAME and VALUE font sizes are NOT hardcoded.  They are computed in
# __init__ from the cell geometry, using the longest NAME and longest
# VALUE that will actually appear on the sheet, minus a small physical
# margin on each side so the text never sits flush against the cell
# edges.  Two constraints are considered:
#
#     vertical:   the rotated NAME (or VALUE) must fit inside the
#                 cell's height, after the swatch slot, the
#                 swatch-to-text gap, and the margin
#     horizontal: the NAME's and VALUE's rotated line thicknesses
#                 must fit side by side, after the margin
#
# The tighter of the two wins.  Because the vertical constraint is
# usually the binding one, the vertical reservations — the swatch slot,
# the gap, and the margin — are kept as tight as possible so that the
# text itself can grow.
#
# The swatch slot is DERIVED from the actual swatch size (the largest
# of the square side and the disk diameter), with only a 5% buffer.
# This means the slot reserves no more room than the swatch needs.
#
# Swatch shapes match what they represent:
#     * hatched square  — for step 1 and step 2 (filled squares);
#     * hatched circle  — for the pen disk;
#     * very thin vertical line — for the outer jig (it is a boundary,
#       so it gets a hairline stroke);
#     * vertical arrow  — for the rotation track;
#     * vertical line   — for the built jig and target sweep.
#
# Cells fill DOWN the rightmost column first, then down the leftmost
# column.  Because every cell has a fixed width and height computed
# from the grid, adjacent cells cannot overlap, and the rotated text
# lines within a cell are spaced by the actual font height (not an
# arbitrary constant).
#
# The legend uses short functional names from the workshop vocabulary of
# jigs, tracks, and steps (outer jig, rotation track, step 1, built jig,
# step 2, target sweep, pen disk).  The formulas, ruler labels, and
# drawing annotations keep the geometric symbols (r1, r2, R, L, s, c, e)
# so the sheet still reads as a single self-consistent derivation.
# ============================================================================
class Legend:
    """
    Grid of rotated legend cells inside an axes whose coords are 0..1.

    Parameters
    ----------
    ax              : the axes to draw into
    leg_w_in        : physical width of the axes in inches (used to
                      compute rotated-text thicknesses in axes-X
                      fraction, and to size the circular / square
                      swatches so they appear properly proportioned)
    leg_h_in        : physical height of the axes in inches
    n_cells         : total number of cells in the grid (header counts
                      as one)
    n_cols          : number of columns in the grid
    n_label_max     : length of the longest NAME string that will appear
                      in the legend (in characters) — used to auto-size
                      the NAME font so it fits the cell
    n_sub_max       : length of the longest VALUE string that will appear
                      in the legend (in characters) — used to auto-size
                      the VALUE font
    right           : right edge of the rightmost column, in axes-X
                      fraction
    top             : top edge of the topmost row, in axes-Y fraction
    """
    def __init__(self, ax, leg_w_in, leg_h_in,
                 n_cells, n_cols=2,
                 n_label_max=14, n_sub_max=12,
                 right=0.985, top=0.985):
        self.ax = ax
        self.leg_w_in = leg_w_in
        self.leg_h_in = leg_h_in
        self.right = right
        self.top = top

        self.n_cells = n_cells
        self.n_cols = n_cols
        self.n_rows = (n_cells + n_cols - 1) // n_cols

        # ------------------------------------------------------------------
        # IMPORTANT LAYOUT KNOBS — cell-to-cell spacing
        #
        # pad_x, pad_y     : outer margin between the grid and the axes'
        #                    edges.  Increase to shrink the whole legend
        #                    block inward.
        # col_gap, row_gap : gap BETWEEN cells.  row_gap is the vertical
        #                    gap between stacked cells; col_gap is the
        #                    horizontal gap between the two columns.
        #                    Increase these to loosen the grid without
        #                    touching anything inside a cell.
        #
        # Setting chosen for this sheet: row_gap = 0.02, col_gap = 0.04.
        # They open up the space between cells while leaving the
        # swatch↔label pairing (see swatch_gap below) as tight as the
        # user wanted.
        # ------------------------------------------------------------------
        self.pad_x   = 0.015
        self.pad_y   = 0.012
        self.col_gap = 0.04
        self.row_gap = 0.02

        # Cell dimensions derived from grid geometry
        avail_w = 1.0 - 2*self.pad_x - (n_cols - 1)*self.col_gap
        avail_h = 1.0 - 2*self.pad_y - (self.n_rows - 1)*self.row_gap
        self.cell_w = avail_w / n_cols
        self.cell_h = avail_h / self.n_rows

        # ------------------------------------------------------------------
        # Physical swatch sizes, in inches.
        #
        # These are calibrated so the shapes appear square / circular on
        # paper despite the anisotropic axes stretch.  They are kept
        # SMALL — 0.36 in on a side / diameter — so the swatches read
        # as modest badges rather than headline shapes, and so the
        # vertical slot they reserve leaves as much room as possible
        # for the body text below them.
        # ------------------------------------------------------------------
        self.square_side_in = 0.36     # hatched square swatch (step 1, step 2)
        self.disk_diam_in   = 0.36     # hatched circle swatch (pen disk)

        # ------------------------------------------------------------------
        # Swatch slot and gap.
        #
        # The slot is DERIVED from the actual swatch size — the largest
        # of the square side and the disk diameter — plus a 5% buffer so
        # the swatch never touches the top of the slot.
        #
        # IMPORTANT LAYOUT KNOB — inside the cell:
        #
        #   swatch_gap — the gap between the swatch badge and its text
        #                label.  This is the knob that controls "how
        #                far is each symbol from its legend text".
        #                Small values keep them welded together; larger
        #                values push the label down from the badge.
        #
        # Setting chosen for this sheet: swatch_gap = 0.01 (about
        # 0.10 in).  That reads as a single visual unit — badge on top,
        # label just below it — while still giving a visible breath
        # between the two.
        # ------------------------------------------------------------------
        _swatch_size_in     = max(self.square_side_in, self.disk_diam_in)
        self.swatch_slot_h  = (_swatch_size_in * 1.05) / self.leg_h_in
        self.swatch_gap     = 0.01     # axes Y — swatch ↔ label distance

        # Small inset used by the header so its text doesn't sit flush
        # against the top edge of the cell.
        self.header_top_inset = 0.005

        # ------------------------------------------------------------------
        # HEADER ("LEGEND") font size
        #
        # Computed so the rotated header text nearly fills its cell
        # without spilling into the next column.  Rotated text length ≈
        # n_chars * 0.60 * size / 72 inches; thickness ≈ size * 1.30 /
        # 72 inches.  We clamp to whichever dimension binds first.
        #
        #   SPAN_FILL  — fraction of cell height the header text may
        #                occupy (drives how long the header reads)
        #   THICK_FILL — fraction of cell width the header's
        #                perpendicular thickness may occupy
        # ------------------------------------------------------------------
        _hdr_n_chars    = len("LEGEND")
        _cell_h_in      = self.cell_h * self.leg_h_in
        _cell_w_in      = self.cell_w * self.leg_w_in
        _SPAN_FILL      = 0.75
        _THICK_FILL     = 0.70
        _size_by_span   = (_cell_h_in * _SPAN_FILL) * 72 / (_hdr_n_chars * 0.60)
        _size_by_thick  = (_cell_w_in * _THICK_FILL) * 72 / 1.30
        self.size_header = min(_size_by_span, _size_by_thick)

        # ------------------------------------------------------------------
        # BODY ("NAME" and "VALUE") font sizes
        #
        # Both are computed from the cell's physical dimensions, using
        # the longest NAME and VALUE strings that will actually appear
        # on the sheet.  The margin is applied to BOTH ends of each
        # dimension, so a change to cell_margin_in has twice the visible
        # effect it had when it was applied once.
        #
        # Two constraints bind:
        #
        #   vertical:   the rotated NAME (or VALUE) must fit inside the
        #               cell's height, after the swatch slot, the
        #               swatch-to-text gap, and the margin
        #   horizontal: the NAME's and VALUE's rotated line thicknesses
        #               must fit side by side, after the margin
        #
        # We solve for the NAME size, keep a fixed ratio between NAME
        # and VALUE, and take the tighter of the two constraints.
        # ------------------------------------------------------------------
        self.LABEL_VALUE_RATIO = 0.84     # size_s / size_l
        self.cell_margin_in    = 0.035    # physical margin per side
        self._n_label_max      = n_label_max
        self._n_sub_max        = n_sub_max

        # Space available inside a cell, in physical inches.  Margin is
        # applied twice — once per end — in both directions.
        avail_h_in = (self.cell_h * self.leg_h_in
                      - self.swatch_slot_h * self.leg_h_in
                      - self.swatch_gap    * self.leg_h_in
                      - 2.0 * self.cell_margin_in)
        avail_w_in = (self.cell_w * self.leg_w_in
                      - 2.0 * self.cell_margin_in)

        # Vertical constraint: NAME fits, VALUE fits.
        size_by_h_label = avail_h_in * 72.0 / (n_label_max * 0.60)
        size_by_h_sub   = (avail_h_in * 72.0
                           / (n_sub_max * 0.60 * self.LABEL_VALUE_RATIO))
        size_by_h = min(size_by_h_label, size_by_h_sub)

        # Horizontal constraint: NAME thickness + VALUE thickness fit
        # side by side.  Thickness ≈ size * 1.30 / 72 inches.
        size_by_w = (avail_w_in * 72.0
                     / (1.30 * (1.0 + self.LABEL_VALUE_RATIO)))

        self.size_l = min(size_by_h, size_by_w)
        self.size_s = self.size_l * self.LABEL_VALUE_RATIO

        # Line thicknesses in axes-X fraction, used by _row() to place
        # the two rotated text columns side by side.
        self.line_h_l = (self.size_l * 1.30 / 72.0) / self.leg_w_in
        self.line_h_s = (self.size_s * 1.30 / 72.0) / self.leg_w_in

        # Record which constraint bound, for the console summary.
        self.bound_by = ("vertical" if size_by_h <= size_by_w
                         else "horizontal")

        self.idx = 0

    def _next_cell(self):
        """
        Return (x_left, x_right, y_top, y_bot) for the next cell in the
        grid.  Cells fill DOWN the rightmost column first, then down
        the next column to its left, etc.
        """
        i = self.idx
        self.idx += 1
        col = i // self.n_rows
        row = i % self.n_rows

        # Column 0 is the rightmost column.
        x_right = self.right - self.pad_x - col*(self.cell_w + self.col_gap)
        x_left  = x_right - self.cell_w

        y_top = self.top - self.pad_y - row*(self.cell_h + self.row_gap)
        y_bot = y_top - self.cell_h
        return x_left, x_right, y_top, y_bot

    def _txt_rot(self, x, y, txt, size, color=INK, weight="normal"):
        """
        Write one line of monospace text rotated -90 degrees (so it
        reads top-to-bottom).  The anchor is at (x, y); with ha='left'
        and va='center', the text starts at the anchor and extends
        downward, with the text's horizontal centerline at x.
        """
        self.ax.text(x, y, txt, transform=self.ax.transAxes,
                     rotation=-90, rotation_mode='anchor',
                     ha='left', va='center',
                     fontsize=size, family=FONT_MONO,
                     color=color, weight=weight)

    def _wrap(self, txt, size):
        """
        Wrap `txt` so it fits inside a single cell's height.  Rotated
        text's length is bounded by the cell's vertical extent, not its
        horizontal extent.  Currently unused — kept for reference.
        """
        if not txt:
            return []
        char_w = size * 0.60 / 72.0
        cell_h_in = self.cell_h * self.leg_h_in
        max_chars = max(6, int(cell_h_in / char_w))
        return textwrap.wrap(txt, width=max_chars)

    def _row(self, draw_swatch, label, sub, sl=None, ss=None):
        """
        Emit one legend cell.

        Layout within the cell:
          - a swatch is drawn by the `draw_swatch(x_center, y_top,
            y_slot_bot)` callback, inside a fixed-height slot at the
            top of the cell;
          - rotated NAME and VALUE text starts just below the slot,
            separated from it only by swatch_gap (see __init__).

        "label" is the NAME (e.g. "outer jig").  "sub" is the VALUE
        (e.g. "L = 20.20 cm").  Reading top-to-bottom, the value
        column appears first, then the name column.

        The NAME and VALUE font sizes default to the auto-computed
        self.size_l and self.size_s.
        """
        sl = sl if sl is not None else self.size_l
        ss = ss if ss is not None else self.size_s

        x_left, x_right, y_top, y_bot = self._next_cell()
        x_center = (x_left + x_right) / 2.0

        y_slot_bot = y_top - self.swatch_slot_h
        draw_swatch(x_center, y_top, y_slot_bot)

        # --- text: rotated -90, top-aligned just below the slot -------
        #
        # Swatch and text form a single visual unit: the swatch_gap
        # (see __init__) separates the slot's bottom edge from the top
        # of the text block, and any slack the auto-sizing left unused
        # collects at the BOTTOM of the cell.
        text_y_top = y_slot_bot - self.swatch_gap

        # VALUE (sub) sits in the LEFT column; NAME (label) sits in the
        # RIGHT column.  The two rotated text columns are centred as a
        # pair within the cell.
        text_total_w = self.line_h_l + self.line_h_s
        x_sub = x_center - text_total_w / 2.0 + self.line_h_s / 2.0
        x_lab = x_center + text_total_w / 2.0 - self.line_h_l / 2.0

        if sub:
            self._txt_rot(x_sub, text_y_top, sub, ss, INK_SOFT)
        self._txt_rot(x_lab, text_y_top, label, sl, INK)

    def header(self, txt):
        """
        Section header, drawn as bold rotated text in its own cell.

        The font size is computed in __init__ so the rotated header
        text fills roughly three quarters of the cell's height without
        spilling into the neighbouring column.

        The text is TOP-ALIGNED in the cell: its reading-start edge sits
        near the cell's top (with a small inset), matching the anchor
        used by the swatches of every other entry.  This keeps the
        header in the same vertical rhythm as the rest of the grid.
        """
        x_left, x_right, y_top, y_bot = self._next_cell()
        x_center = (x_left + x_right) / 2.0

        # Text starts just inside the top edge of the cell.
        text_y = y_top - self.header_top_inset

        self._txt_rot(x_center, text_y, txt,
                      self.size_header, weight="bold")

    def line_entry(self, key, label, sub=None, ghost=False, hatch=None):
        """
        Legend cell.

        If `hatch` is None: the swatch is a vertical line in
        STYLE[key], spanning the full slot.  Used for boundaries
        (outer jig, built jig, target sweep).

        If `hatch` is given: the swatch is a HATCHED SQUARE, centered
        in the slot and physically square in print.  The square's
        outline uses STYLE[key] and its interior is filled with the
        hatch pattern.  Used for step 1 and step 2, which are squares
        in the drawing.
        """
        def swatch(x_center, y_top, y_slot_bot):
            st = STYLE[key]
            lw = st["lw"] * (GHOST_LW_FACTOR if ghost else 1.0)
            tone = GHOST if ghost else INK
            if hatch:
                # Physically square hatched swatch, centered in the slot.
                side_in = self.square_side_in
                w_ax = side_in / self.leg_w_in
                h_ax = side_in / self.leg_h_in
                y_center = (y_top + y_slot_bot) / 2.0
                x0 = x_center - w_ax / 2.0
                y0 = y_center - h_ax / 2.0
                self.ax.add_patch(Rectangle(
                    (x0, y0), w_ax, h_ax,
                    transform=self.ax.transAxes,
                    fill=True, fc="none", hatch=hatch, ec=tone,
                    lw=lw, ls=st["ls"]))
            else:
                # Vertical line spanning the slot.
                self.ax.add_line(Line2D(
                    [x_center, x_center], [y_slot_bot, y_top],
                    transform=self.ax.transAxes,
                    color=tone, lw=lw, ls=st["ls"]))
        self._row(swatch, label, sub)

    def arrow_entry(self, label, sub=None):
        """
        Legend cell whose swatch is a short VERTICAL arrow pointing
        downward — the same direction the rotated text reads, and the
        same direction the tangential rotation-track arrows move in
        the drawing.
        """
        def swatch(x_center, y_top, y_slot_bot):
            self.ax.annotate("",
                             xy=(x_center, y_slot_bot),
                             xytext=(x_center, y_top),
                             xycoords=self.ax.transAxes,
                             textcoords=self.ax.transAxes,
                             arrowprops=dict(arrowstyle="->",
                                             color=ARROW_TONE,
                                             lw=1.6,
                                             mutation_scale=ROT_ARROW_HEAD,
                                             shrinkA=0, shrinkB=0))
        self._row(swatch, label, sub)

    def hatch_entry(self, hatch, label, sub=None,
                    ghost=False, round_shape=False):
        """
        Legend cell whose swatch is a hatched patch centered in the
        slot.  `round_shape=True` draws an ellipse whose axes are
        calibrated so it appears circular in physical inches — this is
        how the pen-disk entry shows a true circle even though the
        legend axes is anisotropically stretched.
        """
        def swatch(x_center, y_top, y_slot_bot):
            tone = GHOST if ghost else INK
            lw = 0.6 if ghost else 0.9
            if round_shape:
                # Physically circular hatched swatch, centered in slot.
                diameter_in = self.disk_diam_in
                w_ax = diameter_in / self.leg_w_in
                h_ax = diameter_in / self.leg_h_in
                y_center = (y_top + y_slot_bot) / 2.0
                self.ax.add_patch(Ellipse(
                    (x_center, y_center), w_ax, h_ax,
                    transform=self.ax.transAxes,
                    fill=True, fc="none", hatch=hatch, ec=tone, lw=lw))
            else:
                # Physically square hatched swatch, centered in slot.
                side_in = self.square_side_in
                w_ax = side_in / self.leg_w_in
                h_ax = side_in / self.leg_h_in
                y_center = (y_top + y_slot_bot) / 2.0
                self.ax.add_patch(Rectangle(
                    (x_center - w_ax / 2.0, y_center - h_ax / 2.0),
                    w_ax, h_ax,
                    transform=self.ax.transAxes,
                    fill=True, fc="none", hatch=hatch, ec=tone, lw=lw))
        self._row(swatch, label, sub)


# ---- legend instantiation --------------------------------------------------
# 8 cells total (header + 7 entries), arranged as a 2-column × 4-row grid.
# Cells fill DOWN the right column first (header at top-right, then down
# to the bottom), then down the left column.
#
# The grid:
#
#             RIGHT column   |  LEFT column
#         -------------------+------------------
#  row 0:  LEGEND             |  built jig
#  row 1:  outer jig          |  target sweep
#  row 2:  rotation track     |  pen disk
#  row 3:  step 1             |  step 2
#
# Step 1 and step 2 share the bottom row: step 1 in the right column,
# step 2 in the left column, side by side.
#
# The exact NAME and VALUE strings are gathered FIRST, so the Legend
# constructor can auto-size the body text to whatever the longest of
# each actually is.  If any of these strings change — a longer label,
# a larger number with more digits, a different unit — the auto-sizing
# follows automatically.
# ----------------------------------------------------------------------------
_legend_labels = [
    "outer jig",
    "rotation track",
    "step 1",
    "built jig",
    "target sweep",
    "pen disk",
    "step 2",
]
_legend_subs = [
    f"L = {L_side:.2f} cm",
    f"R = {R_outer:.2f} cm",
    f"s={s:g}, c={c:g} cm",
    f"r2 = {r2:.2f} cm",
    f"r1 = {r1:g} cm",
    f"e = {e:g} cm",
    f"s={s:g}, c={c:g} cm",
]
_n_label_max = max(len(s) for s in _legend_labels)
_n_sub_max   = max(len(s) for s in _legend_subs)

_leg_w_in = LEG_W * FIG_W
_leg_h_in = LEG_H * FIG_H

leg = Legend(lg, leg_w_in=_leg_w_in, leg_h_in=_leg_h_in,
             n_cells=8, n_cols=2,
             n_label_max=_n_label_max, n_sub_max=_n_sub_max)

leg.header("LEGEND")
leg.line_entry("outer_sq",  "outer jig",      f"L = {L_side:.2f} cm")
leg.arrow_entry("rotation track",  f"R = {R_outer:.2f} cm")
leg.line_entry("mid_sq",    "step 1",         f"s={s:g}, c={c:g} cm",
               hatch=HATCH_MID)
leg.line_entry("mid_cir",   "built jig",      f"r2 = {r2:.2f} cm")
leg.line_entry("inner_cir", "target sweep",   f"r1 = {r1:g} cm")
leg.hatch_entry(HATCH_DISK, "pen disk",
                sub=f"e = {e:g} cm",
                round_shape=True)
leg.line_entry("inner_sq",  "step 2",         f"s={s:g}, c={c:g} cm",
               hatch=HATCH_INNER)


# ============================================================================
# OUTPUT
# ============================================================================
with PdfPages("nested_squares.pdf") as pdf:
    pdf.savefig(fig, facecolor=PAPER)
plt.close(fig)

# ---- console summary -------------------------------------------------------
print(f"a         = {a:.4f} cm")
print(f"e         = {e:.4f} cm   (both pen disks)")
print(f"rho_inner = {rho_inner:.4f} cm   (r1 - e)")
print(f"r2        = {r2:.4f} cm")
print(f"L_side    = {L_side:.4f} cm")
print(f"R_outer   = {R_outer:.4f} cm")
print()
print("--- fit test ---")
print(f"r2_fit_limit  = {r2_fit_limit:.4f} cm")
print(f"r1_fit_limit  = {r1_fit_limit:.4f} cm")
print(f"r2            = {r2:.4f} cm")
print(f"r1            = {r1:.4f} cm")
print(f"fit_ok        = {fit_ok}")
print(f"fit_status    = {fit_status}")
print()
print("--- layout margins ---")
print(f"MARGIN          = {MARGIN:.4f}")
print(f"HEADER_L        = {HEADER_L:.4f}")
print(f"HEADER_R        = {HEADER_R:.4f}")
print(f"HEADER_TITLE_Y  = {HEADER_TITLE_Y:.4f}")
print(f"HEADER_RULE_Y   = {HEADER_RULE_Y:.4f}")
print(f"CONTENT_L       = {CONTENT_L:.4f}")
print(f"CONTENT_R       = {CONTENT_R:.4f}")
print(f"CONTENT_T       = {CONTENT_T:.4f}")
print(f"CONTENT_B       = {CONTENT_B:.4f}")
print(f"left  inset     = {CONTENT_L:.4f}")
print(f"right inset     = {1.0 - CONTENT_R:.4f}")
print(f"top   inset     = {1.0 - HEADER_TITLE_Y:.4f}")
print(f"bot   inset     = {CONTENT_B:.4f}")
print()
print("--- column split ---")
print(f"COL_SPLIT       = {COL_SPLIT:.4f}")
print(f"LEG_W (fig)     = {LEG_W:.4f}")
print(f"LEG_W (in)      = {_leg_w_in:.3f}")
print(f"LEG_H (in)      = {_leg_h_in:.3f}")
print(f"DRAW_W (fig)    = {DRAW_W:.4f}")
print()
print("--- line weights ---")
for k, v in STYLE.items():
    print(f"  {k:<10}  lw = {v['lw']:.2f}")
print()
print("--- legend grid (IMPORTANT LAYOUT KNOBS) ---")
print(f"n_cells         = {leg.n_cells}")
print(f"n_cols          = {leg.n_cols}")
print(f"n_rows          = {leg.n_rows}")
print(f"pad_x           = {leg.pad_x:.4f}  (outer margin, axes X)")
print(f"pad_y           = {leg.pad_y:.4f}  (outer margin, axes Y)")
print(f"col_gap         = {leg.col_gap:.4f}  (space BETWEEN columns)")
print(f"row_gap         = {leg.row_gap:.4f}  (space BETWEEN rows)")
print(f"swatch_gap      = {leg.swatch_gap:.4f}  (space INSIDE cell,"
      f" swatch ↔ label)")
print(f"  swatch_gap in inches  = {leg.swatch_gap * _leg_h_in:.3f} in")
print(f"  row_gap    in inches  = {leg.row_gap * _leg_h_in:.3f} in")
print(f"  col_gap    in inches  = {leg.col_gap * _leg_w_in:.3f} in")
print(f"cell_w (ax X)   = {leg.cell_w:.4f}")
print(f"cell_h (ax Y)   = {leg.cell_h:.4f}")
print(f"  cell_w (in)   = {leg.cell_w * _leg_w_in:.3f}")
print(f"  cell_h (in)   = {leg.cell_h * _leg_h_in:.3f}")
print(f"swatch_slot_h   = {leg.swatch_slot_h:.4f}  (auto: swatch size × 1.05)")
print(f"  in inches     = {leg.swatch_slot_h * _leg_h_in:.3f} in")
print(f"square_side_in  = {leg.square_side_in:.3f} in  (hatched square swatch)")
print(f"disk_diam_in    = {leg.disk_diam_in:.3f} in  (hatched circle swatch)")
print()
print("--- legend fonts ---")
print(f"n_label_max       = {_n_label_max} chars  (longest NAME)")
print(f"n_sub_max         = {_n_sub_max} chars  (longest VALUE)")
print(f"LABEL_VALUE_RATIO = {leg.LABEL_VALUE_RATIO:.2f}")
print(f"cell_margin_in    = {leg.cell_margin_in:.3f} in  (per side, ×2)")
print(f"bound_by          = {leg.bound_by}")
print(f"size_header       = {leg.size_header:.2f} pt  (LEGEND header)")
print(f"  header span     = {leg.size_header * 0.60 * len('LEGEND') / 72:.3f} in")
print(f"  cell h (in)     = {leg.cell_h * _leg_h_in:.3f} in")
print(f"  fill fraction   = {leg.size_header * 0.60 * len('LEGEND') / 72 / (leg.cell_h * _leg_h_in):.2f}")
print(f"size_l (NAME)     = {leg.size_l:.2f} pt  (auto-sized)")
print(f"  span            = {leg.size_l * 0.60 * _n_label_max / 72:.3f} in")
print(f"size_s (VALUE)    = {leg.size_s:.2f} pt  (auto-sized)")
print(f"  span            = {leg.size_s * 0.60 * _n_sub_max / 72:.3f} in")
print(f"line_h_l (ax X)   = {leg.line_h_l:.4f}  (NAME column thickness)")
print(f"line_h_s (ax X)   = {leg.line_h_s:.4f}  (VALUE column thickness)")
print(f"2 text lines      = {leg.line_h_l + leg.line_h_s:.4f}  (must be < cell_w)")
print(f"  cell_w (ax X)   = {leg.cell_w:.4f}")
print(f"  fit margin      = {leg.cell_w - (leg.line_h_l + leg.line_h_s):.4f} axX")
print()
print("--- legend cell map (right column fills first) ---")
_cell_map = [
    "LEGEND", "outer jig", "rotation track", "step 1",
    "built jig", "target sweep", "pen disk", "step 2",
]
for i, name in enumerate(_cell_map):
    col = i // leg.n_rows
    row = i % leg.n_rows
    col_name = "RIGHT" if col == 0 else "LEFT "
    print(f"  idx {i}: col={col_name} row={row}  {name}")
print()
print("--- geometry alignment ---")
print(f"DRAW  rect: x=[{DRAW_L:.4f}, {DRAW_L + DRAW_W:.4f}]  w={DRAW_W:.4f}")
print(f"FORM  rect: x=[{FORM_L:.4f}, {FORM_L + FORM_W:.4f}]  w={FORM_W:.4f}")
print(f"aligned? {abs((DRAW_L + DRAW_W) - (FORM_L + FORM_W)) < 1e-12}")
print()
print(f"FORM_COL_SPLIT  = {FORM_COL_SPLIT:.4f}")
print(f"FORM_CELL_H     = {FORM_CELL_H:.4f}")
print(f"FORM_MAX_CHARS_L = {FORM_MAX_CHARS_L}")
print(f"FORM_MAX_CHARS_R = {FORM_MAX_CHARS_R}")
print()
print("--- general (wrapped, left) ---")
print(general)
print("--- solved (wrapped, left) ---")
print(solved)
print("--- test (wrapped, right) ---")
print(test_s)
print("--- eval (wrapped, right) ---")
print(eval_s)
print()
print(f"BODY_HALF                 = {BODY_HALF:.4f}")
print(f"RULER_GAP                 = {RULER_GAP:.4f}")
print(f"RULER_PAD                 = {RULER_PAD:.4f}")
print(f"RULER_X_POS               = {RULER_X_POS:.4f}")
print(f"RULER_NAME_RIGHT_OFFSET   = {RULER_NAME_RIGHT_OFFSET:.4f} cm")
print(f"xlim = ({cx_lo:.3f}, {cx_hi:.3f})")
print(f"ylim = ({cy_lo:.3f}, {cy_hi:.3f})")
print("Wrote nested_squares.pdf")
