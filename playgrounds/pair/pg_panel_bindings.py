"""
pg_panel_bindings.py — every non-scrub panel control binding, plus
the pair playground's registered actions.

Each button in the panel has two entry points: a local
addEventListener bound to the button itself, and a
`pgkitRegisterAction` declaration that makes it invokable by name
from a remote client.  Both entry points call the same underlying
function, so behaviour cannot drift.

Actions that need user-supplied values before they can run declare
`requires` in their meta.  A remote client gathers those values
(where the click gesture is present, so file pickers and prompts
work) and sends them in the command's payload.  The action's
`run(payload)` reads them and, when absent, falls back to the
original local path.
"""

PANEL_BINDINGS_JS = r"""
/* ==========================================================================
   ACTION HELPERS
   ========================================================================== */

function _addPairAction() {
  addPair();
}

function _cloneSelectedPairAction() {
  if (selectedPair < 0 || selectedPair >= pairs.length) {
    flashStatus("No pair selected", "warn");
    return;
  }
  clonePair(selectedPair);
}

function _deleteSelectedPairAction() {
  if (selectedPair < 0 || selectedPair >= pairs.length) {
    flashStatus("No pair selected", "warn");
    return;
  }
  deletePair(selectedPair);
}

function _moveSelectedPairUpAction() {
  if (selectedPair <= 0) return;
  movePair(selectedPair, -1);
}

function _moveSelectedPairDownAction() {
  if (selectedPair < 0 || selectedPair >= pairs.length - 1) return;
  movePair(selectedPair, +1);
}

function _toggleFillSelectedAction() {
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
}

function _clearSelectedPairSegmentsAction() {
  if (selectedPair < 0) {
    flashStatus("No pair selected", "warn");
    return;
  }
  pairs[selectedPair].segments.length = 0;
  selectedSegment = null;
  _clearShiftDragSet();
  syncPairList();
  draw();
}

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

  /* ---- local button bindings ------------------------------------ */

  if (addBtn)         addBtn.addEventListener("click", _addPairAction);
  if (cloneBtn)       cloneBtn.addEventListener("click", _cloneSelectedPairAction);
  if (toggleFillBtn)  toggleFillBtn.addEventListener("click", _toggleFillSelectedAction);
  if (clearSegBtn)    clearSegBtn.addEventListener("click", _clearSelectedPairSegmentsAction);

  if (drawSegBtn) drawSegBtn.addEventListener("click", () => {
    state.drawSegmentMode = !state.drawSegmentMode;
    drawSegBtn.classList.toggle("active", state.drawSegmentMode);
    pgkitNotifyLocal("ui.drawSegmentMode");
    draw();
  });

  if (saveSceneBtn)   saveSceneBtn.addEventListener("click", saveSceneJSON);
  if (loadSceneBtn)   loadSceneBtn.addEventListener("click", promptLoadScene);
  if (exportBtn)      exportBtn.addEventListener("click",
                                                 exportVisualStateJSON);
  if (generateKraBtn) generateKraBtn.addEventListener("click",
                                                 generateKraFromScene);

  /* ---- checkbox / select bindings ------------------------------- */

  if (overlapEl) overlapEl.addEventListener("change", () => {
    state.overlapHighlight = !!overlapEl.checked;
    pgkitNotifyLocal("ui.overlapHighlight");
    syncPairList();
    draw();
  });

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
    + "SHIFT-DRAG SET — hold Shift and click segment bodies in the\n"
    + "segment view to build a persistent set; shift-drag any member\n"
    + "to move every member together.  Releasing Shift keeps the set;\n"
    + "a plain (non-shift) canvas click clears it.\n\n"
    + "REMOTE API — every value field and every button in this panel\n"
    + "is published at /remote.  When a remote client connects, the\n"
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
        _moveSelectedPairUpAction();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        _moveSelectedPairDownAction();
        return;
      }
    }
  });

  /* ---- remote field registrations ------------------------------- */

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

  /* ---- remote action registrations ------------------------------
     Each action is registered exactly once.  The two that need
     user-supplied values (loadScene, generateKra) declare a
     `requires` array; a remote client gathers those values on its
     side and sends them in the payload.  Absent a payload, each
     falls back to its local prompt. */

  pgkitRegisterAction("action.addPair", {
    label: "+ Pair",
    run: _addPairAction,
    meta: { section: "pair", variant: "primary" },
  });

  pgkitRegisterAction("action.clonePair", {
    label: "+ Clone",
    run: _cloneSelectedPairAction,
    meta: { section: "pair", variant: "primary" },
  });

  pgkitRegisterAction("action.deletePair", {
    label: "Delete",
    run: _deleteSelectedPairAction,
    meta: { section: "pair", variant: "danger" },
  });

  pgkitRegisterAction("action.movePairUp", {
    label: "\u2191",
    run: _moveSelectedPairUpAction,
    meta: { section: "pair", variant: "plain" },
  });

  pgkitRegisterAction("action.movePairDown", {
    label: "\u2193",
    run: _moveSelectedPairDownAction,
    meta: { section: "pair", variant: "plain" },
  });

  pgkitRegisterAction("action.toggleFill", {
    label: "Toggle Fill",
    run: _toggleFillSelectedAction,
    meta: { section: "segments", variant: "plain" },
  });

  pgkitRegisterAction("action.clearSegments", {
    label: "Clear Segments",
    run: _clearSelectedPairSegmentsAction,
    meta: { section: "segments", variant: "danger" },
  });

  pgkitRegisterAction("action.saveScene", {
    label: "Save scene",
    run: () => saveSceneJSON(),
    meta: { section: "export", variant: "plain" },
  });

  pgkitRegisterAction("action.loadScene", {
    label: "Load scene",
    run: (payload) => {
      if (payload && typeof payload.scene_json === "string") {
        const ok = loadSceneFromText(payload.scene_json);
        if (!ok && typeof flashStatus === "function") {
          flashStatus("Remote load failed", "bad");
        }
      } else {
        promptLoadScene();
      }
    },
    meta: {
      section: "export",
      variant: "plain",
      requires: [
        {
          name:   "scene_json",
          kind:   "file",
          label:  "scene file",
          accept: ".json,application/json",
        },
      ],
    },
  });

  pgkitRegisterAction("action.exportVisual", {
    label: "Export visual state",
    run: () => exportVisualStateJSON(),
    meta: { section: "export", variant: "plain" },
  });

  pgkitRegisterAction("action.generateKra", {
    label: "Generate .kra",
    run: (payload) => {
      const path = payload && typeof payload.output_path === "string"
        ? payload.output_path.trim() : "";
      if (path) {
        generateKraToPath(path);
      } else {
        generateKraFromScene();
      }
    },
    meta: {
      section: "export",
      variant: "danger",
      requires: [
        {
          name:    "output_path",
          kind:    "text",
          label:   "path",
          default: "/tmp/pair_scene.kra",
        },
      ],
    },
  });

})();
"""
