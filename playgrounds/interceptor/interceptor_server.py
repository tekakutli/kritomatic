"""HTTP routes for the interceptor playground.

Receives tasks from another playground (by default, the inpaint
playground's "Route via interceptor" mode), queues them, and runs
`inpaint_server.handle_inpaint` when the user clicks Inpaint.  Posts
the result (or error, or discard notice) back to the source's
callback URL.

Routes:
  POST /intercept/intake    accept a new task from a source playground
  POST /intercept/list      small summary of all tasks (polled)
  POST /intercept/item      full task body incl. payload, on selection
  POST /intercept/run       run handle_inpaint, then callback source
  POST /intercept/discard   drop a queued task; callback gets {error}
  POST /intercept/cancel    alias for discard, used by source's cancel

CONCURRENCY
===========
`handle_inpaint` blocks for the whole ComfyUI round-trip (20–60s).
The server is a `ThreadingHTTPServer` (from pgkit.server), so a
blocking `/intercept/run` occupies one thread while `/intercept/list`
keeps answering on others.  The browser polls `/intercept/list` while
the run is in flight and sees `status: "running"` until it flips.

CALLBACK BODY
=============
The interceptor POSTs `{task_id, image_b64, timing}` on success and
`{task_id, error}` on failure / discard / cancellation.  `task_id`
rides in the body, not the query string, because pgkit.server passes
JSON handlers only the parsed body — query params would be invisible.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from inpaint.inpaint_server import handle_inpaint


_TASKS: dict = {}
_LOCK = threading.Lock()
_GC_MAX_AGE_SEC = 1800   # 30 min


def _log(msg):
    print(f"[interceptor] {msg}", flush=True)


def _post_json(url, body, timeout=10.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _post_callback(url, body, task_id):
    body = dict(body)
    body["task_id"] = task_id
    for attempt in range(3):
        try:
            status, _ = _post_json(url, body, timeout=10.0)
            _log(f"callback {url} -> {status}")
            return
        except Exception as e:
            _log(f"callback {url} attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    _log(f"callback {url} abandoned after 3 attempts")


def _gc():
    now = time.time()
    with _LOCK:
        stale = [tid for tid, t in _TASKS.items()
                 if (now - t["received_at"]) > _GC_MAX_AGE_SEC
                 and t["status"] in ("done", "error", "cancelled")]
        for tid in stale:
            del _TASKS[tid]


# ---------------------------------------------------------------------------
#  Handlers
# ---------------------------------------------------------------------------

def handle_intake(payload):
    task_id      = (payload or {}).get("task_id")
    callback_url = (payload or {}).get("callback_url")
    inner        = (payload or {}).get("payload")
    if not task_id or not callback_url or not isinstance(inner, dict):
        return {"success": False, "message": "bad intake shape"}

    with _LOCK:
        if task_id in _TASKS:
            return {"success": False, "message": "task_id already exists"}
        _TASKS[task_id] = {
            "task_id":      task_id,
            "callback_url": callback_url,
            "source_label": (payload or {}).get("source_label", ""),
            "received_at":  time.time(),
            "status":       "queued",
            "payload":      inner,
            "result":       None,
            "message":      "",
        }
    _gc()
    _log(f"intake {task_id[:8]} from {payload.get('source_label')!r}")
    return {"success": True, "task_id": task_id}


def handle_list(_payload):
    with _LOCK:
        tasks = [
            {
                "task_id":      t["task_id"],
                "source_label": t["source_label"],
                "received_at":  t["received_at"],
                "status":       t["status"],
                "has_result":   t["result"] is not None,
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


def handle_run(payload):
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] != "queued":
            return {"success": False,
                    "message": f"task is {t['status']}"}
        t["status"] = "running"
        entry = dict(t)

    body = dict(entry["payload"])
    if (payload or {}).get("prompt") is not None:
        body["prompt"] = payload["prompt"]

    _log(f"run {task_id[:8]} "
         f"(prompt={body.get('prompt', '')[:40]!r})")
    try:
        result = handle_inpaint(body)
    except Exception as e:
        result = {"success": False,
                  "message": f"handle_inpaint threw: {e}"}

    if result.get("success"):
        with _LOCK:
            t = _TASKS.get(task_id)
            if t is not None:
                t["status"] = "done"
                t["result"] = {
                    "image_b64": result.get("image_b64", ""),
                    "timing":    result.get("timing"),
                }
        _post_callback(
            entry["callback_url"],
            {"image_b64": result.get("image_b64", ""),
             "timing":    result.get("timing")},
            entry["task_id"],
        )
        return {"success": True,
                "image_b64": result.get("image_b64", ""),
                "timing":    result.get("timing")}

    msg = result.get("message", "unknown")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is not None:
            t["status"] = "error"
            t["message"] = msg
    _post_callback(entry["callback_url"], {"error": msg}, entry["task_id"])
    return {"success": False, "message": msg}


def handle_discard(payload):
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] != "queued":
            return {"success": False,
                    "message": f"task is {t['status']}"}
        t["status"]  = "cancelled"
        t["message"] = "discarded by interceptor"
        entry = dict(t)
    _post_callback(entry["callback_url"],
                   {"error": "discarded by interceptor"},
                   entry["task_id"])
    return {"success": True}


def handle_cancel(payload):
    """Called by the source playground's cancel path."""
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] != "queued":
            return {"success": True}   # already running/done, no-op
        t["status"]  = "cancelled"
        t["message"] = "cancelled by source"
        entry = dict(t)
    _post_callback(entry["callback_url"],
                   {"error": "cancelled by source"},
                   entry["task_id"])
    return {"success": True}


ROUTES = {
    "/intercept/intake":  handle_intake,
    "/intercept/list":    handle_list,
    "/intercept/item":    handle_item,
    "/intercept/run":     handle_run,
    "/intercept/discard": handle_discard,
    "/intercept/cancel":  handle_cancel,
}

RAW_ROUTES = {}