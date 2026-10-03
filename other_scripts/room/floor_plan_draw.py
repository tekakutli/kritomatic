"""
floor_plan_draw.py — SVG string generation for the floor plan.

Reads the layout dict from floor_plan_layout and renders it.  No
routing or ruler resolution happens here — floor_plan_layout owns
the label positions, the leader geometries, and the ruler segments
because those are mutually constrained and the resolution has to
happen before drawing.

Coordinate handling: source space (from room.py) has +Y pointing
north, i.e. up on a printed plan.  SVG has +Y pointing down.  Every
source point is mapped through `to_screen`, which flips Y and scales
by PRINT_SCALE at once.  Nothing is emitted inside a group transform,
which is what keeps text upright.

Fonts
-----
The typeface is chosen the same way the cable and boxes playgrounds
choose theirs: everything flows from playground_fonts.py.  This
module imports two names from it —

    FONT_FAMILY_SANS    the CSS font-family stack for the sans face
    FONT_FACE_CSS       the @font-face declaration, base64-inlined

— and does two things with them:

    FONT_FAMILY         set to FONT_FAMILY_SANS, and used as the
                        font-family attribute on every <text> the
                        renderer emits.

    @font-face block    embedded inside the SVG, in a <style> inside
                        <defs>, so the SVG file is fully self-
                        contained: opening it in a browser or
                        embedding it anywhere shows the intended
                        typeface without the reader needing the font
                        installed.

To change the typeface, edit playground_fonts.py — do not touch
this file.  That is the same single-source-of-truth arrangement the
two playgrounds use.

One caveat about PNG: cairosvg does not parse @font-face.  The .svg
renders correctly everywhere (browser, Inkscape, Word, etc.); the
.png, which is produced by cairosvg, falls back to whatever the
system has for the fallback stack.  If you need the .png to show the
same exact face, install the font file system-wide and reference it
by name — but that is a build-environment concern, not something
this module can solve.

Margins
-------
The whole drawing sits inside a uniform empty border of MARGIN_MM
millimetres on every edge.  The final SVG's width and height are

    W = (bbox_width  in source units) * PRINT_SCALE + 2 * MARGIN_MM
    H = (bbox_height in source units) * PRINT_SCALE + 2 * MARGIN_MM

and every drawn point is placed at

    screen_x = (x - minx) * PRINT_SCALE + MARGIN_MM
    screen_y = (maxy - y) * PRINT_SCALE + MARGIN_MM

so the drawing's bbox spans [MARGIN_MM, W - MARGIN_MM] ×
[MARGIN_MM, H - MARGIN_MM] and nothing touches the SVG edges.

The bbox itself is computed by _bbox_of, which collects every
element the renderer will draw.  Labels get an extra font-
proportional pad (LABEL_BBOX_PAD_FRAC) because their collision-box
half-extents are CHAR_ASPECT-based estimates, and a wide display
face can render wider than the estimate.

Everything is ink
-----------------
There is one line colour: black.  Dimension lines used to be red;
they are black now.  What distinguishes the four kinds of line is
TEXTURE, not colour:

    walls, columns      solid black FILLS
    step edges          solid (against a wall) or dashed (free
                        riser against open floor)
    leaders             solid thin black lines with arrowheads or
                        dots at their anchor ends
    dimension lines     STRIPED black lines — a series of short
                        ticks rotated away from the line's own
                        direction, evenly spaced along it.

The stripe is the "rotated dashes" idea: instead of dashes running
ALONG the line, each mark is a short segment crossing the line at
an angle.  On a horizontal dimension line the marks read as a row
of diagonal ticks; on a vertical one, as a column of them.  It is
unmistakably not a leader and unmistakably not a wall outline.

A ruler has three striped strokes: the main dim line (the one with
the endpoint ticks) and two extension lines that run from the wall
to the dim line.  The main dim line uses _STRIPE_TICK_MM; the
extension lines use the shorter _STRIPE_EXT_TICK_MM so the ruler's
side lines read as thinner than its spine, while keeping the same
tick stroke weight — the stripes are the same texture, they just
form a narrower band.

Tuning:
    _STRIPE_PERIOD_MM     spacing between tick centres, along the line
    _STRIPE_TICK_MM       length of each individual tick — the main
                          dim line and the endpoint ticks
    _STRIPE_EXT_TICK_MM   tick length for the ruler's extension
                          (side) lines only — shorter than
                          _STRIPE_TICK_MM, so the side lines read as
                          a thinner line than the ruler's spine
                          while keeping the same stroke weight (the
                          same texture, narrower band)
    _STRIPE_ANGLE_DEG     angle between the tick and the line's own
                          direction; 90° would make them perpendicular,
                          0° would make them parallel (a normal dashed
                          line), 60° gives the striped look.
    _W_DIM_STRIPE         the stripe tick stroke — heavier than the
                          leader stroke, so the ticks read as a
                          texture rather than as hairlines.  Shared
                          by the main dim line and the extension
                          lines; only the tick LENGTH differs.

Walls and columns — solid black fills
-------------------------------------
The wall regions (plan face) and the column footprints are drawn as
solid black fills.  A column that abuts a wall is visually
indistinguishable from it.  The wall face's `fill-rule="evenodd"`
cuts the room interior and the door openings out of the fill — a
point inside a hole ring is not wall material and stays white.

White inversion over the black fills
------------------------------------
Two line families — the leaders and the striped dimension lines —
read as inverted where they cross or end over a black fill: white
on black, so they stay legible instead of disappearing into the
fill.

Mechanism: draw the geometry twice.

    1.  Pass 1: BLACK, unmasked, over paper — the normal
        appearance.  Drawn first.

    2.  Pass 2: WHITE, masked to the wall + column black fills.
        Drawn second, on top of pass 1, so the white wins wherever
        the mask is opaque.

The mask carries WHITE wall paths and WHITE column paths and
nothing else.  Its default background is transparent black — the
"hidden" state under both luminance masking (RGB 0) and alpha
masking (alpha 0).  So the mask is opaque exactly over a wall or a
column, and transparent everywhere else.

Why a <mask>, not a <clipPath>
------------------------------
SVG 1.1 restricts <clipPath> children to <path>, <text>, and <use>.
cairosvg enforces that restriction and, worse, appears to consider
only the FIRST <path> child of a <clipPath> — so a wall path works
but additional column paths inside the same clip are silently
dropped.  That was the bug that kept the C1 column dot from
inverting.  A <mask> has no such restriction: both <path> and
<rect> are valid children, and multiple children composite as
expected.

`fill-rule="evenodd"` on the wall paths inside the mask is what
keeps the hole rings (room interior, door openings) out of the
masked region.  A leader that passes through a doorway is over
paper, not over wall, and stays black there.

Line weights
------------
The six _W_* constants are the printed widths, in millimetres.

    _W_PLAN       1.2   wall silhouette
    _W_COL        1.1   column footprint outline
    _W_STEP       0.9   step riser outline
    _W_DIM_MAIN   0.6   dim-line endpoint ticks
    _W_DIM_STRIPE 0.8   stripe tick stroke (shared by the dim line
                        and by the ruler's extension lines)
    _W_LEADER     0.8   leader lines and their arrowhead chevrons

Self-check
----------
After assembly, `render` parses its own output via xml.etree.  On
failure it prints the line range around the parse error.
"""

