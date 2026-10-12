"""
pg_panel_bindings.py — every non-scrub control binding.

One IIFE, and inside it one addEventListener per control that is NOT
a scrub input:

    the five graphics sliders (depth, half-angle, rings,
    meridians, shape depth)

    the four checkboxes (mirror, mirror flip, visual bottom,
    draw no-rotate — the last of which only flashes a status)

    the thirteen buttons (help, collapse handled in pg_base's BOOT_JS,
    reset, center, addQuad, cloneQuad, addSquare, cloneSquare,
    export, generateKra, saveScene, loadScene, drawQuad)

    the draw-fit select (which only flashes a status on change —
    the value is read at draw-finish time)

    the two keyboard shortcuts not owned by the canvas (Escape
    cancels an in-progress draw; Shift+A aligns the selected shape
    to the 45° grid; Delete removes the selected shape).

This is the file to edit when a control is added, removed, or its
action changes.  Scrub fields live in pg_panel_scrubs.py; the
canvas's mouse and wheel handlers live in pg_dispatch.py.

GENERATE .KRA
=============
The "Generate .kra" button walks floatSquares, packages each shape
as a flat-view item, and POSTs the list to /generate-kra on the
local server.  The server forwards to cone_kra.py, which builds a
Kritomatic batch and sends it to the Krita daemon.  The output is a
.kra with one vector-text layer per shape.

CORNER RESIZE
=============
By default a corner drag anchors the DIAGONALLY OPPOSITE corner and
resizes only along the two edges that meet at the dragged corner.
Holding Shift switches to a centred resize: the shape resizes about
its own centre, moving all four sides symmetrically.  The behaviour
itself lives in pg_view_squares.py; the drag-state anchor is
captured in pg_dispatch.py at mousedown time.  The help popup's
"Squares" section documents both modes.
"""

PANEL_BINDINGS_JS = r"""
/* ==========================================================================
   PANEL BINDINGS
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
  const mirrorEl       = document.getElementById("patchMirror");

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

  /* ---- patch mirror toggle --------------------------------------
     When checked, every square on the selected patch is also drawn
     on the patch's mirror — the same patch shifted by q.mirrorAngle
     in φ, i.e. on the opposite side of the cone.  The mirror is a
     visual clone, not an independent object; it is not hit-tested
     and it does not appear in the square list.  The checkbox's own
     state is kept in sync with the selected patch by
     _syncPatchCoordInputs, called from draw() and syncQuadList(). */
  if (mirrorEl) {
    mirrorEl.addEventListener("change", () => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      quads[selectedQuad].mirror = mirrorEl.checked;
      draw();
      flashStatus(mirrorEl.checked
                    ? "Mirror on for " + quads[selectedQuad].name
                    : "Mirror off",
                  "ok");
    });
  }

  /* ---- mirror flip toggle ---------------------------------------
     When checked (the default), the shape's θ is negated for the
     duration of the mirror draw.  The φ-shift of the mirror rotates
     the patch's local U/V frame by the same angle, which would
     otherwise render a tilted, rotated, or anchored shape 180°-
     rotated relative to the original.  See pg_panel_fields.py,
     MIRROR, MIRROR ANGLE, MIRROR FLIP. */
  const mirrorFlipEl = document.getElementById("patchMirrorFlip");
  if (mirrorFlipEl) {
    mirrorFlipEl.addEventListener("change", () => {
      if (selectedQuad < 0 || selectedQuad >= quads.length) return;
      quads[selectedQuad].mirrorFlip = mirrorFlipEl.checked;
      draw();
      flashStatus(mirrorFlipEl.checked
                    ? "Mirror flip on for " + quads[selectedQuad].name
                    : "Mirror flip off",
                  "ok");
    });
  }

  /* ---- per-square visual-bottom toggle --------------------------
     A per-square meta option: when checked, the shape's KRA text
     anchor and its four corner senses are read from the visual
     bottom of the scene (the edge of the drawn square whose
     midpoint sits lowest on screen) instead of from the shape's own
     longest edge.  See the VISUAL BOTTOM section in
     pg_panel_fields.py.  The checkbox's own state follows the
     selected square, kept in sync by _syncSquareSizeInputs. */
  const visualBottomEl = document.getElementById("squareVisualBottom");
  if (visualBottomEl) {
    visualBottomEl.addEventListener("change", () => {
      if (selectedSquare < 0 || selectedSquare >= floatSquares.length) return;
      const sq = floatSquares[selectedSquare];
      sq.visualBottom = visualBottomEl.checked;
      draw();
      flashStatus(visualBottomEl.checked
                    ? "Visual bottom on for " + squareDisplayName(sq)
                    : "Visual bottom off",
                  "ok");
    });
  }

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
