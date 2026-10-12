"""
pg_panel_bindings.py — every non-scrub panel control binding.

One IIFE, and inside it one addEventListener per control that is
NOT a scrub input:

    the pair buttons       + Pair, + Clone

    the segment buttons    Draw Segment (toggle), Toggle Fill,
                           Clear Segments.

    the overlap toggle     "Overlap" checkbox.

    the export buttons     Save scene, Load scene, Export visual
                           state.

    the KRA button         Generate .kra.

    the help button        "?"

    the keydown handler    three shortcuts, all installed on
                           `window`:
                             · Escape cancels Draw Segment mode;
                             · Alt+↑ / Alt+↓ move the selected pair
                               in the hierarchy.
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

  /* ---- buttons -------------------------------------------------- */

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

  if (overlapEl) overlapEl.addEventListener("change", () => {
    state.overlapHighlight = !!overlapEl.checked;
    syncPairList();
    draw();
  });

  if (saveSceneBtn)   saveSceneBtn.addEventListener("click", saveSceneJSON);
  if (loadSceneBtn)   loadSceneBtn.addEventListener("click", promptLoadScene);
  if (exportBtn)      exportBtn.addEventListener("click",
                                                 exportVisualStateJSON);
  if (generateKraBtn) generateKraBtn.addEventListener("click",
                                                 generateKraFromScene);

  if (helpBtn) helpBtn.addEventListener("click", () => {
    alert(
      "Pairs — help\n\n"
    + "Each pair is two parallel lines.  Click a line or a trapezoid\n"
    + "to select that pair; drag anywhere on the canvas to move the\n"
    + "whole pair — the two lines and every segment translate\n"
    + "together, since a pair and its segments are one object.\n\n"
    + "The panel's fields control the pair:\n"
    + "  Incl.°         the pair's tilt against the horizontal axis,\n"
    + "                 in [-90, +90].  A pair of parallel lines is\n"
    + "                 symmetric under a 180° reversal, so this is\n"
    + "                 the pair's direction without the ambiguity\n"
    + "                 of a full circle.\n"
    + "  Metric A/B     world units per abstract unit along each line\n"
    + "  Offset         shift of line B along the direction of line A\n"
    + "  Gap            perpendicular distance between the two lines\n\n"
    + "SHIFT-DRAG SET\n"
    + "While you hold Shift, each shift-click on a segment body in\n"
    + "the segment view ADDS that segment to a persistent set.  The\n"
    + "set grows with every subsequent shift-click, so you can pick\n"
    + "out several segments by clicking them one at a time.  A\n"
    + "shift-drag on any member then moves every member of the set\n"
    + "together along the pair's own axis, preserving the gaps\n"
    + "between them.\n\n"
    + "Set members are marked with a bright white dashed outline on\n"
    + "their bar in the segment view, and a soft cyan inset outline\n"
    + "on their row in the panel's segment list.\n\n"
    + "Releasing Shift clears the set; a non-shift click anywhere\n"
    + "also clears it.  Without Shift, a body drag moves the whole\n"
    + "row (the original behaviour).\n\n"
    + "OVERLAP MODE\n"
    + "The \"Overlap\" checkbox turns on a global viewing aid.  When\n"
    + "on, any segment whose trapezoid VISUALLY overlaps a trapezoid\n"
    + "on a different pair is outlined in red — on its visible\n"
    + "trapezoid(s) on the canvas, on its bar in the segment view,\n"
    + "and via a red ring on its swatch in the pair list and the\n"
    + "segment list.  The comparison is a polygon-polygon test in\n"
    + "screen space, so it changes as you zoom or pan the view.\n\n"
    + "While Overlap mode is on, a \"make room\" button (⇣) appears\n"
    + "on any segment row that overlaps a segment on the pair\n"
    + "immediately below it.  Clicking it shifts the offending\n"
    + "segment on the lower pair — and every segment that follows\n"
    + "it in the same direction — by the smallest amount that\n"
    + "clears the overlap, preserving the gaps inside the shifted\n"
    + "tail.\n\n"
    + "PAIR ORDER\n"
    + "The order of the pair list is the pair hierarchy.  The ↑ and\n"
    + "↓ buttons on each pair row swap that pair with its neighbour;\n"
    + "the same move is available from anywhere via Alt+↑ and Alt+↓\n"
    + "on the selected pair.  Reordering the list also reorders the\n"
    + "segment view's rows and the canvas's paint order.\n\n"
    + "The Segments section lists every segment on the selected pair.\n"
    + "Each row shows the segment's name, a swatch (solid when the\n"
    + "segment is filled, hollow when it is empty), and a delete\n"
    + "button.  Clicking a row selects that segment.\n\n"
    + "The lower band is the SEGMENT VIEW.  Each row is one pair.\n"
    + "The axis is periodic, so a segment dragged past one end\n"
    + "reappears at the other — on the segment view and,\n"
    + "correspondingly, at the opposite end of the pair of lines.\n\n"
    + "  · drag a segment's LEFT or RIGHT EDGE to widen or shrink\n"
    + "    that one segment.\n\n"
    + "  · drag a segment's BODY to translate every segment in the\n"
    + "    pair together.\n\n"
    + "  · hold SHIFT and shift-click segment bodies to build a set,\n"
    + "    then shift-drag any member to move the whole set together.\n\n"
    + "  · click a segment's BODY to select it.\n\n"
    + "The same segment bodies are hit-testable in the canvas band:\n"
    + "clicking a trapezoid there selects its segment AND its pair,\n"
    + "and dragging moves the whole pair.\n\n"
    + "\"Draw Segment\" turns the segment view into a placement tool:\n"
    + "click a row to drop a segment of default width.  Esc cancels.\n\n"
    + "\"Toggle Fill\" flips the selected segment between filled and\n"
    + "empty.  An empty segment shows as a dashed outline on the\n"
    + "canvas; a filled one is a solid trapezoid.\n\n"
    + "\"Clear Segments\" removes every segment on the selected pair.\n\n"
    + "\"Save scene\" writes the whole workspace — every pair together\n"
    + "with its segments — as one JSON file.\n\n"
    + "KRA EXPORT\n"
    + "The KRA section controls the .kra export.  Each filled segment\n"
    + "becomes a filled trapezoid; each segment gets a text label.\n"
    + "The label is warped by an affine derived from the trapezoid's\n"
    + "own local frame, so it appears to sit on the trapezoid's\n"
    + "plane — the same way cone_playground's text-shear mode warps\n"
    + "labels onto a square's plane."
    );
  });

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

    if (e.key === "Escape" && state.drawSegmentMode) {
      e.preventDefault();
      state.drawSegmentMode = false;
      if (drawSegBtn) drawSegBtn.classList.remove("active");
      draw();
      return;
    }

    if (e.altKey && !e.ctrlKey && !e.metaKey) {
      if (e.key === "ArrowUp") {
        e.preventDefault();
        if (selectedPair > 0) {
          movePair(selectedPair, -1);
        }
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
})();
"""
