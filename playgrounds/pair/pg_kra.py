"""
pg_kra.py — panel-side glue for the "Generate .kra" button.

For each segment of each pair, the client computes:

    the trapezoid's four screen corners, from the pair's own frame
    and the segment's abstract interval [a, b];

    a TEXT item whose affine transform reproduces the trapezoid's
    local frame at the text's anchor point.  The transform is
    derived from the local Jacobian of the segment's parametric map
    — decomposed into rotation · shear · per-axis scale, then
    normalized so the text's on-screen footprint fits inside a
    square of size font_px.  The text thus warps as if it were
    painted on the trapezoid's own plane: it stretches along the
    lines and shears with the trapezoid, exactly as a 2D label
    painted on a warped surface would.

    a RECT item — the trapezoid itself — that the daemon draws as a
    filled polygon, matching what the canvas band shows.

All exported text is drawn in BLACK.  An earlier revision offered a
"Text color" dropdown (pair hue vs. black); the pair-hue variant is
no longer produced, so the option was removed and the color is now
hard-coded.  See the module docstring of pg_base.py, KRA EXPORT,
for the current set of KRA-section controls.

THE PARAMETRIC MAP
==================
A segment [a, b] on a pair p is parametrised by two coordinates
(s, t) ∈ [0, 1]²:

    s   along the segment, from the segment's start (s = 0) to its
        end (s = 1);
    t   across the gap, from line A (t = 0) to line B (t = 1).

The world position at (s, t) is

    u = a + s·(b − a)
    Px = p.cx + u·mA·ux + t·(off + u·(mB − mA))·ux + t·gap·vx
    Py = p.cy + u·mA·uy + t·(off + u·(mB − mA))·uy + t·gap·vy

with (ux, uy) = (cos angle, sin angle), (vx, vy) = (−uy, ux), and

    mA  = p.metricA · pairScale
    mB  = p.metricB · pairScale
    off = p.offset  · pairScale
    gap = p.gap     · pairScale

where `pairScale` is the pair's `scale` field (see pg_core.py,
PER-PAIR SCALE).  The parametric map is exact for all four corners
and matches segmentCornersWorldAt: a nonzero scale simply multiplies
every (u·m, off, gap) term by the same factor, so the trapezoid is
the canvas band's trapezoid, uniformly scaled about the anchor.

THE JACOBIAN AT (s, t)
======================
Differentiating with respect to s and t:

    ∂Px/∂s = k·(mA + t·(mB − mA))·ux
    ∂Py/∂s = k·(mA + t·(mB − mA))·uy
    ∂Px/∂t = (off + u·(mB − mA))·ux + gap·vx
    ∂Py/∂t = (off + u·(mB − mA))·uy + gap·vy

where k = b − a and u = a + s·k.  Screen coordinates are obtained
by (x·scale, −y·scale), so the screen Jacobian J is:

    J00 = +scale·∂Px/∂s     J01 = +scale·∂Px/∂t
    J10 = −scale·∂Py/∂s     J11 = −scale·∂Py/∂t

J maps a small tangent (ds, dt) in the segment's parameter space to
a screen displacement.  That is precisely the linear map a text
sitting on the trapezoid should be warped by.

READING DIRECTION — THE MORE-HORIZONTAL AXIS
============================================
Earlier revisions tried two other rules for picking the text's
local x-axis (its reading direction):

    · always map x to the s-direction — correct only when the
      pair's lines run more horizontally than vertically on
      screen; for a near-vertical pair the label comes out
      rotated 90°.

    · pick the trapezoid edge whose midpoint has the largest
      screen y (the "lowest" edge) — correct in most cases, but
      fragile: for a tall thin trapezoid, or one whose shape and
      position make a vertical edge momentarily the lowest, the
      picker flips to the wrong axis and the label reads sideways.

The current rule is simpler and does not have those failure modes:
between the two Jacobian columns — the screen direction of s and
the screen direction of t — use whichever is MORE HORIZONTAL
(larger |x-component| relative to its length) as the reading axis.
A label reads left-to-right by convention, and the trapezoid edge
whose screen direction is closest to horizontal is the one that
matches that convention.  For a wide short trapezoid this is the
t-edge; for a narrow tall one it is the s-edge; for a diagonal
trapezoid it is whichever of the two happens to lie flatter on
screen.

HANDEDNESS — THE FRAME MUST NOT MIRROR THE GLYPHS
=================================================
SVG's coordinate system has y growing downward.  For a text glyph
to render unmirrored, the local frame (m11, m12, m21, m22) must
have a positive determinant:

    det = m11·m22 − m21·m12 > 0.

The world→screen mapping flips the world y-axis (screen y = −world
y · scale + ty), which inverts the handedness of the (s, t) frame.
The raw Jacobian therefore gives det < 0 for a wide range of pair
orientations, and the daemon faithfully applies that reflected
transform — the glyphs come out mirrored.  The classic symptom is
a label that "reads backwards" or, combined with a 90° axis
choice, looks like the text was rotated and flipped.

The fix is to detect the negative determinant and negate the
y-axis column: (m21, m22) → (−m21, −m22).  That is a reflection
of the y-axis ALONE, which restores det > 0.  The x-axis — the
reading direction — is untouched, so the label still lies along
the trapezoid's chosen reading edge; only the perpendicular
orientation of the glyphs changes, and only to correct a mirror
that should never have been there.

After the handedness fix, the reading direction may still point
leftward on screen; a 180° flip of BOTH columns (which preserves
det > 0 and therefore does not re-mirror) brings the reading
direction rightward without disturbing the glyphs.

NORMALIZATION
=============
The two Jacobian columns are scaled by `1 / max(|s-column|,
|t-column|)`, so after normalization the longer column has length
1.  The text is drawn at font_px = min(W_sq, H_sq) · 0.40 and
warped by the normalized columns; the longer on-screen dimension
therefore reaches font_px, the shorter is proportionally smaller,
and the glyphs fit within a font_px × font_px bounding box without
overflowing the trapezoid.  Rotation and shear are invariant under
the normalization, so the shape of the text is preserved.

ANCHOR POINT
============
The user picks a position — tl, tc, tr, ml, mc, mr, bl, bc, br,
top, bottom, left, right, or center.  The position maps to a (col,
row) pair on the trapezoid, from which an inset anchor is computed
with the requested padding.  Alignment follows from the column
(left-aligned for the left column, right-aligned for the right, and
center-aligned otherwise), so the anchor is the point the text
grows from.

The anchor's (s, t) is passed through the parametric map to obtain
its screen position; the transform's translation is chosen so the
anchor is fixed by the warp.  Whatever alignment and rotation the
text carries, the anchor pixel stays put and the rest of the text
is stretched around it.

WRAPPING
========
The export considers only the k = 0 copy of each segment.  A segment
that has been dragged off the abstract range is still exported at its
stored (a, b): the user can re-position it inside the visible range
before export if they want the wrapped appearance baked in.  This
matches the intent of the export — a .kra document is a 2D layout,
and the user is expected to lay the segments out where they want them
before exporting.
"""

