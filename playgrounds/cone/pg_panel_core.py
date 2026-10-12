"""
pg_panel_core.py — panel primitives.

The reusable building blocks the other panel files depend on:

    SCRUB RATES      units-per-pixel constants shared by every
                     scrub binding (pg_panel_scrubs.py reads them).

    φ CONVERSION     the two functions that convert between the
                     patch's stored radian angle and the panel's
                     [-1, 1] display units.  The model speaks
                     radians; the "Patch φ" field speaks display
                     units; these are the only two places the two
                     systems meet.

    flashStatus      the transient one-line message at the bottom
                     of the panel.  Every other panel file calls it.

    _installScrubInput
                     the generic horizontal-drag installer every
                     scrub field uses.  The installer owns the drag
                     loop, the snap step, and the Enter/Escape/blur
                     handling; each field supplies rate, snap,
                     read() and write().

    syncPanelSliders / updatePanelLabels
                     the five graphics sliders' two-way binding.

Nothing in this file references a specific field's semantics; if
you are adding a new field, the edit lives in pg_panel_fields.py
and pg_panel_scrubs.py, not here.

φ DISPLAY UNITS
===============
The "Patch φ" field shows the patch's angular centre in DISPLAY
UNITS, not raw radians.  The range is [-1, 1] and the two endpoints
identify — -1 and +1 are the same physical direction, reached from
opposite sides.

    0     the visually-bottom direction of the cone view
          (world φ = -π/2)
    +1     the visually-top direction (world φ = +π/2), reached
          by going clockwise on screen (bottom → right → top)
    -1     the same top direction, reached counterclockwise
          (bottom → left → top)

Increasing values run through the right side of the cone view
(bottom → right → top → left → bottom); decreasing values run
through the left.

The scene file and the visual-state export both store the raw
radian values; only the panel speaks display units.

SHAPE DEPTH
===========
The "Shape depth" slider drives SHAPE_DEPTH_CONE only — the cone
view's perspective projection.  The flat (unfolded-cone) view reads
SHAPE_DEPTH_FLAT, a fixed constant, so its square footprint is
independent of the slider.  See the note in pg_view_squares.py.
"""

