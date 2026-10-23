"""
pg_panel.py — the UI panel and its boot-time wiring.

ES module.  Owns:

    installVerticalMenu        the *translation pass*.  Walks every
                               element with a `data-i18n` attribute
                               and sets its text (and title, when
                               `data-i18n-title` is present) from
                               `T()`.  No DOM creation.  No CSS
                               injection.

    installDarkScreenStyles    no-op.  All CSS now lives in
                               `pg_base.PG_CSS`.

    installInstructionsPopover attach the ? button's click handler
                               to the baked `#helpBtn`; create the
                               popover shell at boot (it's
                               `display:none` from `PG_CSS` until
                               `.show`)

    installPanelBindings       attach every button handler, wire the
                               ruler's MEASURE fields and the CABLE
                               delete buttons, register the pgkit
                               fields
    pgkitHandleSelect          translate remote `select` commands
    pgkitBootSync              initial sync + remote prime

DOM AND CSS ARE BAKED
=====================
`pg_base.PG_CSS` has the final stylesheet — dark theme, 200px width,
section labels, title block, `#rulerNumericInput { display: none }`,
`#helpPopover { display: none }`.  `pg_base.PG_PANEL_BODY` has the
final markup — every section, every field, every button.

`installVerticalMenu` therefore does not reshape anything; it just
sets the translated text on each element that carries a
`data-i18n="<key>"` attribute.  `installDarkScreenStyles` was the
second runtime stylesheet; with all its rules folded into
`PG_CSS`, its body is empty.

Element IDs that used to be created at boot (`rulerBtn`,
`zoomResetBtn`, `measureSpan`, `measureAngle`, `measureDeleteBtn`,
`cableIndex`, `cableVertexInfo`, `cableDeleteVertexBtn`,
`cableDeleteCableBtn`, `exportBtn`, `arrowDiagBtn`,
`arrowVisibilityToggle`, `tb_project`, `tb_sheet`, `tb_ceiling`,
`tb_scale`, `tb_selection`) are now in the served HTML.
`installPanelBindings` finds them by ID exactly as before.

WHAT'S STILL DYNAMIC
====================
The panel title bar is created by `page()` from `_PANEL_TITLE` in
`cable_html.py`; that string bakes the help button.  The status line
and title-block values are set by `updateStatus` / `updateTitleBlock`
at draw time.  The MEASURE and CABLE sections toggle `display:block`
when their respective selection is active.  The draw and ruler
buttons have their labels set by `updateDrawButton` /
`updateRulerButton`.

None of those cause a flash: their initial DOM state comes from the
baked markup, and the updates happen at boot's draw time on
identical DOM nodes.

RETURN-YOUR-ASYNC-WORK
======================
Every `pgkitRegisterAction` whose `run` function is not itself an
`async function` must `return` the promise from any async call it
makes.  `pgkit/remote_client.js`'s `pgkitRemoteHandleCommand` does
`await entry.run(...)`; a `run` that dispatches async work without
returning it resolves in a microtask, and `command_result {ok: true}`
fires before the work starts.  The CLI's `command()` — and the
`bundle()` sequencing built on it — block on `command_result`, so a
sync wrapper that drops its promise silently breaks script
sequencing.

Three wrappers in this file were affected (`_exportRunsAction`,
`_diagnosticsAction`, `_toggleArrowsAction`).  Each now `return`s
its promise chain and threads the inner call's return value through
the `.then` callback, so `await entry.run(...)` in pgkit waits for
the exported rendering / diagnostic dump / visibility flip to
complete.  The other eleven actions are either `async function`
themselves (`doSave`) or do only synchronous work.
"""