import math

from playground_fonts import FONT_FAMILY_SANS, FONT_FACE_CSS


PRINT_SCALE = 0.1

# Outer margin around the whole drawing, in printed millimetres.
MARGIN_MM = 30.0

# Extra safety pad around each label, as a fraction of the label's
# font size, applied only to the whole-image bbox.
LABEL_BBOX_PAD_FRAC = 0.15

# Printed widths, in mm.
_W_PLAN       = 1.2
_W_COL        = 1.1
_W_STEP       = 0.9
_W_DIM_MAIN   = 0.6     # dim-line endpoint ticks
_W_DIM_STRIPE = 0.8     # stripe tick stroke — shared by the dim line
                        # and by the ruler's extension (side) lines
_W_LEADER     = 0.8     # leader lines and their arrowhead chevrons

# Stripe parameters for the dimension lines.  See the module docstring.
#
# The main dim line and the two extension lines share the same period,
# tick stroke, and angle, so they read as one family of strokes.  Only
# the tick LENGTH differs: the extension lines get the shorter
# _STRIPE_EXT_TICK_MM, which narrows the band they form and makes the
# side of the ruler read as a thinner line than the ruler's spine.
_STRIPE_PERIOD_MM   = 2.5
_STRIPE_TICK_MM     = 2.4
_STRIPE_EXT_TICK_MM = 1.6
_STRIPE_ANGLE_DEG   = 60.0

