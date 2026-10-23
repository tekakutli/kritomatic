PANEL_SCRUBS_JS = r"""
import {
  board, selectedMask, selectedMaskIdx,
  clearMaskSelection, replaceMaskSelection, setMaskPrompt,
} from "./pg_core.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import { pgkitRegisterField, pgkitNotifyLocal }
  from "./pgkit/remote_client.js";

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
"""
