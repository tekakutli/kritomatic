"""Interactive handlers for the interceptor.

Poll loop, task selection, run / discard, view toggle.

The prompt has no textarea in the panel.  It is exposed only through
two pgkit interfaces registered at the bottom of this module:

  · the `intercept.prompt` field, whose getter falls back to the
    selected task's payload prompt when no override has been set
    (so a script that reads the field after `select` sees the
    prompt that arrived with the task), and whose setter stores an
    override on `board.prompt`;

  · the `action.run` / `action.discard` actions, so a script can
    trigger the interceptor's two buttons the same way a remote
    client would trigger any other playground action.

`_selectTask` resets `board.prompt` to "" so the fallback re-engages
for each freshly-selected task — an override set for task A does not
leak into task B.  `_runAction` sends the override only when
non-empty; when empty, the interceptor's `handle_run` uses the
payload prompt unchanged.

Exports `_selectTask` (used by `pg_panel_fields.js` via dynamic
import), `installPanelBindings`, and `interceptorBootSync` (used by
boot).
"""

PANEL_BINDINGS_JS = r"""
import { board, _scheduleDraw } from "./pg_state.js";
import { syncPanelFields }     from "./pg_panel_fields.js";
import {
  interceptList, interceptItem, interceptRun, interceptDiscard,
} from "./pg_client.js";
import { flashStatus } from "./pgkit/panel_core.js";
import {
  pgkitRegisterAction, pgkitRegisterField,
} from "./pgkit/remote_client.js";

const POLL_MS = 2000;

async function _refreshList() {
  const res = await interceptList();
  if (!res.success) return;

  const prevStatus = {};
  for (const [tid, t] of Object.entries(board.tasks)) {
    prevStatus[tid] = t.status;
  }

  board.tasks = {};
  board.order = [];
  for (const t of res.tasks) {
    board.tasks[t.task_id] = t;
    board.order.push(t.task_id);
  }

  // If the selected task's status changed (e.g. running -> done,
  // running -> error, queued -> cancelled), refetch its full body.
  if (board.selectedTaskId) {
    const now = board.tasks[board.selectedTaskId];
    if (now && prevStatus[board.selectedTaskId] !== now.status) {
      await _refreshItem(board.selectedTaskId);
    }
  }

  _scheduleDraw();
}

async function _refreshItem(taskId) {
  const res = await interceptItem(taskId);
  if (!res.success) return;
  board.selectedItem = res.task;
  _scheduleDraw();
}

export async function _selectTask(taskId) {
  board.selectedTaskId = taskId;
  board.selectedItem   = null;
  board.view           = "input";
  board.prompt         = "";   // reset override; field falls back to payload
  await _refreshItem(taskId);
  _scheduleDraw();
}

async function _runAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  const summary = board.tasks[tid];
  if (!summary || summary.status !== "queued") return;

  // Only forward an override when one has been set.  Passing
  // `undefined` makes interceptRun omit the `prompt` key entirely,
  // so the interceptor uses the payload's own prompt.
  const override = board.prompt || undefined;

  flashStatus("inpainting\u2026", "");
  const res = await interceptRun(tid, override);

  if (res.success) {
    flashStatus(
      "inpainted in " + ((res.timing || 0).toFixed(2)) + "s",
      "ok");
    board.view = "result";
  } else {
    flashStatus("inpaint failed: " + (res.message || "unknown"), "bad");
  }

  await _refreshList();
  await _refreshItem(tid);
  _scheduleDraw();
}

async function _discardAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  const res = await interceptDiscard(tid);
  if (res.success) {
    flashStatus("discarded", "ok");
  } else {
    flashStatus("discard failed: " + (res.message || "unknown"), "bad");
  }
  await _refreshList();
  if (board.selectedTaskId === tid) await _refreshItem(tid);
  _scheduleDraw();
}

function _toggleView() {
  if (!board.selectedItem || !board.selectedItem.result) return;
  board.view = (board.view === "result") ? "input" : "result";
  _scheduleDraw();
}

export function installPanelBindings() {
  const runBtn     = document.getElementById("runBtn");
  const discardBtn = document.getElementById("discardBtn");
  const toggleLink = document.getElementById("toggleViewLink");

  if (runBtn)     runBtn.addEventListener("click", _runAction);
  if (discardBtn) discardBtn.addEventListener("click", _discardAction);
  if (toggleLink) toggleLink.addEventListener("click", _toggleView);

  if (board.pollTimer) clearInterval(board.pollTimer);
  board.pollTimer = setInterval(() => {
    if (document.visibilityState !== "visible") return;
    _refreshList();
  }, POLL_MS);
}

export function interceptorBootSync() {
  syncPanelFields();
  _refreshList();
  _scheduleDraw();
}

/* ============================================================
   pgkit interfaces — the prompt without a textarea
   ============================================================
   `intercept.prompt` getter falls back to the payload prompt so a
   script that reads the field after a select sees a meaningful
   value without having to know about the payload.  The setter
   stores the override; empty means "revert to payload".

   `visible: false` keeps the field API-only, same convention as
   `status.running` in the inpaint playground.

   `action.run` and `action.discard` mirror the two buttons, so
   the whole interceptor can be driven from a script:

       intercept.prompt = "a red fox"
       action.run       (or bundle(actions=[{kind: "action.run"}]))
   */

pgkitRegisterField("intercept.prompt", {
  get: () => {
    if (board.prompt) return board.prompt;
    const item = board.selectedItem;
    if (item && item.payload && typeof item.payload.prompt === "string") {
      return item.payload.prompt;
    }
    return "";
  },
  set: (v) => {
    board.prompt = (v === null || v === undefined) ? "" : String(v);
  },
  meta: {
    kind: "text",
    label: "Inpaint prompt (override)",
    visible: false,
  },
});

pgkitRegisterAction("action.run", {
  label: "Inpaint",
  scope: "global",
  run: _runAction,
  meta: { variant: "primary" },
});

pgkitRegisterAction("action.discard", {
  label: "Discard",
  scope: "global",
  run: _discardAction,
  meta: { variant: "danger" },
});
"""