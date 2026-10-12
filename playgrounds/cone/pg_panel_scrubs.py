"""
pg_panel_scrubs.py — every scrub-input binding.

One IIFE, and inside it one _installScrubInput(...) call per scrub
field.  Each call supplies rate, snapStep, read(), write() and
(where needed) onDragStart / symmetricOnShift + writeSymmetric.

This is the file to edit when a field's read/write/rate/snap
changes.  The field's DOM element lives in pg_base.py; its
semantics (what the value means, what it clamps to) live in
pg_panel_fields.py; the plumbing the binding uses lives in
pg_panel_core.py.

FIELDS INSTALLED HERE
=====================
    patchPhiVal          patch angular centre, in display units
    patchSVal            patch axial centre, in fraction-of-height
    patchMirrorAngle     mirror offset, degrees in, radians in model
    patchWarp            plane-warp parameter, [0, 1]
    squareWVal           selected shape's reference-unit width
    squareHVal           selected shape's reference-unit height
    squareSlopeVal       selected shape's hinge parameter, [0, 1]
    shapeStandingMultVal global standing-height multiplier
    squarePhiVal         selected shape's flat-view φ
    squareSVal           selected shape's flat-view s

The KRA text-padding field (kraTextPad) is not a scrub field: it is
read as a plain text input at KRA-export time, and is installed
nowhere.
"""

