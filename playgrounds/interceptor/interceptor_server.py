"""HTTP routes for the interceptor playground.

The interceptor sits between the daemon's `comfy_generate` (in
intercept mode) and the user.  When intercept mode is on, the daemon
prepares the workflow — reads the canvas / selection / context pixels
out of Krita, uploads them as assets, substitutes the graph — and then,
instead of submitting to ComfyUI, POSTs the prepared snapshot to this
playground's `/intercept/intake`.  The user reviews the snapshot in
the browser, optionally edits the prompt, and clicks Run.  This
playground calls `comfy_resume_intercepted` on the daemon, which
submits the prepared graph, polls to completion, downloads the
result, and pastes it into Krita.

Routes
======
    POST /intercept/intake   daemon → here: new intercepted task
    POST /intercept/list     browser poll: summary of all tasks
    POST /intercept/item     browser: full task body incl. payload
    POST /intercept/run      browser → daemon: resume generation
    POST /intercept/discard  browser → daemon: drop the batch
    POST /intercept/cancel   daemon → here (best-effort): user clicked
                             Cancel-All in the docker; drop the task

The `/intercept/cancel` route is idempotent: unknown tasks (already
resumed, already discarded, never seen) return `{success: true}` so
the daemon's best-effort POST never has to care about ordering.

Task identity
=============
`task_id` **is** the daemon's `InterceptedBatch.batch_id` (e.g.
`intercept_a1b2c3d4e5f6`).  This makes the interceptor's local task
list and the daemon's `_intercepted_batches` dict the same set of
keys: reconciliation is a key-set comparison, not a content match.

Payload shape
=============
The daemon sends one `payload` dict per task:

    {
      "canvas_b64":      <base64 PNG>,
      "selection_b64":   <base64 PNG, grayscale mask>,
      "context_b64":     <base64 PNG, grayscale mask>,
      "prompt":          <current prompt>,
      "workflow_name":   <slug>,
      "params":          {<param title>: <value>, ...},
      "crop_bounds":     [x, y, w, h],
      "output_rotation": <float>,
      "warnings":        [<string>, ...],
    }

`canvas_b64` is what the model sees as input (rotated + cropped for
theta≠0, raw when the inpaint playground was not running at prepare
time).  `selection_b64` and `context_b64` are the two masks the
workflow will consume.  The browser renders all three overlaid on the
canvas so the user sees exactly what will be affected before they
click Run.

Timeouts
========
`handle_run` blocks for the full generation duration — the daemon's
`comfy_resume_intercepted` internally polls the proxy to completion,
typically 20–60 s but potentially up to the poll timeout (180 s).
The browser's fetch long-polls against this handler, so the pgkit
server's socket stays open the whole time.  This matches the original
interceptor's blocking-Run UX.

Failure modes
=============
- Daemon unreachable at resume: `handle_run` returns `{success:
  false, message: "<error>"}`; the local task flips to `error`.
- Daemon says "batch expired or unknown" (swept between intake and
  resume): same — local task flips to `error`.
- Paste fails during resume: the daemon's `comfy_resume_intercepted`
  returns `{success: false, message: "<paste error>"}`; local task
  flips to `error`; the user can retry with a different prompt, but
  the batch is gone — they have to re-run from the docker.
"""

from __future__ import annotations

import threading
import time

from inpaint import krita_client


_TASKS: dict = {}
_LOCK = threading.Lock()
_GC_MAX_AGE_SEC = 1800   # 30 min


def _log(msg):
    print(f"[interceptor] {msg}", flush=True)


def _gc():
    now = time.time()
    with _LOCK:
        stale = [
            tid for tid, t in _TASKS.items()
            if (now - t["received_at"]) > _GC_MAX_AGE_SEC
            and t["status"] in ("done", "error", "cancelled")
        ]
        for tid in stale:
            del _TASKS[tid]


# ---------------------------------------------------------------------------
#  Intake
# ---------------------------------------------------------------------------

def handle_intake(payload):
    """Receive one intercepted task from the daemon.

    Called by `comfy_generate` in intercept mode.  The task_id is the
    daemon's batch_id; storing it verbatim here means the two task
    lists are the same set of keys and reconciliation is trivial.
    """
    payload = payload or {}
    task_id = payload.get("task_id")
    inner = payload.get("payload")
    if not task_id or not isinstance(inner, dict):
        return {"success": False, "message": "bad intake shape"}

    source_label = payload.get("source_label") or "daemon"

    with _LOCK:
        if task_id in _TASKS:
            return {
                "success": False,
                "message": "task_id already exists",
            }
        _TASKS[task_id] = {
            "task_id": task_id,
            "source_label": source_label,
            "received_at": time.time(),
            "status": "queued",
            "payload": {
                "canvas_b64":      inner.get("canvas_b64", ""),
                "selection_b64":   inner.get("selection_b64", ""),
                "context_b64":     inner.get("context_b64", ""),
                "prompt":          inner.get("prompt", ""),
                "workflow_name":   inner.get("workflow_name", ""),
                "params":          inner.get("params", {}) or {},
                "crop_bounds":     inner.get("crop_bounds"),
                "output_rotation": inner.get("output_rotation", 0.0),
                "warnings":        inner.get("warnings", []) or [],
            },
            "result": None,
            "message": "",
        }
    _gc()
    _log(f"intake {task_id[:8]} from {source_label!r}")
    return {"success": True, "task_id": task_id}


