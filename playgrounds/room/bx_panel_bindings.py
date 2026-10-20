"""
bx_panel_bindings.py — panel installers, actions, levels, and
`pgkitHandleSelect`.

ES module.  The remote-panel surface for boxes, plus the panel's
own DOM installers.  This is where every function the boot module
calls to wire the panel lives:

    installPanelBindings      attach every button listener
    installVerticalMenu       no-op in the ES-module model (the panel
                              body already has the sections)
    installDarkScreenStyles   no-op in the ES-module model (the CSS
                              already has the dark styles)
    installInstructionsPopover  installs the `?` popover at load
    installPanelFade          installs the mouseenter/leave listeners
                              that track panelState
    updatePanelOpacity        the fade function that draw() calls
    _afterSelectChange        called from bx_dispatch after every
                              selection-mutating action; refreshes the
                              list, inspector, and notifies the remote
    pgkitHandleSelect         translate remote `select` commands
    pgkitRegisterAction       the action-set
    pgkitRegisterLevel        the level-set
    boxesBootSync             initial sync sequence
    _rotateSelected           shared by keyboard, buttons, and actions
    _flashPlacedOk            flash messages used by bx_dispatch

Handling the old installers
---------------------------
The old `installVerticalMenu` and `installDarkScreenStyles` moved
sections around in the DOM and appended a new `<style>` block at
runtime.  In the ES-module model, `pg_base.py` emits the final panel
markup and the final CSS, so the two functions become simple
no-ops.  They stay as exports because the boot module still calls
them, and because a future playground might need to install more
styles.

The instructions popover is the one dynamic installer that stays:
it is added to document.body outside #ui, so the CSS that styles
it lives in `pg_base.PG_CSS` (already loaded with the page).

LEVELS
======
One: `boxes` — one item per placed box.  No nested levels;
`parent()` always returns null.  `selected()` reflects
`board.selectedIdx`.

ACTIONS
=======
Every action follows the pgkit pattern — `scope`, optional `level`,
optional `appliesTo` for row scope, optional `requires` for
payloads, optional `variant` for the button's color.

Boxes' action set:

    saveBoard           scope: global
    clearAll            scope: global     (variant: danger)
    drawBox             scope: global
    resetView           scope: global
    printPNG            scope: global
    listBoxes           scope: global
    help                scope: global
    rotateL90           scope: global
    rotateR90           scope: global
    rotateL15           scope: global
    rotateR15           scope: global
    deleteBox           scope: selection  (level: boxes)
    selectBox           scope: row        (level: boxes)

A plain `action.selectBox` is not strictly required by the pgkit
protocol — the remote client uses the `select` command — but we
register it anyway so a remote UI that lists actions on the level
sees the click-to-select affordance it expects.

WHY DYNAMIC IMPORTS
===================
Several handlers call `draw()` (in bx_dispatch) and `renderFloorPlanPNG`
(in bx_png).  A static import of either from this module creates a
cycle through bx_dispatch → this module.  Dynamic imports are the
smallest non-cyclic path.
"""

