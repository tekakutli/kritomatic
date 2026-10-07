"""
pg_panel.py — panel bindings.

Five graphics sliders, five scrub-inputs (the selected patch's φ and
s, the selected square's width, height, and slope), ten buttons
(Save scene, Load scene, Reset, Center apex, Export visual state,
Generate .kra, + Patch, + Clone, + Square, + Clone), two lists, a
persistent hint block.

Save scene writes the whole editable workspace — cone state, patch
and shape data, id counters — to a JSON file.  Load scene opens a
file picker and applies the file in place.  Both buttons delegate to
pg_scene.py.

Generate .kra POSTs the current scene's shapes to the local HTTP
server, which forwards them to the Kritomatic daemon as a batch of
vector-text creation commands.  See cone_kra.py and pg_kra.py.

The scrub-input mechanics, patch-position setters, and list sync are
unchanged from the previous revision; see that module for the details.

DRAW TOOL AND HELP POPUP
========================
Two panel additions, replacing the persistent inline hint block the
panel used to carry:

    Draw Quad    toggles the four-corner fitting tool.  A "Draw fit"
                 select next to it chooses the fit mode, read at
                 finish time so it can be changed mid-draw.  The
                 default is "shape → patch": the shape is fitted
                 first, and a default-sized patch is centred on its
                 footprint.  A "No rotate" checkbox, also read at
                 finish time, constrains the fitted shape's
                 rotation to zero — its edges align with the flat
                 view's φ and s axes, reading as north/south aligned
                 on the unfolded sheet.  Escape cancels an
                 in-progress draw.  The tool's state and both
                 fitting modes live in pg_core.py; its event
                 interception lives in pg_dispatch.py.

    ? (help)     opens a separate popup subwindow containing the
                 reference card.  The window is created once and
                 reused on subsequent clicks: it is named, so
                 window.open returns the same browsing context if
                 it is still open, and its content is re-written
                 each time so a page reload of the main view does
                 not leave a stale help window behind.  If the
                 popup is blocked, a status flash says so.

SHAPE DEPTH
===========
The "Shape depth" slider drives SHAPE_DEPTH_CONE only — the cone
view's perspective projection.  The flat (unfolded-cone) view reads
SHAPE_DEPTH_FLAT, a fixed constant, so its square footprint is
independent of the slider.  See the note in pg_view_squares.py.

PHI DISPLAY
===========
The "Patch φ" scrub input displays the selected patch's angular
centre in DISPLAY UNITS, not raw radians.  The range is [-1, 1] and
the two endpoints identify — -1 and +1 are the same physical
direction, reached from opposite sides.

    0     the visually-bottom direction of the cone view
          (world φ = -π/2)
    +1     the visually-top direction (world φ = +π/2), reached
          by going clockwise on screen (bottom → right → top)
    -1     the same top direction, reached counterclockwise
          (bottom → left → top)

Increasing values run through the right side of the cone view
(bottom → right → top → left → bottom); decreasing values run
through the left.  On the cone view that is clockwise for increasing
display, since φ_world also increases counterclockwise.

The inverse mapping turns a typed or dragged display value back into
a world angle before it reaches _setPatchPhiCenter, so the rest of
the codebase keeps working in radians.  The scene file and the
visual-state export both store the raw radian values; only the panel
speaks display units.

PATCH S BOUNDS
==============
The "Patch s" scrub input is the patch's axial centre, where s = 0
sits at the base ring and s = 1 at the apex.

Both bounds are inset by halfSpan:  the interval cannot slide so far
that either edge leaves [FLAT_S_MIN, FLAT_S_MAX].  For a default
patch (span 0.50, halfSpan 0.25) the centre is confined to
[0.25, 0.75] — bottom edge at the base on the low end, top edge at
the apex on the high end.

SQUARE W / H
============
The "Square W" and "Square H" scrub inputs show the selected shape's
intrinsic size in REFERENCE units, independent of the patch it sits
on and of the cone's current depth or half-angle:

    W = SHAPE_REL_SIZE · scaleU
    H = SHAPE_REL_SIZE · scaleV

The shape's ACTUAL on-patch world size is this times the patch's
per-view aspect scale (Ku or Kv, which track the patch's own
uLen / vLen — see pg_view_squares.py).  That is what makes a shape
cover a fixed fraction of its patch, so patches and their shapes
scale together whenever the cone depth changes.  The fields report
the invariant reference value so the displayed number does not
jump around as you slide the depth or half-angle sliders.

Scrub rate is expressed in reference units per pixel of horizontal
drag, so a 100-pixel drag changes W or H by 0.5 at the default rate.
The snap step with Shift held is 0.25 reference units.  Typed values
are clamped to [SHAPE_MIN_SCALE · SHAPE_REL_SIZE, SHAPE_MAX_SCALE ·
SHAPE_REL_SIZE] by the setters, which delegate to the same scale
bounds the corner-drag resize uses.

SLOPE
=====
The "Slope" scrub input shows the selected shape's pseudo-3D tilt
about its own reference U axis, in degrees.  Zero leaves the shape
flat on the patch (the historic behaviour); a positive value tips
the +v edge — the edge that points toward the apex — into the
cone's cavity, so the shape reads as dipping forward off the plane.
Negative values tip the −v edge instead.

The rate is 0.5° per pixel of horizontal drag; Shift snaps to 15°
increments, matching one meridian.  The stored value is in radians
(the model's convention); the panel converts to and from degrees at
the field boundary.  Values are clamped to ±85°.

The flat view ignores slope — its footprint is the un-tilted (φ, s)
projection — so editing in that band stays exact.  Only the cone
view reads slope.  See the SLOPE section in pg_view_squares.py.

CLONING SQUARES
===============
"+ Clone" in the Squares row duplicates the selected square at the
EXACT same location as its source: same patch (quadId), same
normalized (u, v), same scaleU / scaleV, same theta, same slope.
The clone overlaps its source pixel-for-pixel until it is dragged
away.  The clone receives a fresh id and default name and becomes
the new selection.

GENERATE .KRA
=============
The "Generate .kra" button walks floatSquares, packages each shape
as a flat-view item, and POSTs the list to /generate-kra on the
local server.  The server forwards to cone_kra.py, which builds a
Kritomatic batch and sends it to the Krita daemon.  The output is a
.kra with one vector-text layer per shape.

SHIFT-HELD CORNER RESIZE
========================
Holding Shift while dragging a shape's corner vertex anchors the
DIAGONALLY OPPOSITE corner and resizes only along the two edges
that meet at the dragged corner.  Without Shift, the corner drag
resizes about the shape's centre, moving all four sides
symmetrically.  The behaviour itself lives in pg_view_squares.py;
the drag-state anchor is captured in pg_dispatch.py at mousedown
time.  The help popup's "Squares" section documents this modifier.
"""