PANEL_SCRUBS_JS = r"""
/* ==========================================================================
   SCRUB-INPUT BINDINGS
   ========================================================================== */

(function installScrubs() {

  /* ---- patch-position scrub inputs -------------------------------
     The φ field reads and writes in display units (see the module
     docstring of pg_panel_core.py); the s field reads and writes in
     fraction-of-height units, unchanged. */

  const phiInput = document.getElementById("patchPhiVal");
  const sInput   = document.getElementById("patchSVal");

  if (phiInput) _installScrubInput(phiInput, {
    rate: PATCH_PHI_SCRUB_RATE,
    snapStep: PATCH_SNAP_PHI_NORM,
    read: () => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return 0;
      return _phiToDisplay(_patchPhiCenter(quads[selectedQuad]));
    },
    write: (raw) => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      _setPatchPhiCenter(quads[selectedQuad], _displayToPhi(raw));
    },
  });

  if (sInput) _installScrubInput(sInput, {
    rate: PATCH_S_SCRUB_RATE,
    read: () => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return 0;
      return _patchSCenter(quads[selectedQuad]);
    },
    write: (raw) => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      _setPatchSCenter(quads[selectedQuad], raw);
    },
  });

  /* ---- mirror angle scrub input ---------------------------------
     The angular offset at which the mirror patch is drawn, in
     radians in the model and degrees in the field.  No snap on this
     field: the plain drag is continuous, and the Shift-held
     symmetric drag below is also continuous.

     Plain drag: moves only the mirror angle.

     Shift-held drag (the symmetric mode): moves the mirror AND the
     original patch symmetrically about the midpoint they shared at
     drag start.  If the mirror angle advances by Δ, the patch's φ
     shifts by −Δ/2 and the mirror (which sits at patch_φ +
     mirrorAngle) moves by +Δ/2.

     The midpoint is captured at mousedown — not recomputed from
     the current patch position — so a long drag does not
     accumulate drift.  Releasing Shift mid-drag restores the
     patch to its start φ on the next motion event. */

  const mirrorAngleInput = document.getElementById("patchMirrorAngle");
  if (mirrorAngleInput) _installScrubInput(mirrorAngleInput, {
    rate: MIRROR_ANGLE_SCRUB_RATE_DEG_PER_PX * Math.PI / 180,
    symmetricOnShift: true,

    read: () => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return Math.PI;
      const q = quads[selectedQuad];
      return (typeof q.mirrorAngle === "number") ? q.mirrorAngle : Math.PI;
    },

    /* Snapshot the patch's φ at drag start so a symmetric drag can
       always be recomputed from the same origin. */
    onDragStart: (drag) => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      drag.startPhi = _patchPhiCenter(quads[selectedQuad]);
    },

    /* Plain drag: set the mirror angle.  If the drag was in
       symmetric mode at any point, restore the patch's φ to its
       start value so releasing Shift does not leave the patch
       stranded mid-offset. */
    write: (raw, drag) => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      const q = quads[selectedQuad];
      if (drag && typeof drag.startPhi === "number") {
        _setPatchPhiCenter(q, drag.startPhi);
      }
      q.mirrorAngle = raw;
    },

    /* Shift-held drag: Δ = mirrorAngle − startMirrorAngle.  Mirror
       moves by Δ; patch moves by −Δ/2.  Both are absolute from the
       drag-start snapshot, so repeated mousemoves are idempotent
       and no drift accumulates. */
    writeSymmetric: (raw, drag) => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      const q = quads[selectedQuad];
      const startAngle = drag.startValue;
      const startPhi   = (typeof drag.startPhi === "number")
                           ? drag.startPhi
                           : _patchPhiCenter(q);
      const delta = raw - startAngle;
      q.mirrorAngle = startAngle + delta;
      _setPatchPhiCenter(q, startPhi - delta / 2);
    },
  });

  /* ---- plane warp scrub input -----------------------------------
     The warp parameter reparametrises the patch's own tangential
     plane.  The plane is not moved and not bent — only the chart
     used to address points on it changes.  The value is unitless
     on [0, 1]. */

  const warpInput = document.getElementById("patchWarp");
  if (warpInput) _installScrubInput(warpInput, {
    rate: PATCH_WARP_SCRUB_RATE,
    snapStep: PATCH_WARP_SNAP_STEP,
    read: () => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return 0;
      const q = quads[selectedQuad];
      return (typeof q.warp === "number") ? q.warp : 0;
    },
    write: (raw) => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      const q = quads[selectedQuad];
      q.warp = Math.max(0, Math.min(1, raw));
    },
  });

  /* ---- square-size scrub inputs ---------------------------------
     The two fields read and write the shape's reference-unit size,
     independent of the patch it sits on (see pg_panel_fields.py).
     Both are disabled until a square is selected. */

  const wInput = document.getElementById("squareWVal");
  const hInput = document.getElementById("squareHVal");

  if (wInput) _installScrubInput(wInput, {
    rate: SQUARE_SIZE_SCRUB_RATE,
    snapStep: SQUARE_SIZE_SNAP_STEP,
    read: () => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return 0;
      return squareWorldWidth(floatSquares[selectedSquare]);
    },
    write: (raw) => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return;
      _setSquareWidth(floatSquares[selectedSquare], raw);
    },
  });

  if (hInput) _installScrubInput(hInput, {
    rate: SQUARE_SIZE_SCRUB_RATE,
    snapStep: SQUARE_SIZE_SNAP_STEP,
    read: () => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return 0;
      return squareWorldHeight(floatSquares[selectedSquare]);
    },
    write: (raw) => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return;
      _setSquareHeight(floatSquares[selectedSquare], raw);
    },
  });

  /* ---- slope scrub input ----------------------------------------
     The field reads and writes the shape's hinge parameter — a
     unitless value in [0, 1]. */

  const slopeInput = document.getElementById("squareSlopeVal");

  if (slopeInput) _installScrubInput(slopeInput, {
    rate: SLOPE_SCRUB_RATE_PER_PX,
    snapStep: SLOPE_SNAP_STEP,
    read: () => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return 0;
      return floatSquares[selectedSquare].slope || 0;
    },
    write: (raw) => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return;
      const s = Math.max(SHAPE_SLOPE_MIN, Math.min(SHAPE_SLOPE_MAX, raw));
      floatSquares[selectedSquare].slope = s;
    },
  });

  /* ---- standing-height multiplier scrub input -------------------
     The field reads and writes the global aesthetic factor
     SHAPE_STANDING_HEIGHT_MULT — how tall a hinged shape stands,
     as a multiple of its flat-view height.  Applies to every shape
     in the scene; not per-square, and not written by scene save.
     See the STANDING HEIGHT MULTIPLIER section in
     pg_view_squares.py. */
  const multInput = document.getElementById("shapeStandingMultVal");
  if (multInput) _installScrubInput(multInput, {
    rate: 0.005,
    snapStep: 0.1,
    read: () => SHAPE_STANDING_HEIGHT_MULT,
    write: (raw) => {
      SHAPE_STANDING_HEIGHT_MULT =
        Math.max(0.05, Math.min(10, raw));
    },
  });

  /* ---- square-position scrub inputs -----------------------------
     The two fields read and write the selected shape's centre in
     the flat view's (φ, s) coordinates.  φ is displayed in
     [0, 2π); a value outside that range, whether typed or produced
     by scrubbing across the seam, is folded back in by the read
     side.  The write side passes the raw value through to
     setSquareFlatCenter, which unwraps it relative to the shape's
     current φ so a jump across the seam moves the shape the short
     way around the cone. */

  const squarePhiInput = document.getElementById("squarePhiVal");
  const squareSInput   = document.getElementById("squareSVal");

  if (squarePhiInput) _installScrubInput(squarePhiInput, {
    rate: SQUARE_PHI_SCRUB_RATE,
    read: () => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return 0;
      const c = squareFlatCenter(floatSquares[selectedSquare]);
      if (!c) return 0;
      let p = c.phi;
      p = ((p % (2 * Math.PI)) + 2 * Math.PI) % (2 * Math.PI);
      return p;
    },
    write: (raw) => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return;
      const sq = floatSquares[selectedSquare];
      const c = squareFlatCenter(sq);
      if (!c) return;
      setSquareFlatCenter(sq, raw, c.s);
    },
  });

  if (squareSInput) _installScrubInput(squareSInput, {
    rate: SQUARE_S_SCRUB_RATE,
    read: () => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return 0;
      const c = squareFlatCenter(floatSquares[selectedSquare]);
      if (!c) return 0;
      return c.s;
    },
    write: (raw) => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return;
      const sq = floatSquares[selectedSquare];
      const c = squareFlatCenter(sq);
      if (!c) return;
      setSquareFlatCenter(sq, c.phi, raw);
    },
  });

})();
"""
