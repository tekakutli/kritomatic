PAIR_PANEL_BINDINGS_JS = r"""
import {
  pairs, pairState, state, view, bringToTop, fitView,
  addPair, clonePair, deletePair, movePair, notifyChanged,
  _segmentOverlapsPairBelow, _makeRoomInferior,
  _pairPeriod,
} from "./pg_core.js";
import { syncPairList, syncSegmentList } from "./pg_panel_lists.js";
import { _syncPairFieldInputs } from "./pg_panel_fields.js";
import {
  saveSceneJSON, promptLoadScene, loadSceneFromText,
} from "./pg_scene.js";
import { exportVisualStateJSON } from "./pg_export.js";
import {
  generateKraFromScene, generateKraToPath,
} from "./pg_kra.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel,
  pgkitNotifyLocal, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

function _addPairAction() { addPair(); }

function _cloneSelectedPairAction() {
  if (pairState.selectedPair < 0 || pairState.selectedPair >= pairs.length) {
    flashStatus("No pair selected", "warn"); return;
  }
  clonePair(pairState.selectedPair);
}

function _deleteSelectedPairAction() {
  if (pairState.selectedPair < 0 || pairState.selectedPair >= pairs.length) {
    flashStatus("No pair selected", "warn"); return;
  }
  deletePair(pairState.selectedPair);
}

function _moveSelectedPairUpAction() {
  if (pairState.selectedPair <= 0) return;
  movePair(pairState.selectedPair, -1);
}

function _moveSelectedPairDownAction() {
  if (pairState.selectedPair < 0 || pairState.selectedPair >= pairs.length - 1) return;
  movePair(pairState.selectedPair, +1);
}

function _toggleFillSelectedAction() {
  const sel = pairState.selectedSegment;
  if (!sel) { flashStatus("No segment selected", "warn"); return; }
  const p = pairs[sel.pairIdx];
  if (!p) return;
  const seg = p.segments[sel.segIdx];
  if (!seg) return;
  seg.filled = !(seg.filled !== false);
  notifyChanged();
}

function _clearSelectedPairSegmentsAction() {
  if (pairState.selectedPair < 0) { flashStatus("No pair selected", "warn"); return; }
  pairs[pairState.selectedPair].segments.length = 0;
  pairState.selectedSegment = null;
  state.shiftDragSet = null;
  notifyChanged();
}

/* ==========================================================================
   LEVELS
   ========================================================================== */

pgkitRegisterLevel("pairs", {
  label: "Pairs",
  items: () => pairs.map((p, i) => ({ name: "pair." + i, label: p.name })),
  selected: () => pairState.selectedPair >= 0
    ? "pair." + pairState.selectedPair : null,
  parent: () => null,
});

pgkitRegisterLevel("segments", {
  label: "Segments",
  parent: () => pairState.selectedPair >= 0
    ? { level: "pairs", name: "pair." + pairState.selectedPair } : null,
  items: () => {
    if (pairState.selectedPair < 0) return [];
    const p = pairs[pairState.selectedPair];
    return p.segments.map((s, i) => ({
      name: "segment." + i,
      label: s.name,
      extra: { filled: s.filled !== false },
    }));
  },
  selected: () => pairState.selectedSegment
    ? "segment." + pairState.selectedSegment.segIdx : null,
});

/* ==========================================================================
   SELECT
   ========================================================================== */

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;

  if (level === "pairs") {
    const prefix = "pair.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < pairs.length)) return false;
    pairState.selectedPair = idx;
    pairState.selectedSegment = null;
    notifyChanged();
    return true;
  }

  if (level === "segments") {
    const prefix = "segment.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (pairState.selectedPair < 0) return false;
    if (!(idx >= 0 && idx < pairs[pairState.selectedPair].segments.length)) return false;
    pairState.selectedSegment = { pairIdx: pairState.selectedPair, segIdx: idx };
    syncSegmentList();
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
    return true;
  }

  return false;
}

/* ==========================================================================
   BUTTON BINDINGS
   ========================================================================== */

export function installPanelBindings() {
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

  if (addBtn)         addBtn.addEventListener("click", _addPairAction);
  if (cloneBtn)       cloneBtn.addEventListener("click", _cloneSelectedPairAction);
  if (toggleFillBtn)  toggleFillBtn.addEventListener("click", _toggleFillSelectedAction);
  if (clearSegBtn)    clearSegBtn.addEventListener("click", _clearSelectedPairSegmentsAction);

  if (drawSegBtn) drawSegBtn.addEventListener("click", () => {
    state.drawSegmentMode = !state.drawSegmentMode;
    drawSegBtn.classList.toggle("active", state.drawSegmentMode);
    pgkitNotifyLocal("ui.drawSegmentMode");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  });

  if (saveSceneBtn)   saveSceneBtn.addEventListener("click", saveSceneJSON);
  if (loadSceneBtn)   loadSceneBtn.addEventListener("click", () => promptLoadScene());
  if (exportBtn)      exportBtn.addEventListener("click", exportVisualStateJSON);
  if (generateKraBtn) generateKraBtn.addEventListener("click", generateKraFromScene);

  if (overlapEl) overlapEl.addEventListener("change", () => {
    state.overlapHighlight = !!overlapEl.checked;
    pgkitNotifyLocal("ui.overlapHighlight");
    notifyChanged();
  });

  const kraTextPosEl = document.getElementById("kraTextPos");
  const kraDrawRectsEl = document.getElementById("kraDrawRects");
  if (kraTextPosEl)    kraTextPosEl.addEventListener("change", () => pgkitNotifyLocal("kra.textPosition"));
  if (kraDrawRectsEl)  kraDrawRectsEl.addEventListener("change", () => pgkitNotifyLocal("kra.drawRects"));

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

    if (e.key === "Escape" && state.drawSegmentMode) {
      e.preventDefault();
      state.drawSegmentMode = false;
      if (drawSegBtn) drawSegBtn.classList.remove("active");
      pgkitNotifyLocal("ui.drawSegmentMode");
      import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
      return;
    }

    if (e.altKey && !e.ctrlKey && !e.metaKey) {
      if (e.key === "ArrowUp")   { e.preventDefault(); _moveSelectedPairUpAction();   return; }
      if (e.key === "ArrowDown") { e.preventDefault(); _moveSelectedPairDownAction(); return; }
    }
  });
}

/* ==========================================================================
   FIELDS
   ========================================================================== */

pgkitRegisterField("ui.overlapHighlight", {
  get: () => !!state.overlapHighlight,
  set: (v) => {
    state.overlapHighlight = !!v;
    const el = document.getElementById("overlapHighlight");
    if (el) el.checked = !!v;
    notifyChanged();
  },
  meta: { kind: "bool", label: "Overlap" },
});

pgkitRegisterField("ui.drawSegmentMode", {
  get: () => !!state.drawSegmentMode,
  set: (v) => {
    state.drawSegmentMode = !!v;
    const el = document.getElementById("drawSegmentBtn");
    if (el) el.classList.toggle("active", state.drawSegmentMode);
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "bool", label: "Draw Segment" },
});

pgkitRegisterField("kra.textPosition", {
  get: () => {
    const el = document.getElementById("kraTextPos");
    return el ? el.value : "center";
  },
  set: (v) => {
    const el = document.getElementById("kraTextPos");
    if (el && typeof v === "string") el.value = v;
  },
  meta: { kind: "enum", label: "KRA text position",
    options: ["center","tl","tc","tr","ml","mr","bl","bc","br","top","bottom","left","right"] },
});

pgkitRegisterField("kra.drawRects", {
  get: () => {
    const el = document.getElementById("kraDrawRects");
    return el ? !!el.checked : true;
  },
  set: (v) => {
    const el = document.getElementById("kraDrawRects");
    if (el) el.checked = !!v;
  },
  meta: { kind: "bool", label: "KRA rectangles" },
});

/* ==========================================================================
   ACTIONS
   ========================================================================== */

pgkitRegisterAction("action.saveScene", {
  label: "Save scene", scope: "global", run: saveSceneJSON, meta: { variant: "plain" },
});

pgkitRegisterAction("action.loadScene", {
  label: "Load scene", scope: "global",
  run: (payload) => {
    if (payload && typeof payload.scene_json === "string") {
      const ok = loadSceneFromText(payload.scene_json);
      if (!ok && typeof flashStatus === "function") flashStatus("Remote load failed", "bad");
    } else {
      promptLoadScene();
    }
  },
  meta: {
    variant: "plain",
    requires: [{ name: "scene_json", kind: "file", label: "scene file", accept: ".json,application/json" }],
  },
});

pgkitRegisterAction("action.exportVisual", {
  label: "Export visual state", scope: "global", run: exportVisualStateJSON, meta: { variant: "plain" },
});

pgkitRegisterAction("action.generateKra", {
  label: "Generate .kra", scope: "global",
  run: (payload) => {
    const path = payload && typeof payload.output_path === "string"
      ? payload.output_path.trim() : "";
    if (path) generateKraToPath(path);
    else generateKraFromScene();
  },
  meta: {
    variant: "danger",
    requires: [{ name: "output_path", kind: "text", label: "path", default: "/tmp/pair_scene.kra" }],
  },
});

pgkitRegisterAction("action.addPair", {
  label: "+ Pair", scope: "all", level: "pairs",
  run: _addPairAction, meta: { variant: "primary" },
});

pgkitRegisterAction("action.clonePair", {
  label: "+ Clone", scope: "selection", level: "pairs",
  run: _cloneSelectedPairAction, meta: { variant: "primary" },
});

pgkitRegisterAction("action.deletePair", {
  label: "Delete", scope: "selection", level: "pairs",
  run: _deleteSelectedPairAction, meta: { variant: "danger" },
});

pgkitRegisterAction("action.movePairUp", {
  label: "\u2191", scope: "selection", level: "pairs",
  run: _moveSelectedPairUpAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.movePairDown", {
  label: "\u2193", scope: "selection", level: "pairs",
  run: _moveSelectedPairDownAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.toggleFill", {
  label: "Toggle Fill", scope: "selection", level: "segments",
  run: _toggleFillSelectedAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.clearSegments", {
  label: "Clear Segments", scope: "selection", level: "segments",
  run: _clearSelectedPairSegmentsAction, meta: { variant: "danger" },
});

pgkitRegisterAction("action.makeRoom", {
  label: "\u21E3", scope: "row", level: "segments",
  appliesTo: (item) => {
    if (pairState.selectedPair < 0) return false;
    if (typeof item.name !== "string") return false;
    const prefix = "segment.";
    if (!item.name.startsWith(prefix)) return false;
    const idx = parseInt(item.name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < pairs[pairState.selectedPair].segments.length)) return false;
    return _segmentOverlapsPairBelow(pairState.selectedPair, idx);
  },
  run: (payload) => {
    let idx = pairState.selectedSegment ? pairState.selectedSegment.segIdx : -1;
    if (payload && payload.target && payload.target.level === "segments") {
      const prefix = "segment.";
      if (typeof payload.target.name === "string" && payload.target.name.startsWith(prefix)) {
        idx = parseInt(payload.target.name.slice(prefix.length), 10);
      }
    }
    if (idx < 0) { flashStatus("No segment to make room for", "warn"); return; }
    const moved = _makeRoomInferior(pairState.selectedPair, idx);
    if (moved > 0) {
      notifyChanged();
      const lowerName = (pairState.selectedPair + 1 < pairs.length)
        ? pairs[pairState.selectedPair + 1].name : "the pair below";
      flashStatus("Made room on " + lowerName + " (" + moved + " shift" + (moved === 1 ? "" : "s") + ")", "ok");
    } else {
      flashStatus("Could not resolve overlap", "warn");
    }
  },
  meta: { variant: "primary" },
});

/* ==========================================================================
   BOOT HOOK
   ========================================================================== */

export function pairBootSync() {
  syncPairList();
  _syncPairFieldInputs();
  import("./pg_dispatch.js").then(m => m.updateStatus()).catch(() => {});
}
"""
