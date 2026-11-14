"""comfyui_intercept.py — intercept mode: prepare-and-hand-off branch,
resume / discard / list commands, and the multimask snapshot helpers.

Intercept mode is the newest surface in the `comfyui` handler family
and the most likely to be edited as the interceptor playground
evolves.  It is grouped here so all of its moving parts live in one
place:

  · Multimask snapshot — `_get_multimask_url_from_graph`,
    `_post_json`, `_fetch_multimask_snapshot`.
  · The prepare-and-hand-off branch — `generate_intercepted` (what
    `comfy_generate` calls when the intercept toggle is on).
  · Batch building + intake POST — `_make_intercepted_batch`,
    `_post_intake`, `notify_interceptor_cancel`.
  · The `InterceptCommands` handler with `comfy_set_intercept`,
    `comfy_resume_intercepted`, `comfy_discard_intercepted`,
    `comfy_list_intercepted`.
  · `_poll_job_for_resume` — the standalone poll loop the resume path
    uses (no `_BatchRecord`, no cancellation event).

Output download and paste live in `comfyui_paste.py`; both branches
import from there.  This module does not import from
`comfyui_generation.py`, so the two stay acyclic.

Dependency direction:

    comfyui_paste.py
        ←  comfyui_intercept.py
    comfyui_generation.py
        ←  imports InterceptCommands, generate_intercepted,
           notify_interceptor_cancel from this module.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Optional

from ..decorators import command, AutoDispatchHandler
from ..utils.comfy_apply import prepare_workflow
from ..utils.comfyui_krita import invalidate_asset_cache
from ..utils.comfyui_proxy_client import (
    ProxyHTTPError,
    ProxyUnreachable,
    ProxyV2,
)
from .comfyui_document import (
    _check_color_mode,
    _get_live_active_document,
)
from .comfyui_paste import download_outputs, paste_result
from .comfyui_types import (
    _DEFAULT_MULTIMASK_URL,
    InterceptedBatch,
)


_POLL_INTERVAL_S = 0.25

# Timeout for the resume command's internal poll loop.  Longer than a
# normal generate's --timeout because the interceptor user has just
# clicked Run and there is no separate cancel path once the poll
# starts.
_RESUME_POLL_TIMEOUT_S = 180.0

# Timeout for the intake POST.  The interceptor's intake handler is
# lightweight (just queues the task); 5 s is generous.
_INTAKE_TIMEOUT_S = 5.0

# Timeouts for the multimask snapshot fetch.  /state is metadata-only
# (sub-100 ms); /mask triggers a transform per area (still fast on
# localhost, but give it room for a few areas).
_MULTIMASK_STATE_TIMEOUT_S = 5.0
_MULTIMASK_MASK_TIMEOUT_S = 10.0


# ---------------------------------------------------------------------------
#  Multimask helpers (module-level, no state)
# ---------------------------------------------------------------------------

def _get_multimask_url_from_graph(graph: dict) -> Optional[str]:
    """Return the multimask playground URL if `graph` uses
    `MultiAreaConditioning`, else None.

    The URL comes from that node's `playground_url` widget value.  An
    empty/blank value falls back to `_DEFAULT_MULTIMASK_URL`, matching
    the node's own default.  A workflow with two `MultiAreaConditioning`
    nodes picks the first one — they always share one multimask URL in
    practice, and picking deterministically is better than picking "last
    one wins".
    """
    if not isinstance(graph, dict):
        return None
    for _node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") != "MultiAreaConditioning":
            continue
        url = (node.get("inputs") or {}).get("playground_url")
        if isinstance(url, str) and url.strip():
            return url.strip()
        return _DEFAULT_MULTIMASK_URL
    return None


def _post_json(url: str, body: dict, timeout: float = 5.0) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else {}


def _fetch_multimask_snapshot(url: str) -> Optional[dict]:
    """Fetch multimask's state and per-area transformed masks.

    Returns `{"url": str, "state": {...}, "masks": {hash: b64}}`, or
    `None` on unrecoverable failure (multimask unreachable, `/state`
    rejected, or returned non-JSON).  A partial mask-fetch failure is
    tolerated — the affected areas are simply absent from `masks`, and
    the interceptor draws the ones that made it.

    `/mask?hash=…` returns the mask transformed to the prepared frame
    (rotate+crop per `/crop_frame`) at the moment of the call.  Since
    `_fetch_multimask_snapshot` runs before the prepare loop, the crop
    frame it sees is the same one `prepare_workflow` will use for
    `@thecanvas` on the immediately-following iteration — unless the
    user rotates inpaint during the microsecond between the two calls,
    in which case the caller's warning list will already note the
    multimask fetch succeeded and the prepare is per-run consistent.
    """
    base = url.rstrip("/")
    try:
        state = _post_json(
            base + "/state", {}, timeout=_MULTIMASK_STATE_TIMEOUT_S,
        )
    except (urllib.error.URLError, OSError, ValueError) as e:
        print(f"[comfyui] multimask /state failed at {url!r}: {e}",
              flush=True)
        return None

    if not isinstance(state, dict) or not state.get("success", False):
        print(f"[comfyui] multimask /state rejected at {url!r}",
              flush=True)
        return None

    areas = list(state.get("areas") or [])
    masks: dict = {}
    for area in areas:
        mask_meta = area.get("mask") or {}
        h = mask_meta.get("hash")
        if not h:
            continue
        try:
            mr = _post_json(
                base + "/mask", {"hash": h},
                timeout=_MULTIMASK_MASK_TIMEOUT_S,
            )
        except (urllib.error.URLError, OSError, ValueError) as e:
            print(f"[comfyui] multimask /mask {h[:8]} failed: {e}",
                  flush=True)
            continue
        if mr.get("success") and mr.get("b64"):
            masks[h] = mr["b64"]

    return {
        "url": url,
        "state": {
            "globals": state.get("globals") or {},
            "areas": areas,
        },
        "masks": masks,
    }


# ---------------------------------------------------------------------------
#  Intercept branch — prepare and hand off
# ---------------------------------------------------------------------------

def generate_intercepted(
    *,
    parent,
    doc,
    proxy: ProxyV2,
    requested_count: int,
    base_seed: int,
    workflow_name: str,
    graph: dict,
    resource_slots: dict,
    param_slots: dict,
    config: dict,
    interceptor_url: str,
) -> dict:
    """Prepare N workflows and hand them to the interceptor.

    All inputs come from `_comfy_generate_impl`'s entry-time snapshot.
    This is what makes intercepted runs safe under concurrent clicks:
    two intercepted runs each read the same handler state at their
    own entry, and neither reads `parent._active_config` /
    `parent._active_workflow_param_slots` / `parent._intercept_url` at
    prepare time.

    `interceptor_url` is used both in the log message and in every
    `InterceptedBatch.interceptor_url`, so a concurrent
    `comfy_set_intercept --url` cannot result in a batch that is
    half-registered against one URL and half against another.

    `param_slots` is passed to `_make_intercepted_batch` for the
    prompt-node lookup.  Without it, a concurrent `comfy_load` could
    swap the param tables between entry-time snapshot and batch
    construction, causing the prompt node id to point at a
    nonexistent node in the prepared graph; the later prompt override
    would then silently no-op.

    Unlike the normal path, this function never enters `parent._batches`:
    it builds `InterceptedBatch` records and stores them in
    `parent._intercepted_batches`.  No poll loop runs here; the
    interceptor's `comfy_resume_intercepted` runs the
    submit/poll/download/paste half later, on the user's schedule.

    A multimask snapshot (when the workflow uses
    `MultiAreaConditioning`) is fetched once, before the prepare
    loop, so all runs share it and the crop frame it uses matches
    what `prepare_workflow` will use on the first iteration.

    Partial failures are tolerated.  A failed `prepare_workflow` or
    `POST intake` for run i is recorded in `errors` and the loop
    continues; the runs that succeeded are still handed to the
    interceptor.  If every run fails, the call returns
    `success: False`.
    """
    has_seed_slot = "seed" in param_slots
    if requested_count > 1 and not has_seed_slot:
        count = 1
        warnings_list = [
            f"count={requested_count} clamped to 1: the workflow "
            f"has no Primitive titled 'seed'."
        ]
    else:
        count = requested_count
        warnings_list = []

    # ---- Multimask snapshot, fetched once before the prepare loop ----
    multimask_url = _get_multimask_url_from_graph(graph)
    multimask_snapshot: Optional[dict] = None
    if multimask_url:
        print(f"[comfyui] intercept: fetching multimask snapshot "
              f"from {multimask_url}", flush=True)
        multimask_snapshot = _fetch_multimask_snapshot(multimask_url)
        if multimask_snapshot is None:
            warnings_list.append(
                f"multimask unreachable at {multimask_url}; "
                f"workflow may fail at MultiAreaConditioning"
            )
            print(f"[comfyui] intercept: multimask unreachable; "
                  f"workflow's MultiAreaConditioning node may fail",
                  flush=True)
        else:
            n_areas = len(
                (multimask_snapshot.get("state") or {}).get("areas") or []
            )
            n_masks = len(multimask_snapshot.get("masks") or {})
            print(f"[comfyui] intercept: multimask snapshot "
                  f"fetched ({n_areas} area(s), {n_masks} mask(s))",
                  flush=True)

    intercepted_ids: list = []
    errors: list = []

    for i in range(count):
        seed_i = (base_seed + i) & 0xFFFFFFFF
        print(
            f"[comfyui] intercept {i + 1}/{count}: preparing "
            f"seed {seed_i}", flush=True,
        )

        try:
            prepared = prepare_workflow(
                doc,
                graph,
                resource_slots,
                param_slots,
                config,
                proxy,
                seed=seed_i,
            )
        except ProxyUnreachable as e:
            parent.invalidate_proxy_client()
            errors.append(f"prepare[{i}]: proxy unreachable: {e}")
            continue
        except ValueError as e:
            errors.append(f"prepare[{i}]: {e}")
            continue

        blobs = prepared.blobs or {}
        if "@thecanvas" not in blobs:
            errors.append(
                f"prepare[{i}]: workflow has no @thecanvas; "
                f"intercept mode cannot display anything to review. "
                f"Turn off intercept mode for this workflow."
            )
            continue

        try:
            record = _make_intercepted_batch(
                index=i,
                count=count,
                workflow_name=workflow_name,
                prepared=prepared,
                seed=seed_i,
                base_seed=base_seed,
                multimask_snapshot=multimask_snapshot,
                config=config,
                param_slots=param_slots,
                interceptor_url=interceptor_url,
            )
        except Exception as e:
            errors.append(f"batch_build[{i}]: {e}")
            continue

        ok, err = _post_intake(record)
        if not ok:
            # The record never reached the interceptor; nothing can
            # resume it.  Drop it and record the error.
            errors.append(f"intake[{i}]: {err}")
            continue

        with parent._intercepted_lock:
            parent._intercepted_batches[record.batch_id] = record
        intercepted_ids.append(record.batch_id)

    if intercepted_ids:
        parent._publish_state_change()

    if not intercepted_ids:
        msg = "All intercept attempts failed"
        if warnings_list:
            msg += "; " + "; ".join(warnings_list)
        if errors:
            msg += "; " + "; ".join(errors)
        return {
            "success": False,
            "message": msg,
            "data": {
                "intercepted": False,
                "errors": errors,
                "warnings": warnings_list,
            },
        }

    msg = (f"Intercepted {len(intercepted_ids)} of "
           f"{requested_count} run(s) at {interceptor_url}")
    combined = list(warnings_list) + list(errors)
    if combined:
        msg += " \u2014 " + "; ".join(combined)

    return {
        "success": True,
        "message": msg,
        "data": {
            "intercepted": True,
            "batch_ids": intercepted_ids,
            "count": len(intercepted_ids),
            "requested_count": requested_count,
            "interceptor_url": interceptor_url,
            "warnings": warnings_list,
            "errors": errors,
            "multimask_url": multimask_url,
            "multimask_areas": (
                len((multimask_snapshot.get("state") or {}).get("areas") or [])
                if multimask_snapshot else 0
            ),
        },
    }


def _make_intercepted_batch(
    *,
    index: int,
    count: int,
    workflow_name: str,
    prepared,
    seed: int,
    base_seed: int,
    multimask_snapshot: Optional[dict],
    config: dict,
    param_slots: dict,
    interceptor_url: str,
) -> InterceptedBatch:
    """Build one InterceptedBatch from a prepared workflow.

    Snapshots everything the resume path will need: the prepared
    graph (for submit), crop bounds + paste config + rotation (for
    paste), the prompt node id (for the override), the three
    `@`-resource blobs (for display in the interceptor's canvas),
    and the multimask snapshot (for area display + prompt edits).
    """
    # batch_id: suffix per-run index when count > 1 so ids are
    # distinct across runs of the same comfy_generate call.
    token = uuid.uuid4().hex[:12]
    batch_id = (
        f"intercept_{token}"
        if count == 1 else
        f"intercept_{token}_{index}"
    )

    # Resolve the prompt node.  The workflow's `prompt` Primitive slot,
    # if it has one, is where the override goes.  Look it up in the
    # load-time param_slots snapshot; the node is guaranteed to exist
    # in prepared.graph because the same snapshot was passed to
    # prepare_workflow.
    prompt_slot = param_slots.get("prompt") or {}
    prompt_node_id = prompt_slot.get("node_id")
    prompt_original = ""
    if prompt_node_id:
        node = prepared.graph.get(prompt_node_id) or {}
        v = (node.get("inputs") or {}).get("value", "")
        prompt_original = str(v) if v is not None else ""

    # Pull the three resource blobs.
    blobs = prepared.blobs or {}
    canvas_bytes = blobs.get("@thecanvas", b"")
    sel_bytes = blobs.get("@theselection", b"")
    ctx_bytes = blobs.get("@thecontext", b"")

    def _b64(b: bytes) -> str:
        return base64.b64encode(b).decode("ascii") if b else ""

    warnings = list(prepared.warnings or [])
    if not prompt_node_id:
        warnings.append(
            "workflow has no 'prompt' Primitive slot; the interceptor "
            "prompt override will have no effect."
        )

    # Multimask snapshot fields, flattened from the optional dict.
    mm_url = None
    mm_state = None
    mm_masks: dict = {}
    if multimask_snapshot:
        mm_url = multimask_snapshot.get("url")
        mm_state = multimask_snapshot.get("state")
        mm_masks = multimask_snapshot.get("masks") or {}

    return InterceptedBatch(
        batch_id=batch_id,
        workflow_name=workflow_name,
        prepared_graph=prepared.graph,
        prompt_node_id=prompt_node_id,
        prompt_original=prompt_original,
        crop_bounds=tuple(prepared.crop_bounds),
        output_rotation=float(prepared.rotation),
        paste_config=dict(config["paste"]),
        base_seed=int(base_seed),
        seed=int(seed),
        canvas_b64=_b64(canvas_bytes),
        selection_b64=_b64(sel_bytes),
        context_b64=_b64(ctx_bytes),
        params_snapshot=dict(config.get("params") or {}),
        warnings=warnings,
        interceptor_url=interceptor_url,
        multimask_url=mm_url,
        multimask_state=mm_state,
        multimask_masks=mm_masks,
    )


def _post_intake(record) -> tuple:
    """POST one InterceptedBatch to the interceptor's intake endpoint.

    Returns `(ok, error_message)`.  On success `error_message` is an
    empty string.  Any failure (connection refused, timeout, non-JSON
    body, `{success: false}` response) is returned as
    `(False, reason)` so the caller can accumulate it in the
    batch-level errors list.

    The multimask snapshot rides as a SIBLING of `payload`, not
    inside it — `payload` is conceptually the inpaint data, and the
    multimask fields are a different concern the interceptor stores
    in its own state.
    """
    url = record.interceptor_url
    body = {
        "task_id": record.batch_id,
        "source_label": record.workflow_name,
        "payload": {
            "canvas_b64":      record.canvas_b64,
            "selection_b64":   record.selection_b64,
            "context_b64":     record.context_b64,
            "prompt":          record.prompt_original,
            "workflow_name":   record.workflow_name,
            "params":          record.params_snapshot,
            "crop_bounds":     list(record.crop_bounds),
            "output_rotation": record.output_rotation,
            "warnings":        list(record.warnings),
        },
    }
    if record.multimask_state is not None:
        body["multimask"] = {
            "url": record.multimask_url,
            "state": record.multimask_state,
            "masks": record.multimask_masks or {},
        }

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(
            req, timeout=_INTAKE_TIMEOUT_S,
        ) as r:
            raw = r.read()
        parsed = json.loads(raw) if raw else {}
    except urllib.error.URLError as e:
        return False, f"unreachable: {e.reason}"
    except OSError as e:
        return False, f"io error: {e}"
    except ValueError as e:
        return False, f"bad response: {e}"

    if not isinstance(parsed, dict):
        return False, "response not an object"
    if not parsed.get("success", False):
        return False, str(
            parsed.get("message") or "rejected by interceptor"
        )
    return True, ""


def notify_interceptor_cancel(url: str,
                              batch_id: Optional[str]) -> None:
    """Best-effort POST to the interceptor's `/cancel` sibling.

    Called by `comfy_cancel` (in `comfyui_generation.py`) when it
    drops an intercepted batch, so the interceptor's own task list can
    mark the item cancelled before the user tries to run it and gets
    "batch expired or unknown".  Failure is swallowed: the
    interceptor's resume path handles a missing batch gracefully.
    """
    if not url:
        return
    cancel_url = None
    try:
        from urllib.parse import urlparse, urlunparse
        p = urlparse(url)
        path_parts = p.path.rstrip("/").rsplit("/", 1)
        if len(path_parts) == 2 and path_parts[1] == "intake":
            cancel_url = urlunparse(
                p._replace(path=path_parts[0] + "/cancel")
            )
        else:
            if len(path_parts) == 2:
                cancel_url = urlunparse(
                    p._replace(path=path_parts[0] + "/cancel")
                )
            else:
                cancel_url = urlunparse(
                    p._replace(path="/intercept/cancel")
                )
    except Exception:
        return

    body: dict = {}
    if batch_id:
        body["task_id"] = batch_id
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        cancel_url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as r:
            r.read()
    except Exception as e:
        print(
            f"[comfyui] interceptor cancel notify "
            f"({cancel_url}) failed: {e}",
            flush=True,
        )


# ---------------------------------------------------------------------------
#  Resume poll loop
# ---------------------------------------------------------------------------

def poll_job_for_resume(
    proxy: ProxyV2, job_id: str, *, timeout: float,
) -> Optional[dict]:
    """Poll one proxy job to terminal.  Standalone helper for the
    resume path (no `_BatchRecord` available — the resume runs
    outside a batch)."""
    deadline = time.monotonic() + timeout
    last_error: Optional[str] = None

    while time.monotonic() < deadline:
        try:
            job = proxy.get_job(job_id)
        except ProxyUnreachable as e:
            last_error = str(e)
            time.sleep(_POLL_INTERVAL_S)
            continue
        except ProxyHTTPError as e:
            if e.status == 404:
                return {
                    "id": job_id,
                    "status": "expired",
                    "outputs": [],
                    "error": {
                        "code": "not_found",
                        "message": "job unknown to proxy",
                    },
                }
            last_error = str(e)
            time.sleep(_POLL_INTERVAL_S)
            continue

        status = job.get("status")
        if status in ("succeeded", "failed", "canceled", "expired"):
            return job

        time.sleep(_POLL_INTERVAL_S)

    if last_error:
        print(
            f"[comfyui] resume poll {job_id[:8]} timed out; "
            f"last error: {last_error}",
            flush=True,
        )
    return None


# ---------------------------------------------------------------------------
#  Intercept commands
# ---------------------------------------------------------------------------

class InterceptCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Enable or disable interceptor routing of "
                  "comfy_generate. When enabled, comfy_generate "
                  "prepares the workflow and hands it to the "
                  "interceptor playground; the interceptor resumes "
                  "generation when the user clicks Run. Pass only "
                  "--url to change the URL without touching the "
                  "enabled flag.",
        args={
            "--enabled": {"type": "bool", "default": None,
                          "help": "True to enable, False to disable. "
                                  "Omit to leave unchanged."},
            "--url":     {"type": "str", "default": None,
                          "help": "Interceptor intake URL. Omit to "
                                  "leave unchanged."},
        },
    )
    def comfy_set_intercept(self, enabled=None, url=None):
        from .comfyui_types import _save_intercept_config

        parent = self.parent
        changed = False
        if enabled is not None:
            parent._intercept_enabled = bool(enabled)
            changed = True
        if url is not None:
            parent._intercept_url = str(url)
            changed = True
        if changed:
            _save_intercept_config(
                parent._intercept_enabled, parent._intercept_url,
            )

        state = "enabled" if parent._intercept_enabled else "disabled"
        return {
            "success": True,
            "message": (
                f"intercept {state}; url={parent._intercept_url}"
            ),
            "data": {
                "enabled": parent._intercept_enabled,
                "url": parent._intercept_url,
            },
        }

    @command(
        category="comfyui",
        help_text="Resume an intercepted batch: apply the prompt "
                  "override (if any) in-place on the prepared graph, "
                  "then submit / poll / download / paste. Returns the "
                  "same shape as comfy_generate's success path. The "
                  "batch record is removed from the daemon on entry — "
                  "a second call on the same batch_id returns "
                  "'expired or unknown'.  The interceptor is expected "
                  "to have pushed an updated multimask state before "
                  "calling this; the daemon does not push multimask "
                  "state itself.",
        args={
            "--batch_id":         {"type": "str", "required": True,
                                   "help": "batch id from the intake "
                                           "response."},
            "--prompt_override":  {"type": "str", "default": None,
                                   "help": "New prompt to substitute "
                                           "into the workflow's "
                                           "'prompt' Primitive. "
                                           "No-op if the workflow has "
                                           "no such slot."},
        },
    )
    def comfy_resume_intercepted(self, batch_id,
                                 prompt_override=None):
        parent = self.parent

        # Sweep first so an expired record cannot be resumed.
        parent._sweep_expired_intercepts()

        with parent._intercepted_lock:
            record = parent._intercepted_batches.pop(batch_id, None)
        if record is None:
            return {
                "success": False,
                "message": (
                    f"batch {batch_id!r} expired or unknown"
                ),
            }

        # Apply the prompt override in-place on the prepared graph.
        prompt_override_applied = False
        if prompt_override is not None:
            if record.prompt_node_id:
                node = record.prepared_graph.get(record.prompt_node_id)
                if node is not None and "inputs" in node:
                    node["inputs"]["value"] = str(prompt_override)
                    prompt_override_applied = True

        # Notify the docker the batch is gone from the review queue.
        parent._publish_state_change()

        # ---- resolve resources for the resume ----
        try:
            proxy = parent.get_proxy_client()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False,
                    "message": f"proxy unavailable: {e}"}

        doc = _get_live_active_document()
        if doc is None:
            return {"success": False,
                    "message": "No active document in Krita"}
        ok, msg = _check_color_mode(doc)
        if not ok:
            return {"success": False, "message": msg}

        started = time.time()

        # ---- submit ----
        try:
            job = proxy.submit_job(record.prepared_graph)
        except ProxyHTTPError as e:
            if proxy.pid:
                invalidate_asset_cache(proxy.pid)
            return {
                "success": False,
                "message": f"submit failed: {e} (code={e.code})",
                "data": {
                    "batch_id": record.batch_id,
                    "prompt_override_applied": prompt_override_applied,
                },
            }
        except ProxyUnreachable as e:
            parent.invalidate_proxy_client()
            return {
                "success": False,
                "message": f"submit failed: {e}",
                "data": {
                    "batch_id": record.batch_id,
                    "prompt_override_applied": prompt_override_applied,
                },
            }

        job_id = job["id"]

        # ---- poll ----
        terminal = poll_job_for_resume(
            proxy, job_id, timeout=_RESUME_POLL_TIMEOUT_S,
        )
        if terminal is None:
            return {
                "success": False,
                "message": "poll timed out",
                "data": {
                    "batch_id": record.batch_id,
                    "job_id": job_id,
                },
            }

        status = terminal.get("status")
        if status != "succeeded":
            err = (terminal.get("error") or {}).get("message") or status
            return {
                "success": False,
                "message": f"job {status}: {err}",
                "data": {
                    "batch_id": record.batch_id,
                    "job_id": job_id,
                    "status": status,
                },
            }

        # ---- download ----
        downloaded = download_outputs(proxy, terminal)
        if not downloaded:
            return {
                "success": False,
                "message": "job succeeded but produced no "
                           "retrievable outputs",
                "data": {
                    "batch_id": record.batch_id,
                    "job_id": job_id,
                },
            }

        # ---- paste ----
        seed_suffix = (
            record.seed if record.base_seed != record.seed else None
        )

        with parent._paste_lock:
            try:
                paste_result_dict = paste_result(
                    doc,
                    downloaded,
                    crop_bounds=record.crop_bounds,
                    paste_config=record.paste_config,
                    job_id=job_id,
                    seed_suffix=seed_suffix,
                    output_rotation=record.output_rotation,
                )
            except Exception as e:
                import traceback
                print("[comfyui] resume paste raised:", flush=True)
                traceback.print_exc()
                return {
                    "success": False,
                    "message": f"paste raised: {e}",
                    "data": {
                        "batch_id": record.batch_id,
                        "job_id": job_id,
                    },
                }

            if not paste_result_dict.get("success"):
                return paste_result_dict

            pdata = paste_result_dict.get("data") or {}
            elapsed = time.time() - started

            return {
                "success": True,
                "message": paste_result_dict.get("message") or "Pasted",
                "data": {
                    "batch_id": record.batch_id,
                    "job_id": job_id,
                    "workflow": record.workflow_name,
                    "layer_names": list(pdata.get("layer_names") or []),
                    "output_count": int(pdata.get("output_count", 0)),
                    "image_count": len(downloaded),
                    "timing": round(elapsed, 3),
                    "prompt_override_applied": prompt_override_applied,
                },
            }

    @command(
        category="comfyui",
        help_text="Discard an intercepted batch without running it. "
                  "Idempotent; returns success even if the batch was "
                  "already gone.",
        args={
            "--batch_id": {"type": "str", "required": True},
        },
    )
    def comfy_discard_intercepted(self, batch_id):
        parent = self.parent
        parent._sweep_expired_intercepts()

        with parent._intercepted_lock:
            existed = (
                parent._intercepted_batches.pop(batch_id, None)
                is not None
            )

        if existed:
            return {
                "success": True,
                "message": f"discarded {batch_id!r}",
                "data": {"batch_id": batch_id, "discarded": True},
            }
        return {
            "success": True,
            "message": f"batch {batch_id!r} was already gone",
            "data": {"batch_id": batch_id, "discarded": False},
        }

    @command(
        category="comfyui",
        help_text="List intercepted batches currently awaiting review.",
        args={},
    )
    def comfy_list_intercepted(self, params):
        parent = self.parent
        parent._sweep_expired_intercepts()

        with parent._intercepted_lock:
            entries = [
                {
                    "batch_id":       r.batch_id,
                    "workflow_name":  r.workflow_name,
                    "created_at":     r.created_at,
                    "expiry_at":      r.expiry_at,
                    "interceptor_url": r.interceptor_url,
                    "multimask_url":  r.multimask_url,
                    "has_multimask":  r.multimask_state is not None,
                }
                for r in parent._intercepted_batches.values()
            ]
        entries.sort(key=lambda e: e["created_at"])

        return {
            "success": True,
            "message": f"{len(entries)} intercepted batch(es)",
            "data": {
                "batches": entries,
                "interceptor_url": parent._intercept_url,
                "enabled": parent._intercept_enabled,
            },
        }