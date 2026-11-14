"""Panel handlers: task selection, prompt textarea, overlay toggles,
poll loop, Run / Discard.

Run sends `comfy_resume_intercepted` to the daemon, which applies the
prompt override, submits the prepared workflow, polls, downloads, and
pastes.  The daemon returns the same shape as `comfy_generate`: layer
names, timing, etc.

Discard sends `comfy_discard_intercepted`, which just drops the
pending record from the daemon's `_intercepted_batches`.

The poll loop refreshes the task list every 2 s while the tab is
visible.  The `_consecutivePollFailures` counter is the same
warn-streak pattern used by the old interceptor: silence the first
two failures, warn on the third, and re-warn every ten after that.

PROMPT OVERRIDE PER TASK
========================
`board.promptOverrides[task_id]` holds each task's user-typed
override (or the payload's original prompt if never typed over).
`_selectTask` restores it on selection.  The prompt textarea's own
`input` handler writes to both `board.prompt` (current value) and
`board.promptOverrides[selectedTaskId]` (per-task persistence) so
switching back to a task after editing another one restores the
edit.

OVERLAY TOGGLES
===============
Two checkboxes in the Canvas section drive `board.showSelection` and
`board.showContext`.  Both default OFF so the canvas shows the raw
prepared image; turning either on triggers an immediate redraw with
the corresponding tint.  See `pg_view_image.js` for the tint
mechanics and `pg_base.py`'s module docstring for the design
rationale.
"""

PANEL_BINDINGS_JS = r"""
import { board, _scheduleDraw, getCachedImage } from "./pg_state.js";
import { syncPanelFields } from "./pg_panel_fields.js";
import {
  interceptList, interceptItem, interceptRun, interceptDiscard,
} from "./pg_client.js";
import { fitView } from "./pg_view.js";
import { flashStatus } from "./pgkit/panel_core.js";
import {
  pgkitRegisterAction, pgkitRegisterField,
} from "./pgkit/remote_client.js";

const POLL_MS = 2000;
let _consecutivePollFailures = 0;

/* ============================================================
   Poll loop
   ============================================================ */

async function _refreshList() {
  const res = await interceptList();
  if (!res.success) {
    _consecutivePollFailures += 1;
    if (_consecutivePollFailures === 3 ||
        (_consecutivePollFailures > 3 &&
         (_consecutivePollFailures - 3) % 10 === 0)) {
      flashStatus(
        "interceptor: can't reach the server ("
          + (res.message || "unknown") + ")",
        "warn"
      );
    }
    return;
  }
  _consecutivePollFailures = 0;

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

  await _refreshItem(taskId);
  const item = board.selectedItem;
  if (!item) { _scheduleDraw(); return; }

  // Restore the prompt for this task.  If the user has previously
  // typed an override for this task, use it; otherwise seed from the
  // payload's original prompt.  `board.prompt` mirrors the textarea
  // value so `_runAction` can read it without touching the DOM.
  const promptInput = document.getElementById("promptInput");
  const existingOverride = board.promptOverrides[taskId];
  const initialValue = (
    existingOverride !== undefined
      ? existingOverride
      : (item.payload && item.payload.prompt) || ""
  );
  if (promptInput) {
    promptInput.value = initialValue;
  }
  board.prompt = initialValue;

  if (item.payload && item.payload.canvas_b64) {
    getCachedImage(item.payload.canvas_b64);
    getCachedImage(item.payload.selection_b64);
    getCachedImage(item.payload.context_b64);
    fitView();
  }
  _scheduleDraw();
}

/* ============================================================
   Run / Discard
   ============================================================ */

async function _runAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  const summary = board.tasks[tid];
  if (!summary || summary.status !== "queued") return;

  const runBtn     = document.getElementById("runBtn");
  const discardBtn = document.getElementById("discardBtn");
  if (runBtn)     runBtn.disabled     = true;
  if (discardBtn) discardBtn.disabled = true;

  flashStatus("inpainting\u2026", "");

  const override = (board.prompt || "").trim();
  const res = await interceptRun(tid, override || null);

  if (res.success) {
    const timing = res.data && res.data.timing != null
      ? " in " + Number(res.data.timing).toFixed(2) + "s"
      : "";
    flashStatus("inpainted" + timing, "ok");
  } else {
    flashStatus(
      "inpaint failed: " + (res.message || "unknown"), "bad",
    );
  }

  await _refreshList();
  await _refreshItem(tid);
  _scheduleDraw();
}

async function _discardAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  const res = await interceptDiscard(tid);
  if (res.success) flashStatus("discarded", "ok");
  else             flashStatus(
    "discard failed: " + (res.message || "unknown"), "bad",
  );
  await _refreshList();
  if (board.selectedTaskId === tid) await _refreshItem(tid);
  _scheduleDraw();
}

/* ============================================================
   Install
   ============================================================ */

export function installPanelBindings() {
  const runBtn       = document.getElementById("runBtn");
  const discardBtn   = document.getElementById("discardBtn");
  const promptInput  = document.getElementById("promptInput");
  const showSelCb    = document.getElementById("showSelection");
  const showCtxCb    = document.getElementById("showContext");

  if (runBtn)     runBtn.addEventListener("click", _runAction);
  if (discardBtn) discardBtn.addEventListener("click", _discardAction);

  if (promptInput) {
    promptInput.addEventListener("input", () => {
      const v = promptInput.value;
      board.prompt = v;
      if (board.selectedTaskId) {
        board.promptOverrides[board.selectedTaskId] = v;
      }
    });
  }

  // Overlay toggles.  Both default OFF (matching `board`'s initial
  // state); the checkbox `checked` attribute is initialized from
  // `board` in case a future change flips the default.
  if (showSelCb) {
    showSelCb.checked = !!board.showSelection;
    showSelCb.addEventListener("change", () => {
      board.showSelection = !!showSelCb.checked;
      _scheduleDraw();
    });
  }
  if (showCtxCb) {
    showCtxCb.checked = !!board.showContext;
    showCtxCb.addEventListener("change", () => {
      board.showContext = !!showCtxCb.checked;
      _scheduleDraw();
    });
  }

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
   pgkit interfaces
   ============================================================ */

pgkitRegisterField("intercept.prompt", {
  get: () => {
    if (board.prompt) return board.prompt;
    const item = board.selectedItem;
    if (item && item.payload
        && typeof item.payload.prompt === "string") {
      return item.payload.prompt;
    }
    return "";
  },
  set: (v) => {
    board.prompt = (v === null || v === undefined) ? "" : String(v);
    if (board.selectedTaskId) {
      board.promptOverrides[board.selectedTaskId] = board.prompt;
    }
    const el = document.getElementById("promptInput");
    if (el && document.activeElement !== el) el.value = board.prompt;
  },
  meta: {
    kind: "text",
    label: "Inpaint prompt (override)",
    visible: false,
  },
});

pgkitRegisterAction("action.run", {
  label: "Inpaint", scope: "global", run: _runAction,
  meta: { variant: "primary" },
});
pgkitRegisterAction("action.discard", {
  label: "Discard", scope: "global", run: _discardAction,
  meta: { variant: "danger" },
});
"""