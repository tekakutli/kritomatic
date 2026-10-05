"""
pg_panel.py — panel bindings.

Five graphics sliders, two scrub-inputs for the selected patch's
position, six buttons (Save scene, Load scene, Reset, Center apex,
Export visual state, + Patch, + Square — seven), two lists, a
persistent hint block.

Save scene writes the whole editable workspace — cone state, patch
and shape data, id counters — to a JSON file.  Load scene opens a
file picker and applies the file in place.  Both buttons delegate to
pg_scene.py.

The scrub-input mechanics, patch-position setters, and list sync are
unchanged from the previous revision; see that module for the details.

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
   PATCH-POSITION FIELDS
   ========================================================================== */

function _syncPatchCoordInputs() {
  const phiInput = document.getElementById("patchPhiVal");
  const sInput   = document.getElementById("patchSVal");
  if (!phiInput || !sInput) return;

  const q = (selectedQuad >= 0 && selectedQuad < quads.length)
    ? quads[selectedQuad] : null;

  if (!q) {
    phiInput.disabled = true;
    sInput.disabled   = true;
    if (document.activeElement !== phiInput) phiInput.value = "\u2014";
    if (document.activeElement !== sInput)   sInput.value   = "\u2014";
    return;
  }

  phiInput.disabled = false;
  sInput.disabled   = false;

  /* φ is displayed in normalized units on [-1, 1] (see the module
     docstring); s is displayed as a fraction of the axial height,
     unchanged. */
  if (document.activeElement !== phiInput) {
    phiInput.value = _phiToDisplay(_patchPhiCenter(q)).toFixed(3);
  }
  if (document.activeElement !== sInput) {
    sInput.value = _patchSCenter(q).toFixed(3);
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

  const resetBtn     = document.getElementById("resetBtn");
  const centerBtn    = document.getElementById("centerBtn");
  const addBtn       = document.getElementById("addQuadBtn");
  const addSqBtn     = document.getElementById("addSquareBtn");
  const exportBtn    = document.getElementById("exportBtn");
  const saveSceneBtn = document.getElementById("saveSceneBtn");
  const loadSceneBtn = document.getElementById("loadSceneBtn");

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

  /* ---- buttons and lists ----------------------------------------- */

  if (resetBtn)     resetBtn.addEventListener("click", resetView);
  if (centerBtn)    centerBtn.addEventListener("click", () => {
    cone.ax = 0;
    cone.ay = 0;
    draw();
  });
  if (addBtn)       addBtn.addEventListener("click", addQuad);
  if (addSqBtn)     addSqBtn.addEventListener("click", () => {
    if (selectedQuad < 0 || selectedQuad >= quads.length) return;
    addSquareAtCenter(selectedQuad);
  });
  if (exportBtn)    exportBtn.addEventListener("click",
                                               exportVisualStateJSON);
  if (saveSceneBtn) saveSceneBtn.addEventListener("click", saveSceneJSON);
  if (loadSceneBtn) loadSceneBtn.addEventListener("click", promptLoadScene);

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

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
