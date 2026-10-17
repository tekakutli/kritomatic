"""
pg_panel_bindings.py — every non-scrub panel control binding.

The buttons, the checkboxes, and the two KRA selects.  Buttons are
local-only (they are not registered over the remote API).  The
values that remote clients can read or write are the two UI flags
and the two KRA options:

    ui.overlapHighlight   overlap-marking mode
    ui.drawSegmentMode    draw-segment tool active
    kra.textPosition      label position on the trapezoid
    kra.drawRects         whether filled trapezoids are emitted

Each of them calls `pgkitNotifyLocal("...")` at the end of its
change handler so the wire sees the new value.  The Esc-key path
that cancels Draw Segment mode does the same.  There is no polling
anywhere in the kit; if a value changes and no notify fires, the
wire does not see it.

Buttons whose effect is to change a registered *value* — the pair
list's own controls, the + Pair / + Clone buttons — notify via
`pgkitNotifyAll()` inside their underlying model functions in
`pg_core.py`, not here.
"""

PANEL_BINDINGS_JS = r"""
/* ==========================================================================
   PANEL BINDINGS
   ========================================================================== */

(function installPanel() {

  const addBtn         = document.getElementById("addPairBtn");
  const cloneBtn       = document.getElementById("clonePairBtn");
  const drawSegBtn     = document.getElementById("drawSegmentBtn");
  const toggleFillBtn  = document.getElementById("toggleFillBtn");
  const clearSegBtn    = document.getElementById("clearSegmentsBtn");
  const overlapEl      = document.getElementById("overlapHighlight");
  const saveSceneBtn   = document.getElementById("saveSceneBtn");
  const loadSceneBtn   = document.getElementById("loadSceneBtn");
  const exportBtn      = document.getElementById("exportBtn");
  const generateKraBtn = document.getElementById("generateKraBtn");
  const helpBtn        = document.getElementById("helpBtn");
  const kraTextPosEl   = document.getElementById("kraTextPos");
  const kraDrawRectsEl = document.getElementById("kraDrawRects");

  /* ---- buttons (local only; the notifies live in pg_core.py) ----- */

  if (addBtn)     addBtn.addEventListener("click", addPair);
  if (cloneBtn)   cloneBtn.addEventListener("click", () => {
    if (selectedPair < 0 || selectedPair >= pairs.length) {
      flashStatus("No pair selected", "warn");
      return;
    }
    clonePair(selectedPair);
  });

  if (drawSegBtn) drawSegBtn.addEventListener("click", () => {
    state.drawSegmentMode = !state.drawSegmentMode;
    drawSegBtn.classList.toggle("active", state.drawSegmentMode);
    pgkitNotifyLocal("ui.drawSegmentMode");
    draw();
  });

  if (toggleFillBtn) toggleFillBtn.addEventListener("click", () => {
    if (!selectedSegment) {
      flashStatus("No segment selected", "warn");
      return;
    }
    const p = pairs[selectedSegment.pairIdx];
    if (!p) return;
    const seg = p.segments[selectedSegment.segIdx];
    if (!seg) return;
    seg.filled = !(seg.filled !== false);
    syncPairList();
    draw();
  });

  if (clearSegBtn) clearSegBtn.addEventListener("click", () => {
    if (selectedPair < 0) {
      flashStatus("No pair selected", "warn");
      return;
    }
    pairs[selectedPair].segments.length = 0;
    selectedSegment = null;
    _clearShiftDragSet();
    syncPairList();
    draw();
  });

  if (saveSceneBtn)   saveSceneBtn.addEventListener("click", saveSceneJSON);
  if (loadSceneBtn)   loadSceneBtn.addEventListener("click", promptLoadScene);
  if (exportBtn)      exportBtn.addEventListener("click",
                                                 exportVisualStateJSON);
  if (generateKraBtn) generateKraBtn.addEventListener("click",
                                                 generateKraFromScene);

  /* ---- overlap toggle ------------------------------------------- */

  if (overlapEl) overlapEl.addEventListener("change", () => {
    state.overlapHighlight = !!overlapEl.checked;
    pgkitNotifyLocal("ui.overlapHighlight");
    syncPairList();
    draw();
  });

  /* ---- KRA option controls -------------------------------------- */

  if (kraTextPosEl) {
    kraTextPosEl.addEventListener("change", () => {
      pgkitNotifyLocal("kra.textPosition");
    });
  }
  if (kraDrawRectsEl) {
    kraDrawRectsEl.addEventListener("change", () => {
      pgkitNotifyLocal("kra.drawRects");
    });
  }

  /* ---- help ----------------------------------------------------- */

  if (helpBtn) helpBtn.addEventListener("click", () => {
    alert(
      "Pairs — help\n\n"
    + "Each pair is two parallel lines.  Click a line or a trapezoid\n"
    + "to select that pair; drag anywhere on the canvas to move the\n"
    + "whole pair.\n\n"
    + "The panel's fields control the pair: inclination, the two\n"
    + "metrics, offset, gap, whole-pair scale, and loop gap.\n\n"
    + "SHIFT-DRAG SET — hold Shift and click segment bodies in the\n"
    + "segment view to build a persistent set; shift-drag any member\n"
    + "to move every member together.  Releasing Shift keeps the set;\n"
    + "a plain (non-shift) canvas click clears it.\n\n"
    + "OVERLAP MODE — the Overlap checkbox highlights segments whose\n"
    + "trapezoids overlap a trapezoid on another pair.  While on, a\n"
    + "make-room button appears on any segment row that overlaps a\n"
    + "segment on the pair below.\n\n"
    + "REMOTE API — every value field in this panel is also\n"
    + "published at /remote.  When a remote client connects, the\n"
    + "panel hides and a 'Controlled remotely' banner appears."
    );
  });

  /* ---- keydown -------------------------------------------------- */

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

    if (e.key === "Escape" && state.drawSegmentMode) {
      e.preventDefault();
      state.drawSegmentMode = false;
      if (drawSegBtn) drawSegBtn.classList.remove("active");
      pgkitNotifyLocal("ui.drawSegmentMode");
      draw();
      return;
    }

    if (e.altKey && !e.ctrlKey && !e.metaKey) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (selectedPair > 0) movePair(selectedPair, -1);
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        if (selectedPair >= 0 && selectedPair < pairs.length - 1) {
          movePair(selectedPair, +1);
        }
        return;
      }
    }
  });

  /* ---- remote registrations -------------------------------------
     The scrub inputs are registered in pg_panel_scrubs.py.  These
     are the remaining value fields: two UI flags, two KRA options. */

  pgkitRegisterField("ui.overlapHighlight", {
    get: () => !!state.overlapHighlight,
    set: (v) => {
      state.overlapHighlight = !!v;
      if (overlapEl) overlapEl.checked = !!v;
      syncPairList();
      draw();
    },
    meta: { kind: "bool", label: "Overlap" },
  });

  pgkitRegisterField("ui.drawSegmentMode", {
    get: () => !!state.drawSegmentMode,
    set: (v) => {
      state.drawSegmentMode = !!v;
      if (drawSegBtn) {
        drawSegBtn.classList.toggle("active", state.drawSegmentMode);
      }
      draw();
    },
    meta: { kind: "bool", label: "Draw Segment" },
  });

  pgkitRegisterField("kra.textPosition", {
    get: () => kraTextPosEl ? kraTextPosEl.value : "center",
    set: (v) => {
      if (kraTextPosEl && typeof v === "string") kraTextPosEl.value = v;
    },
    meta: {
      kind: "enum",
      label: "KRA text position",
      options: [
        "center", "tl", "tc", "tr", "ml", "mr",
        "bl", "bc", "br", "top", "bottom", "left", "right",
      ],
    },
  });

  pgkitRegisterField("kra.drawRects", {
    get: () => kraDrawRectsEl ? !!kraDrawRectsEl.checked : true,
    set: (v) => {
      if (kraDrawRectsEl) kraDrawRectsEl.checked = !!v;
    },
    meta: { kind: "bool", label: "KRA rectangles" },
  });

})();
"""
