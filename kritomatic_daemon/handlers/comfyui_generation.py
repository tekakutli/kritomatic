"""comfyui_generation.py — submit workflows via the v2 proxy, poll,
download outputs, paste into Krita.

Rewritten in Stage 5 to use comfy-api-proxy's `/api/v2/*` endpoints
instead of the old WebSocket + tooling-nodes pipeline. The graph the
daemon submits is fully native — no `ETN_*` nodes — because
`prepare_workflow` (Stage 4) has already substituted the resource
blobs and param values into the graph.

Pipeline shape
==============
    prepare_workflow(doc, graph, ..., proxy) → PreparedWorkflow
    proxy.submit_job(prepared.graph)         → job with id
    proxy.get_job(id)                        → poll until terminal
    proxy.download_asset(out.id)             → raw PNG bytes per output
    _paste_result(...)                       → layers in Krita

Batch semantics (`--count N`):
  * N runs are prepared sequentially; each run's graph has the seed
    substituted at prepare time (base_seed + i).
  * N jobs are submitted. `record.job_ids` tracks them.
  * Each job is polled to terminal. Success → download + paste;
    failure → recorded; cancellation → recorded.
  * `comfy_cancel` sets `record.cancel_event`; the poll loop sends
    `cancel_job` once per in-flight job (`record._cancel_sent` tracks).

Cancellation
============
  * During prepare: `record.cancel_event.is_set()` breaks the loop,
    remaining runs marked cancelled, already-submitted jobs still
    polled (they'll wind down via the cancel requests sent below).
  * During poll: on `cancel_event` set, the loop calls
    `proxy.cancel_job(job_id)` once and continues polling. The job
    transitions to `canceling` then `canceled` within a step boundary.

`--no_paste`
============
Returns PNG bytes as base64 in `data.images` instead of pasting into
Krita. Used by the inpaint playground, which pastes client-side. Also
skips `_paste_result` entirely. Sets `_last_result` so a subsequent
`comfy_paste_last` still works.

`--output_rotation`
===================
Applied per-image at paste time via `paste_image(rotation=...)`. Same
semantics as before. Stored in `_last_result` so `comfy_paste_last`
preserves it.

`--output_name`
===============
Deprecated: it filtered ETN_KritaOutput nodes by `name`. In v2, output
names are SaveImage filenames. Accepted for CLI compatibility but
ignored — v2 pastes every SaveImage output that carries
`type: "output"`.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from krita import Krita

from ..decorators import command, AutoDispatchHandler
from ..utils.comfy_apply import prepare_workflow
from ..utils.comfyui_krita import (
    paste_animation,
    paste_batch_as_layers,
    paste_image,
    resize_canvas,
)
from ..utils.comfyui_proxy_client import (
    ProxyHTTPError,
    ProxyUnreachable,
    ProxyV2,
)
from .comfyui_types import _BatchRecord, _HISTORY_DIR
from .comfyui_document import (
    _check_color_mode,
    _get_live_active_document,
    _png_dimensions,
    _public_run_record,
)


# Time allowance per run. The old pipeline's default of 55s matches;
# poll adds a 60s margin so the total wall clock budget is
# timeout + 60 per job.
_POLL_MARGIN_S = 60.0
_POLL_INTERVAL_S = 0.25


class GenerationCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    # ------------------------------------------------------------------
    #  comfy_generate
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Run the active workflow via the v2 proxy. Requires "
                  "comfy_apply_config to have been called (that's what "
                  "tells the daemon which resources to read and which "
                  "params to substitute). With --count N, run the "
                  "workflow N times with consecutive seeds; requires a "
                  "Primitive titled 'seed' in the graph.",
        args={
            "--timeout": {"type": "float", "default": 55.0,
                          "help": "Seconds to wait per run for the job "
                                  "to reach a terminal state."},
            "--count": {"type": "int", "default": 1,
                        "help": "Number of generations to run."},
            "--seed": {"type": "int", "required": False,
                       "help": "Base seed. Run i uses base+i. Omit for "
                               "a random base. Ignored unless the "
                               "workflow has a Primitive titled 'seed'."},
            "--no_paste": {"type": "bool", "default": False,
                           "help": "Skip pasting; return generated PNGs "
                                   "as base64 in data.images. Used by "
                                   "the inpaint playground."},
            "--output_rotation": {"type": "float", "default": 0.0,
                                  "help": "Degrees to rotate each "
                                          "generation output before "
                                          "pasting."},
            "--output_name": {"type": "str", "required": False,
                              "help": "Deprecated. Ignored in the v2 "
                                      "pipeline; retained for CLI "
                                      "compatibility."},
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

        # ---- preconditions ----
        if parent._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded. Run comfy_load or "
                               "publish a graph from ComfyUI's browser."}
        if parent._active_config is None:
            return {"success": False,
                    "message": "No panel config applied. Run "
                               "comfy_apply_config first (or load a "
                               "panel config that sets [resources], "
                               "[params], [crop], [paste])."}
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

        try:
            proxy = ProxyV2.from_pid_file()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False,
                    "message": f"proxy unavailable: {e}"}

        # ---- parse args ----
        requested_count = max(1, int(params.get("count", 1)))
        count = requested_count
        try:
            timeout = float(params.get("timeout", 55.0))
        except (TypeError, ValueError):
            timeout = 55.0
        no_paste = bool(params.get("no_paste", False))
        try:
            output_rotation = float(params.get("output_rotation") or 0.0)
        except (TypeError, ValueError):
            output_rotation = 0.0

        raw_seed = params.get("seed")
        if raw_seed in (None, ""):
            base_seed = int(time.time()) & 0xFFFFFFFF
        else:
            try:
                base_seed = int(raw_seed) & 0xFFFFFFFF
            except (TypeError, ValueError):
                base_seed = int(time.time()) & 0xFFFFFFFF

        batch_id = params.get("_batch_id") or str(uuid.uuid4())
        workflow_name = parent._active_workflow_name or "(unnamed)"

        # ---- register batch ----
        record = _BatchRecord(
            batch_id=batch_id,
            workflow_name=workflow_name,
            requested_count=requested_count,
            total_runs=requested_count,
            base_seed=base_seed,
            output_name=params.get("output_name") or None,
        )
        with parent._batches_lock:
            parent._batches[batch_id] = record
        parent._publish_state_change()

        try:
            warnings: list = []

            # ---- batch feasibility ----
            has_seed_slot = "seed" in parent._active_workflow_param_slots
            if count > 1 and not has_seed_slot:
                warnings.append(
                    f"count={requested_count} clamped to 1: the workflow "
                    f"has no Primitive titled 'seed'. Wire a PrimitiveInt "
                    f"titled 'seed' to the sampler's seed input to enable "
                    f"batch generation."
                )
                count = 1
                record.total_runs = 1

            paste_config = dict(parent._active_config["paste"])
            batch_started = time.time()

            # ---- phase 1: prepare + submit ----
            record.current_phase = "preparing"
            submitted: list = []

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
                        parent._active_workflow_graph,
                        parent._active_workflow_resource_slots,
                        parent._active_workflow_param_slots,
                        parent._active_config,
                        proxy,
                        seed=seed_i,
                    )
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
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"submit: {e} "
                                 f"(code={e.code}, status={e.status})",
                    })
                    continue
                except ProxyUnreachable as e:
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
                print(f"[comfyui] batch {i + 1}/{count}: submitted job "
                      f"{job_id}", flush=True)

            # ---- phase 2: poll each job ----
            record.current_phase = "running"

            for entry in submitted:
                job_id = entry["job_id"]
                seed_i = entry["seed"]
                record.current_run_index = entry["index"]

                terminal = self._poll_job(
                    proxy, job_id, record,
                    timeout=timeout + _POLL_MARGIN_S,
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

                # ---- download outputs ----
                downloaded = self._download_outputs(proxy, terminal)
                if not downloaded:
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "error",
                        "error": "job succeeded but produced no "
                                 "retrievable outputs",
                    })
                    continue

                # ---- no_paste path ----
                if no_paste:
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "success",
                        "image_count": len(downloaded),
                        "output_count": 0,
                        "layer_names": [],
                        "_downloaded": downloaded,
                    })
                    parent._last_result = {
                        "outputs": downloaded,
                        "job_id": job_id,
                        "crop_bounds": entry["prepared"].crop_bounds,
                        "paste_config": paste_config,
                        "output_rotation": output_rotation,
                    }
                    parent._last_document = doc
                    print(f"[comfyui] batch {entry['index'] + 1}/{count}: "
                          f"downloaded {len(downloaded)} image(s) "
                          f"(no_paste)", flush=True)
                    continue

                # ---- paste ----
                try:
                    paste_result = self._paste_result(
                        doc,
                        downloaded,
                        crop_bounds=entry["prepared"].crop_bounds,
                        paste_config=paste_config,
                        job_id=job_id,
                        seed_suffix=seed_i if count > 1 else None,
                        output_rotation=output_rotation,
                    )
                except Exception as e:
                    import traceback
                    print(f"[comfyui] batch {entry['index'] + 1}/{count}: "
                          f"paste raised:", flush=True)
                    traceback.print_exc()
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "paste_failed",
                        "error": f"paste raised: {e}",
                    })
                    continue

                if not paste_result.get("success"):
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "paste_failed",
                        "error": paste_result.get(
                            "message", "paste failed"),
                    })
                    print(f"[comfyui] batch {entry['index'] + 1}/{count}: "
                          f"paste failed: "
                          f"{paste_result.get('message')}", flush=True)
                    continue

                pdata = paste_result.get("data") or {}
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
                    "crop_bounds": entry["prepared"].crop_bounds,
                    "paste_config": paste_config,
                    "output_rotation": output_rotation,
                }
                parent._last_document = doc
                print(f"[comfyui] batch {entry['index'] + 1}/{count}: "
                      f"pasted {len(pdata.get('layer_names', []))} "
                      f"layer(s)", flush=True)

            # ---- finalize ----
            record.current_phase = "finalizing"

            self._write_history_record(
                batch_id=batch_id,
                base_seed=base_seed,
                base_params=dict(parent._active_config["params"]),
                run_records=record.run_records,
                warnings=warnings,
                duration_s=time.time() - batch_started,
            )

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

            # no_paste: include base64 PNG bytes so the caller can use
            # them without a second round trip.
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

            # ---- success/failure verdict ----
            if successful == 0:
                # In no_paste mode, "successful" counts downloaded runs
                # even without pasting — same counter applies.
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

            if no_paste:
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
            parent._publish_state_change()

    # ------------------------------------------------------------------
    #  Poll helpers
    # ------------------------------------------------------------------

    def _poll_job(self, proxy: ProxyV2, job_id: str,
                  record: _BatchRecord, *,
                  timeout: float) -> Optional[dict]:
        """Poll one job until terminal, sending cancel once on cancel_event.

        Returns the terminal Job dict, or None on transport failure
        or timeout. A returned `status` of "canceled" means the job
        wound down cleanly.
        """
        deadline = time.monotonic() + timeout
        last_error: Optional[str] = None

        while time.monotonic() < deadline:
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
                # 404 means the proxy has no record — treat as expired.
                # Any other error is transient; keep polling.
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

            time.sleep(_POLL_INTERVAL_S)

        if last_error:
            print(f"[comfyui] poll {job_id[:8]} timed out; last error: "
                  f"{last_error}", flush=True)
        return None

    def _download_outputs(self, proxy: ProxyV2,
                          job: dict) -> list:
        """Download every committed output of a succeeded job.

        Skips entries with `type: "temp"` — those are `PreviewImage`
        snapshots written to temp/ and are not user-visible results.
        Returns a list of ``{"png_bytes", "name", "node_id", "type"}``.
        """
        real_outputs = [
            o for o in (job.get("outputs") or [])
            if o.get("type") and o["type"] != "temp" and o.get("id")
        ]
        downloaded = []
        for out in real_outputs:
            try:
                png = proxy.download_asset(out["id"])
            except ProxyHTTPError as e:
                if e.status == 404 and e.code == "output_unavailable":
                    # Bytes gone; skip without failing the run.
                    print(f"[comfyui] output {out.get('name')} bytes "
                          f"unavailable; skipping", flush=True)
                    continue
                print(f"[comfyui] download {out.get('name')} failed: "
                      f"{e}", flush=True)
                continue
            except ProxyUnreachable as e:
                print(f"[comfyui] download {out.get('name')} failed: "
                      f"{e}", flush=True)
                continue
            downloaded.append({
                "png_bytes": png,
                "name": out.get("name", ""),
                "node_id": out.get("node_id", ""),
                "type": out.get("type"),
            })
        return downloaded

    # ------------------------------------------------------------------
    #  comfy_cancel
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Cancel one or more running batches.",
        args={
            "--batch_id": {"type": "str", "required": False,
                           "help": "The batch id to cancel. Omit to "
                                   "cancel all active batches."},
        },
    )
    def comfy_cancel(self, params):
        parent = self.parent
        batch_id = params.get("batch_id") or None

        with parent._batches_lock:
            if batch_id:
                record = parent._batches.get(batch_id)
                if record is None:
                    return {
                        "success": False,
                        "message": (f"No active batch with id "
                                    f"{batch_id!r}"),
                        "data": {
                            "active_batches": sorted(
                                parent._batches.keys()
                            ),
                        },
                    }
                records = [record]
            else:
                records = list(parent._batches.values())

        if not records:
            return {"success": True,
                    "message": "Cancel requested (nothing was running)",
                    "data": {"batches": []}}

        for r in records:
            r.cancel_event.set()

        active_ids = sorted(r.batch_id for r in records)
        if batch_id:
            msg = f"Cancel requested for batch {batch_id}"
        else:
            msg = f"Cancel requested for {len(records)} batch(es)"
        return {
            "success": True,
            "message": msg,
            "data": {"batches": active_ids},
        }

    # ------------------------------------------------------------------
    #  comfy_paste_last
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Re-paste the most recent generation's result as new "
                  "layers.",
        args={
            "--name": {"type": "str", "required": False,
                       "help": "Override the layer name"},
            "--blend_mode": {"type": "str", "required": False,
                             "help": "Krita blend mode; default normal"},
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

        return self._paste_result(
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
    #  comfy_history
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Return the last N generation records from the panel's "
                  "history directory.",
        args={
            "--limit": {"type": "int", "default": 10,
                        "help": "How many entries to return"},
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

    # ------------------------------------------------------------------
    #  Paste
    # ------------------------------------------------------------------

    def _paste_result(self, doc, outputs: list, *,
                      crop_bounds: tuple,
                      paste_config: dict,
                      job_id: str,
                      seed_suffix: Optional[int] = None,
                      output_rotation: float = 0.0,
                      blend_mode: str = "normal") -> dict:
        """Paste downloaded outputs as Krita layers.

        `outputs` is the list produced by `_download_outputs`:
        ``[{"png_bytes", "name", "node_id", "type"}, ...]``.

        Groups by `node_id` (one group per SaveImage node). For each
        group:
          * `batch_mode="layers"` and multiple images → multiple layers
            via `paste_batch_as_layers`.
          * `batch_mode="animation"` and multiple images → import as an
            animation.
          * otherwise → one layer per image, name-suffixed if >1.

        `crop_bounds` is the crop origin the workflow ran against. Final
        paste position is ``crop_bounds[0] + paste_config["x"]`` (and
        same for y). A workflow run against a sub-region of the canvas
        pastes its output back at the crop origin, not at the top-left
        of the document.

        `job_id` is used to derive a default layer name when
        `paste_config["name"]` is empty.
        """
        if not outputs:
            return {"success": True,
                    "message": "No outputs to paste",
                    "data": {"layer_names": [],
                             "output_count": 0, "frame_count": 0}}

        # Group by SaveImage node.
        groups: dict[str, list] = {}
        for o in outputs:
            groups.setdefault(o["node_id"], []).append(o)

        # resize_canvas from the first output (if configured).
        resize_canvas_flag = bool(paste_config.get("resize_canvas", False))
        if resize_canvas_flag:
            w, h = _png_dimensions(outputs[0]["png_bytes"])
            if (int(doc.width()), int(doc.height())) != (w, h):
                try:
                    resize_canvas(doc, w, h)
                except Exception as e:
                    return {"success": False,
                            "message": f"resize_canvas failed: {e}"}

        # Final paste origin = crop origin + configured offset.
        paste_x = int(crop_bounds[0]) + int(paste_config.get("x", 0))
        paste_y = int(crop_bounds[1]) + int(paste_config.get("y", 0))
        batch_mode = paste_config.get("batch_mode", "default")
        configured_name = paste_config.get("name") or ""

        pasted_layers: list = []
        pasted_frames = 0
        failures: list = []

        for node_id, group in groups.items():
            images = [o["png_bytes"] for o in group]

            # Layer name.
            if configured_name and len(groups) == 1:
                base_name = configured_name
            elif configured_name:
                base_name = f"{configured_name} ({node_id})"
            elif len(groups) == 1:
                base_name = f"[Generated] {job_id[:8]}"
            else:
                base_name = f"[Generated] {job_id[:8]} ({node_id})"

            if seed_suffix is not None:
                base_name = f"{base_name} ({seed_suffix})"

            try:
                if batch_mode == "animation" and len(images) > 1:
                    if paste_animation(doc, images):
                        pasted_frames += len(images)
                        pasted_layers.append(f"[Animation] {base_name}")
                        continue
                    # Fallback to single paste if animation import failed.
                    node = paste_image(
                        doc, images[0], paste_x, paste_y, base_name,
                        blend_mode=blend_mode,
                        rotation=output_rotation,
                    )
                    pasted_layers.append(node.name())
                elif batch_mode == "layers" and len(images) > 1:
                    nodes = paste_batch_as_layers(
                        doc, images, paste_x, paste_y, base_name,
                        blend_mode=blend_mode,
                        rotation=output_rotation,
                    )
                    pasted_layers.extend(n.name() for n in nodes)
                else:
                    for idx, png in enumerate(images):
                        sub_name = (
                            base_name if len(images) == 1
                            else f"{base_name} #{idx + 1}"
                        )
                        node = paste_image(
                            doc, png, paste_x, paste_y, sub_name,
                            blend_mode=blend_mode,
                            rotation=output_rotation,
                        )
                        pasted_layers.append(node.name())
            except ValueError as e:
                failures.append(f"node {node_id}: {e}")

        if failures and not pasted_layers:
            return {"success": False, "message": "; ".join(failures)}

        summary = f"Pasted {len(pasted_layers)} layer(s)"
        if pasted_frames:
            summary += f", imported {pasted_frames} frame(s)"
        if failures:
            summary += f"; {len(failures)} failed: " + "; ".join(failures)

        return {
            "success": True,
            "message": summary,
            "data": {
                "layer_names": pasted_layers,
                "frame_count": pasted_frames,
                "output_count": len(groups),
            },
        }

    # ------------------------------------------------------------------
    #  History
    # ------------------------------------------------------------------

    def _write_history_record(self,
                              batch_id: str,
                              base_seed: int,
                              base_params: dict,
                              run_records: list,
                              warnings: list,
                              duration_s: float) -> None:
        parent = self.parent
        try:
            runs_summary = []
            for r in run_records:
                entry = {
                    "seed": r.get("seed"),
                    "prompt_id": r.get("prompt_id"),
                    "status": r.get("status"),
                }
                if r.get("error"):
                    entry["error"] = r["error"]
                if r.get("image_count") is not None:
                    entry["image_count"] = r["image_count"]
                if r.get("output_count") is not None:
                    entry["output_count"] = r["output_count"]
                if r.get("layer_names"):
                    entry["layer_names"] = list(r["layer_names"])
                runs_summary.append(entry)

            record = {
                "batch_id": batch_id,
                "workflow": parent._active_workflow_name,
                "base_seed": base_seed,
                "count": len(run_records),
                "runs": runs_summary,
                "parameters": dict(base_params),
                "warnings": list(warnings),
                "finished": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(duration_s, 3),
            }
            path = _HISTORY_DIR / f"{batch_id}.json"
            path.write_text(json.dumps(record, indent=2),
                            encoding="utf-8")
        except Exception:
            pass