PANEL_JS = r"""
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

/* Slope scrub rate: 0.5° per pixel of horizontal drag, so a
   100-px drag swings the shape 50°.  Snap step with Shift held is
   one meridian (15°).  Values are stored in radians in the model
   and shown in degrees in the field. */
const SLOPE_SCRUB_RATE_DEG_PER_PX = 0.5;
const SLOPE_SNAP_STEP_DEG         = 15;

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
   HELP POPUP SUBWINDOW
   ==========================================================================
   The help card is not part of the main document.  It lives as a
   string constant here and is written into a separate popup window
   when the "?" button is clicked.

   The window is created with a stable name ("cone_help"), so a
   second click reuses the existing popup instead of spawning another
   one.  Its content is (re)written on every open, which means:

       - the popup always shows the current help card (no stale
         copy lingering if the main page was reloaded with an older
         script), and
       - if the user closed the popup, clicking "?" reopens it.

   The popup window is a plain HTML document styled to match the
   panel: same dark palette, same monospaced face, same cyan accent.
   It carries no scripts, so it cannot affect the main view.

   If the browser blocks the popup (some do, by default, for
   window.open calls not tied to a user gesture — this one is), the
   failure is reported through flashStatus rather than silently
   swallowed. */

const HELP_HTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Cone interior \u2014 help</title>
<style>
  html, body {
    margin:0; padding:0;
    background:#0b1018; color:#a8b5c4;
    font-family: 'JetBrains Mono', 'Fira Code', 'Consolas',
                 'SF Mono', monospace;
    font-size:12px;
  }
  body { padding:20px 24px 28px; }

  h1 {
    display:flex; align-items:center;
    margin:0 0 18px; padding:0 0 12px;
    border-bottom:1px solid #1e2836;
    font-size:11px; font-weight:700;
    letter-spacing:0.16em; text-transform:uppercase;
    color:#dce6f2;
  }

  .section {
    margin:18px 0 6px;
    font-size:10px; font-weight:700;
    letter-spacing:0.14em; text-transform:uppercase;
    color:#00e5ff;
  }
  .section:first-of-type { margin-top:0; }

  .hint {
    padding:5px 0 5px 12px;
    color:#8a97a6;
    letter-spacing:0.03em;
    line-height:1.6;
    border-left:2px solid #1a2433;
    margin:2px 0;
  }
  .hint strong {
    color:#dce6f2; font-weight:700;
  }
  .hint em {
    color:#ffc966; font-style:normal;
    letter-spacing:0.04em;
  }

  .kbd {
    display:inline-block;
    padding:1px 6px;
    border:1px solid #223040;
    border-radius:2px;
    background:#0e1622;
    color:#00e5ff;
    font-family:inherit;
    font-weight:700;
    letter-spacing:0.02em;
    font-size:11px;
    margin:0 1px;
  }
</style>
</head>
<body>
<h1>Cone interior \u2014 help</h1>

<div class="section">View</div>
<div class="hint">drag the <strong>apex</strong> in the upper band to tilt the cone</div>
<div class="hint">scroll in the upper band to change <strong>depth</strong></div>
<div class="hint"><span class="kbd">H</span> toggles the side panel</div>

<div class="section">Patches</div>
<div class="hint"><strong>shift</strong>+click a patch to drop a square on it</div>
<div class="hint">drag a patch in either band to move it</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging a patch or a
  square to slide along the <em>centre&rarr;apex</em> axis only</div>

<div class="section">Squares</div>
<div class="hint">hold <span class="kbd">Alt</span> while clicking a square to grab
  its <strong>patch</strong> instead</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging a
  <strong>corner</strong> to anchor the <em>opposite corner</em> in place \u2014
  only the two edges that meet at that corner move</div>
<div class="hint">hold <span class="kbd">Shift</span> while dragging the
  rotate handle to jump in 15&deg; steps</div>
<div class="hint"><span class="kbd">Shift</span>+<span class="kbd">A</span>
  aligns the selected square to 45&deg;</div>
<div class="hint"><span class="kbd">Delete</span> removes the selected square</div>

<div class="section">Panel fields</div>
<div class="hint">drag a value sideways to <strong>scrub</strong> it;
  hold <span class="kbd">Shift</span> to snap</div>
<div class="hint">fields with <em>&phi;</em>, <em>s</em>, <em>W</em>, <em>H</em>,
  and <em>slope</em> follow the current selection</div>

<div class="section">Draw Quad \u2014 two fit modes</div>
<div class="hint">click four corners in <strong>one band</strong> to fit a patch
  and a rectangle to them</div>
<div class="hint">all four points must be in the same band
  (upper cone, or lower flat)</div>
<div class="hint"><span class="kbd">Esc</span> cancels an in-progress draw</div>
<div class="hint"><strong>shape &rarr; patch</strong> (default): the shape is
  fitted first, then a patch of the <em>same size</em> as the one created
  by <em>+ Patch</em> is centred on the shape's footprint</div>
<div class="hint"><strong>patch &rarr; shape</strong>: the four clicks' centroid
  defines the patch's centre; a patch of the <em>same size</em> as <em>+ Patch</em>
  is created there, and the shape is fitted inside it</div>
<div class="hint"><strong>No rotate</strong>: when checked, the fitted shape's
  rotation is locked to zero \u2014 its edges align with the flat view's
  &phi; and s axes, so it reads as <em>north/south aligned</em> on the
  unfolded sheet.  Applies to both fit modes.</div>

<div class="section">Export</div>
<div class="hint"><strong>Export visual state</strong> writes a JSON dump of
  everything on screen</div>
<div class="hint"><strong>Generate .kra</strong> writes a Krita document
  with one vector-text layer per square</div>
<div class="hint"><strong>Save scene</strong> / <strong>Load scene</strong>
  round-trip the whole workspace as JSON</div>

</body>
</html>`;

let _helpWindow = null;

function openHelpWindow() {
  let win = null;
  try {
    win = window.open(
      "",
      "cone_help",
      "width=560,height=680," +
      "menubar=no,toolbar=no,location=no,status=no," +
      "resizable=yes,scrollbars=yes"
    );
  } catch (e) {
    win = null;
  }

  if (!win) {
    if (typeof flashStatus === "function") {
      flashStatus("Popup blocked \u2014 allow popups to see help", "warn");
    }
    return;
  }

  /* Write the card every time, so a stale help window from a prior
     page load is refreshed rather than reused verbatim. */
  try {
    win.document.open();
    win.document.write(HELP_HTML);
    win.document.close();
  } catch (e) {
    /* Some browsers refuse document.write on a cross-origin or
       already-closed window.  Fall back to a same-origin navigation
       of the popup to a data URL carrying the same content. */
    try {
      win.location.href =
        "data:text/html;charset=utf-8," + encodeURIComponent(HELP_HTML);
    } catch (e2) {
      if (typeof flashStatus === "function") {
        flashStatus("Could not open help window", "bad");
      }
      return;
    }
  }

  /* Bring it to the front if it was already open behind the main
     window. */
  try { win.focus(); } catch (e) {}

  _helpWindow = win;
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
   squareWorldWidth / squareWorldHeight — which now return
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

  _syncSquareSizeInputs();
}

/* ==========================================================================
   SQUARE-SIZE FIELDS
   ==========================================================================
   The W / H fields show the shape's reference-unit size, which is
   independent of the patch it sits on and of the cone's depth /
   half-angle.  The Slope field shows the shape's pseudo-3D tilt in
   degrees.  All three are disabled until a square is selected. */

function _syncSquareSizeInputs() {
  const wInput = document.getElementById("squareWVal");
  const hInput = document.getElementById("squareHVal");
  const sInput = document.getElementById("squareSlopeVal");
  if (!wInput || !hInput || !sInput) return;

  const sq = (selectedSquare >= 0 && selectedSquare < floatSquares.length)
    ? floatSquares[selectedSquare] : null;

  if (!sq) {
    wInput.disabled = true;
    hInput.disabled = true;
    sInput.disabled = true;
    if (document.activeElement !== wInput) wInput.value = "\u2014";
    if (document.activeElement !== hInput) hInput.value = "\u2014";
    if (document.activeElement !== sInput) sInput.value = "\u2014";
    return;
  }

  wInput.disabled = false;
  hInput.disabled = false;
  sInput.disabled = false;

  if (document.activeElement !== wInput) {
    wInput.value = squareWorldWidth(sq).toFixed(3);
  }
  if (document.activeElement !== hInput) {
    hInput.value = squareWorldHeight(sq).toFixed(3);
  }
  if (document.activeElement !== sInput) {
    sInput.value = ((sq.slope || 0) * 180 / Math.PI).toFixed(1);
  }
}

/* ==========================================================================
   SCRUB-INPUT INSTALLER
   ========================================================================== */

function _installScrubInput(el, opts) {
  let drag = null;

  function onMove(e) {
    if (!drag) return;
    const dx = e.clientX - drag.startX;
    if (!drag.moved) {
      if (Math.abs(dx) < SCRUB_DRAG_THRESHOLD) return;
      drag.moved = true;
    }
    let raw = drag.startValue + dx * opts.rate;
    if (opts.snapStep && e.shiftKey) {
      const delta = raw - drag.startValue;
      const snapped = Math.round(delta / opts.snapStep) * opts.snapStep;
      raw = drag.startValue + snapped;
    }
    opts.write(raw);
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
   PATCH LIST
   ========================================================================== */

function syncQuadList() {
  syncSquareList();
  _syncPatchCoordInputs();

  const list = document.getElementById("quadList");
  if (!list) return;
  list.innerHTML = "";
  if (quads.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no patches yet";
    list.appendChild(e);
    return;
  }
  for (let i = 0; i < quads.length; i++) {
    const q   = quads[i];
    const hue = patchHue(q);
    const isSel = (i === selectedQuad);
    const row = document.createElement("div");
    row.className = "quadRow" + (isSel ? " selected" : "");
    row.dataset.idx = String(i);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    sw.style.background  = _huergb(hue, isSel ? 0.95 : 0.55);
    sw.style.borderColor = _huergb(hue, isSel ? 1.00 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text";
    lb.className = "quadLabelInput";
    lb.value = q.name;
    lb.spellcheck = false;
    const startName = q.name;
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => {
      q.name = lb.value;
      draw();
    });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") {
        q.name = startName;
        lb.value = startName;
        lb.blur();
        draw();
      }
    });
    row.appendChild(lb);

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete patch";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteQuad(i);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectedQuad = i;
      syncQuadList();
      draw();
    });

    list.appendChild(row);
  }
}

/* ==========================================================================
   SQUARE LIST
   ========================================================================== */

function syncSquareList() {
  const list = document.getElementById("squareList");
  if (!list) return;
  list.innerHTML = "";
  if (selectedQuad < 0 || selectedQuad >= quads.length) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "select a patch";
    list.appendChild(e);
    return;
  }
  const activeQ = quads[selectedQuad];
  const activeHue = patchHue(activeQ);
  const rows = [];
  for (let i = 0; i < floatSquares.length; i++) {
    if (floatSquares[i].quadId === activeQ.id) {
      rows.push({ sq: floatSquares[i], idx: i });
    }
  }
  if (rows.length === 0) {
    const e = document.createElement("div");
    e.className = "empty";
    e.textContent = "no squares on " + activeQ.name + " yet";
    list.appendChild(e);
    return;
  }
  for (const { sq, idx } of rows) {
    const isSel = (idx === selectedSquare);
    const row = document.createElement("div");
    row.className = "quadRow squareRow" + (isSel ? " selected" : "");
    row.dataset.idx = String(idx);

    const sw = document.createElement("span");
    sw.className = "quadSwatch";
    sw.style.background  = _huergb(activeHue, isSel ? 0.95 : 0.55);
    sw.style.borderColor = _huergb(activeHue, isSel ? 1.00 : 0.85);
    row.appendChild(sw);

    const lb = document.createElement("input");
    lb.type = "text";
    lb.className = "quadLabelInput";
    lb.value = squareDisplayName(sq);
    lb.spellcheck = false;
    const startName = squareDisplayName(sq);
    lb.addEventListener("mousedown", (ev) => ev.stopPropagation());
    lb.addEventListener("click",     (ev) => ev.stopPropagation());
    lb.addEventListener("input", () => {
      sq.name = lb.value;
      draw();
    });
    lb.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); lb.blur(); }
      if (ev.key === "Escape") {
        sq.name = startName;
        lb.value = startName;
        lb.blur();
        draw();
      }
    });
    row.appendChild(lb);

    const del = document.createElement("button");
    del.className = "quadDel";
    del.textContent = "\u00D7";
    del.title = "Delete square";
    del.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteSquare(idx);
    });
    row.appendChild(del);

    row.addEventListener("click", () => {
      selectedSquare = idx;
      syncQuadList();
      draw();
    });

    list.appendChild(row);
  }
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
   BINDINGS
   ========================================================================== */

(function installPanel() {
  const d  = document.getElementById("depthSlider");
  const a  = document.getElementById("angleSlider");
  const r  = document.getElementById("ringsSlider");
  const m  = document.getElementById("meridiansSlider");
  const sd = document.getElementById("shapeDepthSlider");

  const resetBtn       = document.getElementById("resetBtn");
  const centerBtn      = document.getElementById("centerBtn");
  const addBtn         = document.getElementById("addQuadBtn");
  const cloneBtn       = document.getElementById("cloneQuadBtn");
  const addSqBtn       = document.getElementById("addSquareBtn");
  const cloneSqBtn     = document.getElementById("cloneSquareBtn");
  const exportBtn      = document.getElementById("exportBtn");
  const generateKraBtn = document.getElementById("generateKraBtn");
  const saveSceneBtn   = document.getElementById("saveSceneBtn");
  const loadSceneBtn   = document.getElementById("loadSceneBtn");
  const drawQuadBtn    = document.getElementById("drawQuadBtn");
  const drawFitModeEl  = document.getElementById("drawFitMode");
  const drawNoRotateEl = document.getElementById("drawNoRotate");
  const helpBtn        = document.getElementById("helpBtn");

  if (d) d.addEventListener("input", () => {
    cone.depth = parseFloat(d.value);
    clampApex();
    updatePanelLabels();
    draw();
  });
  if (a) a.addEventListener("input", () => {
    cone.halfAngle = parseFloat(a.value) * Math.PI / 180;
    clampApex();
    updatePanelLabels();
    draw();
  });
  if (r) r.addEventListener("input", () => {
    cone.ringCount = parseInt(r.value, 10);
    updatePanelLabels();
    draw();
  });
  if (m) m.addEventListener("input", () => {
    cone.meridianCount = parseInt(m.value, 10);
    updatePanelLabels();
    draw();
  });
  if (sd) sd.addEventListener("input", () => {
    SHAPE_DEPTH_CONE = parseFloat(sd.value);
    updatePanelLabels();
    draw();
  });

  /* ---- patch-position scrub inputs -------------------------------
     The φ field reads and writes in display units (see the module
     docstring); the s field reads and writes in fraction-of-height
     units, unchanged. */

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

  /* ---- square-size scrub inputs ---------------------------------
     The two fields read and write the shape's reference-unit size,
     independent of the patch it sits on (see the module
     docstring).  Both are disabled until a square is selected. */

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
     The field reads and writes the shape's pseudo-3D tilt.  The
     model speaks radians; the field speaks degrees (see the SLOPE
     note in the module docstring).  The rate and snap step are
     converted from degrees to radians here so the installer's
     radian-per-pixel contract is honoured. */

  const slopeInput = document.getElementById("squareSlopeVal");

  if (slopeInput) _installScrubInput(slopeInput, {
    rate: SLOPE_SCRUB_RATE_DEG_PER_PX * Math.PI / 180,
    snapStep: SLOPE_SNAP_STEP_DEG * Math.PI / 180,
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

  /* ---- draw tool + help popup -----------------------------------
     Draw Quad toggles the fitting tool (state and fitting logic in
     pg_core.py; event interception in pg_dispatch.py).  The "?"
     button opens the help card in a separate named popup window.
     Both the fit-mode select and the "No rotate" checkbox are read
     at finish time, so changing either mid-draw affects the next
     completed draw. */

  if (drawQuadBtn) drawQuadBtn.addEventListener("click", toggleDrawTool);
  if (helpBtn)     helpBtn.addEventListener("click", openHelpWindow);

  if (drawFitModeEl) {
    drawFitModeEl.addEventListener("change", () => {
      const labels = {
        "shape-first": "shape \u2192 patch",
        "patch-first": "patch \u2192 shape",
      };
      flashStatus("Draw fit: "
                  + (labels[drawFitModeEl.value] || drawFitModeEl.value),
                  "ok");
    });
  }

  if (drawNoRotateEl) {
    drawNoRotateEl.addEventListener("change", () => {
      flashStatus("Rotation: "
                  + (drawNoRotateEl.checked
                       ? "locked to 0 \u00B7 axis-aligned"
                       : "free"),
                  "ok");
    });
  }

  /* ---- buttons and lists ----------------------------------------- */

  if (resetBtn)       resetBtn.addEventListener("click", resetView);
  if (centerBtn)      centerBtn.addEventListener("click", () => {
    cone.ax = 0;
    cone.ay = 0;
    draw();
  });
  if (addBtn)         addBtn.addEventListener("click", addQuad);
  if (cloneBtn)       cloneBtn.addEventListener("click", () => {
    if (selectedQuad < 0 || selectedQuad >= quads.length) {
      flashStatus("No patch selected", "warn");
      return;
    }
    cloneQuad(selectedQuad);
  });
  if (addSqBtn)       addSqBtn.addEventListener("click", () => {
    if (selectedQuad < 0 || selectedQuad >= quads.length) return;
    addSquareAtCenter(selectedQuad);
  });
  if (cloneSqBtn)     cloneSqBtn.addEventListener("click", () => {
    if (selectedSquare < 0 || selectedSquare >= floatSquares.length) {
      flashStatus("No square selected", "warn");
      return;
    }
    cloneSquare(selectedSquare);
  });
  if (exportBtn)      exportBtn.addEventListener("click",
                                                 exportVisualStateJSON);
  if (generateKraBtn) generateKraBtn.addEventListener("click",
                                                 generateKraFromScene);
  if (saveSceneBtn)   saveSceneBtn.addEventListener("click", saveSceneJSON);
  if (loadSceneBtn)   loadSceneBtn.addEventListener("click", promptLoadScene);

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

    /* Escape cancels an in-progress draw before anything else. */
    if (e.key === "Escape" && drawTool.active) {
      e.preventDefault();
      cancelDrawTool();
      return;
    }

    const shiftOnly = e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey;
    if (shiftOnly) {
      const k = e.key.toLowerCase();
      if (k === "a") {
        e.preventDefault();
        if (alignSelectedShapeToAxis()) {
          flashStatus("Aligned to axis", "ok");
        }
        return;
      }
    }

    if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedSquare >= 0) {
        e.preventDefault();
        deleteSquare(selectedSquare);
      }
    }
  });
})();
"""
