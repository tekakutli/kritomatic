"""
pg_panel_fields.py — setters and per-field sync functions.

Everything that knows what a specific patch- or square-field MEANS
lives here: the patch φ/s centre setters, the square W/H setters,
the two field-sync functions that push the model's current state
into the DOM each frame, and the mirror / warp / slope / visual-
bottom fields' semantics.

The scrub-binding plumbing itself is in pg_panel_core.py; the
per-field scrub bindings that call these functions live in
pg_panel_scrubs.py.  Editing a field's meaning (how it interprets
its value, what it clamps to) is this file.  Editing a field's
read/write/rate/snap is pg_panel_scrubs.py.  Editing the field's
DOM element is pg_base.py.

PATCH φ AND PATCH S
===================
The "Patch φ" field displays the selected patch's angular centre in
DISPLAY UNITS.  See pg_panel_core.py for the conversion.  The
setter here takes a radian target.

The "Patch s" field is the patch's axial centre, where s = 0 sits
at the base ring and s = 1 at the apex.  Both bounds are inset by
halfSpan:  the interval cannot slide so far that either edge leaves
[FLAT_S_MIN, FLAT_S_MAX].  For a default patch (span 0.50, halfSpan
0.25) the centre is confined to [0.25, 0.75] — bottom edge at the
base on the low end, top edge at the apex on the high end.

SQUARE φ / S
============
The "Square φ" and "Square s" scrub inputs show and set the
selected shape's centre in the FLAT view's (φ, s) coordinates — the
same axis the unfolded-cone sheet is drawn in, and the same numbers
the status line reports for a cursor over that sheet.

φ is displayed in [0, 2π) so the user reads the canonical value the
flat sheet draws with.  The cone wraps: 0 and 2π are the same
physical meridian, and the flat view draws every integer-2π copy of
a shape whose patch straddles the seam.  Typing a value on the far
side of the seam therefore moves the shape the short way around the
cone — the write side unwraps the new φ against the shape's current
φ before applying it.

SQUARE W / H
============
The "Square W" and "Square H" fields show the selected shape's
intrinsic size in REFERENCE units, independent of the patch it sits
on and of the cone's current depth or half-angle:

    W = SHAPE_REL_SIZE · scaleU
    H = SHAPE_REL_SIZE · scaleV

The shape's ACTUAL on-patch world size is this times the patch's
per-view aspect scale (Ku or Kv).  That is what makes a shape cover
a fixed fraction of its patch.  The fields report the invariant
reference value so the displayed number does not jump around as you
slide the depth or half-angle sliders.

SLOPE
=====
The "Slope" field shows the selected shape's hinge parameter, a
unitless value in [0, 1].  Zero leaves the shape flat on the patch.
As the value rises the shape hinges upward about its near edge; at
1 it has rotated a full 90 degrees out of the patch plane.  The
flat view ignores slope — its footprint is the un-hinged (φ, s)
projection — so editing in that band stays exact.

PLANE WARP
==========
The "Plane warp" field shows the selected patch's warp parameter, a
unitless value in [0, 1].  It reparametrises the patch's own
tangential plane without moving or bending it.  Only the cone view
reads warp; the flat view is unchanged.  See pg_view_squares.py
(_warpedFrameToWorld and the PLANE WARP section of the module
docstring).

VISUAL BOTTOM
=============
The "Visual bot." checkbox is a per-square meta option that changes
how the shape's text anchor and its four corner senses are chosen
during KRA export.  It is stored on the square object as
`visualBottom`.  Unchecked (the default) reads the shape's own
longest edge; checked reads the visual bottom of the scene.  The
flag affects only the KRA export (pg_kra.py); it does not change
how the shape is drawn on the canvas.

MIRROR, MIRROR ANGLE, MIRROR FLIP
=================================
A per-patch "Mirror" checkbox sits in the Patches section.  When
checked, every square on the selected patch is drawn a second time
on the patch's mirror — the same patch shifted by `mirrorAngle` in
φ, i.e. on the opposite side of the cone.  The mirror copy is a
pure visual clone; it is not independently editable and does not
appear in the lists.

`mirrorAngle` (default π) is the angular offset at which the
mirror is drawn.  The rate is 1° per pixel of horizontal drag.
Stored in radians in the model, shown in degrees in the field.
Shift-drag enters a symmetric mode: the mirror angle advances by Δ
and the patch's φ shifts by −Δ/2, so the mirror and the original
separate at equal rates about the midpoint they shared at drag
start.

`mirrorFlip` (default true) negates the shape's θ for the duration
of the mirror draw.  The φ-shift of the mirror rotates the patch's
local U/V frame by the same angle, which would otherwise render a
tilted, rotated, or anchored shape 180°-rotated relative to the
original; negating θ cancels that frame rotation.

Both fields round-trip through Save / Load scene.  Old scene files
load with mirrorAngle defaulting to π and mirrorFlip to true, so a
scene written before these fields existed renders identically to
one written after with the defaults.
"""

