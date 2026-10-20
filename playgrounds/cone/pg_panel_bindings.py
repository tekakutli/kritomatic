"""
pg_panel_bindings.py — sliders, checkboxes, selects, buttons, levels,
actions, `pgkitHandleSelect`, and `coneBootSync`.

ES module.  The remote-panel surface for cone.

EXPORTS
=======
    installPanelBindings    attach DOM listeners
    pgkitHandleSelect       translate remote `select` commands
    coneBootSync            initial sync sequence

LEVELS
======
    patches   one item per quad.  selected() is coneState.selectedQuad.
    squares   nested under the selected patch; one item per
              floatSquares entry with matching quadId.  selected() is
              coneState.selectedSquare.

ACTIONS
=======
Global:
    action.resetView            reset view.zoom/panX/panY to origin
    action.centerApex           cone.axRaw = cone.ayRaw = 0, clampApex
    action.saveScene            download scene JSON
    action.loadScene            requires scene_json file
    action.exportVisual         download visual-state JSON
    action.generateKra          requires output_path text
    action.toggleDrawQuad       flip drawTool.active

On `patches` level:
    action.addPatch             all scope
    action.clonePatch           selection scope
    action.deletePatch          row scope (button label `×`)

On `squares` level:
    action.addSquare            all scope (needs selected patch)
    action.cloneSquare          selection scope
    action.deleteSquare         row scope (button label `×`)

WHY NO AFTER-CHANGE HOOK HERE
=============================
The after-change hook (`pg_state.setAfterChange`) is wired in
`_boot.js` and fires `syncQuadList + draw + pgkitNotifyAll`.
Everything in this file routes mutations through either
`notifyChanged()` (from pg_state) or a direct `draw()` call + the
specific `pgkitNotifyLocal(...)` for whichever field changed.
Sliders call `notifyChanged()` at drag end for the broader sync.

CLAMP-ON-CONE-CHANGE — EVERY R-AFFECTING WRITE
==============================================
Four handlers in this file write directly to cone state that
participates in `R = depth · tan(halfAngle)`, the base radius the
apex is clamped against:

    depthSlider      input  → cone.depth
    cone.depth       setter → cone.depth
    angleSlider      input  → cone.halfAngle
    cone.halfAngle   setter → cone.halfAngle

Every one of these is followed by `clampApex()`.  Without it, a
panel-driven shrink of the depth or half-angle would leave the
DISPLAYED apex (`cone.ax/ay`) outside the (now-smaller) base, so
the apex marker renders off the cone and shapes project from a
point the cone surface no longer passes through.  The wheel
handler and the apex drag in `pg_dispatch.py` already call
`clampApex()` after their writes; the button handlers here for
`resetBtn` and `centerBtn` do too.  The four slider/field paths
above were the only remaining gaps.

The reason all four need explicit calls rather than a central
"clamp whenever depth or half-angle changes" hook: `cone` is a
plain object in `pg_state.js`, not a class with setters.  Adding
setters would mean every write site reads `cone.depth = x` (a
property assignment) and then a `clampApex()` on the next line —
which is exactly what we have.  A shared helper (`setDepth`/
`setHalfAngle`) would remove the four duplicated calls but would
force every future writer to remember to use the helper, which is
the same discipline we are trying to eliminate.  Duplicated explicit
calls are the more auditable form for four sites.
"""