# ---------------------------------------------------------------------------
#  List / item
# ---------------------------------------------------------------------------

def handle_list(_payload):
    with _LOCK:
        tasks = [
            {
                "task_id":       t["task_id"],
                "source_label":  t["source_label"],
                "workflow_name": t["payload"].get("workflow_name", ""),
                "received_at":   t["received_at"],
                "status":        t["status"],
                "has_result":    t["result"] is not None,
            }
            for t in sorted(_TASKS.values(),
                            key=lambda x: -x["received_at"])
        ]
    return {"success": True, "tasks": tasks}


def handle_item(payload):
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        return {"success": True, "task": {
            "task_id":      t["task_id"],
            "source_label": t["source_label"],
            "received_at":  t["received_at"],
            "status":       t["status"],
            "payload":      t["payload"],
            "result":       t["result"],
            "message":      t["message"],
        }}


# ---------------------------------------------------------------------------
#  Run
# ---------------------------------------------------------------------------

def handle_run(payload):
    """Resume the intercepted batch on the daemon.

    Blocks for the full generation duration.  The browser's fetch is a
    long-poll against this handler.  On success, the daemon returns
    the pasted layer names + timing, which we echo back to the
    browser and store on the local task for the next `/intercept/item`
    read.
    """
    payload = payload or {}
    task_id = payload.get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] not in ("queued", "done", "error"):
            return {"success": False,
                    "message": f"task is {t['status']}"}
        t["status"] = "running"

    prompt_override = payload.get("prompt")
    _log(f"run {task_id[:8]} prompt={(prompt_override or '')[:40]!r}")

    resp = krita_client.send({
        "type": "comfy_resume_intercepted",
        "batch_id": task_id,
        "prompt_override": prompt_override,
    }, timeout=600.0)

    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "task was removed"}

        if resp.get("status") == "success":
            data = resp.get("data") or {}
            t["status"] = "done"
            t["result"] = data
            return {
                "success": True,
                "data": data,
                "message": resp.get("message", "") or "Pasted",
            }

        msg = resp.get("message")
        if not msg:
            data = resp.get("data")
            if isinstance(data, dict):
                msg = data.get("message")
        msg = msg or "unknown"
        t["status"] = "error"
        t["message"] = msg
        return {"success": False, "message": msg}


# ---------------------------------------------------------------------------
#  Discard
# ---------------------------------------------------------------------------

def handle_discard(payload):
    """Discard a queued task, telling the daemon to drop its record.

    Idempotent from the daemon's perspective: it returns
    `{success: true}` even if the record was already gone, so this
    handler can always call it without checking.
    """
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] != "queued":
            return {"success": False,
                    "message": f"task is {t['status']}"}
        t["status"] = "cancelled"
        t["message"] = "discarded by user"

    krita_client.send({
        "type": "comfy_discard_intercepted",
        "batch_id": task_id,
    }, timeout=10.0)
    _log(f"discard {task_id[:8]}")
    return {"success": True}


# ---------------------------------------------------------------------------
#  Cancel (from the daemon)
# ---------------------------------------------------------------------------

def handle_cancel(payload):
    """Mark the task cancelled.  Called by the daemon when the user
    clicked Cancel-All in the docker.

    Idempotent: unknown tasks and already-non-queued tasks are treated
    as success.  A `{task_id: null}` broadcast cancels every queued
    task (used when the daemon cancel had no specific batch id in
    mind).
    """
    task_id = (payload or {}).get("task_id")

    with _LOCK:
        if not task_id:
            # Cancel-all propagation.
            for tt in _TASKS.values():
                if tt["status"] == "queued":
                    tt["status"] = "cancelled"
                    tt["message"] = "cancelled by daemon"
            _log("cancel (all)")
            return {"success": True, "message": "cleared queued tasks"}

        t = _TASKS.get(task_id)
        if t is None:
            _log(f"cancel {task_id[:8]} (already gone)")
            return {"success": True,
                    "message": "unknown or already gone"}
        if t["status"] == "queued":
            t["status"] = "cancelled"
            t["message"] = "cancelled by daemon"
    _log(f"cancel {task_id[:8]}")
    return {"success": True}


ROUTES = {
    "/intercept/intake":   handle_intake,
    "/intercept/list":     handle_list,
    "/intercept/item":     handle_item,
    "/intercept/run":      handle_run,
    "/intercept/discard":  handle_discard,
    "/intercept/cancel":   handle_cancel,
}

RAW_ROUTES = {}