"""Scrub inputs, checkboxes, tool radios, text inputs, and the
`pgkitRegisterField` registrations for every panel field.

STATUS.RUNNING
==============
`status.running` is a read-only boolean registered at the bottom of
this file.  It exposes `board.running` — already toggled by
`pg_comfy_client.py` around every HTTP round-trip — as a pgkit field
that scripting clients (the CLI, `remote_panel`, any Python using
`RemoteCliClient`) can wait on.  `bundle(wait_for_running=True)` in
`pgkit.remote_cli_client` waits on exactly this name; without the
registration the CLI silently falls back to `wait_change`, which is
imprecise for back-to-back actions.

No calls to `pgkitNotifyAll` need adding: `pg_comfy_client.py` already
calls it immediately after every `board.running` flip, so the notify
path is already in place.

`visible: false` marks the field as API-only — `pgkit/render.py`
already honours this, and `remote_panel/rp_client.py` was updated in
parallel to match that contract.  If a future build omits the
`rp_client.py` guard, the field would render as a checkbox the user
could click with no effect; the guard is what keeps the panel honest."""

PANEL_SCRUBS_JS = r"""
import { board } from "./pg_state.js";
import {
  selectedMask, selectedMaskIdx,
  clearMaskSelection, replaceMaskSelection, setMaskPrompt,
} from "./pg_masks.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import {
  pgkitRegisterField, pgkitNotifyLocal,
} from "./pgkit/remote_client.js";

/* ============================================================
   Scrub inputs
   ============================================================ */

const thresholdEl = document.getElementById("thresholdVal");
if (thresholdEl) _installScrubInput(thresholdEl, {
  rate: 0.002, snapStep: 0.05,
  read: () => board.threshold,
  write: (raw) => {
    board.threshold = Math.max(0, Math.min(1, raw));
    pgkitNotifyLocal("settings.threshold");
  },
});

const refineEl = document.getElementById("refineVal");
if (refineEl) _installScrubInput(refineEl, {
  rate: 0.05,
  read: () => board.refineIterations,
  write: (raw) => {
    board.refineIterations = Math.max(0, Math.min(20, Math.round(raw)));
    pgkitNotifyLocal("settings.refineIterations");
  },
});

const regionSizeEl = document.getElementById("regionSize");
if (regionSizeEl) _installScrubInput(regionSizeEl, {
  rate: 0.5, snapStep: 5,
  read: () => board.regionSize,
  write: (raw) => {
    board.regionSize = Math.max(2, Math.min(500, Math.round(raw)));
    pgkitNotifyLocal("region.size");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
});

/* ============================================================
   Krita edge-cleanup scrubs
   ============================================================
   Four scrub inputs feeding the daemon's edge-cleanup pipeline.
   Rates are tuned so one screen-pixel of horizontal drag moves the
   value by ~1 for the integer fields (expand/contract/smooth) and
   ~0.1 for feather, which is the one field users will dial in
   finely. Snap steps are 1 for all four so a drag lands on whole
   pixels; a user who wants sub-pixel granularity can type into the
   field directly. */

const kritaFeatherEl = document.getElementById("kritaFeather");
if (kritaFeatherEl) _installScrubInput(kritaFeatherEl, {
  rate: 0.1, snapStep: 1,
  read: () => board.kritaFeather,
  write: (raw) => {
    board.kritaFeather = Math.max(0, Math.min(100, Math.round(raw)));
    pgkitNotifyLocal("krita.feather");
  },
});

const kritaExpandEl = document.getElementById("kritaExpand");
if (kritaExpandEl) _installScrubInput(kritaExpandEl, {
  rate: 0.1, snapStep: 1,
  read: () => board.kritaExpand,
  write: (raw) => {
    board.kritaExpand = Math.max(0, Math.min(50, Math.round(raw)));
    pgkitNotifyLocal("krita.expand");
  },
});

const kritaContractEl = document.getElementById("kritaContract");
if (kritaContractEl) _installScrubInput(kritaContractEl, {
  rate: 0.1, snapStep: 1,
  read: () => board.kritaContract,
  write: (raw) => {
    board.kritaContract = Math.max(0, Math.min(50, Math.round(raw)));
    pgkitNotifyLocal("krita.contract");
  },
});

const kritaSmoothEl = document.getElementById("kritaSmooth");
if (kritaSmoothEl) _installScrubInput(kritaSmoothEl, {
  rate: 0.05, snapStep: 1,
  read: () => board.kritaSmooth,
  write: (raw) => {
    board.kritaSmooth = Math.max(0, Math.min(10, Math.round(raw)));
    pgkitNotifyLocal("krita.smooth");
  },
});

/* Opacity scrub — PERCENT (0-100).
   Rate 0.5 means 2 screen-pixels of drag = 1%. Snap step 5 means
   Shift+drag moves in 5% increments, which is how a user reading a
   percentage usually thinks. The value stored on `board.kritaOpacity`
   and the value this scrub reads/writes are both percent; the byte
   conversion happens once, on the way to the daemon, in
   `_getKritaExtractOptions` in pg_panel_bindings.js. */

const kritaOpacityEl = document.getElementById("kritaOpacity");
if (kritaOpacityEl) _installScrubInput(kritaOpacityEl, {
  rate: 0.5, snapStep: 5,
  read: () => board.kritaOpacity,
  write: (raw) => {
    board.kritaOpacity = Math.max(0, Math.min(100, Math.round(raw)));
    pgkitNotifyLocal("krita.opacity");
  },
});

/* ============================================================
   Checkboxes
   ============================================================ */

const individualEl = document.getElementById("individualMasks");
if (individualEl) {
  individualEl.checked = !!board.individualMasks;
  individualEl.addEventListener("change", () => {
    board.individualMasks = !!individualEl.checked;
    pgkitNotifyLocal("settings.individualMasks");
  });
}

const autoRunEl = document.getElementById("autoRun");
if (autoRunEl) {
  autoRunEl.checked = !!board.autoRun;
  autoRunEl.addEventListener("change", () => {
    board.autoRun = !!autoRunEl.checked;
    pgkitNotifyLocal("settings.autoRun");
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
  });
}

const regionModeEl = document.getElementById("regionMode");
if (regionModeEl) {
  regionModeEl.checked = !!board.regionMode;
  regionModeEl.addEventListener("change", () => {
    board.regionMode = !!regionModeEl.checked;
    board.lassoPoints = [];
    board.freehandPoints = [];
    board.freehandActive = false;
    pgkitNotifyLocal("region.mode");
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  });
}

const regionSubtractEl = document.getElementById("regionSubtract");
if (regionSubtractEl) {
  regionSubtractEl.checked = !!board.regionSubtract;
  regionSubtractEl.addEventListener("change", () => {
    board.regionSubtract = !!regionSubtractEl.checked;
    pgkitNotifyLocal("region.subtract");
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  });
}

const kritaInvertEl = document.getElementById("kritaInvert");
if (kritaInvertEl) {
  kritaInvertEl.checked = !!board.kritaInvert;
  kritaInvertEl.addEventListener("change", () => {
    board.kritaInvert = !!kritaInvertEl.checked;
    pgkitNotifyLocal("krita.invert");
  });
}

// Opacity has a companion checkbox that gates whether the value is
// sent at all. Without it, a fresh extract would always issue
// set_layer_opacity, which would silently override the opacity a
// duplicate-mode extract inherited from the source layer. Gating
// keeps "inherit" the default and makes "override" an explicit choice.
const kritaOpacityEnabledEl = document.getElementById("kritaOpacityEnabled");
if (kritaOpacityEnabledEl && kritaOpacityEl) {
  kritaOpacityEl.disabled = !kritaOpacityEnabledEl.checked;
  kritaOpacityEnabledEl.addEventListener("change", () => {
    kritaOpacityEl.disabled = !kritaOpacityEnabledEl.checked;
    pgkitNotifyLocal("krita.opacityEnabled");
  });
}

/* ============================================================
   Tool radios
   ============================================================ */

const regionToolBrush    = document.getElementById("regionToolBrush");
const regionToolLasso    = document.getElementById("regionToolLasso");
const regionToolFreehand = document.getElementById("regionToolFreehand");

function _syncRegionToolRadios() {
  if (regionToolBrush)    regionToolBrush.checked    = (board.regionTool === 'brush');
  if (regionToolLasso)    regionToolLasso.checked    = (board.regionTool === 'lasso');
  if (regionToolFreehand) regionToolFreehand.checked = (board.regionTool === 'freehand');
}
_syncRegionToolRadios();

function _setRegionTool(tool) {
  board.regionTool = tool;
  board.lassoPoints = [];
  board.freehandPoints = [];
  board.freehandActive = false;
  board.freehandIsErase = false;
  pgkitNotifyLocal("region.tool");
  import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
  import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
}

if (regionToolBrush) regionToolBrush.addEventListener("change", () => {
  if (regionToolBrush.checked) _setRegionTool('brush');
});
if (regionToolLasso) regionToolLasso.addEventListener("change", () => {
  if (regionToolLasso.checked) _setRegionTool('lasso');
});
if (regionToolFreehand) regionToolFreehand.addEventListener("change", () => {
  if (regionToolFreehand.checked) _setRegionTool('freehand');
});

/* ============================================================
   Text inputs
   ============================================================ */

const promptEl = document.getElementById("promptText");
if (promptEl) {
  promptEl.value = board.textPrompt || "";
  promptEl.addEventListener("input", () => {
    board.textPrompt = promptEl.value;
    pgkitNotifyLocal("prompt.text");
  });
}

const maskPromptEl = document.getElementById("maskPromptText");
if (maskPromptEl) {
  maskPromptEl.addEventListener("input", () => {
    const idx = selectedMaskIdx();
    if (idx < 0) return;
    setMaskPrompt(idx, maskPromptEl.value);
    pgkitNotifyLocal("mask.textPrompt");
  });
}

const kritaGroupEl = document.getElementById("kritaGroup");
if (kritaGroupEl) {
  kritaGroupEl.value = board.kritaGroup || "";
  kritaGroupEl.addEventListener("input", () => {
    board.kritaGroup = kritaGroupEl.value;
    pgkitNotifyLocal("krita.group");
  });
}

/* ============================================================
   Model radios
   ============================================================ */

const modelSam2 = document.getElementById("modelSam2");
const modelSam3 = document.getElementById("modelSam3");
function _syncModelRadios() {
  if (modelSam2) modelSam2.checked = (board.refineModel === 'sam2');
  if (modelSam3) modelSam3.checked = (board.refineModel === 'sam3');
}
_syncModelRadios();
if (modelSam2) modelSam2.addEventListener("change", () => {
  if (modelSam2.checked) {
    board.refineModel = 'sam2';
    pgkitNotifyLocal("refine.model");
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  }
});
if (modelSam3) modelSam3.addEventListener("change", () => {
  if (modelSam3.checked) {
    board.refineModel = 'sam3';
    pgkitNotifyLocal("refine.model");
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  }
});

/* ============================================================
   Krita selects (sel mode + blend mode)
   ============================================================ */

const kritaSelModeEl = document.getElementById("kritaSelMode");
if (kritaSelModeEl) {
  kritaSelModeEl.value = board.kritaSelMode || "replace";
  kritaSelModeEl.addEventListener("change", () => {
    board.kritaSelMode = kritaSelModeEl.value;
    pgkitNotifyLocal("krita.selMode");
  });
}

const kritaBlendModeEl = document.getElementById("kritaBlendMode");
if (kritaBlendModeEl) {
  kritaBlendModeEl.value = board.kritaBlendMode || "";
  kritaBlendModeEl.addEventListener("change", () => {
    board.kritaBlendMode = kritaBlendModeEl.value;
    pgkitNotifyLocal("krita.blendMode");
  });
}

/* ============================================================
   Field registrations
   ============================================================ */

pgkitRegisterField("prompt.text", {
  get: () => board.textPrompt || "",
  set: (v) => {
    board.textPrompt = String(v || "");
    const el = document.getElementById("promptText");
    if (el && document.activeElement !== el) el.value = board.textPrompt;
  },
  meta: { kind: "text", label: "Global prompt" },
});

pgkitRegisterField("mask.textPrompt", {
  get: () => {
    const m = selectedMask();
    return m ? (m.textPrompt || "") : "";
  },
  set: (v) => {
    const idx = selectedMaskIdx();
    if (idx < 0) return;
    setMaskPrompt(idx, v);
    const el = document.getElementById("maskPromptText");
    if (el && document.activeElement !== el) el.value = String(v || "");
  },
  meta: { kind: "text", label: "Selected mask prompt" },
});

pgkitRegisterField("refine.model", {
  get: () => board.refineModel,
  set: (v) => {
    if (v !== 'sam2' && v !== 'sam3') return;
    board.refineModel = v;
    _syncModelRadios();
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "enum", label: "Refine model", options: ["sam2", "sam3"] },
});

pgkitRegisterField("region.mode", {
  get: () => !!board.regionMode,
  set: (v) => {
    board.regionMode = !!v;
    board.lassoPoints = [];
    board.freehandPoints = [];
    board.freehandActive = false;
    if (regionModeEl && document.activeElement !== regionModeEl)
      regionModeEl.checked = board.regionMode;
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "bool", label: "Region paint mode" },
});

pgkitRegisterField("region.tool", {
  get: () => board.regionTool,
  set: (v) => {
    if (v !== 'brush' && v !== 'lasso' && v !== 'freehand') return;
    board.regionTool = v;
    board.lassoPoints = [];
    board.freehandPoints = [];
    board.freehandActive = false;
    _syncRegionToolRadios();
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "enum", label: "Region tool",
          options: ["brush", "lasso", "freehand"] },
});

pgkitRegisterField("region.subtract", {
  get: () => !!board.regionSubtract,
  set: (v) => {
    board.regionSubtract = !!v;
    if (regionSubtractEl && document.activeElement !== regionSubtractEl)
      regionSubtractEl.checked = board.regionSubtract;
    import("./pg_panel_fields.js").then(m => m.syncPanelFields()).catch(() => {});
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "bool", label: "Region subtract mode" },
});

pgkitRegisterField("region.size", {
  get: () => board.regionSize,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    board.regionSize = Math.max(2, Math.min(500, Math.round(n)));
    if (regionSizeEl && document.activeElement !== regionSizeEl)
      regionSizeEl.value = String(board.regionSize);
  },
  meta: { kind: "number", label: "Region brush size", min: 2, max: 500 },
});

pgkitRegisterField("settings.threshold", {
  get: () => board.threshold,
  set: (v) => {
    const n = parseFloat(v);
    if (!isFinite(n)) return;
    board.threshold = Math.max(0, Math.min(1, n));
    const el = document.getElementById("thresholdVal");
    if (el && document.activeElement !== el)
      el.value = board.threshold.toFixed(2);
  },
  meta: { kind: "number", label: "Threshold", min: 0, max: 1 },
});

pgkitRegisterField("settings.refineIterations", {
  get: () => board.refineIterations,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    board.refineIterations = Math.max(0, Math.min(20, n));
    const el = document.getElementById("refineVal");
    if (el && document.activeElement !== el)
      el.value = String(board.refineIterations);
  },
  meta: { kind: "number", label: "Refine iterations", min: 0, max: 20 },
});

pgkitRegisterField("settings.individualMasks", {
  get: () => !!board.individualMasks,
  set: (v) => {
    board.individualMasks = !!v;
    if (individualEl && document.activeElement !== individualEl)
      individualEl.checked = board.individualMasks;
  },
  meta: { kind: "bool", label: "Individual masks (Segment All)" },
});

pgkitRegisterField("settings.autoRun", {
  get: () => !!board.autoRun,
  set: (v) => {
    board.autoRun = !!v;
    if (autoRunEl && document.activeElement !== autoRunEl)
      autoRunEl.checked = board.autoRun;
  },
  meta: { kind: "bool", label: "Auto-run" },
});

pgkitRegisterField("masks.selectedIndex", {
  get: () => board.selectedMaskIndices.length > 0
    ? board.selectedMaskIndices[0] : -1,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    if (n < 0 || n >= board.masks.length) clearMaskSelection();
    else                                   replaceMaskSelection(n);
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "First selected mask" },
});

pgkitRegisterField("krita.targetLayer", {
  get: () => {
    const el = document.getElementById("kritaTargetLayer");
    return el ? (el.value || "") : "";
  },
  set: (v) => {
    const el = document.getElementById("kritaTargetLayer");
    if (el && document.activeElement !== el) el.value = String(v || "");
  },
  meta: { kind: "text", label: "Krita target layer" },
});

/* ---- Krita edge-cleanup field registrations ---- */

pgkitRegisterField("krita.feather", {
  get: () => board.kritaFeather,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    board.kritaFeather = Math.max(0, Math.min(100, n));
    const el = document.getElementById("kritaFeather");
    if (el && document.activeElement !== el)
      el.value = String(board.kritaFeather);
  },
  meta: { kind: "number", label: "Krita feather radius", min: 0, max: 100 },
});

pgkitRegisterField("krita.expand", {
  get: () => board.kritaExpand,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    board.kritaExpand = Math.max(0, Math.min(50, n));
    const el = document.getElementById("kritaExpand");
    if (el && document.activeElement !== el)
      el.value = String(board.kritaExpand);
  },
  meta: { kind: "number", label: "Krita expand pixels", min: 0, max: 50 },
});

pgkitRegisterField("krita.contract", {
  get: () => board.kritaContract,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    board.kritaContract = Math.max(0, Math.min(50, n));
    const el = document.getElementById("kritaContract");
    if (el && document.activeElement !== el)
      el.value = String(board.kritaContract);
  },
  meta: { kind: "number", label: "Krita contract pixels", min: 0, max: 50 },
});

pgkitRegisterField("krita.smooth", {
  get: () => board.kritaSmooth,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    board.kritaSmooth = Math.max(0, Math.min(10, n));
    const el = document.getElementById("kritaSmooth");
    if (el && document.activeElement !== el)
      el.value = String(board.kritaSmooth);
  },
  meta: { kind: "number", label: "Krita smooth iterations", min: 0, max: 10 },
});

pgkitRegisterField("krita.invert", {
  get: () => !!board.kritaInvert,
  set: (v) => {
    board.kritaInvert = !!v;
    const el = document.getElementById("kritaInvert");
    if (el && document.activeElement !== el) el.checked = board.kritaInvert;
  },
  meta: { kind: "bool", label: "Krita invert mask" },
});

pgkitRegisterField("krita.selMode", {
  get: () => board.kritaSelMode || "replace",
  set: (v) => {
    if (["replace", "add", "subtract", "intersect"].indexOf(v) < 0) return;
    board.kritaSelMode = v;
    const el = document.getElementById("kritaSelMode");
    if (el && document.activeElement !== el) el.value = v;
  },
  meta: { kind: "enum", label: "Selection mode",
          options: ["replace", "add", "subtract", "intersect"] },
});

pgkitRegisterField("krita.blendMode", {
  get: () => board.kritaBlendMode || "",
  set: (v) => {
    board.kritaBlendMode = String(v || "");
    const el = document.getElementById("kritaBlendMode");
    if (el && document.activeElement !== el) el.value = board.kritaBlendMode;
  },
  meta: { kind: "enum", label: "Layer blend mode",
          options: ["", "normal", "multiply", "screen", "overlay",
                    "add", "difference", "color", "saturation",
                    "hue", "luminosity"] },
});

pgkitRegisterField("krita.opacity", {
  get: () => board.kritaOpacity,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    board.kritaOpacity = Math.max(0, Math.min(100, n));
    const el = document.getElementById("kritaOpacity");
    if (el && document.activeElement !== el)
      el.value = String(board.kritaOpacity);
  },
  meta: { kind: "number", label: "Layer opacity (%)", min: 0, max: 100 },
});

pgkitRegisterField("krita.opacityEnabled", {
  get: () => !!board.kritaOpacityEnabled,
  set: (v) => {
    board.kritaOpacityEnabled = !!v;
    const cb  = document.getElementById("kritaOpacityEnabled");
    const inp = document.getElementById("kritaOpacity");
    if (cb && document.activeElement !== cb) cb.checked = board.kritaOpacityEnabled;
    if (inp) inp.disabled = !board.kritaOpacityEnabled;
  },
  meta: { kind: "bool", label: "Override opacity on extract" },
});

pgkitRegisterField("krita.group", {
  get: () => board.kritaGroup || "",
  set: (v) => {
    board.kritaGroup = String(v || "");
    const el = document.getElementById("kritaGroup");
    if (el && document.activeElement !== el) el.value = board.kritaGroup;
  },
  meta: { kind: "text", label: "Target group (extract only)" },
});

/* ============================================================
   Status: running
   ============================================================
   A read-only boolean that the remote CLI (and remote_panel) can use
   to sequence async actions. `board.running` is already toggled by
   `pg_comfy_client.py` around the HTTP round-trips; `pgkitNotifyAll()`
   there already includes this field, so no additional notify calls
   are needed.

   Marked `visible: false` so the panel does not render a toggle the
   user could click (nothing happens; `set` is a no-op). The field
   still appears in `schema` and `state`, so scripting clients see it.

   Convention: any playground whose actions are async should publish
   a bool field named `status.running`. `bundle(wait_for_running=True)`
   waits on exactly this name. */

pgkitRegisterField("status.running", {
  get: () => !!board.running,
  set: () => {},
  meta: {
    kind: "bool",
    label: "Running",
    visible: false,
  },
});
"""
