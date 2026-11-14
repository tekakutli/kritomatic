"""Panel sync: task list, selected-task details, prompt textarea,
button enable/disable, result display.

`syncPanelFields` is called every frame from `draw()`.  Every DOM
write is guarded against the current value so a steady-state frame
touches nothing.

The prompt textarea is NOT written by `syncPanelFields` — that would
stomp user input every frame.  `_selectTask` in
`pg_panel_bindings.js` sets the textarea when the selection changes,
using the per-task override if it exists or the payload's original
prompt if not.
"""

PANEL_FIELDS_JS = r"""
import { board } from "./pg_state.js";

let _els = null;
function _get() {
  if (_els) return _els;
  _els = {
    taskInfo:    document.getElementById("taskInfo"),
    taskList:    document.getElementById("taskList"),
    taskPayload: document.getElementById("taskPayload"),
    promptInput: document.getElementById("promptInput"),
    runBtn:      document.getElementById("runBtn"),
    discardBtn:  document.getElementById("discardBtn"),
    resultInfo:  document.getElementById("resultInfo"),
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

export function syncPanelFields() {
  const e = _get();
  const summary = board.tasks[board.selectedTaskId];
  const item    = board.selectedItem;

  _syncTaskList();
  _syncTaskInfo(item);
  _syncTaskPayload(item);
  _syncResult(item);

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