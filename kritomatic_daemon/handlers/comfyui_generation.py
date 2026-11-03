"""The generation pipeline: submit, wait, cancel, paste.

This is the most dynamic part of the handler. New behaviour — a new
output mode, a retry strategy, a preview stream, a live progress
plumbing — lands here. Keep the file organised around the phase the
code is in: preparing, submitting, collecting, pasting, recording.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from krita import Krita

from ..decorators import command, AutoDispatchHandler
from ..utils.comfyui_client import (
    CANCELLED,
    ComfyCancelled,
    ComfyError,
    ComfyTimeout,
    GenerationResult,
    JobInfo,
)
from ..utils.comfyui_krita import (
    build_krita_resources,
    paste_animation,
    paste_batch_as_layers,
    paste_image,
    resize_canvas,
)
from ..utils.comfyui_tooling import (
    ExpansionError,
    expand_custom,
    scan_requirements,
)
from .comfyui_types import (
    _BatchRecord,
    _HISTORY_DIR,
    _PendingJob,
    _SEED_PARAMETER_NAME,
)
from .comfyui_document import (
    _capture_document_key,
    _check_color_mode,
    _get_live_active_document,
    _png_dimensions,
    _public_run_record,
    _resolve_live_document_for_pending,
    _resolve_live_document_for_prepared,
)
from .comfyui_inspect import _canvas_seed_is_wired


class GenerationCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    # ------------------------------------------------------------------
    #  comfy_generate
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Run the active workflow. With --count N, run it N "
                  "times with consecutive seeds.",
        args={
            "--timeout": {"type": "float", "default": 55.0,
                          "help": "Seconds to wait per run for ComfyUI."},
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the ETN_KritaOutput "
                                      "node whose `name` widget matches "
                                      "this value."},
            "--count": {"type": "int", "default": 1,
                        "help": "Number of generations to run."},
            "--seed": {"type": "int", "required": False,
                       "help": "Base seed. Run i uses base+i. Omit for "
                               "a random base."},
            "--output_rotation": {"type": "float", "default": 0.0,
                                  "help": "Degrees to rotate the "
                                          "generation output by before "
                                          "pasting."},
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
        requested_count = max(1, int(params.get("count", 1)))
        count = requested_count
        timeout = float(params.get("timeout", 55.0))
        output_name = params.get("output_name") or None
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

        record = _BatchRecord(
            batch_id=batch_id,
            workflow_name=workflow_name,
            requested_count=requested_count,
            total_runs=requested_count,
            base_seed=base_seed,
            output_name=output_name,
        )
        with parent._batches_lock:
            parent._batches[batch_id] = record

        try:
            warnings: list = []

            graph = parent._active_workflow_graph
            if graph is None:
                return {"success": False,
                        "message": "No workflow loaded. Run comfy_load or "
                                   "publish a graph from ComfyUI's browser.",
                        "data": {"batch_id": batch_id}}

            if parent._client is None:
                return {"success": False,
                        "message": "Not connected; run comfy_connect first",
                        "data": {"batch_id": batch_id}}

            parent._publish_state_change()

            has_seed_param = any(
                p["name"] == _SEED_PARAMETER_NAME
                for p in parent._active_workflow_parameters
            )
            has_seed_wire = _canvas_seed_is_wired(graph)
            can_batch = has_seed_param or has_seed_wire

            if count > 1 and not can_batch:
                warnings.append(
                    f"count={requested_count} clamped to 1: the active "
                    f"workflow does not vary its seed between runs. Wire "
                    f"either an ETN_Parameter named {_SEED_PARAMETER_NAME!r} "
                    f"or the ETN_KritaCanvas 'seed' output to the sampler's "
                    f"seed input to enable batch generation."
                )
                count = 1
                record.total_runs = 1

            batch_started = time.time()
            base_params = dict(parent._params)
            original_seed = parent._params.get(_SEED_PARAMETER_NAME)

            prepared: list = []
            run_records = record.run_records
            record.current_phase = "preparing"

            for i in range(count):
                seed_i = (base_seed + i) & 0xFFFFFFFF
                record.current_run_index = i

                if record.cancel_event.is_set():
                    for j in range(i, count):
                        seed_j = (base_seed + j) & 0xFFFFFFFF
                        run_records.append({
                            "seed": seed_j, "prompt_id": None,
                            "status": "cancelled",
                            "error": "cancelled before submission",
                        })
                    warnings.append("batch cancelled during preparation")
                    break

                if has_seed_param:
                    parent._params[_SEED_PARAMETER_NAME] = seed_i

                print(f"[comfyui] batch {i + 1}/{count}: preparing seed "
                      f"{seed_i}", flush=True)

                doc = _get_live_active_document()
                if doc is None:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": "no active document",
                    })
                    continue

                ok, msg = _check_color_mode(doc)
                if not ok:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error", "error": msg,
                    })
                    continue

                try:
                    reqs = scan_requirements(graph)
                    resources = build_krita_resources(
                        doc, reqs,
                        selection_context=parent._selection_context,
                        selection_padding=parent._selection_padding,
                        layer_aliases=dict(parent._layer_aliases),
                        scratch_blobs=dict(parent._scratch_blobs),
                    )
                except (ValueError, ExpansionError) as e:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error", "error": str(e),
                    })
                    continue

                try:
                    expansion = expand_custom(
                        graph, parent._params, resources,
                        seed=seed_i,
                        node_defs=parent._object_info,
                    )
                except ExpansionError as e:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"Workflow rewrite failed: {e}",
                    })
                    continue

                upload_error = None
                for h, data in expansion.image_data.items():
                    try:
                        parent._client.upload_image(h, data)
                    except ComfyError as e:
                        upload_error = f"image upload failed: {e}"
                        break
                if upload_error is not None:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error", "error": upload_error,
                    })
                    continue

                prepared.append({
                    "index": i,
                    "seed": seed_i,
                    "graph": expansion.graph,
                    "document": doc,
                    "document_key": _capture_document_key(doc),
                })

            if has_seed_param:
                if original_seed is None:
                    parent._params.pop(_SEED_PARAMETER_NAME, None)
                else:
                    parent._params[_SEED_PARAMETER_NAME] = original_seed

            if record.cancel_event.is_set() and prepared:
                for p in prepared:
                    run_records.append({
                        "seed": p["seed"], "prompt_id": None,
                        "status": "cancelled",
                        "error": "cancelled before submission",
                    })
                prepared = []
                if "batch cancelled during preparation" not in warnings:
                    warnings.append("batch cancelled during preparation")

            total_layers = 0
            record.current_phase = "running"

            if prepared:
                prepared_graphs = [p["graph"] for p in prepared]
                batch_timeout = timeout * count + 60.0

                def on_result(index: int,
                              result: GenerationResult) -> None:
                    p = prepared[index]
                    seed_i = p["seed"]

                    if result.error == CANCELLED:
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "cancelled",
                            "error": "cancelled mid-batch",
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"cancelled", flush=True)
                        return

                    if result.error:
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "error",
                            "error": result.error,
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"failed: {result.error}", flush=True)
                        return

                    doc = _resolve_live_document_for_prepared(p)
                    if doc is None:
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "skipped",
                            "error": "target document not found",
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"skipped (no target document)", flush=True)
                        return

                    try:
                        paste_result = self._paste_result(
                            doc, result,
                            blend_mode="normal",
                            output_name=output_name,
                            seed_suffix=seed_i if count > 1 else None,
                            output_rotation=output_rotation,
                        )
                    except Exception as e:
                        import traceback
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"paste raised:", flush=True)
                        traceback.print_exc()
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "paste_failed",
                            "error": f"paste raised: {e}",
                        })
                        return

                    if not paste_result.get("success"):
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "paste_failed",
                            "error": paste_result.get("message",
                                                      "paste failed"),
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"paste failed: "
                              f"{paste_result.get('message')}", flush=True)
                        return

                    data = paste_result.get("data", {}) or {}
                    layer_names = list(data.get("layer_names", []))
                    run_records.append({
                        "seed": seed_i,
                        "prompt_id": result.prompt_id,
                        "status": "success",
                        "image_count": len(result.images),
                        "output_count": data.get("output_count", 0),
                        "layer_names": layer_names,
                    })
                    print(f"[comfyui] batch {index + 1}/{count}: pasted "
                          f"{len(layer_names)} layer(s) "
                          f"({', '.join(layer_names) or 'none'})",
                          flush=True)

                    parent._last_result = result
                    parent._last_document = doc

                try:
                    parent._client.generate_batch(
                        prepared_graphs,
                        timeout=batch_timeout,
                        on_result=on_result,
                        should_stop=record.cancel_event.is_set,
                    )
                except ComfyError as e:
                    recorded_seeds = {r.get("seed") for r in run_records}
                    for p in prepared:
                        if p["seed"] not in recorded_seeds:
                            run_records.append({
                                "seed": p["seed"],
                                "prompt_id": None,
                                "status": "error",
                                "error": f"batch transport failed: {e}",
                            })

                total_layers = sum(
                    len(r.get("layer_names", [])) for r in run_records
                )

            record.current_phase = "finalizing"

            self._write_history_record(
                batch_id=batch_id,
                base_seed=base_seed,
                base_params=base_params,
                run_records=run_records,
                warnings=warnings,
                duration_s=time.time() - batch_started,
            )

            successful = sum(1 for r in run_records
                             if r["status"] == "success")
            cancelled = sum(1 for r in run_records
                            if r["status"] == "cancelled")
            failed = len(run_records) - successful - cancelled

            response_data = {
                "batch_id": batch_id,
                "workflow": workflow_name,
                "count": count,
                "requested_count": requested_count,
                "successful": successful,
                "failed": failed,
                "cancelled": cancelled,
                "base_seed": base_seed,
                "layer_count": total_layers,
                "layer_names": [
                    name for r in run_records
                    for name in r.get("layer_names", [])
                ],
                "runs": [_public_run_record(r) for r in run_records],
            }
            if warnings:
                response_data["warnings"] = warnings

            if successful == 0:
                if cancelled == len(run_records) and cancelled > 0:
                    msg = (f"Batch cancelled "
                           f"({cancelled} run(s) not completed)")
                else:
                    first_error = next(
                        (r.get("error") for r in run_records
                         if r.get("error")
                         and r["status"] != "cancelled"),
                        "no successful runs",
                    )
                    msg = f"Generation failed: {first_error}"
                if warnings:
                    msg += " \u2014 " + "; ".join(warnings)
                return {"success": False,
                        "message": msg,
                        "data": response_data}

            if requested_count == 1:
                msg = f"Pasted {total_layers} layer(s)"
            else:
                parts = [f"{successful} succeeded"]
                if failed:
                    parts.append(f"{failed} failed")
                if cancelled:
                    parts.append(f"{cancelled} cancelled")
                msg = (f"Batch of {requested_count}: " + ", ".join(parts)
                       + f"; pasted {total_layers} layer(s)")
            if warnings:
                msg += " \u2014 " + "; ".join(warnings)

            return {"success": True, "message": msg, "data": response_data}

        finally:
            with parent._batches_lock:
                parent._batches.pop(batch_id, None)

    # ------------------------------------------------------------------
    #  Async pipeline
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Queue the active workflow without waiting.",
        args={},
    )
    def comfy_generate_async(self, params):
        prompt_id, error = self._queue_generate()
        if error is not None:
            return error
        return {
            "success": True,
            "message": f"Queued generation {prompt_id}",
            "data": {"prompt_id": prompt_id,
                     "workflow": self.parent._active_workflow_name},
        }

    @command(
        category="comfyui",
        help_text="Wait for a previously-queued generation and paste "
                  "its result.",
        args={
            "--prompt_id": {"type": "str", "required": True,
                            "help": "Prompt id returned by "
                                    "comfy_generate_async"},
            "--timeout": {"type": "float", "default": 55.0,
                          "help": "Seconds to wait"},
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the ETN_KritaOutput "
                                      "node whose `name` widget matches "
                                      "this value."},
        },
    )
    def comfy_wait(self, params):
        parent = self.parent
        prompt_id = params.get("prompt_id", "")
        timeout = float(params.get("timeout", 55.0))
        output_name = params.get("output_name") or None

        pending = parent._pending.get(prompt_id)
        if pending is None:
            return {"success": False,
                    "message": f"Unknown prompt id {prompt_id!r}. It "
                               f"may have been cleared by a daemon "
                               f"reload."}

        if not pending.event.wait(timeout=timeout):
            return {"success": False,
                    "message": f"Generation {prompt_id} still running "
                               f"after {timeout}s"}

        return self._finish_pending(prompt_id, paste=True,
                                    output_name=output_name)

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
                record.cancel_event.set()
                return {
                    "success": True,
                    "message": f"Cancel requested for batch {batch_id}",
                    "data": {"batch_id": batch_id},
                }

            records = list(parent._batches.values())
            active = sorted(parent._batches.keys())

        for r in records:
            r.cancel_event.set()

        async_interrupted = False
        if parent._client is not None and parent._pending:
            try:
                parent._client.interrupt()
                parent._client.cancel_queued(
                    list(parent._pending.keys())
                )
                async_interrupted = True
            except ComfyError:
                pass

        parts = []
        if records:
            parts.append(f"{len(records)} batch(es)")
        if async_interrupted:
            parts.append(f"{len(parent._pending)} async generate(s)")
        if parts:
            msg = "Cancel requested for " + " and ".join(parts)
        else:
            msg = "Cancel requested (nothing was running)"

        return {
            "success": True,
            "message": msg,
            "data": {"batches": active,
                     "async": list(parent._pending.keys())},
        }

    # ------------------------------------------------------------------
    #  Paste
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
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the output node whose "
                                      "name matches."},
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
        return self._paste_result(
            doc, parent._last_result,
            name_override=params.get("name"),
            blend_mode=params.get("blend_mode", "normal"),
            output_name=params.get("output_name") or None,
        )

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
    #  Internal: single-prompt async pipeline
    # ------------------------------------------------------------------

    def _queue_generate(self, seed: Optional[int] = None) -> tuple:
        parent = self.parent
        try:
            workflow = parent._active_workflow_graph
            if workflow is None:
                return None, {"success": False,
                              "message": "No workflow loaded. Run "
                                         "comfy_load or publish a graph "
                                         "from ComfyUI's browser."}
            workflow_name = parent._active_workflow_name or "(unnamed)"

            if parent._client is None:
                return None, {"success": False,
                              "message": "Not connected; run "
                                         "comfy_connect first"}

            doc = _get_live_active_document()
            if doc is None:
                return None, {"success": False,
                              "message": "No active document"}

            ok, msg = _check_color_mode(doc)
            if not ok:
                return None, {"success": False, "message": msg}

            try:
                reqs = scan_requirements(workflow)
                resources = build_krita_resources(
                    doc, reqs,
                    selection_context=parent._selection_context,
                    selection_padding=parent._selection_padding,
                    layer_aliases=dict(parent._layer_aliases),
                    scratch_blobs=dict(parent._scratch_blobs),
                )
            except (ValueError, ExpansionError) as e:
                return None, {"success": False, "message": str(e)}

            if seed is None:
                seed = int(time.time()) & 0xFFFFFFFF
            try:
                expansion = expand_custom(
                    workflow, parent._params, resources,
                    seed=seed,
                    node_defs=parent._object_info,
                )
            except ExpansionError as e:
                return None, {"success": False,
                              "message": f"Workflow rewrite failed: {e}"}

            prompt_id = str(uuid.uuid4())
            pending = _PendingJob(
                prompt_id, workflow_name,
                dict(expansion.parameters), seed,
            )
            pending.document = doc
            pending.document_key = _capture_document_key(doc)
            pending.warnings = list(expansion.warnings)
            parent._pending[prompt_id] = pending

            thread = threading.Thread(
                target=self._run_generation,
                args=(pending, expansion),
                name=f"comfy-gen-{prompt_id[:8]}",
                daemon=True,
            )
            thread.start()

            return prompt_id, None
        except Exception as e:
            return None, {"success": False,
                          "message": f"queue setup failed: {e}"}

    def _run_generation(self, pending: _PendingJob, expansion) -> None:
        parent = self.parent
        try:
            for h, data in expansion.image_data.items():
                parent._client.upload_image(h, data)
            result = parent._client.generate(
                expansion.graph, timeout=600.0,
            )
            pending.result = result
        except (ComfyError, ComfyTimeout, ComfyCancelled,
                ExpansionError) as e:
            pending.error = e
        except Exception as e:
            pending.error = ComfyError(f"unexpected error: {e}")
        finally:
            pending.event.set()

    def _finish_pending(self, prompt_id: str, paste: bool,
                        output_name: Optional[str] = None) -> dict:
        parent = self.parent
        pending = parent._pending.pop(prompt_id, None)
        if pending is None:
            return {"success": False,
                    "message": f"No pending job {prompt_id!r}"}

        run_record: dict = {
            "seed": pending.seed,
            "prompt_id": prompt_id,
            "status": "pending",
        }

        def finalize(status: str, error: Optional[str] = None,
                     layer_names: Optional[list] = None,
                     image_count: Optional[int] = None,
                     output_count: Optional[int] = None) -> None:
            run_record["status"] = status
            if error:
                run_record["error"] = error
            if layer_names is not None:
                run_record["layer_names"] = layer_names
            if image_count is not None:
                run_record["image_count"] = image_count
            if output_count is not None:
                run_record["output_count"] = output_count
            self._write_history_record(
                batch_id=prompt_id,
                base_seed=pending.seed,
                base_params={},
                run_records=[run_record],
                warnings=[],
                duration_s=time.time() - pending.started_at,
            )

        if pending.error is not None:
            finalize("error", error=str(pending.error))
            return {"success": False,
                    "message": f"Generation failed: {pending.error}",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        result = pending.result
        if result is None:
            finalize("error", error="no result")
            return {"success": False,
                    "message": "Generation completed without a result"}

        parent._last_result = result
        parent._last_document = pending.document

        if not paste:
            finalize("success", image_count=len(result.images))
            return {"success": True,
                    "message": f"Generation complete "
                               f"({len(result.images)} image(s))",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        doc = _resolve_live_document_for_pending(pending)
        if doc is None:
            finalize("skipped", error="target document not found")
            return {"success": True,
                    "message": "Generation complete but the target "
                               "document could not be found",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        paste_result = self._paste_result(
            doc, result,
            blend_mode="normal",
            output_name=output_name,
        )
        if not paste_result.get("success"):
            finalize("paste_failed",
                     error=paste_result.get("message", "paste failed"))
            return {"success": False,
                    "message": paste_result.get("message",
                                                "paste failed")}

        data = paste_result.get("data", {}) or {}
        finalize("success",
                 layer_names=list(data.get("layer_names", [])),
                 image_count=len(result.images),
                 output_count=data.get("output_count", 0))

        response_data = {
            "prompt_id": prompt_id,
            "workflow": pending.workflow_name,
            "image_count": len(result.images),
            "output_count": data.get("output_count", 0),
            "texts": [{"name": t.name, "text": t.text}
                      for t in result.texts],
            "layer_names": list(data.get("layer_names", [])),
        }
        warnings = list(pending.warnings)
        warnings.extend(result.warnings)
        if warnings:
            response_data["warnings"] = warnings

        return {
            "success": True,
            "message": paste_result.get(
                "message", "Generation complete and pasted",
            ),
            "data": response_data,
        }

    def _paste_result(self, doc, result: GenerationResult,
                      name_override: Optional[str] = None,
                      blend_mode: str = "normal",
                      output_name: Optional[str] = None,
                      seed_suffix: Optional[int] = None,
                      output_rotation: float = 0.0) -> dict:
        if not result.images:
            return {"success": True, "message": "No images to paste"}

        groups: dict = {}
        for i, (img, info) in enumerate(zip(result.images, result.infos)):
            ev = (result.event_index[i]
                  if i < len(result.event_index) else i)
            groups.setdefault(ev, []).append((img, info))

        if output_name:
            filtered = {
                ev: entries for ev, entries in groups.items()
                if entries and entries[0][1].name == output_name
            }
            if not filtered:
                available = sorted({
                    entries[0][1].name
                    for entries in groups.values()
                    if entries and entries[0][1].name
                })
                return {
                    "success": False,
                    "message": (
                        f"No output node named {output_name!r}. "
                        f"Available names: "
                        f"{available or '(none \u2014 outputs are unnamed)'}"
                    ),
                }
            groups = filtered

        pasted_layers: list = []
        pasted_frames = 0
        failures: list = []

        for ev in sorted(groups):
            entries = groups[ev]
            first_info = entries[0][1] if entries else JobInfo()

            group_name = name_override or first_info.name
            if not group_name:
                group_name = f"[Generated] {result.prompt_id[:8]}"
            elif not group_name.startswith("["):
                group_name = f"[Generated] {group_name}"
            if seed_suffix is not None:
                group_name = f"{group_name} ({seed_suffix})"

            if first_info.resize_canvas and entries:
                w, h = _png_dimensions(entries[0][0])
                if (int(doc.width()), int(doc.height())) != (w, h):
                    try:
                        resize_canvas(doc, w, h)
                    except Exception as e:
                        failures.append(
                            f"output {first_info.name or ev}: "
                            f"resize_canvas failed: {e}"
                        )
                        continue

            x = first_info.offset_x
            y = first_info.offset_y
            images = [img for img, _ in entries]

            try:
                if first_info.batch_mode == "animation" and len(images) > 1:
                    ok = paste_animation(doc, images)
                    if ok:
                        pasted_frames += len(images)
                        pasted_layers.append(f"[Animation] {group_name}")
                        continue
                    node = paste_image(doc, images[0], x, y, group_name,
                                       blend_mode=blend_mode,
                                       rotation=output_rotation)
                    pasted_layers.append(node.name())

                elif first_info.batch_mode == "layers" and len(images) > 1:
                    nodes = paste_batch_as_layers(
                        doc, images, x, y, group_name,
                        blend_mode=blend_mode,
                        rotation=output_rotation,
                    )
                    pasted_layers.extend(n.name() for n in nodes)

                else:
                    node = paste_image(doc, images[0], x, y, group_name,
                                       blend_mode=blend_mode,
                                       rotation=output_rotation)
                    pasted_layers.append(node.name())

            except ValueError as e:
                failures.append(
                    f"output {first_info.name or ev}: {e}"
                )

        if failures and not pasted_layers:
            return {"success": False,
                    "message": "; ".join(failures)}

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