"""Panel sync: task list, selected task info, expansion status,
button enable/disable state."""

PANEL_FIELDS_JS = r"""
import { board } from "./pg_state.js";

let _els = null;
function _get() {
  if (_els) return _els;
  _els = {
    taskInfo:           document.getElementById("taskInfo"),
    taskList:           document.getElementById("taskList"),
    runBtn:             document.getElementById("runBtn"),
    discardBtn:         document.getElementById("discardBtn"),
    viewToggle:         document.getElementById("viewToggle"),
    toggleViewLink:     document.getElementById("toggleViewLink"),
    expansionInfo:      document.getElementById("expansionInfo"),
    openKritaBtn:       document.getElementById("openKritaBtn"),
    refreshKritaBtn:    document.getElementById("refreshKritaBtn"),
    resetExpansionBtn:  document.getElementById("resetExpansionBtn"),
  };
  return _els;
}

function _fmtAge(sec) {
  if (sec < 60)   return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return Math.round(sec / 3600) + "h";
}

function _canExpand(item) {
  if (!item || !item.payload) return false;
  return (item.payload.output_rotation || 0) === 0;
}

function _hasExp() {
  const e = board.expansion;
  return e.left > 0 || e.top > 0 || e.right > 0 || e.bottom > 0;
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
    label.textContent = (t.source_label || "task")
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

let _expSig = null;
function _syncExpansionInfo(item) {
  const e = _get();
  if (!e.expansionInfo) return;

  let text, cls = "";
  if (!item) {
    text = "no task selected";
  } else if (!_canExpand(item)) {
    text = "expansion disabled: source payload is rotated ("
         + (item.payload.output_rotation || 0) + "\u00b0)";
    cls = "warn";
  } else if (_hasExp()) {
    const exp = board.expansion;
    text = "expansion  L:" + exp.left + "  T:" + exp.top
         + "  R:" + exp.right + "  B:" + exp.bottom
         + "   (drag handles to change)";
    if (board.kritaDocumentName) {
      text += "\nKrita doc: " + (board.kritaFileHint
                                 || board.kritaDocumentName);
    } else {
      text += "\nnot yet opened in Krita";
    }
    cls = "active";
  } else {
    text = "no expansion \u2014 drag a handle outward to add context";
  }

  const sig = text + "|" + cls;
  if (sig === _expSig) return;
  _expSig = sig;
  e.expansionInfo.textContent = text;
  e.expansionInfo.className = cls;
}

export function syncPanelFields() {
  const e = _get();
  const summary = board.tasks[board.selectedTaskId];
  const item    = board.selectedItem;

  _syncTaskList();

  if (e.taskInfo) {
    if (!summary) {
      e.taskInfo.textContent = "no task selected";
    } else {
      const lines = [
        "source: " + (summary.source_label || "?"),
        "status: " + summary.status,
        "id:     " + summary.task_id,
      ];
      if (item && item.message) lines.push("message: " + item.message);
      e.taskInfo.textContent = lines.join("\n");
    }
  }

  _syncExpansionInfo(item);

  const status = summary ? summary.status : null;
  const canRun      = status === "queued";
  const canDiscard  = status === "queued";
  const canReset    = !!item && _hasExp();

  if (e.runBtn)     e.runBtn.disabled     = !canRun;
  if (e.discardBtn) e.discardBtn.disabled = !canDiscard;

  const canOpenKrita    = !!item && !!board.expandedCanvasEl
                        && _canExpand(item);
  const canRefreshKrita = !!item && !!board.kritaDocumentName
                        && _canExpand(item);
  if (e.openKritaBtn)    e.openKritaBtn.disabled    = !canOpenKrita;
  if (e.refreshKritaBtn) e.refreshKritaBtn.disabled = !canRefreshKrita;
  if (e.resetExpansionBtn)
    e.resetExpansionBtn.disabled = !canReset;

  const hasResult = !!(item && item.result && item.result.image_b64);
  if (e.viewToggle) e.viewToggle.classList.toggle("visible", hasResult);
  if (e.toggleViewLink && hasResult) {
    e.toggleViewLink.textContent = (board.view === "result")
      ? "show input" : "show result";
  }
}
"""