COLOR_INK   = "#000000"
COLOR_PAPER = "#ffffff"     # only used by the inverted pass

# The typeface comes from playground_fonts.py — the same single
# source of truth the cable and boxes playgrounds use.  Edit
# playground_fonts.py to change it; nothing here needs to change.
FONT_FAMILY = FONT_FAMILY_SANS

# Dot radius, as a fraction of arrow_size.
_DOT_RADIUS_FACTOR = 0.35


def _esc(s):
    return (str(s).replace("&",  "&amp;")
                   .replace("<",  "&lt;")
                   .replace(">",  "&gt;")
                   .replace('"', "&quot;"))


def _bbox_of(geom, layout):
    """Compute the drawing's bounding box in source units."""
    xs, ys = [], []

    def add_pt(p):
        xs.append(p[0]); ys.append(p[1])

    def add_segs(segs):
        for a, b in segs:
            add_pt(a); add_pt(b)

    for face in geom["planFaces"]:
        for p in face["outer"]:
            add_pt(p)
        for h in face.get("holes", []):
            for p in h:
                add_pt(p)

    add_segs(layout.get("step_lines_solid",    []))
    add_segs(layout.get("step_lines_dashed",   []))
    add_segs(layout.get("col_outline_lines",   []))
    add_segs(layout.get("dim_lines_main",      []))
    add_segs(layout.get("dim_lines_small",     []))
    add_segs(layout.get("dim_ext_lines_main",  []))
    add_segs(layout.get("dim_ext_lines_small", []))
    add_segs(layout.get("col_dim_lines",       []))
    add_segs(layout.get("col_dim_ext_lines",   []))
    add_segs(layout.get("dim_ticks",           []))

    for lb in layout["labels"] + layout["notes_labels"]:
        pad = lb.size * LABEL_BBOX_PAD_FRAC
        xs.append(lb.pos[0] - lb.hw - pad); xs.append(lb.pos[0] + lb.hw + pad)
        ys.append(lb.pos[1] - lb.hh - pad); ys.append(lb.pos[1] + lb.hh + pad)

    for anchor, seg in (layout.get("leaders") or {}).values():
        if anchor is not None:
            add_pt(anchor)
        if seg is not None:
            add_pt(seg[0]); add_pt(seg[1])

    if not xs:
        return 0.0, 0.0, 0.0, 0.0
    return min(xs), min(ys), max(xs), max(ys)


def _self_check(svg):
    import xml.etree.ElementTree as ET
    try:
        ET.fromstring(svg)
        return True
    except ET.ParseError as e:
        print(f"  WARNING: floor_plan_draw produced malformed XML: {e}")
        try:
            line_no, col_no = e.position
        except Exception:
            return False
        lines = svg.split("\n")
        lo = max(0, line_no - 3)
        hi = min(len(lines), line_no + 3)
        print(f"  ---- around line {line_no}, column {col_no} ----")
        for i in range(lo, hi):
            marker = ">>>" if i + 1 == line_no else "   "
            snippet = lines[i]
            if len(snippet) > 160:
                snippet = snippet[:157] + "..."
            print(f"  {marker} {i + 1:4d} | {snippet}")
        print(f"  ------------------------------------------------")
        return False


def _arrowhead_segments(tip, tail, size):
    """Two chevron segments forming an arrowhead at `tip`, pointing
    back along (tip − tail)."""
    wx, wy = tip
    sx, sy = tail
    dx = wx - sx
    dy = wy - sy
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return []
    ux, uy = dx / L, dy / L
    px, py = -uy, ux
    bx = wx - ux * size
    by = wy - uy * size
    w1 = (bx + px * size * 0.5, by + py * size * 0.5)
    w2 = (bx - px * size * 0.5, by - py * size * 0.5)
    return [(w1, (wx, wy)), (w2, (wx, wy))]


