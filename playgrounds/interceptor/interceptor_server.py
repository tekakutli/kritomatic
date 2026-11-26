"""HTTP routes for the interceptor playground.

The interceptor sits between the daemon's `comfy_generate` (in
intercept mode) and the user.  When intercept mode is on, the daemon
prepares the workflow — reads the canvas / selection / context pixels
out of Krita, uploads them as assets, substitutes the graph — and then,
instead of submitting to ComfyUI, POSTs the prepared snapshot to this
playground's `/intercept/intake`.  The user reviews the snapshot in
the browser, optionally edits the prompt and (when the workflow uses
multimask) the per-area prompts, and clicks Run.  This playground
calls `comfy_resume_intercepted` on the daemon, which submits the
prepared graph, polls to completion, downloads the result, and pastes
it into Krita.

Routes
======
    POST /intercept/intake   daemon → here: new intercepted task
    POST /intercept/list     browser poll: summary of all tasks.
                             Also cross-references the daemon's live
                             `intercepted_batches` registry to mark
                             locally-queued tasks as `expired` when
                             the daemon's 15-minute intercept timeout
                             has swept them.  See "Task expiry" below.
    POST /intercept/item     browser: full task body incl. payload
    POST /intercept/run      browser → daemon: push multimask state,
                             then resume generation
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

Task expiry
===========
The daemon sweeps `_intercepted_batches` every 300 s and drops
entries older than `INTERCEPT_TIMEOUT_SEC` (900 s, 15 min).  Before
this cross-reference existed, an intercepted task that sat idle for
15+ min without being run or discarded would remain visible in the
interceptor's panel as `queued` forever.  Clicking Run would then
fail with the daemon's `"batch expired or unknown"` — accurate but
15-minutes-late and confusing.

`handle_list` now polls the daemon's `comfy_status` on every
browser 2-second tick and, when the daemon answered successfully,
marks any locally-`queued` task whose `task_id` is not in the
daemon's `intercepted_batches` list as `expired`.  The panel renders
`expired` with its own status colour and a
`"re-intercept from the docker"` message.

Only tasks whose status is exactly `queued` are flipped — a task
that is already `running`, `done`, or `error` is left alone (the
daemon's sweep cannot remove a task that has already been resumed
or discarded; those paths remove the record before the sweep can
see it).  A daemon-unreachable response is treated as "unknown" and
does NOT mass-expire tasks — a transient blip must not falsely
retire every queued task.  The next poll after the daemon comes
back will detect real expiry.

`comfy_status` internally calls the daemon's
`_sweep_expired_intercepts`, so each interceptor poll also keeps the
daemon's own sweep current within the interceptor's 2-second cadence
— much tighter than the daemon's default 300-second sweep tick.

Payload shape
=============
The daemon sends one `payload` dict per task plus OPTIONAL
`multimask` and `controlnets` siblings:

    {
      "payload": {
        "canvas_b64":      <base64 PNG>,
        "selection_b64":   <base64 PNG, grayscale mask>,
        "context_b64":     <base64 PNG, grayscale mask>,
        "prompt":          <current prompt>,
        "workflow_name":   <slug>,
        "params":          {<param title>: <value>, ...},
        "crop_bounds":     [x, y, w, h],
        "output_rotation": <float>,
        "warnings":        [<string>, ...],
      },
      "multimask": {                # omitted when workflow has no
                                    # MultiAreaConditioning node
        "url": "<multimask base url>",
        "state": {
          "globals": {...},
          "areas": [                # each area has id, name, prompt,
                                    # strength, use_tagger, mask: {hash,
                                    # width, height}, ...
            ...
          ],
        },
        "masks": {<hash>: <b64>},   # transformed PNG per area
      },
      "controlnets": {              # omitted when workflow has no
                                    # ControlNetsConditioning node
        "url": "<controlnets base url>",
        "state": {
          "controlnets": [
            {"id": str, "name": str,
             "image": {"hash": str, "width": int, "height": int}},
            ...
          ],
          "apply_enabled": {<apply_title>: bool},
          "version":       str,
        },
        "images": {<hash>: <b64>},  # transformed PNG per entry
      },
    }

`canvas_b64` is what the model sees as input (rotated + cropped for
theta≠0, raw when the inpaint playground was not running at prepare
time).  `selection_b64` and `context_b64` are the two masks the
workflow will consume.

`multimask.masks` and `controlnets.images` are in the SAME frame as
`canvas_b64` — the daemon fetched them via the respective
playground's per-hash route, which auto-transforms to the prepared
crop.  Overlaying them on `canvas_b64` at 1:1 is aligned.

Multimask push-on-run
=====================
The only piece of state the interceptor pushes to a service OTHER
than the daemon is the multimask area prompts.  On `/intercept/run`:

    1. Build an updated state from the snapshot with the browser's
       per-area prompt edits applied.
    2. POST `{"op": "set", "state": <updated>}` to `<multimask_url>/state`.
       **No `masks` field** — multimask's `_masks` dict keeps its
       originals, so `/mask` re-transforms correctly at ComfyUI
       execution time.
    3. Call the daemon's `comfy_resume_intercepted`.

Step 2 runs UNCONDITIONALLY when a snapshot exists, even if the user
made no edits.  The snapshot is the source of truth for this run — if
the user (or another tab) changed multimask between intercept and
Run, the snapshot still wins, matching "what you reviewed is what
runs".

If step 2 fails (multimask unreachable, `/state` rejected), the run
aborts before the daemon is called.  The local task flips to `error`
so the user can retry.  This is safer than submitting a run whose
multimask state disagrees with what was displayed.

Controlnets — DISPLAY ONLY
==========================
Unlike multimask, the controlnets snapshot is **read-only**.  The
interceptor does NOT push any state back to the controlnets
playground before resume: controlnet chain configuration (which
ControlNets, strength, start, end) and enable flags live in the
workflow file plus `/state.apply_enabled`, and are treated as
workflow authority, not per-run intent.  A user who wants to change
them does so in the controlnets playground before intercepting.

The `controlnets` sibling is stored on intake and returned as part of
`/intercept/item`, so the panel can display the list of preprocessed
images with their titles, dimensions, and thumbnail previews.  This
is the "what will the workflow consume" review that makes intercept
mode useful for controlnet workflows.

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
- Multimask push fails: same — local task flips to `error` with the
  push error as the message.  The daemon is not called.
- Paste fails during resume: the daemon's `comfy_resume_intercepted`
  returns `{success: false, message: "<paste error>"}`; local task
  flips to `error`; the user can retry with a different prompt, but
  the batch is gone — they have to re-run from the docker.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from pgruntime import daemon_client as krita_client

from pgruntime.ports import port_for

PLAYGROUND_PORT = port_for("interceptor")

_TASKS: dict = {}
_LOCK = threading.Lock()
_GC_MAX_AGE_SEC = 1800   # 30 min


def _log(msg):
    print(f"[interceptor] {msg}", flush=True)


def _gc():
    """Remove terminal-state tasks older than `_GC_MAX_AGE_SEC`.

    Terminal states are `done`, `error`, `cancelled`, and `expired`.
    `expired` was added when `handle_list` started cross-referencing
    the daemon's live `intercepted_batches` registry (see the module
    docstring, "Task expiry") — a task that the daemon has swept gets
    flipped to `expired` locally and is then eligible for the same
    time-based collection the other terminal states use.

    `queued` and `running` are never collected here: the only path to
    a terminal state for a queued task is Run, Discard, Cancel, or
    the daemon's own 15-minute sweep (surfaced via the flip in
    `handle_list`).  Collecting a still-queued task would delete the
    only record of it before the user has any chance to act on it.
    """
    now = time.time()
    with _LOCK:
        stale = [
            tid for tid, t in _TASKS.items()
            if (now - t["received_at"]) > _GC_MAX_AGE_SEC
            and t["status"] in ("done", "error", "cancelled", "expired")
        ]
        for tid in stale:
            del _TASKS[tid]


def _post_json(url, body, timeout=5.0):
    """POST a JSON body and parse a JSON response.  Raises on any
    transport or parse failure.  Used for the multimask push."""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


# ---------------------------------------------------------------------------
#  Intake
# ---------------------------------------------------------------------------

def handle_intake(payload):
    """Receive one intercepted task from the daemon.

    Called by `comfy_generate` in intercept mode.  The task_id is the
    daemon's batch_id; storing it verbatim here means the two task
    lists are the same set of keys and reconciliation is trivial.

    The `multimask` sibling (when present) carries the snapshot the
    daemon captured at intercept time: URL, state metadata, and the
    transformed mask b64s per area.  Stored as-is so `/intercept/item`
    can return it to the browser and `/intercept/run` can push edited
    prompts back to `<url>/state`.

    The `controlnets` sibling (when present) carries the same shape
    for controlnet workflows: URL, state metadata, and transformed
    image b64s per entry.  Stored as-is so `/intercept/item` can
    return it to the browser.  Unlike multimask, no push-back happens
    on Run — controlnet config is workflow authority.
    """
    payload = payload or {}
    task_id = payload.get("task_id")
    inner = payload.get("payload")
    if not task_id or not isinstance(inner, dict):
        return {"success": False, "message": "bad intake shape"}

    source_label = payload.get("source_label") or "daemon"

    # Optional multimask snapshot, sent as a sibling of `payload`.
    mm = payload.get("multimask")
    mm_url = None
    mm_state = None
    mm_masks: dict = {}
    if isinstance(mm, dict):
        url = mm.get("url")
        if isinstance(url, str) and url.strip():
            mm_url = url.strip()
        state = mm.get("state")
        if isinstance(state, dict):
            mm_state = state
        masks = mm.get("masks")
        if isinstance(masks, dict):
            mm_masks = {
                k: v for k, v in masks.items()
                if isinstance(k, str) and isinstance(v, str)
            }

    # Optional controlnets snapshot, sent as a sibling of `payload`.
    cn = payload.get("controlnets")
    cn_url = None
    cn_state = None
    cn_images: dict = {}
    if isinstance(cn, dict):
        url = cn.get("url")
        if isinstance(url, str) and url.strip():
            cn_url = url.strip()
        state = cn.get("state")
        if isinstance(state, dict):
            cn_state = state
        images = cn.get("images")
        if isinstance(images, dict):
            cn_images = {
                k: v for k, v in images.items()
                if isinstance(k, str) and isinstance(v, str)
            }

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
            "multimask_url":   mm_url,
            "multimask_state": mm_state,
            "multimask_masks": mm_masks,
            "controlnets_url":   cn_url,
            "controlnets_state": cn_state,
            "controlnets_images": cn_images,
            "result":  None,
            "message": "",
        }
    _gc()
    _log(f"intake {task_id[:8]} from {source_label!r}"
         + (f" (multimask: {len((mm_state or {}).get('areas') or [])} area(s))"
            if mm_state is not None else "")
         + (f" (controlnets: {len((cn_state or {}).get('controlnets') or [])} entr(ies))"
            if cn_state is not None else ""))
    return {"success": True, "task_id": task_id}


# ---------------------------------------------------------------------------
#  List / item
# ---------------------------------------------------------------------------

def handle_list(_payload):
    """Return a summary of every tracked task.

    Cross-references the daemon's live `intercepted_batches` registry
    to detect tasks the daemon has swept (15-minute intercept timeout)
    and marks them `expired` locally.  Without this, the panel would
    show a stale `queued` status for a task the daemon has forgotten,
    and the user would only discover the expiry when clicking Run
    failed with `"batch expired or unknown"` — accurate but 15-minutes-
    late and confusing.

    `comfy_status` internally calls the daemon's
    `_sweep_expired_intercepts`, so each interceptor poll also keeps
    the daemon's own sweep current within the interceptor's 2-second
    poll cadence, much tighter than the daemon's 300-second sweep
    thread.

    A daemon-unreachable response is treated as "unknown" and does
    NOT mass-expire tasks — a transient blip must not falsely retire
    every queued task.  The next poll after the daemon comes back
    will detect real expiry.
    """
    # Ask the daemon which intercept batches it still considers alive.
    # The daemon's `intercepted_batches` field is the authoritative
    # list; any local `queued` task whose `task_id` is not in it has
    # been swept.
    daemon_responded = False
    daemon_ids: set = set()
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=3.0)
        if krita_client.is_success(resp):
            daemon_responded = True
            data = resp.get("data") or {}
            for entry in (data.get("intercepted_batches") or []):
                if isinstance(entry, dict):
                    tid = entry.get("batch_id")
                    if isinstance(tid, str) and tid:
                        daemon_ids.add(tid)
    except Exception as e:
        _log(f"list: daemon probe failed: {e}")
        daemon_responded = False

    with _LOCK:
        if daemon_responded:
            for tid, t in list(_TASKS.items()):
                if t["status"] == "queued" and tid not in daemon_ids:
                    t["status"] = "expired"
                    t["message"] = (
                        "expired in daemon "
                        "(intercepted-batch timeout, 15 min)"
                    )
                    _log(f"expired {tid[:8]} "
                         f"(not in daemon's intercepted_batches)")

        tasks = [
            {
                "task_id":       t["task_id"],
                "source_label":  t["source_label"],
                "workflow_name": t["payload"].get("workflow_name", ""),
                "received_at":   t["received_at"],
                "status":        t["status"],
                "has_result":    t["result"] is not None,
                "has_multimask": t.get("multimask_state") is not None,
                "has_controlnets": t.get("controlnets_state") is not None,
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
        item = {
            "task_id":      t["task_id"],
            "source_label": t["source_label"],
            "received_at":  t["received_at"],
            "status":       t["status"],
            "payload":      t["payload"],
            "result":       t["result"],
            "message":      t["message"],
        }
        # Multimask snapshot is a sibling, mirroring the intake shape.
        if t.get("multimask_state") is not None:
            item["multimask"] = {
                "url":   t.get("multimask_url"),
                "state": t.get("multimask_state"),
                "masks": t.get("multimask_masks") or {},
            }
        else:
            item["multimask"] = None
        # Controlnets snapshot, same pattern.  Display-only —
        # interceptor never pushes state back to controlnets.
        if t.get("controlnets_state") is not None:
            item["controlnets"] = {
                "url":    t.get("controlnets_url"),
                "state":  t.get("controlnets_state"),
                "images": t.get("controlnets_images") or {},
            }
        else:
            item["controlnets"] = None
        return {"success": True, "task": item}


# ---------------------------------------------------------------------------
#  Run — push multimask state, then resume daemon
# ---------------------------------------------------------------------------

def handle_run(payload):
    """Push updated multimask state (if any), then resume the daemon.

    Blocks for the full generation duration.  The browser's fetch is a
    long-poll against this handler.

    Multimask state push happens FIRST and is FATAL on failure: if the
    workflow uses `MultiAreaConditioning`, its node pulls from
    multimask at ComfyUI execution time.  If we cannot make multimask's
    state match the snapshot the user reviewed, we must not submit
    anything — the alternative is a run with mismatched masks or
    prompts.

    `area_prompts` in the request payload is `{area_id: prompt}`.  If
    an area's prompt is not present, the snapshot's original is used.
    The updated state is always pushed (even with no edits) so that
    drift between intake and Run does not silently override the
    reviewer's intent.

    Controlnets state is NOT pushed anywhere — the snapshot is
    display-only (see the module docstring, "Controlnets — DISPLAY
    ONLY").

    The `expired` status is rejected here just like `done` and
    `error`: the daemon has already dropped the batch, so calling
    `comfy_resume_intercepted` would fail with the same message.
    Surfacing the rejection at `handle_run` keeps the panel's status
    consistent with the daemon's state without a round trip.
    """
    payload = payload or {}
    task_id = payload.get("task_id")
    area_prompts = payload.get("area_prompts") or {}
    if not isinstance(area_prompts, dict):
        area_prompts = {}

    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] not in ("queued", "done", "error"):
            return {"success": False,
                    "message": f"task is {t['status']}"}
        t["status"] = "running"

        # Snapshot the multimask fields outside the lock.  Copy the
        # state so any concurrent mutation (e.g. a new intake, unlikely
        # but cheap to guard) cannot affect this run's push.
        mm_url = t.get("multimask_url")
        mm_state = t.get("multimask_state")
        if isinstance(mm_state, dict):
            mm_state = {
                "globals": dict(mm_state.get("globals") or {}),
                "areas": [dict(a) for a in (mm_state.get("areas") or [])],
            }

    prompt_override = payload.get("prompt")

    # ---- Multimask push (before resume) ----
    if mm_url and mm_state is not None:
        areas = mm_state.get("areas") or []
        for area in areas:
            aid = area.get("id")
            if aid and aid in area_prompts:
                area["prompt"] = str(area_prompts[aid])
        updated_state = {
            "globals": mm_state.get("globals") or {},
            "areas": areas,
        }
        try:
            _post_json(
                mm_url.rstrip("/") + "/state",
                {
                    "op": "set",
                    "state": updated_state,
                    # masks intentionally omitted — multimask's _masks
                    # dict keeps its originals; /mask re-transforms
                    # correctly at ComfyUI execution time.
                },
                timeout=10.0,
            )
        except Exception as e:
            _log(f"multimask push failed for {task_id[:8]}: {e}")
            with _LOCK:
                tt = _TASKS.get(task_id)
                if tt is not None:
                    tt["status"] = "error"
                    tt["message"] = f"multimask push failed: {e}"
            return {
                "success": False,
                "message": f"multimask push failed: {e}",
            }
        _log(f"multimask state pushed for {task_id[:8]} "
             f"({len(areas)} area(s))")

    # ---- Resume daemon ----
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

        if krita_client.is_success(resp):
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
    handler can always call it without checking.  No multimask or
    controlnets interaction — discard means "do not run", so no state
    is pushed.
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