PANEL_CORE_JS = r"""
/* ==========================================================================
   SCRUB RATES
   ========================================================================== */

/* Scrub rates for the two patch-position scrub inputs.  The φ rate
   is expressed in display units per pixel (see the φ-display note in
   the module docstring); one display unit is π radians, so this
   matches the previous 0.008 rad/px physical rate.  The s rate is
   still in s units (fraction of the axial height) per pixel. */

const PATCH_PHI_SCRUB_RATE = 0.008 / Math.PI;
const PATCH_S_SCRUB_RATE   = 0.0015;

/* The φ display snap step with Shift held: one meridian (15°), which
   in display units is 1/12.  Both +1 and -1 are top, so a snap that
   lands on either is the same physical position. */

const PATCH_SNAP_PHI_NORM = 1 / 12;

/* Square width / height scrub rates.  Rate is the change in the
   shape's reference-unit size per pixel of horizontal drag; 0.005
   means a 100-pixel drag moves W or H by 0.5.  Snap step with Shift
   held is 0.25 reference units, an eighth of the reference square's
   side at default scale. */

const SQUARE_SIZE_SCRUB_RATE = 0.005;
const SQUARE_SIZE_SNAP_STEP  = 0.25;

/* Square-position scrub rates.  φ is in flat-view radians; 0.008
   per pixel matches the physical rate the patch-φ field uses.  s
   is in axial-fraction units, matching the patch-s field. */
const SQUARE_PHI_SCRUB_RATE = 0.008;
const SQUARE_S_SCRUB_RATE   = 0.0015;

/* Slope scrub rate: 0.005 parameter units per pixel of horizontal
   drag, so a 100-px drag swings the hinge halfway.  Shift snaps to
   0.1 — ten discrete stops across the [0, 1] range.  The stored
   value is the unitless parameter itself; no conversion. */
const SLOPE_SCRUB_RATE_PER_PX = 0.005;
const SLOPE_SNAP_STEP         = 0.1;

/* Mirror-angle scrub rate: 1° per pixel of horizontal drag.
   Stored in radians in the model and shown in degrees in the
   field.  See the MIRROR ANGLE AND FLIP note in pg_panel_fields.py. */
const MIRROR_ANGLE_SCRUB_RATE_DEG_PER_PX = 1.0;

/* Plane-warp scrub rate: 0.005 parameter units per pixel of
   horizontal drag, so a 100-px drag moves warp halfway.  Shift
   snaps to 0.1 — ten discrete stops across [0, 1], matching the
   Slope field.  The stored value is the unitless parameter itself;
   no conversion.  See the PLANE WARP note in pg_panel_fields.py. */
const PATCH_WARP_SCRUB_RATE = 0.005;
const PATCH_WARP_SNAP_STEP  = 0.1;

const SCRUB_DRAG_THRESHOLD = 3;

/* ==========================================================================
   PHI DISPLAY CONVERSION
   ==========================================================================
   The patch's angular centre is stored in radians in the model; the
   panel shows and accepts it in display units where the range is
   [-1, 1] and the endpoints identify.  Zero sits at the visually-
   bottom direction of the cone view; increasing values run clockwise
   on screen (bottom → right → top), decreasing values run the other
   way (bottom → left → top).  +1 and -1 are the same physical
   direction, the top of the cone view.

   These two functions are the only place the two unit systems meet.
   Everything else in the codebase — the cone projection, the flat
   view, the drag handlers, the export, the scene file — works in
   radians. */

const _TAU = 2 * Math.PI;

function _phiToDisplay(phi) {
  let d = (phi + Math.PI / 2) / Math.PI;
  while (d >  1) d -= 2;
  while (d < -1) d += 2;
  return d;
}

function _displayToPhi(display) {
  let d = display;
  while (d >  1) d -= 2;
  while (d < -1) d += 2;
  let phi = d * Math.PI - Math.PI / 2;
  phi = ((phi % _TAU) + _TAU) % _TAU;
  return phi;
}

/* ==========================================================================
   STATUS FLASH
   ========================================================================== */

let _statusTimer = null;
function flashStatus(msg, cls) {
  const el = document.getElementById("status");
  if (!el) return;
  el.textContent = msg;
  el.className = cls || "";
  if (_statusTimer) clearTimeout(_statusTimer);
  _statusTimer = setTimeout(() => {
    _statusTimer = null;
    el.textContent = "";
    el.className = "";
  }, 1800);
}

/* ==========================================================================
   SCRUB-INPUT INSTALLER
   ==========================================================================
   A scrub input is a text field the user drags horizontally to
   change.  The installer owns the drag loop and the read/write
   plumbing; each binding supplies four things:

       rate             units per pixel of horizontal drag
       snapStep         optional; Shift-held drags step by this
       read()           current value, in the binding's own units
       write(raw, drag) apply a value; `drag` is the drag-state
                        object (see below), or undefined when the
                        call came from Enter / blur rather than a
                        drag

   Two optional hooks extend the loop for bindings that need to
   coordinate more than one model field:

       onDragStart(drag)      called once at mousedown, after `drag`
                              has been created, so a binding can
                              snapshot whatever else it needs to
                              hold fixed for the duration of the
                              drag (e.g. the patch's φ at drag
                              start, for the symmetric mirror-angle
                              mode).

       symmetricOnShift       when true, a Shift-held drag routes to
       + writeSymmetric       writeSymmetric(raw, drag) instead of
                              write.  Any snapStep the binding
                              declared is skipped in this branch, so
                              a symmetric drag is continuous even if
                              the same field snaps in its other
                              modes.  Releasing Shift mid-drag
                              routes back to `write`, which the
                              binding can use to undo any secondary
                              motion the symmetric branch caused.

   The `drag` object passed to the callbacks carries:

       startX       screen x at mousedown
       startValue   the field's value at mousedown (from read())
       moved        true once the drag has passed the threshold
       ... plus anything the binding's onDragStart added

   The installer never reads the model directly — every binding's
   `read` / `write` is the only place that knows what the field
   means. */

function _installScrubInput(el, opts) {
  let drag = null;

  function onMove(e) {
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    if (!drag.moved) {
      if (Math.abs(dx) < SCRUB_DRAG_THRESHOLD) return;
      drag.moved = true;
    }

    const symmetric = opts.symmetricOnShift && opts.writeSymmetric &&
                      e.shiftKey;

    let raw = drag.startValue + dx * opts.rate;
    /* Snap is only applied outside the symmetric branch: the
       symmetric mode is a continuous coordinated motion, and a snap
       on the mirror angle would also snap the patch's φ through the
       Δ/2 coupling. */
    if (!symmetric && opts.snapStep && e.shiftKey) {
      const delta = raw - drag.startValue;
      const snapped = Math.round(delta / opts.snapStep) * opts.snapStep;
      raw = drag.startValue + snapped;
    }

    if (symmetric) {
      opts.writeSymmetric(raw, drag);
    } else {
      opts.write(raw, drag);
    }
    draw();
  }
  function onUp() {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
    const wasMoved = drag && drag.moved;
    drag = null;
    if (!wasMoved) {
      el.focus();
      el.select();
    }
  }

  el.addEventListener("mousedown", (e) => {
    if (el.disabled) return;
    if (document.activeElement === el) return;
    e.preventDefault();
    drag = {
      startX: e.clientX,
      startValue: opts.read(),
      moved: false,
    };
    if (opts.onDragStart) opts.onDragStart(drag);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  });

  el.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const v = parseFloat(el.value);
      if (isFinite(v)) opts.write(v);
      el.blur();
      draw();
    } else if (e.key === "Escape") {
      e.preventDefault();
      el.blur();
      _syncPatchCoordInputs();
    }
  });

  el.addEventListener("blur", () => {
    const v = parseFloat(el.value);
    if (isFinite(v)) opts.write(v);
    _syncPatchCoordInputs();
    draw();
  });
}

/* ==========================================================================
   SLIDER SYNC
   ========================================================================== */

function syncPanelSliders() {
  const d  = document.getElementById("depthSlider");
  const a  = document.getElementById("angleSlider");
  const r  = document.getElementById("ringsSlider");
  const m  = document.getElementById("meridiansSlider");
  const sd = document.getElementById("shapeDepthSlider");
  if (d)  d.value  = cone.depth;
  if (a)  a.value  = Math.round(cone.halfAngle * 180 / Math.PI);
  if (r)  r.value  = cone.ringCount;
  if (m)  m.value  = cone.meridianCount;
  if (sd) sd.value = SHAPE_DEPTH_CONE;
  updatePanelLabels();
}

function updatePanelLabels() {
  const dv  = document.getElementById("depthVal");
  const av  = document.getElementById("angleVal");
  const rv  = document.getElementById("ringsVal");
  const mv  = document.getElementById("meridiansVal");
  const sdv = document.getElementById("shapeDepthVal");
  if (dv)  dv.textContent  = cone.depth.toFixed(1);
  if (av)  av.textContent  =
    Math.round(cone.halfAngle * 180 / Math.PI) + "\u00B0";
  if (rv)  rv.textContent  = String(cone.ringCount);
  if (mv)  mv.textContent  = String(cone.meridianCount);
  if (sdv) sdv.textContent = SHAPE_DEPTH_CONE.toFixed(2);
}
"""