PANEL_FIELDS_JS = r"""
/* ==========================================================================
   PATCH-POSITION SETTERS
   ========================================================================== */

function _patchPhiCenter(q) {
  let c = (q.phi0 + q.phi1) / 2;
  c = ((c % _TAU) + _TAU) % _TAU;
  return c;
}

function _patchSCenter(q) {
  return (q.s0 + q.s1) / 2;
}

function _setPatchPhiCenter(q, target) {
  const cur = _patchPhiCenter(q);
  let delta = target - cur;
  while (delta >  Math.PI) delta -= _TAU;
  while (delta < -Math.PI) delta += _TAU;
  q.phi0 += delta;
  q.phi1 += delta;
  _reclampShapesOnPatch(q);
}

/* The patch's s-centre.  Symmetric bounds inset by halfSpan, so
   neither edge of the patch can leave [FLAT_S_MIN, FLAT_S_MAX].
   For a default patch (halfSpan 0.25) the centre is confined to
   [0.25, 0.75]. */
function _setPatchSCenter(q, target) {
  const cur = _patchSCenter(q);
  const halfSpan = (q.s1 - q.s0) / 2;
  const lo = FLAT_S_MIN + halfSpan;
  const hi = FLAT_S_MAX - halfSpan;
  let t;
  if (hi < lo) {
    t = (FLAT_S_MIN + FLAT_S_MAX) / 2;
  } else {
    t = Math.max(lo, Math.min(hi, target));
  }
  const delta = t - cur;
  q.s0 += delta;
  q.s1 += delta;
  _reclampShapesOnPatch(q);
}

/* ==========================================================================
   SQUARE-SIZE SETTERS
   ==========================================================================
   The panel's W / H fields speak in the shape's intrinsic REFERENCE
   size (see the module docstring).  The setters invert
   squareWorldWidth / squareWorldHeight — which return
   SHAPE_REL_SIZE · scaleU / scaleV — and clamp the underlying
   scaleU / scaleV to the same SHAPE_MIN_SCALE .. SHAPE_MAX_SCALE
   bounds the corner-drag resize uses, so a typed value cannot
   produce a shape that a drag could not. */

function _setSquareWidth(sq, target) {
  const sU = target / SHAPE_REL_SIZE;
  sq.scaleU = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sU));
}

function _setSquareHeight(sq, target) {
  const sV = target / SHAPE_REL_SIZE;
  sq.scaleV = Math.max(SHAPE_MIN_SCALE,
              Math.min(SHAPE_MAX_SCALE, sV));
}

/* ==========================================================================
   PATCH-POSITION FIELDS
   ========================================================================== */

function _syncPatchCoordInputs() {
  const phiInput = document.getElementById("patchPhiVal");
  const sInput   = document.getElementById("patchSVal");

  if (phiInput && sInput) {
    const q = (selectedQuad >= 0 && selectedQuad < quads.length)
      ? quads[selectedQuad] : null;

    if (!q) {
      phiInput.disabled = true;
      sInput.disabled   = true;
      if (document.activeElement !== phiInput) phiInput.value = "\u2014";
      if (document.activeElement !== sInput)   sInput.value   = "\u2014";
    } else {
      phiInput.disabled = false;
      sInput.disabled   = false;

      /* φ is displayed in normalized units on [-1, 1] (see the
         module docstring); s is displayed as a fraction of the
         axial height, unchanged. */
      if (document.activeElement !== phiInput) {
        phiInput.value = _phiToDisplay(_patchPhiCenter(q)).toFixed(3);
      }
      if (document.activeElement !== sInput) {
        sInput.value = _patchSCenter(q).toFixed(3);
      }
    }
  }

  /* Mirror checkbox follows the selected patch: greyed out with no
     selection, reflecting the patch's own flag otherwise. */
  const mirrorEl = document.getElementById("patchMirror");
  if (mirrorEl) {
    const q = (selectedQuad >= 0 && selectedQuad < quads.length)
      ? quads[selectedQuad] : null;
    if (!q) {
      mirrorEl.disabled = true;
      mirrorEl.checked  = false;
    } else {
      mirrorEl.disabled = false;
      if (document.activeElement !== mirrorEl) {
        mirrorEl.checked = !!q.mirror;
      }
    }
  }

  /* Mirror angle field follows the selected patch the same way.
     Displayed in degrees; the model stores radians.  Defaults to
     π (the diametric mirror) when the field is missing, so a scene
     written before this field existed reads as a full π offset. */
  const mirrorAngleEl = document.getElementById("patchMirrorAngle");
  if (mirrorAngleEl) {
    const q = (selectedQuad >= 0 && selectedQuad < quads.length)
      ? quads[selectedQuad] : null;
    if (!q) {
      mirrorAngleEl.disabled = true;
      if (document.activeElement !== mirrorAngleEl) {
        mirrorAngleEl.value = "\u2014";
      }
    } else {
      mirrorAngleEl.disabled = false;
      if (document.activeElement !== mirrorAngleEl) {
        const ang = (typeof q.mirrorAngle === "number")
                      ? q.mirrorAngle : Math.PI;
        mirrorAngleEl.value = (ang * 180 / Math.PI).toFixed(1);
      }
    }
  }

  /* Mirror flip checkbox follows the selected patch.  Defaults to
     true, matching the pre-existing behaviour for scenes that
     predate the field. */
  const mirrorFlipEl = document.getElementById("patchMirrorFlip");
  if (mirrorFlipEl) {
    const q = (selectedQuad >= 0 && selectedQuad < quads.length)
      ? quads[selectedQuad] : null;
    if (!q) {
      mirrorFlipEl.disabled = true;
      mirrorFlipEl.checked  = false;
    } else {
      mirrorFlipEl.disabled = false;
      if (document.activeElement !== mirrorFlipEl) {
        mirrorFlipEl.checked = q.mirrorFlip !== false;
      }
    }
  }

  /* Plane-warp field follows the selected patch the same way Mirror
     and its companions do.  Missing value defaults to 0 (the
     historic axial chart). */
  const warpEl = document.getElementById("patchWarp");
  if (warpEl) {
    const q = (selectedQuad >= 0 && selectedQuad < quads.length)
      ? quads[selectedQuad] : null;
    if (!q) {
      warpEl.disabled = true;
      if (document.activeElement !== warpEl) {
        warpEl.value = "\u2014";
      }
    } else {
      warpEl.disabled = false;
      if (document.activeElement !== warpEl) {
        const w = (typeof q.warp === "number") ? q.warp : 0;
        warpEl.value = w.toFixed(3);
      }
    }
  }

  _syncSquareSizeInputs();
}

/* ==========================================================================
   SQUARE-SIZE AND -POSITION FIELDS
   ==========================================================================
   The W / H fields show the shape's reference-unit size, which is
   independent of the patch it sits on and of the cone's depth /
   half-angle.  The Slope field shows the shape's hinge parameter,
   a unitless value in [0, 1].  The φ / s fields show and set the
   shape's centre in the flat view's coordinates.

   The Visual bot. checkbox is a per-square flag that changes how
   the KRA export reads the shape's "bottom" edge.  All of them are
   disabled until a square is selected, except the global
   standing-height multiplier, which is always shown. */

function _syncSquareSizeInputs() {
  const wInput  = document.getElementById("squareWVal");
  const hInput  = document.getElementById("squareHVal");
  const sInput  = document.getElementById("squareSlopeVal");
  const phiPos  = document.getElementById("squarePhiVal");
  const sPos    = document.getElementById("squareSVal");
  const vbInput = document.getElementById("squareVisualBottom");
  if (!wInput || !hInput || !sInput) return;

  const sq = (selectedSquare >= 0 && selectedSquare < floatSquares.length)
    ? floatSquares[selectedSquare] : null;

  if (!sq) {
    wInput.disabled = true;
    hInput.disabled = true;
    sInput.disabled = true;
    if (phiPos) {
      phiPos.disabled = true;
      if (document.activeElement !== phiPos) phiPos.value = "\u2014";
    }
    if (sPos) {
      sPos.disabled = true;
      if (document.activeElement !== sPos) sPos.value = "\u2014";
    }
    if (vbInput) {
      vbInput.disabled = true;
      if (document.activeElement !== vbInput) vbInput.checked = false;
    }
    if (document.activeElement !== wInput) wInput.value = "\u2014";
    if (document.activeElement !== hInput) hInput.value = "\u2014";
    if (document.activeElement !== sInput) sInput.value = "\u2014";
    return;
  }

  wInput.disabled = false;
  hInput.disabled = false;
  sInput.disabled = false;
  if (phiPos) phiPos.disabled = false;
  if (sPos)   sPos.disabled   = false;
  if (vbInput) {
    vbInput.disabled = false;
    if (document.activeElement !== vbInput) {
      vbInput.checked = !!sq.visualBottom;
    }
  }

  if (document.activeElement !== wInput) {
    wInput.value = squareWorldWidth(sq).toFixed(3);
  }
  if (document.activeElement !== hInput) {
    hInput.value = squareWorldHeight(sq).toFixed(3);
  }
  if (document.activeElement !== sInput) {
    sInput.value = (sq.slope || 0).toFixed(3);
  }

  /* φ / s follow the selected square's centre in the flat view.
     φ is shown normalized to [0, 2π); the underlying value can be
     any real number, but two φ that differ by a full turn are the
     same direction and displaying the canonical one avoids the
     field jumping by 2π while the user scrubs across the seam. */
  const centre = squareFlatCenter(sq);
  if (centre) {
    if (phiPos && document.activeElement !== phiPos) {
      let p = centre.phi;
      p = ((p % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
      phiPos.value = p.toFixed(3);
    }
    if (sPos && document.activeElement !== sPos) {
      sPos.value = centre.s.toFixed(3);
    }
  }

  /* The global standing-height multiplier follows no selection; it
     is always shown.  Updated here so it refreshes on the same
     frame cycle as the slope field. */
  const mInput = document.getElementById("shapeStandingMultVal");
  if (mInput && document.activeElement !== mInput) {
    mInput.value = SHAPE_STANDING_HEIGHT_MULT.toFixed(2);
  }
}
"""