PANEL_BINDINGS_JS = r"""
import {
  quads, floatSquares, coneState, SHAPE_STATE, drawTool,
  view, cone, layout,
  addQuad, cloneQuad, deleteQuad,
  addSquareAt, cloneSquare, deleteSquare,
  quadIdxById,
  syncPanelSliders, updatePanelLabels,
  clampApex,
  notifyChanged, toggleDrawTool, cancelDrawTool,
  fitView, callDraw,
} from "./pg_state.js";
import {
  _syncPatchCoordInputs,
} from "./pg_panel_fields.js";
import { syncQuadList } from "./pg_panel_lists.js";
import { openHelpWindow } from "./pg_panel_help.js";
import {
  saveSceneJSON, promptLoadScene, loadSceneFromText,
} from "./pg_scene.js";
import { exportVisualStateJSON } from "./pg_export.js";
import {
  generateKraFromScene, generateKraToPath,
} from "./pg_kra.js";
import {
  pgkitRegisterAction, pgkitRegisterLevel, pgkitRegisterField,
  pgkitNotifyLocal, pgkitNotifyAll,
} from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ==========================================================================
   RANGE SLIDERS + FIELD REGISTRATIONS
   ==========================================================================
   Each slider: (a) listens for `input`, (b) mutates the model,
   (c) updates the label, (d) calls pgkitNotifyLocal for the field.
   No full `notifyChanged()` per frame — that would rebuild the list
   on every drag pixel.  `draw()` is called via `callDraw()`.

   Depth and half-angle participate in R = depth · tan(halfAngle),
   the base radius the apex is clamped against.  Both sliders and
   both field registrations that write those values call
   `clampApex()` on the next line — see the module docstring,
   CLAMP-ON-CONE-CHANGE. */

const depthSlider = document.getElementById("depthSlider");
if (depthSlider) depthSlider.addEventListener("input", () => {
  cone.depth = parseFloat(depthSlider.value);
  clampApex();
  updatePanelLabels();
  pgkitNotifyLocal("cone.depth");
  callDraw();
});

pgkitRegisterField("cone.depth", {
  get: () => cone.depth,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    cone.depth = Math.max(3, Math.min(40, n));
    clampApex();
    const el = document.getElementById("depthSlider");
    if (el && document.activeElement !== el) el.value = n;
    updatePanelLabels();
    callDraw();
  },
  meta: { kind: "number", label: "Depth", min: 3, max: 40 },
});

const angleSlider = document.getElementById("angleSlider");
if (angleSlider) angleSlider.addEventListener("input", () => {
  cone.halfAngle = parseFloat(angleSlider.value) * Math.PI / 180;
  clampApex();
  updatePanelLabels();
  pgkitNotifyLocal("cone.halfAngle");
  callDraw();
});

pgkitRegisterField("cone.halfAngle", {
  get: () => cone.halfAngle * 180 / Math.PI,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    cone.halfAngle = Math.max(10, Math.min(60, n)) * Math.PI / 180;
    clampApex();
    const el = document.getElementById("angleSlider");
    if (el && document.activeElement !== el) el.value = Math.round(n);
    updatePanelLabels();
    callDraw();
  },
  meta: { kind: "number", label: "Half-angle (deg)", min: 10, max: 60 },
});

const ringsSlider = document.getElementById("ringsSlider");
if (ringsSlider) ringsSlider.addEventListener("input", () => {
  cone.ringCount = parseInt(ringsSlider.value, 10);
  updatePanelLabels();
  pgkitNotifyLocal("cone.ringCount");
  callDraw();
});

pgkitRegisterField("cone.ringCount", {
  get: () => cone.ringCount,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    cone.ringCount = Math.max(4, Math.min(40, n));
    const el = document.getElementById("ringsSlider");
    if (el && document.activeElement !== el) el.value = n;
    updatePanelLabels();
    callDraw();
  },
  meta: { kind: "number", label: "Rings", min: 4, max: 40 },
});

const meridiansSlider = document.getElementById("meridiansSlider");
if (meridiansSlider) meridiansSlider.addEventListener("input", () => {
  cone.meridianCount = parseInt(meridiansSlider.value, 10);
  updatePanelLabels();
  pgkitNotifyLocal("cone.meridianCount");
  callDraw();
});

pgkitRegisterField("cone.meridianCount", {
  get: () => cone.meridianCount,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    cone.meridianCount = Math.max(4, Math.min(48, n));
    const el = document.getElementById("meridiansSlider");
    if (el && document.activeElement !== el) el.value = n;
    updatePanelLabels();
    callDraw();
  },
  meta: { kind: "number", label: "Meridians", min: 4, max: 48 },
});

const shapeDepthSlider = document.getElementById("shapeDepthSlider");
if (shapeDepthSlider) shapeDepthSlider.addEventListener("input", () => {
  SHAPE_STATE.depthCone = parseFloat(shapeDepthSlider.value);
  updatePanelLabels();
  pgkitNotifyLocal("shape.depthCone");
  callDraw();
});

pgkitRegisterField("shape.depthCone", {
  get: () => SHAPE_STATE.depthCone,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    SHAPE_STATE.depthCone = Math.max(0.05, Math.min(2.0, n));
    const el = document.getElementById("shapeDepthSlider");
    if (el && document.activeElement !== el) el.value = n;
    updatePanelLabels();
    callDraw();
  },
  meta: { kind: "number", label: "Shape depth", min: 0.05, max: 2.0 },
});

/* ==========================================================================
   CHECKBOXES
   ========================================================================== */

const mirrorEl = document.getElementById("patchMirror");
if (mirrorEl) mirrorEl.addEventListener("change", () => {
  const i = coneState.selectedQuad;
  if (i < 0 || i >= quads.length) return;
  quads[i].mirror = !!mirrorEl.checked;
  pgkitNotifyLocal("patch.mirror");
  callDraw();
});

pgkitRegisterField("patch.mirror", {
  get: () => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return false;
    return !!quads[i].mirror;
  },
  set: (v) => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return;
    quads[i].mirror = !!v;
    const el = document.getElementById("patchMirror");
    if (el && document.activeElement !== el) el.checked = !!v;
    callDraw();
  },
  meta: { kind: "bool", label: "Mirror" },
});

const mirrorFlipEl = document.getElementById("patchMirrorFlip");
if (mirrorFlipEl) mirrorFlipEl.addEventListener("change", () => {
  const i = coneState.selectedQuad;
  if (i < 0 || i >= quads.length) return;
  quads[i].mirrorFlip = !!mirrorFlipEl.checked;
  pgkitNotifyLocal("patch.mirrorFlip");
  callDraw();
});

pgkitRegisterField("patch.mirrorFlip", {
  get: () => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return true;
    return quads[i].mirrorFlip !== false;
  },
  set: (v) => {
    const i = coneState.selectedQuad;
    if (i < 0 || i >= quads.length) return;
    quads[i].mirrorFlip = !!v;
    const el = document.getElementById("patchMirrorFlip");
    if (el && document.activeElement !== el) el.checked = !!v;
    callDraw();
  },
  meta: { kind: "bool", label: "Mirror flip" },
});

const visualBottomEl = document.getElementById("squareVisualBottom");
if (visualBottomEl) visualBottomEl.addEventListener("change", () => {
  const i = coneState.selectedSquare;
  if (i < 0 || i >= floatSquares.length) return;
  floatSquares[i].visualBottom = !!visualBottomEl.checked;
  pgkitNotifyLocal("square.visualBottom");
  callDraw();
});

pgkitRegisterField("square.visualBottom", {
  get: () => {
    const i = coneState.selectedSquare;
    if (i < 0 || i >= floatSquares.length) return false;
    return !!floatSquares[i].visualBottom;
  },
  set: (v) => {
    const i = coneState.selectedSquare;
    if (i < 0 || i >= floatSquares.length) return;
    floatSquares[i].visualBottom = !!v;
    const el = document.getElementById("squareVisualBottom");
    if (el && document.activeElement !== el) el.checked = !!v;
    callDraw();
  },
  meta: { kind: "bool", label: "Visual bottom" },
});

const kraRectsEl = document.getElementById("kraDrawRects");
if (kraRectsEl) kraRectsEl.addEventListener("change", () => {
  pgkitNotifyLocal("kra.drawRects");
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

const noRotateEl = document.getElementById("drawNoRotate");
if (noRotateEl) noRotateEl.addEventListener("change", () => {
  pgkitNotifyLocal("ui.drawNoRotate");
});

pgkitRegisterField("ui.drawNoRotate", {
  get: () => {
    const el = document.getElementById("drawNoRotate");
    return el ? !!el.checked : false;
  },
  set: (v) => {
    const el = document.getElementById("drawNoRotate");
    if (el) el.checked = !!v;
  },
  meta: { kind: "bool", label: "No rotate (draw fit)" },
});

/* ==========================================================================
   SELECTS
   ========================================================================== */

const drawFitEl = document.getElementById("drawFitMode");
if (drawFitEl) drawFitEl.addEventListener("change", () => {
  pgkitNotifyLocal("ui.drawFitMode");
});

pgkitRegisterField("ui.drawFitMode", {
  get: () => {
    const el = document.getElementById("drawFitMode");
    return el ? el.value : "shape-first";
  },
  set: (v) => {
    const el = document.getElementById("drawFitMode");
    if (el && typeof v === "string") el.value = v;
  },
  meta: {
    kind: "enum",
    label: "Draw fit mode",
    options: ["shape-first", "patch-first"],
  },
});

const kraPosEl = document.getElementById("kraTextPos");
if (kraPosEl) kraPosEl.addEventListener("change", () => {
  pgkitNotifyLocal("kra.textPosition");
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
  meta: {
    kind: "enum",
    label: "KRA text position",
    options: ["center","tl","tr","bl","br","top","bottom","left","right"],
  },
});

const kraColorEl = document.getElementById("kraTextColor");
if (kraColorEl) kraColorEl.addEventListener("change", () => {
  pgkitNotifyLocal("kra.textColor");
});

pgkitRegisterField("kra.textColor", {
  get: () => {
    const el = document.getElementById("kraTextColor");
    return el ? el.value : "color";
  },
  set: (v) => {
    const el = document.getElementById("kraTextColor");
    if (el && typeof v === "string") el.value = v;
  },
  meta: {
    kind: "enum",
    label: "KRA text color",
    options: ["color", "black"],
  },
});

const kraWarpEl = document.getElementById("kraTextWarp");
if (kraWarpEl) kraWarpEl.addEventListener("change", () => {
  pgkitNotifyLocal("kra.textWarp");
});

pgkitRegisterField("kra.textWarp", {
  get: () => {
    const el = document.getElementById("kraTextWarp");
    return el ? el.value : "square";
  },
  set: (v) => {
    const el = document.getElementById("kraTextWarp");
    if (el && typeof v === "string") el.value = v;
  },
  meta: {
    kind: "enum",
    label: "KRA text warp",
    options: ["square", "patch", "text", "text-shear", "patch-shear"],
  },
});

/* ==========================================================================
   ACTIONS — helpers
   ========================================================================== */

function _addPatchAction() {
  addQuad();
}

function _cloneSelectedPatchAction() {
  if (coneState.selectedQuad < 0 ||
      coneState.selectedQuad >= quads.length) {
    flashStatus("No patch selected", "warn");
    return;
  }
  cloneQuad(coneState.selectedQuad);
}

function _deletePatchRowAction(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "patch.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < quads.length)) return;
  deleteQuad(idx);
}

function _addSquareAction() {
  if (coneState.selectedQuad < 0 ||
      coneState.selectedQuad >= quads.length) {
    flashStatus("No patch selected", "warn");
    return;
  }
  addSquareAt(coneState.selectedQuad, 0, 0);
}

function _cloneSelectedSquareAction() {
  if (coneState.selectedSquare < 0 ||
      coneState.selectedSquare >= floatSquares.length) {
    flashStatus("No square selected", "warn");
    return;
  }
  cloneSquare(coneState.selectedSquare);
}

function _deleteSquareRowAction(payload) {
  if (!payload || !payload.target) return;
  const name = payload.target.name || "";
  const prefix = "square.";
  if (!name.startsWith(prefix)) return;
  const idx = parseInt(name.slice(prefix.length), 10);
  if (!(idx >= 0 && idx < floatSquares.length)) return;
  deleteSquare(idx);
}

function _resetViewAction() {
  view.zoom = 1;
  view.panX = 0;
  view.panY = 0;
  fitView();
  pgkitNotifyAll();
  callDraw();
}

function _centerApexAction() {
  // Reset the INTENT (raw) to (0, 0), then re-derive the displayed
  // apex. Because zero is always inside the base, this collapses both
  // values to (0, 0) at any depth.
  cone.axRaw = 0;
  cone.ayRaw = 0;
  clampApex();
  pgkitNotifyLocal("cone.apexX");
  pgkitNotifyLocal("cone.apexY");
  callDraw();
}

function _toggleDrawQuadAction() {
  toggleDrawTool();
}

function _saveSceneAction() {
  saveSceneJSON();
}

function _loadSceneFromPayload(payload) {
  if (!payload || typeof payload.scene_json !== "string") {
    promptLoadScene();
    return;
  }
  loadSceneFromText(payload.scene_json);
}

function _exportVisualAction() {
  exportVisualStateJSON();
}

function _generateKraAction(payload) {
  const path = payload && typeof payload.output_path === "string"
    ? payload.output_path.trim() : "";
  if (path) generateKraToPath(path);
  else generateKraFromScene();
}

/* ==========================================================================
   LEVELS
   ========================================================================== */

pgkitRegisterLevel("patches", {
  label: "Patches",
  items: () => quads.map((q, i) => ({
    name: "patch." + i,
    label: q.name,
  })),
  selected: () => coneState.selectedQuad >= 0
    ? "patch." + coneState.selectedQuad : null,
  parent: () => null,
});

pgkitRegisterLevel("squares", {
  label: "Squares",
  parent: () => coneState.selectedQuad >= 0
    ? { level: "patches", name: "patch." + coneState.selectedQuad }
    : null,
  items: () => {
    if (coneState.selectedQuad < 0 ||
        coneState.selectedQuad >= quads.length) return [];
    const activeQ = quads[coneState.selectedQuad];
    const out = [];
    for (let i = 0; i < floatSquares.length; i++) {
      if (floatSquares[i].quadId === activeQ.id) {
        out.push({
          name: "square." + i,
          label: floatSquares[i].name || ("S" + floatSquares[i].id),
        });
      }
    }
    return out;
  },
  selected: () => coneState.selectedSquare >= 0
    ? "square." + coneState.selectedSquare : null,
});

/* ==========================================================================
   SELECT HANDLER
   ========================================================================== */

export function pgkitHandleSelect(level, name) {
  if (typeof name !== "string") return false;

  if (level === "patches") {
    const prefix = "patch.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < quads.length)) return false;
    coneState.selectedQuad = idx;
    coneState.selectedSquare = -1;
    notifyChanged();
    return true;
  }

  if (level === "squares") {
    const prefix = "square.";
    if (!name.startsWith(prefix)) return false;
    const idx = parseInt(name.slice(prefix.length), 10);
    if (!(idx >= 0 && idx < floatSquares.length)) return false;
    coneState.selectedSquare = idx;
    const sq = floatSquares[idx];
    const qi = quadIdxById(sq.quadId);
    if (qi >= 0) coneState.selectedQuad = qi;
    notifyChanged();
    return true;
  }

  return false;
}

/* ==========================================================================
   BUTTON BINDINGS
   ========================================================================== */

export function installPanelBindings() {
  const resetBtn    = document.getElementById("resetBtn");
  const centerBtn   = document.getElementById("centerBtn");
  const addQuadBtn  = document.getElementById("addQuadBtn");
  const cloneQuadBtn = document.getElementById("cloneQuadBtn");
  const addSqBtn    = document.getElementById("addSquareBtn");
  const cloneSqBtn  = document.getElementById("cloneSquareBtn");
  const exportBtn   = document.getElementById("exportBtn");
  const generateKraBtn = document.getElementById("generateKraBtn");
  const saveSceneBtn = document.getElementById("saveSceneBtn");
  const loadSceneBtn = document.getElementById("loadSceneBtn");
  const drawQuadBtn = document.getElementById("drawQuadBtn");
  const helpBtn     = document.getElementById("helpBtn");

  if (resetBtn) resetBtn.addEventListener("click", () => {
    view.zoom = 1; view.panX = 0; view.panY = 0;
    cone.axRaw = 0; cone.ayRaw = 0;
    cone.depth = 15;
    cone.halfAngle = Math.PI / 6;
    cone.ringCount = 14;
    cone.meridianCount = 24;
    clampApex();
    syncPanelSliders();
    fitView();
    pgkitNotifyAll();
    callDraw();
  });

  if (centerBtn) centerBtn.addEventListener("click", _centerApexAction);

  if (addQuadBtn) addQuadBtn.addEventListener("click", _addPatchAction);
  if (cloneQuadBtn) cloneQuadBtn.addEventListener("click", _cloneSelectedPatchAction);
  if (addSqBtn) addSqBtn.addEventListener("click", _addSquareAction);
  if (cloneSqBtn) cloneSqBtn.addEventListener("click", _cloneSelectedSquareAction);

  if (exportBtn) exportBtn.addEventListener("click", _exportVisualAction);
  if (generateKraBtn) generateKraBtn.addEventListener("click", generateKraFromScene);
  if (saveSceneBtn) saveSceneBtn.addEventListener("click", _saveSceneAction);
  if (loadSceneBtn) loadSceneBtn.addEventListener("click", () => promptLoadScene());
  if (drawQuadBtn) drawQuadBtn.addEventListener("click", _toggleDrawQuadAction);
  if (helpBtn) helpBtn.addEventListener("click", openHelpWindow);

  window.addEventListener("keydown", (e) => {
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" ||
              t.tagName === "SELECT")) return;

    if (e.key === "Escape" && drawTool.active) {
      e.preventDefault();
      cancelDrawTool();
      return;
    }

    if (e.key === "Delete" || e.key === "Backspace") {
      if (coneState.selectedSquare >= 0) {
        e.preventDefault();
        deleteSquare(coneState.selectedSquare);
      }
    }
  });
}

/* ==========================================================================
   ACTIONS — global
   ========================================================================== */

pgkitRegisterAction("action.resetView", {
  label: "Reset view",
  scope: "global",
  run: _resetViewAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.centerApex", {
  label: "Center apex",
  scope: "global",
  run: _centerApexAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.toggleDrawQuad", {
  label: "Draw Quad",
  scope: "global",
  run: _toggleDrawQuadAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.saveScene", {
  label: "Save scene",
  scope: "global",
  run: _saveSceneAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.loadScene", {
  label: "Load scene",
  scope: "global",
  run: _loadSceneFromPayload,
  meta: {
    variant: "plain",
    requires: [
      { name: "scene_json", kind: "file", label: "scene file",
        accept: ".json,application/json" },
    ],
  },
});

pgkitRegisterAction("action.exportVisual", {
  label: "Export visual state",
  scope: "global",
  run: _exportVisualAction,
  meta: { variant: "plain" },
});

pgkitRegisterAction("action.generateKra", {
  label: "Generate .kra",
  scope: "global",
  run: _generateKraAction,
  meta: {
    variant: "danger",
    requires: [
      { name: "output_path", kind: "text", label: "path",
        default: "/tmp/cone_scene.kra" },
    ],
  },
});

/* ==========================================================================
   ACTIONS — patches
   ========================================================================== */

pgkitRegisterAction("action.addPatch", {
  label: "+ Patch",
  scope: "all",
  level: "patches",
  run: _addPatchAction,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.clonePatch", {
  label: "+ Clone",
  scope: "selection",
  level: "patches",
  run: _cloneSelectedPatchAction,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.deletePatch", {
  label: "\u00D7",
  scope: "row",
  level: "patches",
  appliesTo: () => true,
  run: _deletePatchRowAction,
  meta: { variant: "danger" },
});

/* ==========================================================================
   ACTIONS — squares
   ========================================================================== */

pgkitRegisterAction("action.addSquare", {
  label: "+ Square",
  scope: "all",
  level: "squares",
  run: _addSquareAction,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.cloneSquare", {
  label: "+ Clone",
  scope: "selection",
  level: "squares",
  run: _cloneSelectedSquareAction,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.deleteSquare", {
  label: "\u00D7",
  scope: "row",
  level: "squares",
  appliesTo: () => true,
  run: _deleteSquareRowAction,
  meta: { variant: "danger" },
});

/* ==========================================================================
   BOOT HOOK
   ========================================================================== */

export function coneBootSync() {
  syncPanelSliders();
  _syncPatchCoordInputs();
  syncQuadList();
  pgkitNotifyAll();
}
"""
