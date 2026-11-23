"""comfyui_generation.py — the generation container and the normal
(non-intercept) pipeline.

Structure
=========
This module is a container (`GenerationCommands`) that instantiates
two leaf handlers and forwards `comfy_generate` for direct callers:

    NormalGenerationCommands   comfy_generate, comfy_cancel,
                               comfy_history, comfy_paste_last,
                               comfy_paste_image_b64
    InterceptCommands          comfy_set_intercept, comfy_resume_intercepted,
                               comfy_discard_intercepted,
                               comfy_list_intercepted

The container's only non-dispatch method is a plain `comfy_generate`
forwarder, so `comfyui_config.py::comfy_panel_run`'s direct call —
`parent._generation_cmds.comfy_generate(gen_params)` — keeps working
without that file needing to know about the split.

The `@command` decorator lives on the sub-handlers; the container's
`comfy_generate` forwarder is deliberately NOT decorated, so
`build_handler_map` picks up the real `comfy_generate` from
`NormalGenerationCommands` via `_sub_handlers` and routes dispatch to
it.  No collision, no dispatch change.

Helpers that used to be methods on `GenerationCommands` and are used
by both the normal pipeline and the intercept path now live in
`comfyui_paste.py` and are imported here.  The poll loop
(`_poll_job`) stays local because only the normal path uses it.

Poll-loop budgets
=================
`_poll_job` uses four budgets, chosen so a slow-but-progressing
workflow succeeds instead of failing at the first deadline:

  · `_MAX_QUEUE_WAIT_S` (300s) — cap on how long we'll wait for a
    queued job to reach "running".  A slow ComfyUI queue is normal;
    the job will eventually run.

  · `_MIN_EXEC_TIMEOUT_S` (300s) — floor on the execution budget.
    Even a tight `--timeout 55` gets 300s of execution once the job
    is "running".

  · `_MAX_TOTAL_POLL_S` (600s) — hard cap on total poll duration
    for one job.  Defensive bound so a job that somehow cycles
    between states cannot hang the daemon forever.

  · The caller's `--timeout` (default 55s) — the user's expected
    per-run execution budget.  Combined with the floor: the actual
    execution budget is `max(--timeout, _MIN_EXEC_TIMEOUT_S)`.

Progress detection: whenever the job dict's `progress` or
`queue_position` field changes between polls, the execution deadline
resets.  The v2 proxy exposes `progress` as a structured snapshot
dict (e.g. `{"value": 12, "max": 20, "node_id": "..."}`); comparing
the whole dict — not guessing at `step`/`total` key names — is what
makes idle detection work regardless of which keys the current proxy
build emits.  A workflow that advances its progress counter is
demonstrably alive; a workflow stuck at the same snapshot past the
execution budget is presumed hung and fails with a clear message.

Polling is the right primitive here, not SSE
============================================
The v2 API is poll-first by design: `GET /api/v2/jobs/{id}` is the
authoritative, resumable view (status, latest progress snapshot,
outputs committed so far, survives a client disconnect until
`expires_at`).  The SSE stream at `GET /api/v2/jobs/{id}/events` is a
live enhancement, not a replacement — it carries no event ids, has no
resume cursor, and frames emitted while disconnected are lost.  A CLI
batch runner needs a durable result even across a brief proxy
connection blip, so the poll is the only safe primitive for
termination; the stream would only be useful as an in-process
progress display, which is not what this handler produces.

Concurrency
===========
See `comfyui.py`'s module docstring for the full picture.  Three
invariants survive the split unchanged:

  1. `_comfy_generate_impl` snapshots handler state at entry; the
     rest of the pipeline reads the local snapshot only.
  2. `comfy_panel_run` passes its validated config as a kwarg, so
     concurrent clicks never race on `_active_config`.
  3. Document-mutating work (`paste_result`) runs under
     `parent._paste_lock`.

Playground-owned parameters
===========================
A registered playground may claim parameter names (see
`playgrounds.py`'s PARAM SLOTS).  `_fetch_playground_params` runs
right after entry-time snapshotting and before the intercept branch,
so every path — normal, intercepted, no_paste — gets the same
treatment: claimed params are fetched from each playground's
`/params` route, merged into the config's `params` dict, and passed
to `prepare_workflow`.  A claimed parameter therefore never reads
`_active_config` — the playground is the source of truth, and the
merge is what implements "pushed at generation time".  A fetch
failure is logged and the config falls back to whatever
`_active_config` held for that param — the claimed field then reads
a stale default rather than failing the whole run, which matches the
best-effort spirit of the rest of this module.  Intercept batches
freeze the merged values into `prepared_graph` at intercept time, so
`comfy_resume_intercepted` requires no extra work.

Rotation
========
The rotation-aware paste (`paste_result` in `comfyui_paste.py`) is
reached from both the normal path and the intercept path.  Its
behaviour is unchanged: `rotation == 0` pastes at
`(crop_bounds[0]+x, crop_bounds[1]+y)`; `rotation != 0` pads to a
rotated frame, un-rotates by `-rotation`, centre-crops to the doc
dims, pastes at `(paste_config.x, paste_config.y)`.

CANVAS_PUSH — post-paste canvas publication (Slice D + E)
=========================================================
Two events fire from this module after a successful batch:

  1. `paste_applied` — published PER PASTE, from inside the
     `paste_result` success branch, with the pasted PNG bytes +
     position.  The inpaint playground's subscriber forwards it to
     browsers over the `/remote` WS as `patch` on the `paste.applied`
     field, and the browser composites it onto its pre-paste canvas
     for instant visual feedback (~20-90 ms).  Slice E.

  2. `canvas_update` — published ONCE AT BATCH END (in the finally
     block), with the authoritative `doc.projection()` composite.
     The browser replaces the optimistic composite with this when
     it arrives (~200-500 ms), which for inpaint is visually
     identical to the optimistic version — no flicker — but for
     edge cases (blend modes, user painting mid-batch) corrects any
     drift the local composite missed.  Slice D.

The two-event split is what gives the user "playground updates at
the same time as Krita" without paying the `doc.projection()` cost
on the critical path.  The heavy composite still runs — it just runs
*after* the optimistic display, in the background, without holding
up the perceived update.

Both are best-effort: a closed doc, a null composite, or an encode
failure logs one line and returns without publishing.  The batch
itself already returned success by that point; the canvas pushes
are enhancements, not correctness requirements.

OUTPAINT — result publication without paste (Option 3)
======================================================
A workflow whose config carries `OUTPAINT=True` in its `[params]`
is treated specially: the daemon does NOT paste the result into
Krita.  Outpaint results are larger than the source canvas, and
pasting them would either fail the layer bounds check or require
`resize_canvas=true`, which mutates the user's document without
their explicit request.

Instead, `_comfy_generate_impl` calls `_publish_outpaint_result`
once per downloaded output, publishing an `outpaint_result` event
on the event bus.  The outpaint playground's subscriber stores the
latest one in `_last_pasted`, exposes it via `/active_state`, and
the browser's autosync poll calls `applyGeneratedImage` to display
it.  Send to Krita remains the playground's only document-mutating
action, and it is user-initiated (the original document is never
touched).

The `outpaint_result` event type is distinct from `paste_applied`
so the inpaint playground's subscriber never sees outpaint outputs
(and vice versa).  Payload shape is identical, plus an
`"outpaint": True` marker for diagnostics.

For outpaint runs, `any_paste_succeeded` is NOT set and
`canvas_update` does NOT fire — the document was not modified, so
the authoritative composite is unchanged.  The batch summary still
reports `success` per run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from typing import Optional

from krita import Krita

from ..decorators import command, AutoDispatchHandler
from ..utils.comfy_apply import prepare_workflow
from ..utils.comfyui_krita import (
    invalidate_asset_cache,
    paste_image,
)
from ..utils.comfyui_proxy_client import (
    ProxyHTTPError,
    ProxyUnreachable,
    ProxyV2,
)
from .comfyui_document import (
    _check_color_mode,
    _get_live_active_document,
    _png_dimensions,
    _public_run_record,
)
from .comfyui_types import (
    _BatchRecord,
    _HISTORY_DIR,
)
from .comfyui_paste import (
    download_outputs,
    paste_result,
    record_used_layers,
    write_history_record,
)
from .comfyui_intercept import (
    InterceptCommands,
    _post_json,
    generate_intercepted,
    notify_interceptor_cancel,
)


_POLL_INTERVAL_S = 0.25

# Poll-loop budgets for _poll_job.  See the module docstring,
# "Poll-loop budgets".
_MAX_QUEUE_WAIT_S = 300.0
_MIN_EXEC_TIMEOUT_S = 300.0
_MAX_TOTAL_POLL_S = 600.0

# Timeout for the playground-owned param fetch.  Localhost, JSON-only,
# no rendering — 2 s is generous for a healthy process and short
# enough that a dead playground doesn't block generation.
_PLAYGROUND_PARAM_TIMEOUT_S = 2.0


# ---------------------------------------------------------------------------
#  Playground-owned parameter fetch
# ---------------------------------------------------------------------------

def _fetch_playground_params(parent, param_slots: dict) -> dict:
    """Return current values for any workflow param claimed by a
    registered playground.

    Called from `_comfy_generate_impl` right after entry-time
    snapshotting and before the intercept branch.  See the module
    docstring, "Playground-owned parameters".

    Iterates `parent._playgrounds_cmds.list()`.  For each playground,
    checks whether any of its `param_slots` names appear in the
    loaded workflow's `param_slots` — if not, the playground is
    skipped without an HTTP call, so a registered playground never
    costs a round-trip when the current workflow doesn't use its
    params.

    On a match, POSTs `{"": ""}` (empty body) to `<url>/params` and
    merges whatever `values` dict comes back, filtered to the names
    the workflow actually declares.  A failure (connection refused,
    timeout, non-JSON body, `{success: false}`, or an exception) is
    logged as one `[comfyui] playground param fetch …` line and
    treated as "no values from this playground".  The caller falls
    back to `_active_config`'s values for that param — matching the
    best-effort convention used elsewhere in this module (canvas
    pushes, history writes) rather than aborting an entire batch
    over one unavailable playground.

    The graph's `param_slots` keys are authoritative: even if a
    playground returns extra names, only names present in the graph
    are merged.  This keeps a stale playground that still claims a
    param the current workflow no longer declares from polluting the
    config.
    """
    if not isinstance(param_slots, dict) or not param_slots:
        return {}

    try:
        playgrounds = parent._playgrounds_cmds.list()
    except Exception as e:
        print(
            f"[comfyui] playground list failed: {e}",
            flush=True,
        )
        return {}

    graph_param_set = set(param_slots.keys())
    values: dict = {}

    for p in playgrounds or []:
        if not isinstance(p, dict):
            continue
        url = (p.get("url") or "").rstrip("/")
        claimed = p.get("param_slots") or []
        if not url or not claimed:
            continue
        wanted = [n for n in claimed if n in graph_param_set]
        if not wanted:
            continue

        try:
            resp = _post_json(
                url + "/params", {},
                timeout=_PLAYGROUND_PARAM_TIMEOUT_S,
            )
        except Exception as e:
            print(
                f"[comfyui] playground param fetch failed at "
                f"{url}: {e}",
                flush=True,
            )
            continue

        if not isinstance(resp, dict) or not resp.get("success"):
            print(
                f"[comfyui] playground param fetch rejected at "
                f"{url}: {resp!r}",
                flush=True,
            )
            continue

        returned = resp.get("values") or {}
        if not isinstance(returned, dict):
            continue
        for name, val in returned.items():
            if name in wanted:
                values[name] = val

    return values


# ---------------------------------------------------------------------------
#  canvas_update + paste_applied publication (Slices D + E)
# ---------------------------------------------------------------------------

def _publish_canvas_update(doc) -> None:
    """Compose the document and publish a `canvas_update` event.

    Called from `_comfy_generate_impl`'s finally-block when at least
    one paste succeeded.  See the module docstring, CANVAS_PUSH.

    Reads the current composite (`doc.projection()`, with the same
    fallback ladder `DocumentHandler.get_document_composite_b64` uses),
    encodes to PNG, computes a stable hash, and publishes
    `{doc_name, hash, b64, width, height}` on the event bus as a
    `canvas_update` event.  Subscribers (the inpaint server's
    subscriber thread in particular) forward it to browser clients.

    Best-effort: doc closed mid-batch, null composite, encode failure
    — all log one line and return.  The batch has already returned
    success by the time this is called; the push is an enhancement.
    """
    try:
        from .comfyui_document import _document_is_open
        if not _document_is_open(doc):
            print("[comfyui] canvas_update: doc no longer open",
                  flush=True)
            return

        from PyQt6.QtCore import QBuffer, QIODevice, QByteArray

        qimg = doc.projection()
        if qimg is None or qimg.isNull():
            try:
                doc.refreshProjection()
                qimg = doc.projection()
            except Exception:
                qimg = None
        if qimg is None or qimg.isNull():
            try:
                qimg = doc.thumbnail(doc.width(), doc.height())
            except Exception:
                qimg = None
        if qimg is None or qimg.isNull():
            print("[comfyui] canvas_update: composite is null",
                  flush=True)
            return

        buf = QByteArray()
        buffer = QBuffer(buf)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        ok = qimg.save(buffer, "PNG")
        buffer.close()
        if not ok:
            print("[comfyui] canvas_update: PNG encode failed",
                  flush=True)
            return
        png_bytes = bytes(buf)
        if not png_bytes:
            print("[comfyui] canvas_update: empty PNG", flush=True)
            return

        b64 = base64.b64encode(png_bytes).decode("ascii")
        h = hashlib.sha256(png_bytes).hexdigest()[:16]

        from ..server.event_bus import get_event_bus
        get_event_bus().publish("canvas_update", {
            "doc_name": doc.name(),
            "hash":     h,
            "b64":      b64,
            "width":    qimg.width(),
            "height":   qimg.height(),
        })
        print(f"[comfyui] canvas_update published: "
              f"{qimg.width()}x{qimg.height()} hash={h}", flush=True)
    except Exception as e:
        print(f"[comfyui] canvas_update publish failed: {e}",
              flush=True)


def _publish_paste_applied(paste: dict, doc) -> None:
    """Publish one `paste_applied` event for a just-completed paste.

    Called from `_comfy_generate_impl` immediately after each
    successful `paste_result`, per item in its `applied` list.  See
    the module docstring, CANVAS_PUSH.

    The payload is `{doc_name, hash, b64, paste_x, paste_y, width,
    height}`, where `b64` is the exact PNG the Krita layer received.
    The browser composites this over its pre-paste canvas for
    optimistic display (~20-90 ms, vs ~200-500 ms for the full
    `canvas_update`).

    Best-effort: an exception here logs one line and returns.  The
    paste already landed in Krita; the authoritative `canvas_update`
    will correct any display drift at batch end.
    """
    if not isinstance(paste, dict):
        return
    try:
        from ..server.event_bus import get_event_bus
        get_event_bus().publish("paste_applied", {
            "doc_name": doc.name() if doc else "",
            "hash":     paste.get("hash"),
            "b64":      paste.get("b64"),
            "paste_x":  paste.get("paste_x", 0),
            "paste_y":  paste.get("paste_y", 0),
            "width":    paste.get("width", 0),
            "height":   paste.get("height", 0),
        })
    except Exception as e:
        print(f"[comfyui] paste_applied publish failed: {e}",
              flush=True)


def _publish_outpaint_result(out: dict, doc) -> None:
    """Publish one raw outpaint result as an `outpaint_result` event.

    See the module docstring, OUTPAINT.  Unlike `_publish_paste_applied`,
    this does not correspond to an actual paste — the outpaint workflow
    is set to skip the paste stage entirely, so nothing is written to
    the Krita document.  The event carries the raw downloaded PNG so
    the outpaint playground can display it in its viewer.

    The event type is `outpaint_result`, distinct from `paste_applied`,
    so the inpaint playground's subscriber never receives outpaint
    outputs (they are far larger than the canvas and would corrupt its
    optimistic composite).  Payload shape matches `paste_applied` with
    `paste_x`/`paste_y` set to 0 and an `"outpaint": True` marker.

    Best-effort.  A failure logs one line and continues — the batch
    success is already recorded by the time this is called.
    """
    try:
        png = out.get("png_bytes") or b""
        if not png:
            return
        try:
            w, h = _png_dimensions(png)
        except Exception as e:
            print(f"[comfyui] outpaint_result: _png_dimensions "
                  f"failed: {e}", flush=True)
            return
        h_hex = hashlib.sha256(png).hexdigest()[:16]
        b64 = base64.b64encode(png).decode("ascii")
        from ..server.event_bus import get_event_bus
        get_event_bus().publish("outpaint_result", {
            "doc_name": doc.name() if doc else "",
            "hash":     h_hex,
            "b64":      b64,
            "paste_x":  0,
            "paste_y":  0,
            "width":    int(w),
            "height":   int(h),
            "outpaint": True,
        })
        print(f"[comfyui] outpaint_result published: "
              f"{w}x{h} hash={h_hex}", flush=True)
    except Exception as e:
        print(f"[comfyui] outpaint_result publish failed: {e}",
              flush=True)


# ---------------------------------------------------------------------------
#  Poll loop (module-level, normal path only)
# ---------------------------------------------------------------------------

def _job_progress_snapshot(job: dict) -> tuple:
    """Snapshot the fields that indicate real job progress.

    ComfyUI's v2 job dict exposes `progress` as a structured snapshot
    dict (e.g. ``{"value": 12, "max": 20, "node_id": "..."}``).  We
    compare the whole dict rather than guessing at key names — the
    previous version looked for ``step``/``total``, which do not
    appear in the v2 wire shape, and would have silently failed to
    detect progress on a real job (causing a legitimately slow
    workflow to be killed as if it were hung).

    ``tuple(sorted(prog.items()))`` gives a hashable, order-stable
    representation of the dict.  If the proxy later adds ``eta`` or
    ``node_progress`` to ``progress``, the snapshot picks it up
    automatically without a code change.

    ``queue_position`` is compared separately: while a job is
    ``queued``, the ``progress`` field is usually ``None`` or
    ``{}``, but a decrease in queue position is a real signal that
    the queue is draining and the job is about to run.  See
    ``_poll_job``'s queued branch.

    ``status`` is included unconditionally so a queued→running
    transition is always detected as a state change.
    """
    parts = [job.get("status")]

    prog = job.get("progress")
    if isinstance(prog, dict):
        # Sorted items: dict order never causes a false "change".
        parts.append(("progress", tuple(sorted(prog.items()))))
    elif prog is not None:
        parts.append(("progress", prog))

    if "queue_position" in job:
        parts.append(("queue", job["queue_position"]))

    return tuple(parts)


def _poll_job(
    proxy: ProxyV2,
    job_id: str,
    record: _BatchRecord,
    *,
    timeout: float,
) -> Optional[dict]:
    """Poll one proxy job to terminal.

    Four budgets apply (see the module docstring):

      · Queue phase: up to `_MAX_QUEUE_WAIT_S`.  A queued job is
        not a failure; it will run once ComfyUI's queue reaches it.

      · Execution phase: up to `max(timeout, _MIN_EXEC_TIMEOUT_S)`.
        Resets whenever the job dict changes.

      · Hard cap: `_MAX_TOTAL_POLL_S`.  Bounds the worst case.

    Returns the terminal job dict, or None on timeout.  None is
    converted by the caller into a "poll timed out" run-record
    entry, which surfaces in the batch summary and in history.
    """
    started = time.monotonic()
    exec_budget = max(float(timeout), _MIN_EXEC_TIMEOUT_S)

    queue_deadline = started + _MAX_QUEUE_WAIT_S
    run_deadline: Optional[float] = None

    last_snapshot: Optional[tuple] = None
    last_error: Optional[str] = None

    while True:
        now = time.monotonic()

        if now - started > _MAX_TOTAL_POLL_S:
            print(
                f"[comfyui] poll {job_id[:8]} gave up after "
                f"_MAX_TOTAL_POLL_S={_MAX_TOTAL_POLL_S:.0f}s",
                flush=True,
            )
            return None

        if run_deadline is not None:
            if now > run_deadline:
                print(
                    f"[comfyui] poll {job_id[:8]} timed out: "
                    f"execution exceeded {exec_budget:.0f}s",
                    flush=True,
                )
                return None
        elif now > queue_deadline:
            print(
                f"[comfyui] poll {job_id[:8]} timed out: "
                f"queued >{_MAX_QUEUE_WAIT_S:.0f}s",
                flush=True,
            )
            return None

        if (record.cancel_event.is_set()
                and job_id not in record._cancel_sent):
            try:
                proxy.cancel_job(job_id)
            except (ProxyHTTPError, ProxyUnreachable):
                pass
            record._cancel_sent.add(job_id)

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
                    "error": {"code": "not_found",
                              "message": "job unknown to proxy"},
                }
            last_error = str(e)
            time.sleep(_POLL_INTERVAL_S)
            continue

        status = job.get("status")
        if status in ("succeeded", "failed", "canceled", "expired"):
            return job

        snapshot = _job_progress_snapshot(job)
        state_changed = (snapshot != last_snapshot)

        if status == "running":
            if run_deadline is None:
                # First "running" observation: start the execution
                # budget.  From here on, the queue budget is
                # irrelevant — the job has been picked up.
                run_deadline = now + exec_budget
            elif state_changed:
                # Progress detected.  Reset the execution deadline.
                run_deadline = now + exec_budget
        elif status == "queued" and state_changed:
            # Queue progressed (e.g. queue position changed); reset
            # the queue budget.  Rarely needed but harmless.
            queue_deadline = now + _MAX_QUEUE_WAIT_S

        last_snapshot = snapshot
        time.sleep(_POLL_INTERVAL_S)


# ---------------------------------------------------------------------------
#  Container
# ---------------------------------------------------------------------------

class GenerationCommands(AutoDispatchHandler):
    """Container for the two leaf generation handlers.

    Provides a plain `comfy_generate` forwarding method for the
    direct call from `comfyui_config.py::comfy_panel_run`.  All
    actual `@command` methods live on the leaf handlers and are
    reached via `_sub_handlers`.
    """

    def __init__(self, parent):
        self.parent = parent
        self.normal = NormalGenerationCommands(parent)
        self.intercept = InterceptCommands(parent)
        self._sub_handlers = [self.normal, self.intercept]
        super().__init__()

    def comfy_generate(self, params):
        """Forward for direct callers.  The `@command`-decorated
        `comfy_generate` lives on `NormalGenerationCommands`;
        dispatch reaches it through `_sub_handlers`.  This method
        exists only so
        `parent._generation_cmds.comfy_generate(gen_params)` from
        `comfy_panel_run` keeps working without that module having
        to know about the split.
        """
        return self.normal.comfy_generate(params)


# ---------------------------------------------------------------------------
#  Normal path (leaf handler)
# ---------------------------------------------------------------------------

class NormalGenerationCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    # ------------------------------------------------------------------
    #  comfy_generate
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Run the active workflow via the v2 proxy. Requires "
                  "comfy_apply_config to have been called. When "
                  "intercept mode is on, prepares the workflows and "
                  "hands them to the interceptor playground without "
                  "submitting.",
        args={
            "--timeout": {"type": "float", "default": 55.0},
            "--count":   {"type": "int",   "default": 1},
            "--seed":    {"type": "int",   "required": False},
            "--no_paste": {"type": "bool", "default": False},
        },
    )
    def comfy_generate(self, params):
        try:
            return self._comfy_generate_impl(params)
        except Exception as e:
            import traceback
            print("[comfyui] comfy_generate raised:", flush=True)
            traceback.print_exc()
            return {
                "success": False,
                "message": f"comfy_generate raised: {e}",
                "data": {},
            }

    def _comfy_generate_impl(self, params):
        parent = self.parent

        # ---- Snapshot handler state at entry ----
        #
        # `comfy_panel_run` passes an explicit `config` as a kwarg so
        # concurrent runs never touch `_active_config`.  Callers that
        # don't pass it (CLI's build_batch, comfy_save_config's
        # followup) fall back to `parent._active_config`; those
        # callers are inherently serial.  The rest of this method
        # uses only the local snapshots below.  A concurrent
        # comfy_load / comfy_apply_config cannot corrupt an in-flight
        # run.
        graph = parent._active_workflow_graph
        resource_slots = parent._active_workflow_resource_slots
        param_slots = parent._active_workflow_param_slots
        workflow_name = parent._active_workflow_name or "(unnamed)"
        config = params.get("config") or parent._active_config

        # ----- validate -----
        if graph is None:
            return {"success": False,
                    "message": "No workflow loaded. Run comfy_load first."}
        if config is None:
            return {"success": False,
                    "message": "No panel config applied. Run "
                               "comfy_apply_config first."}
        if parent._client is None:
            return {"success": False,
                    "message": "Not connected; run comfy_connect first"}

        doc = _get_live_active_document()
        if doc is None:
            return {"success": False,
                    "message": "No active document in Krita"}
        ok, msg = _check_color_mode(doc)
        if not ok:
            return {"success": False, "message": msg}

        # Shared proxy client.  The first run of a proxy lifetime pays
        # the health probe; subsequent runs reuse the cached client.
        try:
            proxy = parent.get_proxy_client()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False,
                    "message": f"proxy unavailable: {e}"}

        # ----- parse common args -----
        requested_count = max(1, int(params.get("count", 1)))
        count = requested_count
        try:
            timeout = float(params.get("timeout", 55.0))
        except (TypeError, ValueError):
            timeout = 55.0
        no_paste = bool(params.get("no_paste", False))

        raw_seed = params.get("seed")
        if raw_seed in (None, ""):
            base_seed = int(time.time()) & 0xFFFFFFFF
        else:
            try:
                base_seed = int(raw_seed) & 0xFFFFFFFF
            except (TypeError, ValueError):
                base_seed = int(time.time()) & 0xFFFFFFFF

        batch_id = params.get("_batch_id") or str(uuid.uuid4())

        # ----- fetch playground-owned param values -----
        #
        # Runs before the intercept branch so both normal and
        # intercepted paths see the merged config, and before
        # prepare_workflow for the same reason.  A claimed parameter
        # therefore never reads _active_config: the playground that
        # claims it is the source of truth, and this merge is the
        # "pushed at generation time" step.  See the module docstring,
        # "Playground-owned parameters".
        playground_param_values = _fetch_playground_params(
            parent, param_slots,
        )
        if playground_param_values:
            config = {
                **config,
                "params": {
                    **(config.get("params") or {}),
                    **playground_param_values,
                },
            }
            print(
                f"[comfyui] playground params merged: "
                f"{sorted(playground_param_values.keys())}",
                flush=True,
            )

        # ----- intercept branch -----
        #
        # `no_paste=True` bypasses intercept: the caller explicitly
        # asked for raw images with no paste, which is not what an
        # intercepted review produces.  Without this bypass, a
        # no_paste caller with intercept on would get "intercepted"
        # back and never see images.
        if parent._intercept_enabled and not no_paste:
            parent._sweep_expired_intercepts()
            return generate_intercepted(
                parent=parent,
                doc=doc,
                proxy=proxy,
                requested_count=requested_count,
                base_seed=base_seed,
                workflow_name=workflow_name,
                graph=graph,
                resource_slots=resource_slots,
                param_slots=param_slots,
                config=config,
                interceptor_url=parent._intercept_url,
            )

        # ----- normal path -----
        record = _BatchRecord(
            batch_id=batch_id,
            workflow_name=workflow_name,
            requested_count=requested_count,
            total_runs=requested_count,
            base_seed=base_seed,
            output_name=None,
        )
        with parent._batches_lock:
            parent._batches[batch_id] = record
        parent._publish_state_change()

        any_paste_succeeded = False
        is_outpaint = config["params"].get("OUTPAINT") is True

        try:
            warnings: list = []

            has_seed_slot = "seed" in param_slots
            if count > 1 and not has_seed_slot:
                warnings.append(
                    f"count={requested_count} clamped to 1: the workflow "
                    f"has no Primitive titled 'seed'."
                )
                count = 1
                record.total_runs = 1

            paste_config = dict(config["paste"])
            batch_started = time.time()

            record.current_phase = "preparing"
            submitted: list = []
            any_prepare_succeeded = False

            for i in range(count):
                if record.cancel_event.is_set():
                    for j in range(i, count):
                        record.run_records.append({
                            "seed": (base_seed + j) & 0xFFFFFFFF,
                            "prompt_id": None,
                            "status": "cancelled",
                            "error": "cancelled before submission",
                        })
                    warnings.append("batch cancelled during preparation")
                    break

                record.current_run_index = i
                seed_i = (base_seed + i) & 0xFFFFFFFF
                print(f"[comfyui] batch {i + 1}/{count}: preparing "
                      f"seed {seed_i}", flush=True)

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
                    any_prepare_succeeded = True
                    if prepared.warnings:
                        warnings.extend(prepared.warnings)
                except ProxyUnreachable as e:
                    # The proxy died between the initial get and now
                    # (or during the upload phase).  Invalidate the
                    # cached client so the next run re-probes.
                    parent.invalidate_proxy_client()
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"prepare: proxy unreachable: {e}",
                    })
                    continue
                except ValueError as e:
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"prepare: {e}",
                    })
                    continue

                try:
                    job = proxy.submit_job(prepared.graph)
                except ProxyHTTPError as e:
                    # Over-eager invalidation: any submit-time HTTP
                    # error drops this pid's asset cache.  Cheap, and
                    # self-heals the "proxy evicted assets within a
                    # session" edge case on the next run.
                    if proxy.pid:
                        invalidate_asset_cache(proxy.pid)
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"submit: {e}",
                    })
                    continue
                except ProxyUnreachable as e:
                    parent.invalidate_proxy_client()
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"submit: {e}",
                    })
                    continue

                job_id = job["id"]
                record.job_ids.append(job_id)
                submitted.append({
                    "index": i,
                    "seed": seed_i,
                    "prepared": prepared,
                    "job_id": job_id,
                })

            record.current_phase = "running"

            for entry in submitted:
                job_id = entry["job_id"]
                seed_i = entry["seed"]
                prepared = entry["prepared"]
                record.current_run_index = entry["index"]

                terminal = _poll_job(
                    proxy, job_id, record,
                    timeout=timeout,
                )
                if terminal is None:
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "error",
                        "error": "poll timed out",
                    })
                    continue

                status = terminal.get("status")
                if status == "canceled":
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "cancelled",
                        "error": "cancelled",
                    })
                    continue
                if status == "expired":
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "error",
                        "error": "job expired upstream",
                    })
                    continue
                if status == "failed":
                    err = terminal.get("error") or {}
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "error",
                        "error": (err.get("message")
                                  or "execution failed"),
                    })
                    continue
                if status != "succeeded":
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "error",
                        "error": f"unexpected terminal status: {status}",
                    })
                    continue

                downloaded = download_outputs(proxy, terminal)
                if not downloaded:
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "error",
                        "error": "job succeeded but produced no "
                                 "retrievable outputs",
                    })
                    continue

                if no_paste:
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "success",
                        "image_count": len(downloaded),
                        "output_count": 0,
                        "layer_names": [],
                        "_downloaded": downloaded,
                    })
                    with parent._paste_lock:
                        parent._last_result = {
                            "outputs": downloaded,
                            "job_id": job_id,
                            "crop_bounds": prepared.crop_bounds,
                            "paste_config": paste_config,
                            "output_rotation": prepared.rotation,
                        }
                        parent._last_document = doc
                    continue

                # ----- OUTPAINT branch (Option 3) -----
                #
                # The workflow's params carry OUTPAINT=True: skip
                # the paste stage entirely.  The outpaint result is
                # larger than the source canvas and pasting would
                # either fail the layer bounds check or require
                # resize_canvas=true (silently mutating the user's
                # document).  Instead, publish each raw output as an
                # `outpaint_result` event on the bus; the outpaint
                # playground's subscriber forwards it to the browser,
                # where Send to Krita materializes it as a new doc on
                # explicit user action.
                #
                # any_paste_succeeded stays False → canvas_update
                # does NOT fire in the finally block.  The document
                # was not modified, so its projection is unchanged;
                # publishing it would be wasted work and, for a
                # concurrently-open inpaint playground, a spurious
                # "canvas changed" signal.
                #
                # See the module docstring, OUTPAINT.
                if is_outpaint:
                    for out in downloaded:
                        _publish_outpaint_result(out, doc)
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "success",
                        "image_count": len(downloaded),
                        "output_count": len(downloaded),
                        "layer_names": [],
                    })
                    parent._last_result = {
                        "outputs": downloaded,
                        "job_id": job_id,
                        "crop_bounds": prepared.crop_bounds,
                        "paste_config": paste_config,
                        "output_rotation": prepared.rotation,
                    }
                    parent._last_document = doc
                    continue

                with parent._paste_lock:
                    try:
                        paste_result_dict = paste_result(
                            doc,
                            downloaded,
                            crop_bounds=prepared.crop_bounds,
                            paste_config=paste_config,
                            job_id=job_id,
                            seed_suffix=seed_i if count > 1 else None,
                            output_rotation=prepared.rotation,
                        )
                    except Exception as e:
                        import traceback
                        print(f"[comfyui] paste raised:", flush=True)
                        traceback.print_exc()
                        record.run_records.append({
                            "seed": seed_i, "prompt_id": job_id,
                            "status": "paste_failed",
                            "error": f"paste raised: {e}",
                        })
                        continue

                    if not paste_result_dict.get("success"):
                        record.run_records.append({
                            "seed": seed_i, "prompt_id": job_id,
                            "status": "paste_failed",
                            "error": paste_result_dict.get(
                                "message", "paste failed"),
                        })
                        continue

                    pdata = paste_result_dict.get("data") or {}

                    # Slice E: publish per-paste incremental-display
                    # events BEFORE the bookkeeping below, so the
                    # browser starts compositing as early as possible.
                    # Each `applied` item carries the exact PNG the
                    # Krita layer received plus its paste position;
                    # the browser composites it onto its existing
                    # canvas for optimistic display while the daemon
                    # is still running doc.projection() for the
                    # authoritative canvas_update.
                    for applied_item in (pdata.get("applied") or []):
                        try:
                            _publish_paste_applied(applied_item, doc)
                        except Exception as e:
                            print(
                                f"[comfyui] paste_applied publish "
                                f"failed: {e}", flush=True,
                            )

                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "success",
                        "image_count": len(downloaded),
                        "output_count": pdata.get("output_count", 0),
                        "layer_names": list(pdata.get("layer_names", [])),
                    })
                    parent._last_result = {
                        "outputs": downloaded,
                        "job_id": job_id,
                        "crop_bounds": prepared.crop_bounds,
                        "paste_config": paste_config,
                        "output_rotation": prepared.rotation,
                    }
                    parent._last_document = doc
                    any_paste_succeeded = True

            record.current_phase = "finalizing"

            write_history_record(
                batch_id=batch_id,
                workflow_name=parent._active_workflow_name,
                base_seed=base_seed,
                base_params=dict(config["params"]),
                run_records=record.run_records,
                warnings=warnings,
                duration_s=time.time() - batch_started,
            )

            if any_prepare_succeeded:
                record_used_layers(parent._workflow_cmds, config)

            successful = sum(1 for r in record.run_records
                             if r["status"] == "success")
            cancelled = sum(1 for r in record.run_records
                            if r["status"] == "cancelled")
            failed = len(record.run_records) - successful - cancelled

            total_layers = sum(len(r.get("layer_names", []))
                               for r in record.run_records)

            response_data: dict = {
                "batch_id": batch_id,
                "workflow": workflow_name,
                "count": count,
                "requested_count": requested_count,
                "successful": successful,
                "failed": failed,
                "cancelled": cancelled,
                "base_seed": base_seed,
                "job_ids": list(record.job_ids),
                "layer_count": total_layers,
                "layer_names": [
                    n for r in record.run_records
                    for n in r.get("layer_names", [])
                ],
                "runs": [_public_run_record(r) for r in record.run_records],
            }

            if is_outpaint:
                # For outpaint, layer_count / layer_names are 0 /
                # empty by design; the browser is the receiver, not
                # the Krita layer tree.  Surface that explicitly.
                response_data["outpaint"] = True

            if no_paste:
                images = []
                for r in record.run_records:
                    for d in (r.get("_downloaded") or []):
                        images.append(
                            base64.b64encode(d["png_bytes"]).decode("ascii")
                        )
                if images:
                    response_data["images"] = images

            if warnings:
                response_data["warnings"] = warnings

            if successful == 0:
                if cancelled == len(record.run_records) and cancelled > 0:
                    msg = (f"Batch cancelled "
                           f"({cancelled} run(s) not completed)")
                else:
                    first_err = next(
                        (r.get("error") for r in record.run_records
                         if r.get("error") and r["status"] != "cancelled"),
                        "no successful runs",
                    )
                    msg = f"Generation failed: {first_err}"
                if warnings:
                    msg += " \u2014 " + "; ".join(warnings)
                return {"success": False,
                        "message": msg,
                        "data": response_data}

            if is_outpaint:
                if requested_count == 1:
                    msg = "Outpaint result published to playground"
                else:
                    parts = [f"{successful} succeeded"]
                    if failed:
                        parts.append(f"{failed} failed")
                    if cancelled:
                        parts.append(f"{cancelled} cancelled")
                    msg = (f"Outpaint batch of {requested_count}: "
                           + ", ".join(parts))
            elif no_paste:
                msg = (f"Generated {len(response_data.get('images', []))} "
                       f"image(s) in {successful} run(s)")
            elif requested_count == 1:
                msg = f"Pasted {total_layers} layer(s)"
            else:
                parts = [f"{successful} succeeded"]
                if failed:
                    parts.append(f"{failed} failed")
                if cancelled:
                    parts.append(f"{cancelled} cancelled")
                msg = (f"Batch of {requested_count}: "
                       + ", ".join(parts)
                       + f"; pasted {total_layers} layer(s)")
            if warnings:
                msg += " \u2014 " + "; ".join(warnings)

            return {"success": True, "message": msg, "data": response_data}

        finally:
            with parent._batches_lock:
                parent._batches.pop(batch_id, None)
            # Authoritative post-batch canvas push (Slice D).  Runs
            # AFTER paste_applied already fired per-paste, so the
            # browser's optimistic composite has already displayed;
            # this replaces it with ground truth.  The
            # doc.projection() cost here is off the perceived-latency
            # critical path.
            #
            # Outpaint runs skip this: `any_paste_succeeded` is
            # False for them by construction (no paste happened), so
            # `_publish_canvas_update` does not fire — which is
            # correct, the document was not modified.
            try:
                if any_paste_succeeded:
                    _publish_canvas_update(doc)
            except Exception as e:
                print(f"[comfyui] canvas_update publish raised: {e}",
                      flush=True)
            parent._publish_state_change()

    # ------------------------------------------------------------------
    #  comfy_cancel
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Cancel one or more running batches, including "
                  "intercepted batches awaiting review.",
        args={
            "--batch_id": {"type": "str", "required": False},
        },
    )
    def comfy_cancel(self, params):
        import threading

        parent = self.parent
        batch_id = params.get("batch_id") or None

        # ---- intercepted batches first ----
        intercepted_url_to_notify: Optional[str] = None
        intercepted_ids_cancelled: list = []

        with parent._intercepted_lock:
            if batch_id:
                record = parent._intercepted_batches.pop(batch_id, None)
                if record is not None:
                    intercepted_url_to_notify = record.interceptor_url
                    intercepted_ids_cancelled.append(record.batch_id)
            else:
                for bid, rec in list(
                    parent._intercepted_batches.items()
                ):
                    intercepted_url_to_notify = rec.interceptor_url
                    intercepted_ids_cancelled.append(bid)
                    del parent._intercepted_batches[bid]

        if intercepted_ids_cancelled:
            threading.Thread(
                target=notify_interceptor_cancel,
                args=(intercepted_url_to_notify,
                      batch_id if batch_id else None),
                daemon=True,
            ).start()

        # ---- internal / external batches ----
        with parent._batches_lock:
            if batch_id:
                record = parent._batches.get(batch_id)
                if record is None:
                    if intercepted_ids_cancelled:
                        return {
                            "success": True,
                            "message": (
                                f"Cancel requested for intercepted "
                                f"batch {batch_id}"
                            ),
                            "data": {
                                "batches": intercepted_ids_cancelled,
                            },
                        }
                    return {
                        "success": False,
                        "message": f"No active batch with id {batch_id!r}",
                        "data": {
                            "active_batches": sorted(
                                parent._batches.keys()
                            ),
                        },
                    }
                records = [record]
            else:
                records = list(parent._batches.values())

        if not records and not intercepted_ids_cancelled:
            return {
                "success": True,
                "message": "Cancel requested (nothing was running)",
                "data": {"batches": []},
            }

        for r in records:
            external_ids = getattr(r, "external_job_ids", None) or []
            if external_ids:
                try:
                    proxy = parent.get_proxy_client()
                except (ProxyUnreachable, ImportError):
                    continue
                for jid in external_ids:
                    try:
                        proxy.cancel_job(jid)
                    except ProxyUnreachable:
                        parent.invalidate_proxy_client()
                        break
                    except ProxyHTTPError:
                        pass
            else:
                r.cancel_event.set()

        active_ids = sorted(r.batch_id for r in records)
        all_ids = active_ids + intercepted_ids_cancelled
        if batch_id:
            msg = f"Cancel requested for batch {batch_id}"
        else:
            msg = (
                f"Cancel requested for {len(all_ids)} batch(es)"
                + (
                    f" ({len(intercepted_ids_cancelled)} intercepted)"
                    if intercepted_ids_cancelled else ""
                )
            )
        return {
            "success": True,
            "message": msg,
            "data": {
                "batches": all_ids,
                "intercepted": intercepted_ids_cancelled,
            },
        }

    # ------------------------------------------------------------------
    #  comfy_paste_last
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Re-paste the most recent generation's result as new "
                  "layers.",
        args={
            "--name": {"type": "str", "required": False},
            "--blend_mode": {"type": "str", "required": False},
        },
    )
    def comfy_paste_last(self, params):
        parent = self.parent
        if parent._last_result is None:
            return {"success": False,
                    "message": "No result to paste; run comfy_generate "
                               "first"}
        doc = (parent._last_document
               or Krita.instance().activeDocument())
        if doc is None:
            return {"success": False, "message": "No active document"}

        paste_config = dict(parent._last_result.get("paste_config") or {})
        if params.get("name"):
            paste_config["name"] = params["name"]
        blend_mode = params.get("blend_mode") or "normal"

        with parent._paste_lock:
            return paste_result(
                doc,
                parent._last_result["outputs"],
                crop_bounds=parent._last_result.get(
                    "crop_bounds",
                    (0, 0, int(doc.width()), int(doc.height())),
                ),
                paste_config=paste_config,
                job_id=parent._last_result.get("job_id", "?"),
                seed_suffix=None,
                output_rotation=parent._last_result.get(
                    "output_rotation", 0.0,
                ),
                blend_mode=blend_mode,
            )

    # ------------------------------------------------------------------
    #  comfy_paste_image_b64
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Paste a base64-encoded PNG into the active document "
                  "as a new paint layer.",
        args={
            "--name":       {"type": "str",   "default": ""},
            "--image_b64":  {"type": "str",   "required": True},
            "--x":          {"type": "int",   "default": 0},
            "--y":          {"type": "int",   "default": 0},
            "--rotation":   {"type": "float", "default": 0.0},
            "--blend_mode": {"type": "str",   "default": "normal"},
        },
    )
    def comfy_paste_image_b64(self, params):
        import base64 as _b64
        import time as _time
        from krita import Krita
        from ..utils.comfyui_krita import paste_image as _paste_image

        name = (params.get("name") or "").strip()
        image_b64 = params.get("image_b64") or ""
        if not image_b64:
            return {"success": False, "message": "image_b64 is required"}

        try:
            x = int(params.get("x", 0))
            y = int(params.get("y", 0))
            rotation = float(params.get("rotation", 0.0))
        except (TypeError, ValueError) as e:
            return {"success": False, "message": f"bad numeric arg: {e}"}

        blend_mode = params.get("blend_mode") or "normal"

        try:
            png = _b64.b64decode(image_b64)
        except Exception as e:
            return {"success": False, "message": f"bad base64: {e}"}

        doc = Krita.instance().activeDocument()
        if doc is None:
            return {"success": False, "message": "No active document"}

        if not name:
            name = f"[Inpaint] {int(_time.time()) & 0xFFFFFF:06x}"

        try:
            node = _paste_image(
                doc, png, x, y, name,
                blend_mode=blend_mode,
                make_active=True,
                rotation=rotation,
            )
        except Exception as e:
            return {"success": False, "message": f"paste failed: {e}"}

        return {
            "success": True,
            "message": f"Pasted {node.name()}",
            "data": {"layer_name": node.name()},
        }

    # ------------------------------------------------------------------
    #  comfy_history
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Return the last N generation records.",
        args={
            "--limit": {"type": "int", "default": 10},
        },
    )
    def comfy_history(self, params):
        limit = int(params.get("limit", 10))
        files = sorted(
            _HISTORY_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        entries = []
        for f in files:
            try:
                entries.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {
            "success": True,
            "message": f"{len(entries)} history entries",
            "data": {"entries": entries},
        }