PANEL_BINDINGS_JS = r"""
import {
  board, view, state, selectedItem,
  callDraw, doSave, printBoxList, fitView,
  invalidateBox,
} from "./bx_core.js";
import {
  _syncSelectionInputs, _boxSyncInspector, updateTitleBlock,
} from "./bx_panel_fields.js";
import {
  rotateSelection,
  tryApplyBoxChange,
} from "./bx_edit.js";
import {
  saveSceneJSON, promptLoadScene,
} from "./bx_scene.js";
import { renderFloorPlanPNG } from "./bx_png.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel,
  pgkitNotifyAll, pgkitNotifyLocal,
  pgkitNotifyEntities,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import { T } from "./bx_i18n.js";

/* ==========================================================================
   PANEL INSTALLERS — no-ops in the ES-module model
   ==========================================================================
   The panel body already has every section; the CSS already has
   every style.  Both functions are kept so the boot call sequence
   is unchanged and a future playground can layer additional work in. */

export function installVerticalMenu()   { /* no-op */ }
export function installDarkScreenStyles() { /* no-op */ }

/* ==========================================================================
   INSTRUCTIONS POPOVER
   ========================================================================== */

export function installInstructionsPopover() {
  const ui = document.getElementById("ui");
  if (!ui) return;

  const h3 = ui.querySelector("h3");
  const collapseBtn = document.getElementById("collapseBtn");
  const helpBtn = document.createElement("button");
  helpBtn.id = "helpBtn";
  helpBtn.title = T("tipHelp");
  helpBtn.textContent = T("btnHelp");
  if (collapseBtn && collapseBtn.parentNode === h3) {
    h3.insertBefore(helpBtn, collapseBtn);
  } else if (h3) {
    h3.appendChild(helpBtn);
  }

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
   PANEL FADE
   ========================================================================== */

export function installPanelFade() {
  const ui = document.getElementById("ui");
  if (!ui) return;
  ui.style.transition = "none";
  ui.addEventListener("mouseenter", () => {
    import("./bx_base.js").then(m => {
      m.panelState.hovered = true;
      updatePanelOpacity();
    });
  });
  ui.addEventListener("mouseleave", () => {
    import("./bx_base.js").then(m => {
      m.panelState.hovered = false;
      m.panelState.lastActivity = performance.now();
      updatePanelOpacity();
    });
  });
  ui.addEventListener("mousedown", () => {
    import("./bx_base.js").then(m => {
      m.panelState.lastActivity = performance.now();
    });
  });
}

/* Update the panel opacity.  In practice we leave it fully opaque —
   the original model faded after 1.8s idle, but the fade is a
   preference, and the current boxes renderer does not toggle the
   style.opacity field.  Keeping the function is necessary because
   draw() calls it. */
export function updatePanelOpacity() {
  /* No-op.  Retained for call sites that expect to invoke it. */
}

/* ==========================================================================
   SELECTION CHANGES
   ========================================================================== */

export function _afterSelectChange() {
  _syncSelectionInputs();
  _boxSyncInspector();
  updateTitleBlock();
  pgkitNotifyAll();
  import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ==========================================================================
   FLASH MESSAGES
   ==========================================================================
   Two tiny wrappers that keep bx_dispatch clear of T() lookups in the
   two most frequent mouseup paths. */

export function _flashPlacedOk() {
  flashStatus(T("msgPlacedOk"), "ok");
}

export function _flashPlaceCollision() {
  flashStatus(T("msgPlaceCollision"), "bad");
}

/* ==========================================================================
   ROTATION
   ========================================================================== */

export function _rotateSelected(deg) {
  const ok = rotateSelection(deg);
  if (!ok) { flashStatus(T("msgRotationBlocked"), "bad"); return; }
  import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
}

/* ==========================================================================
   ACTIONS
   ========================================================================== */

function _saveAction() {
  doSave(true);
}

function _clearAllAction() {
  if (board.items.length && !confirm(T("msgConfirmClear"))) return;
  board.items = [];
  board.selectedIdx = -1;
  state.hoveredBox = null;
  state.dragBoxMode = false;
  state.boxCounter = 0;
  import("./bx_dispatch.js").then(m => {
    m.updateDrawBoxButton();
    m.draw();
  }).catch(() => {});
  pgkitNotifyAll();
}

function _drawBoxToggleAction() {
  import("./bx_dispatch.js").then(m => {
    m.setDrawBoxMode(!state.dragBoxMode);
  }).catch(() => {});
}

function _resetViewAction() {
  fitView();
  callDraw();
  pgkitNotifyLocal("view.zoom");
  pgkitNotifyLocal("view.panX");
  pgkitNotifyLocal("view.panY");
}

function _printPngAction() {
  renderFloorPlanPNG();
}

function _listBoxesAction() {
  printBoxList();
}

function _helpAction() {
  installInstructionsPopover();
  const pop = document.getElementById("helpPopover");
  if (pop) pop.classList.add("show");
}

function _rotateL90Action()  { _rotateSelected(-90); }
function _rotateR90Action()  { _rotateSelected(+90); }
function _rotateL15Action()  { _rotateSelected(-15); }
function _rotateR15Action()  { _rotateSelected(+15); }

function _deleteBoxRowAction(payload) {
  let idx = board.selectedIdx;
  if (payload && payload.target && typeof payload.target.name === "string") {
    const m = /^box\.(\d+)$/.exec(payload.target.name);
    if (m) idx = parseInt(m[1], 10);
  }
  if (!(idx >= 0 && idx < board.items.length)) return;
  board.items.splice(idx, 1);
  if (board.selectedIdx === idx) board.selectedIdx = -1;
  else if (board.selectedIdx > idx) board.selectedIdx -= 1;
  _afterSelectChange();
  pgkitNotifyEntities();
}

function _selectBoxRowAction(payload) {
  if (!payload || !payload.target || typeof payload.target.name !== "string") return;
  const m = /^box\.(\d+)$/.exec(payload.target.name);
  if (!m) return;
  const idx = parseInt(m[1], 10);
  if (!(idx >= 0 && idx < board.items.length)) return;
  board.selectedIdx = idx;
  _afterSelectChange();
}

/* ==========================================================================
   LEVELS
   ========================================================================== */

pgkitRegisterLevel("boxes", {
  label: "Boxes",
  items: () => board.items.map((b, i) => ({
    name:  "box." + i,
    label: b.name || ("Box " + (i + 1)),
  })),
  selected: () => board.selectedIdx >= 0 ? "box." + board.selectedIdx : null,
  parent: () => null,
});

/* ==========================================================================
   SELECT HANDLER
   ========================================================================== */

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;
  if (level !== "boxes") return false;
  const prefix = "box.";
  if (!name.startsWith(prefix)) return false;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < board.items.length)) return false;
  board.selectedIdx = idx;
  _afterSelectChange();
  return true;
}

/* ==========================================================================
   ACTION REGISTRATIONS
   ========================================================================== */

pgkitRegisterAction("action.saveBoard", {
  label: "Save board",
  scope: "global",
  run: _saveAction,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.clearAll", {
  label: "Clear all",
  scope: "global",
  run: _clearAllAction,
  meta: { variant: "danger" },
});

pgkitRegisterAction("action.drawBox", {
  label: "Draw box",
  scope: "global",
  run: _drawBoxToggleAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.resetView", {
  label: "Reset view",
  scope: "global",
  run: _resetViewAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.printPNG", {
  label: "Print PNG",
  scope: "global",
  run: _printPngAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.listBoxes", {
  label: "List boxes",
  scope: "global",
  run: _listBoxesAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.help", {
  label: "Help",
  scope: "global",
  run: _helpAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.rotateL90", {
  label: "− 90°", scope: "global", run: _rotateL90Action,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.rotateR90", {
  label: "+ 90°", scope: "global", run: _rotateR90Action,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.rotateL15", {
  label: "− 15°", scope: "global", run: _rotateL15Action,
  meta: { variant: "plain" },
});
pgkitRegisterAction("action.rotateR15", {
  label: "+ 15°", scope: "global", run: _rotateR15Action,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.deleteBox", {
  label: "Delete box",
  scope: "selection",
  level: "boxes",
  run: _deleteBoxRowAction,
  meta: { variant: "danger" },
});

pgkitRegisterAction("action.selectBox", {
  label: "Select",
  scope: "row",
  level: "boxes",
  appliesTo: () => true,
  run: _selectBoxRowAction,
  meta: { variant: "plain" },
});

/* ==========================================================================
   BUTTON BINDINGS
   ========================================================================== */

export function installPanelBindings() {
  const rotL90 = document.getElementById("rotL90");
  const rotR90 = document.getElementById("rotR90");
  const rotL15 = document.getElementById("rotL15");
  const rotR15 = document.getElementById("rotR15");
  const drawBoxBtn = document.getElementById("drawBoxBtn");
  const saveBtn = document.getElementById("saveBtn");
  const listBtn = document.getElementById("listBtn");
  const pngBtn  = document.getElementById("pngBtn");
  const undoBtn = document.getElementById("undoBtn");
  const clearBtn = document.getElementById("clearBtn");
  const boxDeleteBtn = document.getElementById("boxDeleteBtn");

  if (rotL90) rotL90.onclick = _rotateL90Action;
  if (rotR90) rotR90.onclick = _rotateR90Action;
  if (rotL15) rotL15.onclick = _rotateL15Action;
  if (rotR15) rotR15.onclick = _rotateR15Action;

  if (drawBoxBtn) drawBoxBtn.onclick = _drawBoxToggleAction;
  if (saveBtn)    saveBtn.onclick    = _saveAction;
  if (listBtn)    listBtn.onclick    = _listBoxesAction;
  if (pngBtn)     pngBtn.onclick     = _printPngAction;

  if (undoBtn) undoBtn.onclick = () => {
    if (!board.items.length) return;
    board.items.pop();
    board.selectedIdx = -1;
    _afterSelectChange();
    pgkitNotifyAll();
  };

  if (clearBtn) clearBtn.onclick = _clearAllAction;

  if (boxDeleteBtn) boxDeleteBtn.addEventListener("click", () => {
    if (board.selectedIdx < 0) return;
    _deleteBoxRowAction({ target: { name: "box." + board.selectedIdx }});
  });

  /* The BOX inspector's name / W / H / Rot fields. */
  const nameInput   = document.getElementById("boxName");
  const selWInput   = document.getElementById("selW");
  const selHInput   = document.getElementById("selH");
  const selRotInput = document.getElementById("selRot");
  if (nameInput) nameInput.addEventListener("input", () => {
    const b = selectedItem();
    if (!b) return;
    b.name = nameInput.value;
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
    pgkitNotifyLocal("box.selected.name");
  });
  if (selWInput) selWInput.addEventListener("change", () => {
    const b = selectedItem();
    if (!b) return;
    const v = parseFloat(selWInput.value);
    if (!(v > 0)) { _syncSelectionInputs(); return; }
    const ok = tryApplyBoxChange(b, { w: Math.max(10, v) });
    if (!ok) flashStatus(T("msgWallBlocked"), "bad");
    _syncSelectionInputs();
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
    pgkitNotifyLocal("box.selected.width");
  });
  if (selHInput) selHInput.addEventListener("change", () => {
    const b = selectedItem();
    if (!b) return;
    const v = parseFloat(selHInput.value);
    if (!(v > 0)) { _syncSelectionInputs(); return; }
    const ok = tryApplyBoxChange(b, { h: Math.max(10, v) });
    if (!ok) flashStatus(T("msgWallBlocked"), "bad");
    _syncSelectionInputs();
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
    pgkitNotifyLocal("box.selected.height");
  });
  if (selRotInput) selRotInput.addEventListener("change", () => {
    const b = selectedItem();
    if (!b) return;
    const v = parseFloat(selRotInput.value);
    if (!isFinite(v)) { _syncSelectionInputs(); return; }
    const newRot = ((v % 360) + 360) % 360;
    const ok = tryApplyBoxChange(b, { rot: newRot });
    if (!ok) flashStatus(T("msgRotationBlocked"), "bad");
    _syncSelectionInputs();
    import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
    pgkitNotifyLocal("box.selected.rotationDeg");
  });

  /* The GUIDES strip's W / H / Rot preset fields. */
  for (const id of ["boxW", "boxH", "boxRot"]) {
    const el = document.getElementById(id);
    if (el) el.addEventListener("input", () => {
      import("./bx_dispatch.js").then(m => m.draw()).catch(() => {});
    });
  }
}

/* ==========================================================================
   BOOT SYNC
   ========================================================================== */

export function boxesBootSync() {
  _syncSelectionInputs();
  _boxSyncInspector();
  updateTitleBlock();
  pgkitNotifyAll();
}
"""