def _striped_segment_svg(x1, y1, x2, y2, color,
                         stroke_width=_W_DIM_STRIPE,
                         tick_length=_STRIPE_TICK_MM):
    """Emit a line from (x1, y1) to (x2, y2) as a series of short
    ticks rotated away from the line's own direction.

    Screen coordinates in, list of SVG <line> element strings out.
    The ticks are laid out every _STRIPE_PERIOD_MM along the line,
    each `tick_length` long, at _STRIPE_ANGLE_DEG from the line's
    own direction, stroked at `stroke_width` millimetres.

    The defaults draw the ruler's spine.  The ruler's extension
    lines call this with the shorter `tick_length=_STRIPE_EXT_TICK_MM`
    so the band they form is narrower — a thinner line — while
    keeping the same tick stroke weight, i.e. the same texture."""
    dx = x2 - x1
    dy = y2 - y1
    L = math.hypot(dx, dy)
    if L < 1e-6:
        return []
    ux = dx / L
    uy = dy / L

    # Tick direction: rotate the line's unit vector by the stripe
    # angle.  At 60° from the line's own direction, the ticks lean
    # sharply across the line and read as "striped", not "dashed".
    a = math.radians(_STRIPE_ANGLE_DEG)
    ca, sa = math.cos(a), math.sin(a)
    tx = ux * ca - uy * sa
    ty = ux * sa + uy * ca

    half = tick_length / 2.0
    period = _STRIPE_PERIOD_MM
    n = int(L / period) + 2

    els = []
    for k in range(n):
        u = k * period
        if u > L + 1e-6:
            break
        cx = x1 + ux * u
        cy = y1 + uy * u
        ex1 = cx - tx * half
        ey1 = cy - ty * half
        ex2 = cx + tx * half
        ey2 = cy + ty * half
        els.append(
            f'<line x1="{ex1:.3f}" y1="{ey1:.3f}" '
            f'x2="{ex2:.3f}" y2="{ey2:.3f}" '
            f'stroke="{color}" stroke-width="{stroke_width:.3f}" '
            f'stroke-linecap="butt"/>'
        )
    return els