PANEL_JS = r"""
import {
  flashStatus,
  collapsePanel, restorePanel,
  addFloorNsGrid, addFloorEwGrid, addWallVGrid, addWallHGrid,
  doSave, updateDrawButton, updateSnapPill,
  gcAnchors, recomputeTrueCables,
  trueCableIdOf, trueCableSiblings,
} from "./pg_core.js";
import {
  beginDraw, cancelDraw,
  deleteSelectedCable, deleteSelectedVertex, clearAll,
} from "./pg_edit.js";
import {
  toggleRuler, updateRulerButton,
  _rulerApplyPanelSpan, _rulerApplyPanelAngle,
  _rulerCloseNumericInput, _rulerSyncMeasureInspector,
} from "./pg_ruler.js";
import { resetZoomPan, fitViews } from "./pg_navigation.js";
import { T } from "./pg_i18n.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitRegisterField,
  pgkitNotifyLocal, pgkitNotifyAll,
} from "./pgkit/remote_client.js";

/* ==========================================================================
   VERTICAL MENU — translation pass
   ==========================================================================
   The panel's markup and CSS are baked in pg_base.  This function
   walks every element carrying a `data-i18n` attribute and sets its
   textContent (or its title, for `data-i18n-title`) from `T()`.

   It replaces what used to be a DOM-creation + CSS-injection pass
   that reshaped a light-themed base panel into the dark themed one. */

export function installVerticalMenu() {
  /* 1. Every translatable text node. */
  for (const el of document.querySelectorAll("[data-i18n]")) {
    const key = el.getAttribute("data-i18n");
    const val = T(key);
    if (typeof val === "function") {
      /* Function-valued keys need arguments; skip — the element's
         text is set by its owning update function (updateStatus,
         updateTitleBlock, etc.) at draw time. */
      continue;
    }
    if (val != null && el.textContent !== val) {
      el.textContent = val;
    }
  }

  /* 2. Every translatable title attribute. */
  for (const el of document.querySelectorAll("[data-i18n-title]")) {
    const key = el.getAttribute("data-i18n-title");
    const val = T(key);
    if (typeof val === "function") continue;
    if (val != null && el.title !== val) el.title = val;
  }
}

/* ==========================================================================
   DARK SCREEN STYLE — no-op
   ==========================================================================
   All the palette rules that used to live here are now in
   `pg_base.PG_CSS`.  The function is kept in the module (and called
   by the boot) so a future contributor who adds a runtime-installed
   style has an obvious place to put it without re-introducing the
   flash. */

export function installDarkScreenStyles() {
  /* no-op */
}

/* ==========================================================================
   INSTRUCTIONS POPOVER
   ==========================================================================
   `#helpBtn` is baked into `_PANEL_TITLE` (cable_html.py).  This
   function attaches the click handler and creates the popover body
   at boot — the popover is `display:none` from `PG_CSS` until
   `.show`, so it never flashes. */

export function installInstructionsPopover() {
  const helpBtn = document.getElementById("helpBtn");
  if (!helpBtn) return;

  /* Popover shell (hidden by default).  Body filled from T() so the
     active language wins; a fork that wants the popover in Spanish
     or English gets it from the same translation table the panel
     uses. */
  const pop = document.createElement("div");
  pop.id = "helpPopover";
  pop.innerHTML =
    '<h3>' + T("popTitle") +
      '<button class="closeBtn" id="helpClose">' + T("btnClose") +
      '</button>' +
    '</h3>' +
    '<div class="body">' + T("popInstructionsHTML") + '</div>';
  document.body.appendChild(pop);

  const closeBtn = pop.querySelector("#helpClose");

  function openPop()  { pop.classList.add("show"); }
  function closePop() { pop.classList.remove("show"); }

  helpBtn.addEventListener("click", () => {
    if (pop.classList.contains("show")) closePop();
    else                                 openPop();
  });
  closeBtn.addEventListener("click", closePop);

  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pop.classList.contains("show")) {
      closePop();
      e.stopPropagation();
    }
    if (e.key === "?" || (e.shiftKey && e.key === "/")) {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      e.preventDefault();
      if (pop.classList.contains("show")) closePop();
      else                                 openPop();
    }
  });
}

/* ==========================================================================
   BUTTON + INPUT BINDINGS
   ==========================================================================
   Attaches listeners to the baked panel elements.  Element IDs are
   the same as before — installVerticalMenu no longer creates them
   because pg_base bakes them. */

export function installPanelBindings() {
  const els = {
    addNsGrid:    document.getElementById("addNsGrid"),
    addEwGrid:    document.getElementById("addEwGrid"),
    addWallV:     document.getElementById("addWallV"),
    addWallH:     document.getElementById("addWallH"),
    saveBtn:      document.getElementById("saveBtn"),
    delVertexBtn: document.getElementById("delVertexBtn"),
    deleteBtn:    document.getElementById("deleteBtn"),
    clearBtn:     document.getElementById("clearBtn"),
    drawBtn:      document.getElementById("drawBtn"),
    rulerBtn:     document.getElementById("rulerBtn"),
    zoomResetBtn: document.getElementById("zoomResetBtn"),
    measureSpan:      document.getElementById("measureSpan"),
    measureAngle:     document.getElementById("measureAngle"),
    measureDeleteBtn: document.getElementById("measureDeleteBtn"),
    cableDeleteVertexBtn: document.getElementById("cableDeleteVertexBtn"),
    cableDeleteCableBtn:  document.getElementById("cableDeleteCableBtn"),
  };

  if (els.addNsGrid)    els.addNsGrid.addEventListener("click", addFloorNsGrid);
  if (els.addEwGrid)    els.addEwGrid.addEventListener("click", addFloorEwGrid);
  if (els.addWallV)     els.addWallV.addEventListener("click", addWallVGrid);
  if (els.addWallH)     els.addWallH.addEventListener("click", addWallHGrid);
  if (els.saveBtn)      els.saveBtn.addEventListener("click", doSave);
  if (els.delVertexBtn) els.delVertexBtn.addEventListener("click", deleteSelectedVertex);
  if (els.deleteBtn)    els.deleteBtn.addEventListener("click", deleteSelectedCable);
  if (els.clearBtn)     els.clearBtn.addEventListener("click", clearAll);

  if (els.drawBtn) els.drawBtn.addEventListener("click", () => {
    if (state.drawing) cancelDraw(false);
    else               beginDraw();
  });

  if (els.rulerBtn)     els.rulerBtn.addEventListener("click", toggleRuler);
  if (els.zoomResetBtn) els.zoomResetBtn.addEventListener("click", _resetViewAction);

  if (els.measureSpan) {
    els.measureSpan.addEventListener("input", () => {
      const v = parseFloat(els.measureSpan.value);
      if (!isFinite(v) || v <= 0) return;
      _rulerApplyPanelSpan(v * 10);
      callDraw();
    });
    els.measureSpan.addEventListener("change", () => {
      const v = parseFloat(els.measureSpan.value);
      if (!isFinite(v) || v <= 0) { _rulerSyncMeasureInspector(); return; }
      _rulerApplyPanelSpan(v * 10);
      callDraw();
    });
  }

  if (els.measureAngle) {
    els.measureAngle.addEventListener("input", () => {
      const v = parseFloat(els.measureAngle.value);
      if (!isFinite(v)) return;
      _rulerApplyPanelAngle(v);
      callDraw();
    });
    els.measureAngle.addEventListener("change", () => {
      const v = parseFloat(els.measureAngle.value);
      if (!isFinite(v)) { _rulerSyncMeasureInspector(); return; }
      _rulerApplyPanelAngle(v);
      callDraw();
    });
  }

  if (els.measureDeleteBtn) els.measureDeleteBtn.addEventListener("click", () => {
    const sel = state.ruler.selected;
    if (sel < 0 || sel >= state.ruler.measures.length) return;
    state.ruler.measures.splice(sel, 1);
    state.ruler.selected = -1;
    state.ruler.hoverIdx = -1;
    _rulerSyncMeasureInspector();
    callDraw();
  });

  if (els.cableDeleteVertexBtn) {
    els.cableDeleteVertexBtn.addEventListener("click", deleteSelectedVertex);
  }
  if (els.cableDeleteCableBtn) {
    els.cableDeleteCableBtn.addEventListener("click", deleteSelectedCable);
  }

  _registerFields();
}

/* ==========================================================================
   PGKIT FIELD REGISTRATIONS
   ========================================================================== */

function _registerFields() {
  pgkitRegisterField("snap.enabled", {
    get: () => !!state.snapEnabled,
    set: (v) => {
      state.snapEnabled = !!v;
      updateSnapPill();
      callDraw();
    },
    meta: { kind: "bool", label: "Snap" },
  });

  pgkitRegisterField("view.zoom", {
    get: () => viewFloor.zoom,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewFloor.zoom = Math.max(0.02, Math.min(8, n));
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Floor zoom", min: 0.02, max: 8 },
  });

  pgkitRegisterField("view.panX", {
    get: () => viewFloor.panX,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewFloor.panX = n;
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Floor pan X" },
  });

  pgkitRegisterField("view.panY", {
    get: () => viewFloor.panY,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewFloor.panY = n;
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Floor pan Y" },
  });

  pgkitRegisterField("wall.zoom", {
    get: () => viewWall.zoom,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewWall.zoom = Math.max(0.02, Math.min(8, n));
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Wall zoom", min: 0.02, max: 8 },
  });

  pgkitRegisterField("wall.zoomX", {
    get: () => viewWall.zoomX,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewWall.zoomX = Math.max(0.02, Math.min(8, n));
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Wall X stretch", min: 0.02, max: 8 },
  });

  pgkitRegisterField("wall.panX", {
    get: () => viewWall.panX,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewWall.panX = n;
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Wall pan X" },
  });

  pgkitRegisterField("wall.panY", {
    get: () => viewWall.panY,
    set: (v) => {
      const n = parseFloat(v);
      if (!isFinite(n)) return;
      viewWall.panY = n;
      fitViews();
      callDraw();
    },
    meta: { kind: "number", label: "Wall pan Y" },
  });
}

/* ==========================================================================
   PGKIT LEVELS
   ========================================================================== */

function _allCables() {
  return state.floorCables.concat(state.wallCables);
}

pgkitRegisterLevel("cables", {
  label: "Cables",
  items: () => _allCables().map((c, i) => ({
    name:  "cable." + i,
    label: c.name || ("Cable " + c.id),
  })),
  selected: () => {
    if (!state.selectedCable) return null;
    const idx = _allCables().indexOf(state.selectedCable);
    return idx >= 0 ? "cable." + idx : null;
  },
  parent: () => null,
});

pgkitRegisterLevel("vertices", {
  label: "Vertices",
  parent: () => {
    if (!state.selectedCable) return null;
    const idx = _allCables().indexOf(state.selectedCable);
    if (idx < 0) return null;
    return { level: "cables", name: "cable." + idx };
  },
  items: () => {
    if (!state.selectedCable) return [];
    return state.selectedCable.anchorIds.map((_id, i) => ({
      name:  "vertex." + i,
      label: "V" + (i + 1),
    }));
  },
  selected: () => state.selectedVertex
    ? "vertex." + state.selectedVertex.index
    : null,
});

/* ==========================================================================
   PGKIT ACTIONS
   ==========================================================================
   Every `run` below is either an `async function` itself, or a sync
   function whose body ends in `return <Promise>`.  pgkit's
   `await entry.run(...)` in `pgkitRemoteHandleCommand` depends on
   this: a `run` that dispatches async work without returning it
   resolves immediately, and `command_result {ok: true}` fires before
   the work starts.  The CLI's `command()` and `bundle()` block on
   `command_result`, so a broken `run` breaks script sequencing. */

function _toggleDrawAction() {
  if (state.drawing) cancelDraw(false);
  else               beginDraw();
}

function _toggleRulerAction() { toggleRuler(); }

function _resetViewAction() {
  resetZoomPan();
  pgkitNotifyLocal("view.zoom");
  pgkitNotifyLocal("view.panX");
  pgkitNotifyLocal("view.panY");
  pgkitNotifyLocal("wall.zoom");
  pgkitNotifyLocal("wall.zoomX");
  pgkitNotifyLocal("wall.panX");
  pgkitNotifyLocal("wall.panY");
}

/* `exportAllCableRuns` is `async function` — it awaits one
   `renderCableRunToCanvas` per cable.  The outer `return` returns the
   dynamic-import promise; the inner `return` threads the export's own
   promise through the `.then` callback.  `await entry.run(...)` in
   pgkit therefore waits for the export loop to finish, not for the
   module load to start. */
function _exportRunsAction() {
  return import("./pg_export_entry.js").then(m => {
    if (typeof m.exportAllCableRuns === "function")
      return m.exportAllCableRuns();
  }).catch(() => {});
}

/* `dumpArrowsDiagnostics` is sync, but it lives inside `.then` — so
   without the outer `return` the diagnostic dump fires on a later
   microtask than `command_result`.  On a cold session (the module
   has not been imported yet), the module load itself happens between
   the two, so `command_result` precedes the console output.  The
   `return` closes that. */
function _diagnosticsAction() {
  return import("./pg_arrows_diag.js").then(m => {
    if (typeof m.dumpArrowsDiagnostics === "function")
      return m.dumpArrowsDiagnostics();
  }).catch(() => {});
}

/* Same shape as `_diagnosticsAction`: `toggleArrowVisibility` is sync,
   but the module might be cold.  `return` makes `command_result` wait
   until the flag has flipped. */
function _toggleArrowsAction() {
  return import("./pg_arrows_diag.js").then(m => {
    if (typeof m.toggleArrowVisibility === "function")
      return m.toggleArrowVisibility();
  }).catch(() => {});
}

pgkitRegisterAction("action.addNsGrid", {
  label: "+ N–S grid", scope: "global",
  run: addFloorNsGrid, meta: { variant: "plain" },
});
pgkitRegisterAction("action.addEwGrid", {
  label: "+ E–W grid", scope: "global",
  run: addFloorEwGrid, meta: { variant: "plain" },
});
pgkitRegisterAction("action.addWallV", {
  label: "+ Wall |", scope: "global",
  run: addWallVGrid, meta: { variant: "plain" },
});
pgkitRegisterAction("action.addWallH", {
  label: "+ Wall ─", scope: "global",
  run: addWallHGrid, meta: { variant: "plain" },
});
pgkitRegisterAction("action.save", {
  label: "Save", scope: "global",
  run: doSave, meta: { variant: "primary" },
});
pgkitRegisterAction("action.deleteVertex", {
  label: "Delete vertex", scope: "global",
  run: deleteSelectedVertex, meta: { variant: "danger" },
});
pgkitRegisterAction("action.deleteCable", {
  label: "Delete cable", scope: "global",
  run: deleteSelectedCable, meta: { variant: "danger" },
});
pgkitRegisterAction("action.clearAll", {
  label: "Clear all", scope: "global",
  run: clearAll, meta: { variant: "danger" },
});
pgkitRegisterAction("action.drawToggle", {
  label: "Draw cable", scope: "global",
  run: _toggleDrawAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.rulerToggle", {
  label: "Measure", scope: "global",
  run: _toggleRulerAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.resetZoomPan", {
  label: "Reset view", scope: "global",
  run: _resetViewAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.exportRuns", {
  label: "Export runs", scope: "global",
  run: _exportRunsAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.diagnostics", {
  label: "Diagnostics", scope: "global",
  run: _diagnosticsAction, meta: { variant: "plain" },
});
pgkitRegisterAction("action.toggleArrows", {
  label: "Toggle arrows", scope: "global",
  run: _toggleArrowsAction, meta: { variant: "plain" },
});

/* ==========================================================================
   PGKIT SELECT HANDLER
   ========================================================================== */

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;

  if (level === "cables") {
    const prefix = "cable.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    const all = _allCables();
    if (!(idx >= 0 && idx < all.length)) return false;
    state.selectedCable  = all[idx];
    state.selectedVertex = null;
    callDraw();
    return true;
  }

  if (level === "vertices") {
    const prefix = "vertex.";
    if (!name.startsWith(prefix)) return false;
    if (!state.selectedCable) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < state.selectedCable.anchorIds.length)) return false;
    state.selectedVertex = { cable: state.selectedCable, index: idx };
    callDraw();
    return true;
  }

  return false;
}

/* ==========================================================================
   BOOT SYNC
   ========================================================================== */

export function pgkitBootSync() {
  updateSnapPill();
  updateDrawButton();
  updateRulerButton();
  _rulerSyncMeasureInspector();
  pgkitNotifyAll();
}
"""