KRA_JS = r"""
/* ==========================================================================
   COLOR HELPERS
   ========================================================================== */

function _hueToHex(h) {
  const to2 = (v) => Math.max(0, Math.min(255, Math.round(v)))
                        .toString(16).padStart(2, "0");
  return "#" + to2(h.r) + to2(h.g) + to2(h.b);
}

/* ==========================================================================
   SEGMENT PARAMETRIC MAP
   ========================================================================== */

function _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy,
                         s, t) {
  const u = a + s * k;
  const bCoef = off + u * (mB - mA);
  const wx = p.cx + u * mA * ux + t * bCoef * ux + t * gap * vx;
  const wy = p.cy + u * mA * uy + t * bCoef * uy + t * gap * vy;
  return [wx, wy];
}

/* ==========================================================================
   ONE SEGMENT → ONE (text, rect) PAIR
   ========================================================================== */

function _computeSegmentKraItem(p, seg, options) {
  const a  = seg.a;
  const b  = seg.b;
  const k  = b - a;
  if (Math.abs(k) < 1e-6) return null;

  const ux = Math.cos(p.angle);
  const uy = Math.sin(p.angle);
  const vx = -uy;
  const vy = ux;

  /* The pair's own scale is folded into the four geometric
     coefficients.  See the module docstring, THE PARAMETRIC MAP.
     `viewScale` is the canvas's world-to-screen factor and is kept
     separate so the two scales cannot be confused. */
  const pairScale = _pairScale(p);
  const viewScale = view.scale;

  const mA  = p.metricA * pairScale;
  const mB  = p.metricB * pairScale;
  const off = p.offset  * pairScale;
  const gap = p.gap     * pairScale;

  /* Trapezoid corners in world and screen space.  Order is
     [A(a), A(b), B(b), B(a)] — the same order the canvas band
     draws in. */
  const w0 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 0, 0);
  const w1 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 1, 0);
  const w2 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 1, 1);
  const w3 = _segmentWorldAt(p, a, k, mA, mB, off, gap, ux, uy, vx, vy, 0, 1);

  const c0 = w2s(w0[0], w0[1]);
  const c1 = w2s(w1[0], w1[1]);
  const c2 = w2s(w2[0], w2[1]);
  const c3 = w2s(w3[0], w3[1]);

  /* Trapezoid's average screen-side lengths.  W_sq is the average
     of the two "along the lines" sides; H_sq the average of the
     two "across the gap" sides.  Both shrink and grow with the
     pair scale, so font_px and the flat-frame padding follow. */
  const lenBottom = Math.hypot(c1[0] - c0[0], c1[1] - c0[1]);
  const lenTop    = Math.hypot(c2[0] - c3[0], c2[1] - c3[1]);
  const lenLeft   = Math.hypot(c3[0] - c0[0], c3[1] - c0[1]);
  const lenRight  = Math.hypot(c2[0] - c1[0], c2[1] - c1[1]);

  const W_sq = (lenBottom + lenTop) / 2;
  const H_sq = (lenLeft   + lenRight) / 2;

  if (W_sq < 2 || H_sq < 2) return null;

  const fontNat = Math.min(W_sq, H_sq) * 0.40;

  const label = seg.name || ("S" + seg.id);

  const pos = options.text_position || "center";
  const padFrac = (typeof options.text_padding === "number")
                    ? options.text_padding : 0.06;
  const drawRects = options.draw_rectangles !== false;

  /* Text extents in pixels — an approximation, as in cone_playground,
     using 0.55 em as the average advance width. */
  const textPixelLen = label.length * fontNat * 0.55;
  const textPixelH   = fontNat;
  const pad = padFrac * Math.min(W_sq, H_sq);

  /* Decode position into (col, row).  col: 0=left, 1=centre, 2=right;
     row: 0=top, 1=centre, 2=bottom. */
  let col = 1, row = 1;
  if      (pos === "tl")                    { col = 0; row = 0; }
  else if (pos === "tc" || pos === "top")   { col = 1; row = 0; }
  else if (pos === "tr")                    { col = 2; row = 0; }
  else if (pos === "ml" || pos === "left")  { col = 0; row = 1; }
  else if (pos === "mr" || pos === "right") { col = 2; row = 1; }
  else if (pos === "bl" || pos === "bottom"){ col = 0; row = 2; }
  else if (pos === "bc")                    { col = 1; row = 2; }
  else if (pos === "br")                    { col = 2; row = 2; }
  /* else "mc", "center", or unknown → col=1, row=1 */

  /* Inset corner in a "flat" [0, W_sq] × [0, H_sq] frame, with
     (0, 0) at the segment's top-left (line B, start).  This is the
     natural screen-reading frame; the y axis points DOWN. */
  const flatX = (col === 0) ? 0 : (col === 2) ? W_sq : W_sq / 2;
  const flatY = (row === 0) ? 0 : (row === 2) ? H_sq : H_sq / 2;
  const dirX  = (col === 0) ? 1 : (col === 2) ? -1 : 0;
  const dirY  = (row === 0) ? 1 : (row === 2) ? -1 : 0;

  const cxFlat = flatX + dirX * (pad + textPixelLen / 2);
  const cyFlat = flatY + dirY * (pad + textPixelH   / 2);

  /* Alignment follows the column: left-column positions are
     left-aligned, right-column right-aligned, the middle column
     (and the top/bottom/centre positions) centre-aligned.  The
     anchor is then shifted along the reading direction accordingly. */
  let alignment;
  if (col === 0) alignment = "left";
  else if (col === 2) alignment = "right";
  else alignment = "center";

  let anchorFlatX = cxFlat;
  if (alignment === "left")  anchorFlatX -= textPixelLen / 2;
  if (alignment === "right") anchorFlatX += textPixelLen / 2;
  const anchorFlatY = cyFlat;

  /* Map the flat-frame anchor into (s, t) on the trapezoid.  flat
     y grows downward, t grows from line A (bottom) to line B (top),
     so t = 1 − flatY / H_sq. */
  const s = anchorFlatX / W_sq;
  const t = 1 - anchorFlatY / H_sq;

  /* Anchor's screen position. */
  const anchorWorld  = _segmentWorldAt(
    p, a, k, mA, mB, off, gap, ux, uy, vx, vy, s, t);
  const [ax, ay] = w2s(anchorWorld[0], anchorWorld[1]);

  /* Jacobian of the parametric map at (s, t). */
  const u = a + s * k;
  const Ja = k * (mA + t * (mB - mA));
  const Jb = (off + u * (mB - mA));

  const J00 =  viewScale * Ja * ux;
  const J10 = -viewScale * Ja * uy;
  const J01 =  viewScale * (Jb * ux + gap * vx);
  const J11 = -viewScale * (Jb * uy + gap * vy);

  /* ------------------------------------------------------------------
     NORMALIZATION

     Compute the length of each Jacobian column and scale both by
     1 / max length, so the longer column reaches length 1. */
  const sLen = Math.hypot(J00, J10);
  const tLen = Math.hypot(J01, J11);
  const maxScale = Math.max(sLen, tLen);
  if (maxScale < 1e-9) return null;
  const invScale = 1 / maxScale;

  /* ------------------------------------------------------------------
     READING DIRECTION — the more-horizontal column

     Between the s-column of J and the t-column of J, pick whichever
     is more horizontal on screen as the text's local x-axis.  See
     the module docstring, READING DIRECTION — THE MORE-HORIZONTAL
     AXIS. */
  const sHoriz = Math.abs(J00) / (sLen || 1);
  const tHoriz = Math.abs(J01) / (tLen || 1);
  const readAlongS = (sHoriz >= tHoriz);

  let m11, m12, m21, m22;
  if (readAlongS) {
    m11 = J00 * invScale;
    m12 = J10 * invScale;
    m21 = J01 * invScale;
    m22 = J11 * invScale;
  } else {
    m11 = J01 * invScale;
    m12 = J11 * invScale;
    m21 = J00 * invScale;
    m22 = J10 * invScale;
  }

  /* HANDEDNESS — no mirroring.  See the module docstring. */
  if (m11 * m22 - m21 * m12 < 0) {
    m21 = -m21;
    m22 = -m22;
  }

  /* READING RIGHTWARD.  See the module docstring. */
  if (m11 < 0) {
    m11 = -m11; m12 = -m12;
    m21 = -m21; m22 = -m22;
  }

  /* Translation that fixes the anchor pixel. */
  const dx = ax - m11 * ax - m21 * ay;
  const dy = ay - m12 * ax - m22 * ay;

  /* Every exported text is drawn in black.  The pair hue is used
     for the trapezoid RECT (below); the label itself is always
     black so it reads on any pair colour. */
  const textColor = "#000000";

  const textItem = {
    name:      seg.name + "_text",
    label:     label,
    text:      label,
    x:         ax,
    y:         ay,
    font_px:   Math.max(6, fontNat),
    alignment: alignment,
    color:     textColor,
    transform: [m11, m12, m21, m22, dx, dy],
    inline_size: W_sq,
  };

  let rectItem = null;
  const filled = seg.filled !== false;
  if (drawRects && filled) {
    const hue = pairHue(p);
    rectItem = {
      name:           seg.name + "_rect",
      points:         [c0, c1, c2, c3],
      fill:           _hueToHex(hue),
      fill_opacity:   0.40,
      stroke:         _hueToHex(hue),
      stroke_width:   2.0,
      stroke_opacity: 1.0,
    };
  }

  return { text: textItem, rect: rectItem };
}

/* ==========================================================================
   PAYLOAD BUILDER
   ========================================================================== */

function buildKraPayload(options) {
  const texts = [];
  const rects = [];
  for (const p of pairs) {
    for (const seg of p.segments) {
      const item = _computeSegmentKraItem(p, seg, options);
      if (!item) continue;
      if (item.text) texts.push(item.text);
      if (item.rect) rects.push(item.rect);
    }
  }
  return { texts, rects };
}

/* ==========================================================================
   OPTION READ / WRITE
   ==========================================================================
   The KRA section controls are: text position, text padding, and the
   draw-rectangles toggle.  The text color is fixed to black in
   `_computeSegmentKraItem`, so there is no longer a color option to
   read or write. */

function readKraOptionsFromDOM() {
  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");

  const text_position = posEl ? posEl.value : "center";

  let text_padding = padEl ? parseFloat(padEl.value) : 0.06;
  if (!isFinite(text_padding) || text_padding < 0) text_padding = 0.06;
  if (text_padding > 0.5) text_padding = 0.5;

  const draw_rectangles = rectEl ? !!rectEl.checked : true;

  return {
    text_position:   text_position,
    text_padding:    text_padding,
    draw_rectangles: draw_rectangles,
  };
}

function applyKraOptionsToDOM(opts) {
  if (!opts || typeof opts !== "object") return;

  const posEl  = document.getElementById("kraTextPos");
  const padEl  = document.getElementById("kraTextPad");
  const rectEl = document.getElementById("kraDrawRects");

  if (posEl && typeof opts.text_position === "string") {
    posEl.value = opts.text_position;
  }
  if (padEl && typeof opts.text_padding === "number") {
    padEl.value = String(opts.text_padding);
  }
  if (rectEl && typeof opts.draw_rectangles === "boolean") {
    rectEl.checked = opts.draw_rectangles;
  }
  /* A `text_color_mode` field may still be present in scene files
     written before the option was removed; it is accepted silently
     and ignored. */
}

/* ==========================================================================
   TOP-LEVEL ENTRY
   ========================================================================== */

async function generateKraFromScene() {
  let segCount = 0;
  for (const p of pairs) segCount += p.segments.length;

  if (segCount === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("No segments to export", "warn");
    }
    return;
  }

  const options  = readKraOptionsFromDOM();
  const band = {
    width:  Math.max(1, Math.round(window.innerWidth)),
    height: Math.max(1, Math.round(layout.topH)),
  };

  const result = buildKraPayload(options);
  if (result.texts.length === 0 && result.rects.length === 0) {
    if (typeof flashStatus === "function") {
      flashStatus("Nothing exportable", "warn");
    }
    return;
  }

  const defaultPath = "/tmp/pair_scene.kra";
  const outputPath = window.prompt("Output .kra path:", defaultPath);
  if (!outputPath) return;

  const body = {
    doc_name:    "Pairs Scene",
    output_path: outputPath,
    options:     options,
    cone_band:   band,
    texts:       result.texts,
    rects:       result.rects,
  };

  if (typeof flashStatus === "function") {
    flashStatus("Generating .kra (" + result.texts.length
                + " text(s))...", "");
  }

  try {
    const response = await fetch("/generate-kra", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(response_body(body)),
    });
    const res = await response.json();
    if (res && res.success) {
      if (typeof flashStatus === "function") {
        flashStatus(res.message || "Done", "ok");
      }
    } else {
      const msg = (res && res.message) ? res.message : "unknown error";
      if (typeof flashStatus === "function") {
        flashStatus("Failed: " + msg, "bad");
      }
    }
  } catch (e) {
    if (typeof flashStatus === "function") {
      flashStatus("Request failed: " + e.message, "bad");
    }
  }
}

/* Small indirection so the fetch body is built in one place, keeping
   the async wrapper's shape unchanged if the body ever grows. */
function response_body(b) { return b; }
"""
