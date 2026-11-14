"""Panel sync: task list, selected-task details, prompt textarea,
multimask area cards, button enable/disable, result display.

`syncPanelFields` is called every frame from `draw()`.  Every DOM
write is guarded against the current value so a steady-state frame
touches nothing.

The prompt textarea and the multimask prompt textareas are NOT
written by `syncPanelFields` unless the current DOM value differs
from the model AND the element is not focused.  `_selectTask` in
`pg_panel_bindings.js` sets the initial values when the selection
changes; user typing is preserved across frames.

MULTIMASK AREA CARDS
====================
Cards are rebuilt only when the set of area ids changes.  Prompt
values are updated in place per-frame (skip-if-focused) so typing is
never interrupted.  Each card carries:

    · swatch       — area colour (mirrors multimask)
    · name         — from the snapshot's area.name
    · dims         — from area.mask.width × area.mask.height
    · prompt       — textarea, value from board.areaPromptOverrides
                     if present, else area.prompt

The section itself is hidden when `board.multimask` is null (no
snapshot → the workflow doesn't use multimask).
"""

PANEL_FIELDS_JS = r"""
import { board, areaColorById } from "./pg_state.js";

let _els = null;
function _get() {
  if (_els) return _els;
  _els = {
    taskInfo:         document.getElementById("taskInfo"),
    taskList:         document.getElementById("taskList"),
    taskPayload:      document.getElementById("taskPayload"),
    promptInput:      document.getElementById("promptInput"),
    runBtn:           document.getElementById("runBtn"),
    discardBtn:       document.getElementById("discardBtn"),
    resultInfo:       document.getElementById("resultInfo"),
    multimaskSection: document.getElementById("multimaskSection"),
    multimaskInfo:    document.getElementById("multimaskInfo"),
    multimaskList:    document.getElementById("multimaskList"),
    showMultimaskLabel: document.getElementById("showMultimaskLabel"),
    showMultimaskCb:  document.getElementById("showMultimask"),
  };
  return _els;
}

function _fmtAge(sec) {
  if (sec < 60)   return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return Math.round(sec / 3600) + "h";
}

function _fmtParams(params) {
  if (!params || typeof params !== "object") return "\u2014";
  const keys = Object.keys(params).sort();
  if (keys.length === 0) return "\u2014";
  return keys.map(k => k + "=" + JSON.stringify(params[k])).join(", ");
}

let _taskListSig = null;
function _syncTaskList() {
  const e = _get();
  if (!e.taskList) return;

  const sig = board.order.map(tid => {
    const t = board.tasks[tid];
    return tid + ":" + (t ? t.status : "");
  }).join("|") + "|" + (board.selectedTaskId || "");
  if (sig === _taskListSig) return;
  _taskListSig = sig;

  e.taskList.innerHTML = "";

  if (board.order.length === 0) {
    const div = document.createElement("div");
    div.className = "empty";
    div.textContent = "no tasks yet";
    e.taskList.appendChild(div);
    return;
  }

  const now = Date.now() / 1000;
  for (const tid of board.order) {
    const t = board.tasks[tid];
    if (!t) continue;
    const row = document.createElement("div");
    row.className = "taskRow"
                  + (tid === board.selectedTaskId ? " selected" : "");

    const label = document.createElement("span");
    label.className = "taskLabel";
    label.textContent = (t.workflow_name || t.source_label || "task")
                       + " \u00b7 " + _fmtAge(now - t.received_at);
    label.title = tid;
    row.appendChild(label);

    const st = document.createElement("span");
    st.className = "taskStatus " + t.status;
    st.textContent = t.status;
    row.appendChild(st);

    row.addEventListener("click", () => {
      import("./pg_panel_bindings.js")
        .then(m => m._selectTask(tid))
        .catch(() => {});
    });

    e.taskList.appendChild(row);
  }
}

let _taskInfoSig = null;
function _syncTaskInfo(item) {
  const e = _get();
  if (!e.taskInfo) return;

  let text;
  if (!item) {
    text = "no task selected";
  } else {
    const p = item.payload || {};
    const lines = [
      "workflow: " + (p.workflow_name || "?"),
      "status:   " + item.status,
      "id:       " + item.task_id,
    ];
    if (item.message) lines.push("message:  " + item.message);
    text = lines.join("\n");
  }
  if (text === _taskInfoSig) return;
  _taskInfoSig = text;
  e.taskInfo.textContent = text;
}

let _taskPayloadSig = null;
function _syncTaskPayload(item) {
  const e = _get();
  if (!e.taskPayload) return;
  let text = "";
  if (item) {
    const p = item.payload || {};
    text = "params: " + _fmtParams(p.params);
    if (p.crop_bounds) {
      const cb = p.crop_bounds;
      text += "\ncrop:   " + cb[2] + "\u00d7" + cb[3]
            + " at (" + cb[0] + "," + cb[1] + ")"
            + "  rotation: " + (p.output_rotation || 0) + "\u00b0";
    }
    const w = p.warnings || [];
    if (w.length) {
      text += "\nwarnings: " + w.join("; ");
    }
    if (item.multimask && item.multimask.state) {
      const n = (item.multimask.state.areas || []).length;
      text += "\nmultimask: " + n + " area(s)";
    }
  }
  if (text === _taskPayloadSig) return;
  _taskPayloadSig = text;
  e.taskPayload.textContent = text;
}

let _resultSig = null;
function _syncResult(item) {
  const e = _get();
  if (!e.resultInfo) return;
  let text = "", cls = "";
  if (item && item.result) {
    const r = item.result;
    const layers = (r.layer_names || []).join(", ") || "(none)";
    const timing = r.timing != null
      ? (r.timing.toFixed ? r.timing.toFixed(2) : r.timing) + "s"
      : "?";
    text = "pasted " + (r.output_count || 0) + " layer(s) in " + timing
         + "\n" + layers;
    cls = "ok";
  } else if (item && item.status === "error") {
    text = "failed: " + (item.message || "unknown");
    cls = "err";
  } else if (item && item.status === "running") {
    text = "inpainting\u2026";
    cls = "";
  } else if (item && item.status === "cancelled") {
    text = "cancelled";
    cls = "";
  }
  const sig = text + "|" + cls;
  if (sig === _resultSig) return;
  _resultSig = sig;
  e.resultInfo.textContent = text;
  e.resultInfo.className = cls;
}

/* ============================================================
   Multimask area cards
   ============================================================ */

let _mmAreasSig = null;

function _makeMmAreaCard(area, overridePrompt) {
  const card = document.createElement("div");
  card.className = "mmAreaCard";
  card.dataset.mmAreaId = area.id;

  const header = document.createElement("div");
  header.className = "mmAreaHeader";

  const swatch = document.createElement("span");
  swatch.className = "mmAreaSwatch";
  const hue = areaColorById(area.id);
  swatch.style.background = "rgb(" + hue.r + "," + hue.g + "," + hue.b + ")";
  header.appendChild(swatch);

  const name = document.createElement("span");
  name.className = "mmAreaName";
  name.textContent = area.name || area.id;
  header.appendChild(name);

  const dims = document.createElement("span");
  dims.className = "mmAreaDims";
  const m = area.mask || {};
  dims.textContent = (m.width || 0) + "\u00d7" + (m.height || 0);
  header.appendChild(dims);

  card.appendChild(header);

  const ta = document.createElement("textarea");
  ta.className = "mmAreaPrompt";
  ta.rows = 2;
  ta.spellcheck = false;
  ta.placeholder = "prompt for this area";
  ta.value = overridePrompt !== undefined
    ? overridePrompt
    : (area.prompt || "");
  ta.addEventListener("input", () => {
    const tid = board.selectedTaskId;
    if (!tid) return;
    const map = board.areaPromptOverrides[tid] || {};
    map[area.id] = ta.value;
    board.areaPromptOverrides[tid] = map;
  });
  card.appendChild(ta);

  return card;
}

function _syncMultimaskAreas(item) {
  const e = _get();
  if (!e.multimaskSection) return;

  const mm = item && item.multimask;
  if (!mm || !mm.state || !Array.isArray(mm.state.areas)) {
    e.multimaskSection.style.display = "none";
    _mmAreasSig = null;
    return;
  }
  e.multimaskSection.style.display = "";

  const areas = mm.state.areas;
  const tid = board.selectedTaskId;
  const overrides = (tid && board.areaPromptOverrides[tid]) || {};

  // Info line
  const url = mm.url || "?";
  const nMasks = Object.keys(mm.masks || {}).length;
  const infoText =
    "source: " + url + "\n"
    + areas.length + " area(s), " + nMasks + " mask(s) drawn";
  if (e.multimaskInfo && e.multimaskInfo.textContent !== infoText) {
    e.multimaskInfo.textContent = infoText;
    e.multimaskInfo.className = "";
  }

  // Rebuild only when the set of area ids changes.
  const idsSig = areas.map(a => a.id).join("|");
  if (idsSig !== _mmAreasSig) {
    _mmAreasSig = idsSig;
    e.multimaskList.innerHTML = "";
    if (areas.length === 0) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "no areas in multimask";
      e.multimaskList.appendChild(empty);
    } else {
      for (const area of areas) {
        e.multimaskList.appendChild(
          _makeMmAreaCard(area, overrides[area.id]),
        );
      }
    }
  } else {
    // Same area set — just sync prompt values in place, skipping
    // focused textareas so user typing is never stomped.
    for (const area of areas) {
      const card = e.multimaskList.querySelector(
        '[data-mm-area-id="' + area.id + '"]'
      );
      if (!card) continue;
      const ta = card.querySelector(".mmAreaPrompt");
      if (!ta || document.activeElement === ta) continue;
      const want = overrides[area.id] !== undefined
        ? overrides[area.id]
        : (area.prompt || "");
      if (ta.value !== want) ta.value = want;
    }
  }

  // Toggle visibility
  if (e.showMultimaskLabel) {
    e.showMultimaskLabel.style.display = "flex";
  }
  if (e.showMultimaskCb) {
    e.showMultimaskCb.checked = !!board.showMultimask;
  }
}

export function syncPanelFields() {
  const e = _get();
  const summary = board.tasks[board.selectedTaskId];
  const item    = board.selectedItem;

  _syncTaskList();
  _syncTaskInfo(item);
  _syncTaskPayload(item);
  _syncResult(item);
  _syncMultimaskAreas(item);

  // Hide the multimask overlay toggle when there is no snapshot.
  if (!(item && item.multimask)) {
    if (e.showMultimaskLabel) e.showMultimaskLabel.style.display = "none";
  }

  // The prompt textarea is NOT written here — see the module
  // docstring.  It is set on task selection (in _selectTask) and
  // updated by the textarea's own input handler.

  const status = summary ? summary.status : null;
  const canRun     = status === "queued";
  const canDiscard = status === "queued";
  if (e.runBtn)     e.runBtn.disabled     = !canRun;
  if (e.discardBtn) e.discardBtn.disabled = !canDiscard;
}
"""