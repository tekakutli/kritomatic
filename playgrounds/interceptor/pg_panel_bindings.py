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

PROMPT OVERRIDES PER TASK
=========================
`board.promptOverrides[task_id]` holds each task's user-typed
inpaint prompt.  `board.areaPromptOverrides[task_id]` holds each
task's user-typed multimask area prompts.  Both are restored on
task selection so switching back to a task after editing another
one preserves the edits.

OVERLAY TOGGLES
===============
Three checkboxes in the Canvas section: selection, context,
multimask.  Selection and context default OFF; multimask defaults ON
when a snapshot exists.  See `pg_base.py`'s module docstring.

Toggle flips use `_scheduleDrawView` — canvas-only.  The panel state
does not react to overlay toggles: the checkboxes are DOM-driven,
the tint layers are canvas-driven, and no field in
`syncPanelFields` reads `showSelection` / `showContext` /
`showMultimask`.  Using `_scheduleDraw` there would schedule a
guaranteed-no-op panel sync per click for zero benefit.

Task selection, task list refresh, and Run/Discard remain on
`_scheduleDraw` — those changes actually touch panel state (task
list highlight, task info text, payload / result lines, multimask
area cards).

PENDING LOCAL RUNS — SEE ALSO `pg_state.py`'s "PENDING LOCAL RUNS"
==================================================================
`_runAction` and `_discardAction` add the selected task's id to
`board.pendingLocalRuns` *before* firing their fetch, and remove it
after the post-fetch refresh completes.  `effectiveStatus(task)`
(exported from `pg_state.js`) reports any tracked task as
`"running"`, so the buttons disable immediately and stay disabled
even if the user re-selects the task before the next poll confirms
the server's status flip.

The task id is removed from `pendingLocalRuns` only *after* the
post-fetch refresh completes, not before.  That ordering closes the
otherwise-possible window where the refresh has not yet updated
`board.tasks[tid].status` and a `_scheduleDraw` triggered by some
other event (a hover, a selection change) would re-enable the
buttons against the stale `"queued"` state.  See `pg_state.py`'s
"PENDING LOCAL RUNS" section for the full rationale.
"""

PANEL_BINDINGS_JS = r"""
import {
  board, _scheduleDraw, _scheduleDrawView, getCachedImage,
} from "./pg_state.js";
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

  // Restore the inpaint prompt for this task.
  const promptInput = document.getElementById("promptInput");
  const existingOverride = board.promptOverrides[taskId];
  const initialValue = (
    existingOverride !== undefined
      ? existingOverride
      : (item.payload && item.payload.prompt) || ""
  );
  if (promptInput) promptInput.value = initialValue;
  board.prompt = initialValue;

  // Multimask snapshot + per-area prompt overrides for this task.
  board.multimask = item.multimask || null;
  if (!board.areaPromptOverrides[taskId]) {
    board.areaPromptOverrides[taskId] = {};
  }
  // Default the multimask overlay ON when a snapshot exists —
  // reviewing the masks is the point.
  board.showMultimask = !!board.multimask;

  // Preload the images the canvas will draw.
  if (item.payload && item.payload.canvas_b64) {
    getCachedImage(item.payload.canvas_b64);
    getCachedImage(item.payload.selection_b64);
    getCachedImage(item.payload.context_b64);
    if (item.multimask && item.multimask.masks) {
      for (const b64 of Object.values(item.multimask.masks)) {
        if (typeof b64 === "string" && b64) getCachedImage(b64);
      }
    }
    fitView();
  }
  _scheduleDraw();
}

/* ============================================================
   Run / Discard
   ============================================================
   See this module's "PENDING LOCAL RUNS" docstring section and
   pg_state.py's corresponding section for the full rationale of
   the `board.pendingLocalRuns` add/remove ordering. */

async function _runAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  if (board.pendingLocalRuns.has(tid)) return;   // double-click guard
  const summary = board.tasks[tid];
  if (!summary || summary.status !== "queued") return;

  // Mark the task as locally pending BEFORE the fetch.  The server
  // flips its own status to "running" under its lock before doing
  // any work, so a poll landing after the fetch begins will see it;
  // a poll landing between this line and the server's flip, or a
  // re-selection during the await, would otherwise re-enable the
  // buttons against stale "queued".  `effectiveStatus` and the
  // panel's button gating both read this set.  See pg_state.py's
  // "PENDING LOCAL RUNS".
  board.pendingLocalRuns.add(tid);
  _scheduleDraw();

  const runBtn     = document.getElementById("runBtn");
  const discardBtn = document.getElementById("discardBtn");
  if (runBtn)     runBtn.disabled     = true;
  if (discardBtn) discardBtn.disabled = true;

  flashStatus("inpainting\u2026", "");

  const override = (board.prompt || "").trim();
  const areaPrompts = board.areaPromptOverrides[tid] || {};

  let res;
  try {
    res = await interceptRun(tid, override || null, areaPrompts);
  } catch (e) {
    res = { success: false, message: e.message || String(e) };
  }

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

  // Refresh BEFORE clearing the pending marker.  `handle_run` on the
  // server flips status to done/error inside the request handler,
  // before returning the HTTP response, so by the time this refresh
  // runs the fresh list already reflects the terminal status.
  // Clearing the marker after the refresh means there is no window
  // where the button could be re-enabled against stale "queued"
  // data.  See pg_state.py's "PENDING LOCAL RUNS".
  try {
    await _refreshList();
    if (board.selectedTaskId === tid) await _refreshItem(tid);
  } finally {
    board.pendingLocalRuns.delete(tid);
    _scheduleDraw();
  }
}

async function _discardAction() {
  const tid = board.selectedTaskId;
  if (!tid) return;
  if (board.pendingLocalRuns.has(tid)) return;   // double-click guard
  const summary = board.tasks[tid];
  if (!summary || summary.status !== "queued") return;

  board.pendingLocalRuns.add(tid);
  _scheduleDraw();

  const runBtn     = document.getElementById("runBtn");
  const discardBtn = document.getElementById("discardBtn");
  if (runBtn)     runBtn.disabled     = true;
  if (discardBtn) discardBtn.disabled = true;

  let res;
  try {
    res = await interceptDiscard(tid);
  } catch (e) {
    res = { success: false, message: e.message || String(e) };
  }

  if (res.success) flashStatus("discarded", "ok");
  else             flashStatus(
    "discard failed: " + (res.message || "unknown"), "bad",
  );

  try {
    await _refreshList();
    if (board.selectedTaskId === tid) await _refreshItem(tid);
  } finally {
    board.pendingLocalRuns.delete(tid);
    _scheduleDraw();
  }
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
  const showMmCb     = document.getElementById("showMultimask");

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

  // Overlay toggles: canvas-only.  See the module docstring.
  if (showSelCb) {
    showSelCb.checked = !!board.showSelection;
    showSelCb.addEventListener("change", () => {
      board.showSelection = !!showSelCb.checked;
      _scheduleDrawView();
    });
  }
  if (showCtxCb) {
    showCtxCb.checked = !!board.showContext;
    showCtxCb.addEventListener("change", () => {
      board.showContext = !!showCtxCb.checked;
      _scheduleDrawView();
    });
  }
  if (showMmCb) {
    showMmCb.checked = !!board.showMultimask;
    showMmCb.addEventListener("change", () => {
      board.showMultimask = !!showMmCb.checked;
      _scheduleDrawView();
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