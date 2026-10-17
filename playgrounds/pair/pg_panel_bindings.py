"""
pg_panel_bindings.py — every non-scrub panel control binding, plus
the pair playground's registered levels, fields, and actions.

LEVELS
======
Two: `pairs` (root) and `segments` (nested under the selected pair).

ACTION SCOPES
=============
    global      Save scene, Load scene, Export visual, Generate .kra
    all         + Pair
    selection   + Clone, Delete, ↑, ↓  (on pairs)
                Toggle Fill, Clear Segments (on segments)
    row         make room (⇣)  on segments that overlap a lower pair

`pgkitHandleSelect(level, name)` translates the remote panel's
`select` command into a change to `selectedPair` or `selectedSegment`.
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
   SELECT COMMAND
   ==========================================================================
   The remote panel's item click sends a `select` command with a
   level and a name.  We translate it to the pair model's selection
   and re-sync the lists. */

function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;

  if (level === "pairs") {
    const prefix = "pair.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < pairs.length)) return false;
    selectedPair    = idx;
    selectedSegment = null;
    syncPairList();
    draw();
    return true;
  }

  if (level === "segments") {
    const prefix = "segment.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (selectedPair < 0) return false;
    if (!(idx >= 0 && idx < pairs[selectedPair].segments.length)) return false;
    selectedSegment = { pairIdx: selectedPair, segIdx: idx };
    syncSegmentList();
    draw();
    return true;
  }

  return false;
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
    + "to move every member together.\n\n"
    + "OVERLAP MODE — the Overlap checkbox highlights segments whose\n"
    + "trapezoids overlap a trapezoid on another pair.  While on, a\n"
    + "make-room button appears on any segment row that overlaps a\n"
    + "segment on the pair below.\n\n"
    + "REMOTE API — every value field and every action is published\n"
    + "at /remote, organised by entity level.  When a remote client\n"
    + "connects, the panel hides."
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

  /* ---- remote levels -------------------------------------------- */

  pgkitRegisterLevel("pairs", {
    label: "Pairs",
    items: () => pairs.map((p, i) => ({
      name: "pair." + i,
      label: p.name,
    })),
    selected: () => selectedPair >= 0 ? "pair." + selectedPair : null,
  });

  pgkitRegisterLevel("segments", {
    label: "Segments",
    parent: () => selectedPair >= 0
      ? { level: "pairs", name: "pair." + selectedPair }
      : null,
    items: () => {
      if (selectedPair < 0) return [];
      const p = pairs[selectedPair];
      return p.segments.map((s, i) => ({
        name: "segment." + i,
        label: s.name,
        extra: { filled: s.filled !== false },
      }));
    },
    selected: () => selectedSegment
      ? "segment." + selectedSegment.segIdx
      : null,
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

  /* ---- remote global actions ------------------------------------ */

  pgkitRegisterAction("action.saveScene", {
    label: "Save scene",
    scope: "global",
    run: () => saveSceneJSON(),
    meta: { variant: "plain" },
  });

  pgkitRegisterAction("action.loadScene", {
    label: "Load scene",
    scope: "global",
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
    scope: "global",
    run: () => exportVisualStateJSON(),
    meta: { variant: "plain" },
  });

  pgkitRegisterAction("action.generateKra", {
    label: "Generate .kra",
    scope: "global",
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

  /* ---- remote pairs-level actions ------------------------------- */

  pgkitRegisterAction("action.addPair", {
    label: "+ Pair",
    scope: "all",
    level: "pairs",
    run: _addPairAction,
    meta: { variant: "primary" },
  });

  pgkitRegisterAction("action.clonePair", {
    label: "+ Clone",
    scope: "selection",
    level: "pairs",
    run: _cloneSelectedPairAction,
    meta: { variant: "primary" },
  });

  pgkitRegisterAction("action.deletePair", {
    label: "Delete",
    scope: "selection",
    level: "pairs",
    run: _deleteSelectedPairAction,
    meta: { variant: "danger" },
  });

  pgkitRegisterAction("action.movePairUp", {
    label: "\u2191",
    scope: "selection",
    level: "pairs",
    run: _moveSelectedPairUpAction,
    meta: { variant: "plain" },
  });

  pgkitRegisterAction("action.movePairDown", {
    label: "\u2193",
    scope: "selection",
    level: "pairs",
    run: _moveSelectedPairDownAction,
    meta: { variant: "plain" },
  });

  /* ---- remote segments-level actions ---------------------------- */

  pgkitRegisterAction("action.toggleFill", {
    label: "Toggle Fill",
    scope: "selection",
    level: "segments",
    run: _toggleFillSelectedAction,
    meta: { variant: "plain" },
  });

  pgkitRegisterAction("action.clearSegments", {
    label: "Clear Segments",
    scope: "selection",
    level: "segments",
    run: _clearSelectedPairSegmentsAction,
    meta: { variant: "danger" },
  });

  /* ---- remote segments-level ROW actions ------------------------ */

  pgkitRegisterAction("action.makeRoom", {
    label: "\u21E3",
    scope: "row",
    level: "segments",
    appliesTo: (item) => {
      if (selectedPair < 0) return false;
      if (typeof item.name !== "string") return false;
      const prefix = "segment.";
      if (!item.name.startsWith(prefix)) return false;
      const idx = parseInt(item.name.slice(prefix.length), 10);
      if (!(idx >= 0 && idx < pairs[selectedPair].segments.length)) return false;
      return _segmentOverlapsPairBelow(selectedPair, idx);
    },
    run: (payload) => {
      let idx = selectedSegment ? selectedSegment.segIdx : -1;
      if (payload && payload.target && payload.target.level === "segments") {
        const prefix = "segment.";
        if (typeof payload.target.name === "string"
            && payload.target.name.startsWith(prefix)) {
          idx = parseInt(payload.target.name.slice(prefix.length), 10);
        }
      }
      if (idx < 0) {
        flashStatus("No segment to make room for", "warn");
        return;
      }
      const moved = _makeRoomInferior(selectedPair, idx);
      if (moved > 0) {
        syncPairList();
        draw();
        const lowerName = (selectedPair + 1 < pairs.length)
          ? pairs[selectedPair + 1].name : "the pair below";
        flashStatus("Made room on " + lowerName
                    + " (" + moved + " shift"
                    + (moved === 1 ? "" : "s") + ")", "ok");
      } else {
        flashStatus("Could not resolve overlap", "warn");
      }
    },
    meta: { variant: "primary" },
  });

})();
"""