def render(geom, layout):
    minx, miny, maxx, maxy = _bbox_of(geom, layout)

    W = (maxx - minx) * PRINT_SCALE + 2 * MARGIN_MM
    H = (maxy - miny) * PRINT_SCALE + 2 * MARGIN_MM

    def to_screen(x, y):
        return ((x - minx) * PRINT_SCALE + MARGIN_MM,
                (maxy - y) * PRINT_SCALE + MARGIN_MM)

    def line(p1, p2, stroke_mm, stroke_color=COLOR_INK):
        x1, y1 = to_screen(p1[0], p1[1])
        x2, y2 = to_screen(p2[0], p2[1])
        return (f'<line x1="{x1:.3f}" y1="{y1:.3f}" '
                f'x2="{x2:.3f}" y2="{y2:.3f}" '
                f'stroke="{stroke_color}" '
                f'stroke-width="{stroke_mm:.3f}"/>')

    def circle(center_src, radius_src, fill):
        cx, cy = to_screen(center_src[0], center_src[1])
        r = radius_src * PRINT_SCALE
        return (f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" '
                f'fill="{fill}"/>')

    def poly_path(poly):
        if not poly:
            return ""
        parts = []
        x, y = to_screen(poly[0][0], poly[0][1])
        parts.append(f"M {x:.3f} {y:.3f}")
        for pt in poly[1:]:
            x, y = to_screen(pt[0], pt[1])
            parts.append(f"L {x:.3f} {y:.3f}")
        parts.append("Z")
        return " ".join(parts)

    def face_path(face):
        d = poly_path(face["outer"])
        for h in face.get("holes", []):
            if h:
                d += " " + poly_path(h)
        return d

    def box_path(x0, y0, x1, y1):
        sx0, sy0 = to_screen(x0, y0)
        sx1, sy1 = to_screen(x1, y1)
        rx = min(sx0, sx1)
        ry = min(sy0, sy1)
        rw = abs(sx1 - sx0)
        rh = abs(sy1 - sy0)
        return (f"M {rx:.3f} {ry:.3f} "
                f"L {rx + rw:.3f} {ry:.3f} "
                f"L {rx + rw:.3f} {ry + rh:.3f} "
                f"L {rx:.3f} {ry + rh:.3f} Z")

    def text(p_src, s, size_src):
        x, y = to_screen(p_src[0], p_src[1])
        size_mm = size_src * PRINT_SCALE
        return (f'<text x="{x:.3f}" y="{y:.3f}" '
                f'font-family="{FONT_FAMILY}" '
                f'font-size="{size_mm:.2f}" '
                f'text-anchor="middle" '
                f'dy=".35em">'
                f'{_esc(s)}</text>')

    labels       = layout["labels"]
    notes_labels = layout["notes_labels"]
    leaders      = layout.get("leaders", {})
    arrow_size   = layout["arrow_size"]

    # Precompute the SVG path strings for every black-filled region.
    wall_ds = []
    for face in geom["planFaces"]:
        d = face_path(face)
        if d:
            wall_ds.append(d)
    col_ds = []
    for col in geom["columns"]:
        x0, y0, x1, y1 = col["box"]
        col_ds.append(box_path(x0, y0, x1, y1))

    # Collect the inverting line geometry once — both passes use it.
    #
    #   striped_segs      the ruler's dim-line geometry — the spine,
    #                     drawn at _STRIPE_TICK_MM / _W_DIM_STRIPE
    #   striped_ext_segs  the ruler's extension-line geometry — the
    #                     side lines, drawn at _STRIPE_EXT_TICK_MM /
    #                     _W_DIM_STRIPE (same texture, thinner band)
    #   solid_segs        the endpoint ticks, drawn as short solid strokes
    #   leader_lines      leader paths and arrowhead chevrons
    #   leader_dots       polygon-target dots
    striped_segs = []
    for group_key in ("dim_lines_main", "dim_lines_small", "col_dim_lines"):
        for (a, b) in layout.get(group_key, []):
            striped_segs.append((a, b))

    striped_ext_segs = []
    for group_key in ("dim_ext_lines_main", "dim_ext_lines_small",
                      "col_dim_ext_lines"):
        for (a, b) in layout.get(group_key, []):
            striped_ext_segs.append((a, b))

    solid_segs = list(layout.get("dim_ticks", []))

    leader_lines = []
    leader_dots  = []
    for i, lb in enumerate(labels):
        leader = leaders.get(i)
        if leader is None:
            continue
        anchor_pt, seg = leader
        if seg is not None:
            tail, tip = seg[0], seg[1]
            leader_lines.append((tail, tip))
            if lb.tip == "arrow":
                for (a, b) in _arrowhead_segments(tip, tail, arrow_size):
                    leader_lines.append((a, b))
        if lb.tip == "dot" and anchor_pt is not None:
            leader_dots.append(anchor_pt)

    p = []
    p.append('<?xml version="1.0" encoding="UTF-8"?>')
    p.append(
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" '
        f'width="{W:.2f}mm" height="{H:.2f}mm" '
        f'viewBox="0 0 {W:.2f} {H:.2f}">')

    # ----------------------------------------------------------------
    # <defs> — fonts, then the mask
    # ----------------------------------------------------------------
    p.append('<defs>')

    # The @font-face block, base64-inlined, so the SVG file is fully
    # self-contained.  The base64 alphabet (A-Za-z0-9+/=) and the
    # @font-face syntax itself contain no characters that clash with
    # XML parsing — the CDATA wrapper is belt-and-braces, and also
    # keeps the browser from misreading any CSS comment-like sequence
    # in the payload.
    if FONT_FACE_CSS:
        p.append('<style type="text/css"><![CDATA[')
        p.append(FONT_FACE_CSS)
        p.append(']]></style>')

    # Mask — visible ONLY where a black fill is (walls + columns).
    MASK_ID = "blackFillMask"
    p.append(f'<mask id="{MASK_ID}" '
             f'maskUnits="userSpaceOnUse" '
             f'maskContentUnits="userSpaceOnUse" '
             f'x="0" y="0" '
             f'width="{W:.3f}" height="{H:.3f}">')
    for d in wall_ds:
        p.append(f'<path d="{d}" fill="white" fill-rule="evenodd"/>')
    for d in col_ds:
        p.append(f'<path d="{d}" fill="white"/>')
    p.append('</mask>')

    p.append('</defs>')

    # ---- 1. WALL FILLS -------------------------------------------------
    p.append(f'<g fill="{COLOR_INK}" stroke="{COLOR_INK}" '
             f'fill-rule="evenodd" '
             f'stroke-width="{_W_PLAN:.3f}">')
    for d in wall_ds:
        p.append(f'<path d="{d}"/>')
    p.append('</g>')

    # ---- 2. COLUMN FILLS -----------------------------------------------
    p.append(f'<g fill="{COLOR_INK}" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_COL:.3f}">')
    for d in col_ds:
        p.append(f'<path d="{d}"/>')
    p.append('</g>')

    # ---- 3. STEP OUTLINES ----------------------------------------------
    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_STEP:.3f}">')
    for (a, b) in layout["step_lines_solid"]:
        p.append(line(a, b, _W_STEP))
    p.append('</g>')

    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_STEP:.3f}" '
             f'stroke-dasharray="6 4">')
    for (a, b) in layout["step_lines_dashed"]:
        p.append(line(a, b, _W_STEP))
    p.append('</g>')

    # ---- 4. COLUMN LEFTOVER OUTLINES -----------------------------------
    p.append(f'<g fill="none" stroke="{COLOR_INK}" '
             f'stroke-width="{_W_COL:.3f}">')
    for (a, b) in layout["col_outline_lines"]:
        p.append(line(a, b, _W_COL))
    p.append('</g>')

    # ----------------------------------------------------------------
    # 5. INVERTING LINE GEOMETRY — collected once, emitted twice
    # ----------------------------------------------------------------
    # The striped dimension lines, the dim-line endpoint ticks, the
    # leaders (with their arrowheads and dots) all read as inverted
    # wherever they cross a wall or a column fill.  Both passes
    # share the same geometry; only the colour and the mask differ.
    def _emit_inverting_pass(color, wrap_mask):
        if wrap_mask:
            p.append(f'<g mask="url(#{MASK_ID})">')

        # The ruler's spine — the main dim line.  Full tick length.
        for (a, b) in striped_segs:
            sa = to_screen(a[0], a[1])
            sb = to_screen(b[0], b[1])
            for el in _striped_segment_svg(sa[0], sa[1], sb[0], sb[1],
                                           color,
                                           tick_length=_STRIPE_TICK_MM):
                p.append(el)

        # The ruler's side lines — the extension lines.  Same tick
        # stroke, same period, same angle: the stripes are the same
        # texture.  Only the tick LENGTH differs, so the band they
        # form is narrower and the side lines read as a thinner line
        # than the spine.
        for (a, b) in striped_ext_segs:
            sa = to_screen(a[0], a[1])
            sb = to_screen(b[0], b[1])
            for el in _striped_segment_svg(sa[0], sa[1], sb[0], sb[1],
                                           color,
                                           tick_length=_STRIPE_EXT_TICK_MM):
                p.append(el)

        # Solid short endpoint ticks.
        for (a, b) in solid_segs:
            p.append(line(a, b, _W_DIM_STRIPE, color))

        # Leaders — lines, arrowheads, dots.
        for (a, b) in leader_lines:
            p.append(line(a, b, _W_LEADER, color))
        for (cx, cy) in leader_dots:
            p.append(circle((cx, cy),
                            arrow_size * _DOT_RADIUS_FACTOR,
                            color))

        if wrap_mask:
            p.append('</g>')

    # Pass 1: black, unmasked — the normal appearance over paper.
    _emit_inverting_pass(COLOR_INK, wrap_mask=False)

    # Pass 2: white, masked to the black fills — visible only over a
    # wall or a column, drawn on top of pass 1, so it wins there.
    _emit_inverting_pass(COLOR_PAPER, wrap_mask=True)

    # ---- 6. TEXT -------------------------------------------------------
    p.append(f'<g fill="{COLOR_INK}" stroke="none">')
    for lb in labels + notes_labels:
        p.append(text(lb.pos, lb.text, lb.size))
    p.append('</g>')

    p.append('</svg>')

    svg = "\n".join(p) + "\n"
    _self_check(shot(svg)) if False else _self_check(svg)
    return svg
