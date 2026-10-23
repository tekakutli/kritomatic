PANEL_SCRUBS_JS = r"""
import { board, setMasks } from "./pg_core.js";
import { _installScrubInput } from "./pgkit/panel_core.js";
import { pgkitRegisterField, pgkitNotifyAll, pgkitNotifyLocal }
  from "./pgkit/remote_client.js";
import { flashStatus } from "./pgkit/panel_core.js";

/* ---- scrubbable inputs ---- */

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

const individualEl = document.getElementById("individualMasks");
if (individualEl) {
  individualEl.checked = !!board.individualMasks;
  individualEl.addEventListener("change", () => {
    board.individualMasks = !!individualEl.checked;
    pgkitNotifyLocal("settings.individualMasks");
  });
}

const promptEl = document.getElementById("promptText");
if (promptEl) {
  promptEl.value = board.textPrompt || "";
  promptEl.addEventListener("input", () => {
    board.textPrompt = promptEl.value;
    pgkitNotifyLocal("prompt.text");
  });
}

/* ---- field registrations (for the remote panel) ---- */

pgkitRegisterField("image.hash", {
  get: () => board.imageHash || "",
  set: () => {},   // set externally via upload
  meta: { kind: "text", label: "Image hash" },
});

pgkitRegisterField("image.width", {
  get: () => board.imageWidth,
  set: () => {},
  meta: { kind: "number", label: "Image width" },
});

pgkitRegisterField("image.height", {
  get: () => board.imageHeight,
  set: () => {},
  meta: { kind: "number", label: "Image height" },
});

pgkitRegisterField("prompt.text", {
  get: () => board.textPrompt || "",
  set: (v) => {
    board.textPrompt = String(v || "");
    const el = document.getElementById("promptText");
    if (el && document.activeElement !== el) el.value = board.textPrompt;
  },
  meta: { kind: "text", label: "Text prompt" },
});

pgkitRegisterField("prompt.positive_count", {
  get: () => board.positive.length,
  set: () => {},
  meta: { kind: "number", label: "Positive points" },
});

pgkitRegisterField("prompt.negative_count", {
  get: () => board.negative.length,
  set: () => {},
  meta: { kind: "number", label: "Negative points" },
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
    const el = document.getElementById("individualMasks");
    if (el && document.activeElement !== el)
      el.checked = board.individualMasks;
  },
  meta: { kind: "bool", label: "Individual masks" },
});

pgkitRegisterField("masks.count", {
  get: () => board.masks.length,
  set: () => {},
  meta: { kind: "number", label: "Mask count" },
});

pgkitRegisterField("masks.selectedIndex", {
  get: () => board.selectedMaskIdx,
  set: (v) => {
    const n = parseInt(v, 10);
    if (!isFinite(n)) return;
    if (n < 0 || n >= board.masks.length) return;
    board.selectedMaskIdx = n;
    import("./pg_dispatch.js").then(m => m.draw()).catch(() => {});
  },
  meta: { kind: "number", label: "Selected mask index" },
});
"""
