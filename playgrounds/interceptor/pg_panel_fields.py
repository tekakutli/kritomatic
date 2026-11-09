"""Panel sync for the interceptor: task list, selected task info,
buttons.

The list is rebuilt only when its signature (task ids + statuses +
selection) changes, so a hover or a canvas redraw does not stomp the
DOM.  No prompt textarea to sync — see `pg_base.py`'s module
docstring.
"""

PANEL_FIELDS_JS = r"""
import { board } from "./pg_state.js";

let _els = null;
function _get() {
  if (_els) return _els;
  _els = {
    taskInfo:       document.getElementById("taskInfo"),
    taskList:       document.getElementById("taskList"),
    runBtn:         document.getElementById("runBtn"),
    discardBtn:     document.getElementById("discardBtn"),
    viewToggle:     document.getElementById("viewToggle"),
    toggleViewLink: document.getElementById("toggleViewLink"),
  };
  return _els;
}

function _fmtAge(sec) {
  if (sec < 60)   return Math.round(sec) + "s";
  if (sec < 3600) return Math.round(sec / 60) + "m";
  return Math.round(sec / 3600) + "h";
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
                       + " \u00b7 "
                       + _fmtAge(now - t.received_at);
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

  const queued = !!(summary && summary.status === "queued");

  if (e.runBtn)     e.runBtn.disabled     = !queued;
  if (e.discardBtn) e.discardBtn.disabled = !queued;

  const hasResult = !!(item && item.result && item.result.image_b64);
  if (e.viewToggle) {
    e.viewToggle.classList.toggle("visible", hasResult);
  }
  if (e.toggleViewLink && hasResult) {
    e.toggleViewLink.textContent = (board.view === "result")
      ? "show input" : "show result";
  }
}
"""