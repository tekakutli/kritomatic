"""
pg_panel_bindings.py — buttons, remote actions, remote levels,
pgkitHandleSelect, and pairBootSync.

ES module.  Exports four names the boot module uses:

    installPanelBindings   attach buttons / list / keyboard listeners
    pgkitHandleSelect      translate the remote `select` command
    pairBootSync           the initial sync sequence

IMPORTS FROM pgkit/remote_client
================================
This module both *registers* (fields, actions, levels) and *notifies*.
Every registration function must be imported explicitly:

    pgkitRegisterField     used for ui.overlapHighlight,
                           ui.drawSegmentMode, kra.textPosition,
                           kra.drawRects
    pgkitRegisterAction    used for every action.* registration
    pgkitRegisterLevel     used for the `pairs` and `segments` levels
    pgkitNotifyLocal       called by the button bindings when the
                           panel's own widgets change

Missing `pgkitRegisterField` from this list is what blanked the pair
playground when the file was first refactored to ES modules: the
module parsed fine (which is what `node --check` verified), but it
threw `ReferenceError: pgkitRegisterField is not defined` at
evaluation time, the browser refused to hand it to `_boot.js`, and
the boot never ran.

LEVELS
======
Two: `pairs` (root, one item per pair) and `segments` (nested under
the selected pair).  `segments.parent()` reports the currently-
selected pair, or null when nothing is selected.

ACTIONS
=======
Every action follows the pgkit pattern — `scope`, optional `level`,
optional `appliesTo` for row scope, optional `requires` for
payloads, optional `variant` for the button's color.  Actions that
mutate the model route through `notifyChanged()` (which triggers
the boot-injected afterChange hook: sync, draw, pgkitNotifyAll) or
through a dynamic `import("./pg_dispatch.js").then(m => m.draw())`
when only the canvas needs repainting.

There is NO `board` object in pair's model.  The only state is
`pairState` (model cursor) and `state` (UI session).  The `board`
references that used to be here were copy-pasted from the text
board and referenced a symbol that never existed in this project.
`_fitAction` and `_resetViewAction` now call `fitView()` (pair's
equivalent of "fit" — it recomputes the world-to-screen transform
from `view.zoom` and `view.panX/Y`), not a hypothetical `fitAll()`.

RETURN-YOUR-ASYNC-WORK
======================
`action.generateKra` is the only action whose wrapper calls an
async helper.  `generateKraToPath` and `generateKraFromScene` in
`pg_kra.js` are both `async function`; `generateKraToPath` does an
`await fetch("/generate-kra", ...)` that is the whole point of the
action (Krita writes the .kra during that fetch).  Without a
`return`, `entry.run()` returns `undefined`, `await entry.run()` in
`pgkitRemoteHandleCommand` resolves in one microtask, and
`command_result {ok: true}` fires before the fetch is even sent —
the CLI thinks the .kra exists when it does not.

`_generateKraAction` therefore `return`s the promise from whichever
entry point it calls.

`action.loadScene` also `return`s its boolean result.  Not because
the function is async (`loadSceneFromText` is synchronous), but so
that a CLI script can tell load success from load failure via
`command_result.result`.  The `flashStatus` on failure stays — that
feedback is for the user, not for the caller.  The file-picker
branch returns `undefined` (opening the picker is a synchronous
action; the user's later file pick is a DOM event the CLI cannot
wait on).

WHY DYNAMIC IMPORTS
===================
`import("./pg_dispatch.js").then(m => m.draw())` appears in several
handlers.  `pg_dispatch` imports `tb_core`-style state from
`pg_core`, and both `pg_panel_scrubs` and `pg_panel_bindings` need
to trigger a repaint.  A static import of `draw` in either file
would create a load-order cycle (`pg_dispatch` → `pg_panel_lists`
→ `pg_panel_fields` → `pg_core`), which the browser resolves but
with Temporal Dead Zone surprises on first evaluation.  Deferring
to a dynamic import keeps the graph clean.
"""

PAIR_PANEL_BINDINGS_JS = r"""
import {
  pairs, pairState, state, view, fitView,
  addPair, clonePair, deletePair, movePair, notifyChanged,
  _segmentOverlapsPairBelow, _makeRoomInferior,
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
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitRegisterField,
  pgkitNotifyLocal,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   ACTION HELPERS
   ========================================================================== */

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
   VIEW RESET / FIT
   ==========================================================================
   Pair has no "fit to content" concept — the lines are infinite and
   the segments wrap around the axis period, so there is nothing to
   frame.  `fitView()` recomputes `view.scale/tx/ty` from the current
   `view.zoom` and `view.panX/Y`.  Calling it after a reset (or on
   its own for a "re-fit the current view" action) is the pair
   equivalent of text_board's `fitAll()`.

   Both actions used to reference `board._autoFitDone`, a symbol that
   exists only in text_board's model; calling either would throw
   `ReferenceError: board is not defined`.  Both are reachable over
   the remote API, so the throw was a real bug, not dead code. */

function _fitAction() {
  fitView();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

function _resetViewAction() {
  view.zoom = 1;
  view.panX = 0;
  view.panY = 0;
  fitView();
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
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
      // Propagate the boolean so the CLI can tell success from failure
      // via command_result.result.  loadSceneFromText is synchronous;
      // the flash is for the user, the return is for the caller.
      return ok;
    }
    promptLoadScene();
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
    // Both entry points in pg_kra.js are `async function` — the
    // whole Krita export happens inside generateKraToPath's awaited
    // fetch.  Return the promise so command_result fires when the
    // .kra has been written, not when the fetch was dispatched.
    // See the module docstring, RETURN-YOUR-ASYNC-WORK.
    if (path) return generateKraToPath(path);
    return generateKraFromScene();
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

pgkitRegisterAction("action.fit", {
  label: "Fit", scope: "global", run: _fitAction, meta: { variant: "plain" },
});

pgkitRegisterAction("action.resetView", {
  label: "Reset view", scope: "global", run: _resetViewAction, meta: { variant: "plain" },
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
