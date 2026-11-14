"""comfyui_generation.py — submit workflows via the v2 proxy, poll,
download outputs, paste into Krita.

ROTATION
========
The paste stage reads `prepared.rotation` (set by `prepare_workflow`).

  · rotation == 0: unchanged — paste at
    `(crop_bounds[0]+x, crop_bounds[1]+y)`.

  · rotation != 0: the workflow ran on a rotated-frame crop at
    `(cropX, cropY, cw, ch)`.  The output is `cw × ch`.  We un-rotate
    it by `-rotation`, yielding a diamond whose bbox is
    `cw_bbox × ch_bbox`.  The diamond's centre lands on the un-rotated
    position of the crop centre (computed by **un-rotating**
    `(cropX + cw/2, cropY + ch/2)` around the rotated frame's centre,
    then translating by `(W/2, H/2)`).

    The diamond is clipped to the document bounds and the resulting
    visible fragment is pasted at its actual doc position
    `(doc_x0, doc_y0)` — NOT at `(0, 0)` with full-document bounds.
    This is what makes Krita's layer panel show the layer at its
    content position, matching the visible content.

    When the diamond is clipped on the left or top (diamond_x < 0 or
    diamond_y < 0), the visible fragment starts at diamond pixel
    `(-diamond_x, -diamond_y)`, so the diamond must be drawn into the
    smaller vis canvas at offset `(diamond_x - doc_x0, diamond_y - doc_y0)`.
    Since `doc_x0 = max(0, diamond_x)` and `doc_y0 = max(0, diamond_y)`,
    those offsets are non-positive exactly when there is a left/top
    clip, and 0 otherwise.  Using the opposite sign (`doc_x0 - diamond_x`)
    shifts the content *down/right* by `2*|diamond_y|` (or `2*|diamond_x|`)
    in the doc — a bug that only fires for top/left clips, matching
    the "sometimes the paste is shifted" symptom.

FILES
=====
`_paste_result` rotation branch: un-rotation math preserved; the
clipping-to-doc + paste-at-visible-bounds logic is preserved; the only
change vs. the previous version is the sign of the `drawImage` offset,
which was inverted for top/left clip cases."""

from __future__ import annotations

import base64
import json
import math
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
                  "comfy_apply_config to have been called.",
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

        if parent._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded. Run comfy_load first."}
        if parent._active_config is None:
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

        try:
            proxy = ProxyV2.from_pid_file()
        except (ProxyUnreachable, ImportError) as e:
            return {"success": False,
                    "message": f"proxy unavailable: {e}"}

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
        workflow_name = parent._active_workflow_name or "(unnamed)"

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

        try:
            warnings: list = []

            has_seed_slot = "seed" in parent._active_workflow_param_slots
            if count > 1 and not has_seed_slot:
                warnings.append(
                    f"count={requested_count} clamped to 1: the workflow "
                    f"has no Primitive titled 'seed'."
                )
                count = 1
                record.total_runs = 1

            paste_config = dict(parent._active_config["paste"])
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
                        parent._active_workflow_graph,
                        parent._active_workflow_resource_slots,
                        parent._active_workflow_param_slots,
                        parent._active_config,
                        proxy,
                        seed=seed_i,
                    )
                    any_prepare_succeeded = True
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
                        "error": f"submit: {e}",
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

            record.current_phase = "running"

            for entry in submitted:
                job_id = entry["job_id"]
                seed_i = entry["seed"]
                prepared = entry["prepared"]
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

                downloaded = self._download_outputs(proxy, terminal)
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
                    parent._last_result = {
                        "outputs": downloaded,
                        "job_id": job_id,
                        "crop_bounds": prepared.crop_bounds,
                        "paste_config": paste_config,
                        "output_rotation": prepared.rotation,
                    }
                    parent._last_document = doc
                    continue

                try:
                    paste_result = self._paste_result(
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

                if not paste_result.get("success"):
                    record.run_records.append({
                        "seed": seed_i, "prompt_id": job_id,
                        "status": "paste_failed",
                        "error": paste_result.get(
                            "message", "paste failed"),
                    })
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
                    "crop_bounds": prepared.crop_bounds,
                    "paste_config": paste_config,
                    "output_rotation": prepared.rotation,
                }
                parent._last_document = doc

            record.current_phase = "finalizing"

            self._write_history_record(
                batch_id=batch_id,
                base_seed=base_seed,
                base_params=dict(parent._active_config["params"]),
                run_records=record.run_records,
                warnings=warnings,
                duration_s=time.time() - batch_started,
            )

            if any_prepare_succeeded:
                self._record_used_layers(parent._active_config)

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
    #  Poll
    # ------------------------------------------------------------------

    def _poll_job(self, proxy: ProxyV2, job_id: str,
                  record: _BatchRecord, *,
                  timeout: float) -> Optional[dict]:
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

    def _download_outputs(self, proxy: ProxyV2, job: dict) -> list:
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
                    continue
                continue
            except ProxyUnreachable:
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
            "--batch_id": {"type": "str", "required": False},
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
            external_ids = getattr(r, "external_job_ids", None) or []
            if external_ids:
                try:
                    proxy = ProxyV2.from_pid_file()
                except (ProxyUnreachable, ImportError):
                    continue
                for jid in external_ids:
                    try:
                        proxy.cancel_job(jid)
                    except (ProxyHTTPError, ProxyUnreachable):
                        pass
            else:
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

    # ------------------------------------------------------------------
    #  Paste
    # ------------------------------------------------------------------
    #
    # Rotation branch:
    #   · rotation == 0 → paste at (crop_bounds[0]+x, crop_bounds[1]+y)
    #   · rotation != 0 → un-rotate each workflow output by -rotation
    #                     into a diamond; compute the diamond's centre
    #                     by un-rotating the crop centre; clip the
    #                     diamond to the doc bounds; paste the visible
    #                     fragment at its actual doc position
    #                     (doc_x0, doc_y0) — not at (0,0).

    def _paste_result(self, doc, outputs: list, *,
                      crop_bounds: tuple,
                      paste_config: dict,
                      job_id: str,
                      seed_suffix: Optional[int] = None,
                      output_rotation: float = 0.0,
                      blend_mode: str = "normal") -> dict:
        if not outputs:
            return {"success": True,
                    "message": "No outputs to paste",
                    "data": {"layer_names": [],
                             "output_count": 0, "frame_count": 0}}

        print(f"[_paste_result] output_rotation={output_rotation} "
              f"crop_bounds={crop_bounds} "
              f"paste_config.x={paste_config.get('x', 0)} "
              f"y={paste_config.get('y', 0)} "
              f"outputs={len(outputs)}", flush=True)

        groups: dict[str, list] = {}
        for o in outputs:
            groups.setdefault(o["node_id"], []).append(o)

        resize_canvas_flag = bool(paste_config.get("resize_canvas", False))
        if resize_canvas_flag:
            w, h = _png_dimensions(outputs[0]["png_bytes"])
            if (int(doc.width()), int(doc.height())) != (w, h):
                try:
                    resize_canvas(doc, w, h)
                except Exception as e:
                    return {"success": False,
                            "message": f"resize_canvas failed: {e}"}

        configured_x = int(paste_config.get("x", 0))
        configured_y = int(paste_config.get("y", 0))

        if output_rotation != 0:
            from PyQt6.QtGui import QImage, QPainter
            from ..utils.comfyui_krita import (
                _decode_png, _rotate_about_center, _save_png,
            )

            W_doc = int(doc.width())
            H_doc = int(doc.height())
            cropX = int(crop_bounds[0])
            cropY = int(crop_bounds[1])
            cw = int(crop_bounds[2])
            ch = int(crop_bounds[3])
            theta = float(output_rotation)

            rad = math.radians(theta)
            cos_t = math.cos(rad)
            sin_t = math.sin(rad)
            cos_abs = abs(cos_t)
            sin_abs = abs(sin_t)
            Wp = max(1, int(round(W_doc * cos_abs + H_doc * sin_abs)))
            Hp = max(1, int(round(W_doc * sin_abs + H_doc * cos_abs)))

            crop_cx_rot = cropX + cw / 2.0
            crop_cy_rot = cropY + ch / 2.0
            dx = crop_cx_rot - Wp / 2.0
            dy = crop_cy_rot - Hp / 2.0
            target_cx = W_doc / 2.0 + dx * cos_t + dy * sin_t
            target_cy = H_doc / 2.0 - dx * sin_t + dy * cos_t

            print(f"[_paste_result] rotated prepare: theta={theta} "
                  f"doc={W_doc}x{H_doc} frame={Wp}x{Hp} "
                  f"crop=({cropX},{cropY},{cw},{ch}) "
                  f"crop_centre_rot=({crop_cx_rot:.1f},{crop_cy_rot:.1f}) "
                  f"target_centre=({target_cx:.1f},{target_cy:.1f})",
                  flush=True)

            new_outputs = []
            for o in outputs:
                img = _decode_png(o["png_bytes"])
                diamond = _rotate_about_center(img, -theta)
                dw = diamond.width()
                dh = diamond.height()

                # Diamond top-left in doc coords.  configured_x /
                # configured_y are added as an additional offset,
                # matching the non-rotated path's paste_config.x/y
                # semantics.
                diamond_x = int(round(target_cx - dw / 2.0)) + configured_x
                diamond_y = int(round(target_cy - dh / 2.0)) + configured_y

                # Clip the diamond to the doc bounds.  The pasted
                # layer is exactly the visible fragment of the
                # diamond, so Krita's layer panel shows the layer at
                # its actual content position rather than (0,0) with
                # full-document bounds.
                doc_x0 = max(0, diamond_x)
                doc_y0 = max(0, diamond_y)
                doc_x1 = min(W_doc, diamond_x + dw)
                doc_y1 = min(H_doc, diamond_y + dh)

                if doc_x1 <= doc_x0 or doc_y1 <= doc_y0:
                    print(f"[_paste_result] output "
                          f"{o.get('name', '?')} entirely outside doc; "
                          f"skipping", flush=True)
                    continue

                vis_w = doc_x1 - doc_x0
                vis_h = doc_y1 - doc_y0

                fully_inside = (
                    doc_x0 == diamond_x
                    and doc_y0 == diamond_y
                    and doc_x1 == diamond_x + dw
                    and doc_y1 == diamond_y + dh
                )
                if fully_inside:
                    vis = diamond
                else:
                    vis = QImage(vis_w, vis_h,
                                 QImage.Format.Format_ARGB32)
                    vis.fill(0)
                    p = QPainter(vis)
                    p.setRenderHint(
                        QPainter.RenderHint.SmoothPixmapTransform,
                    )
                    # Offset the diamond so its content lands at the
                    # correct doc position.  When the diamond is
                    # clipped on the left or top (diamond_x < 0 or
                    # diamond_y < 0), the visible fragment starts at
                    # diamond pixel (-diamond_x, -diamond_y), so the
                    # diamond must be drawn at
                    # (diamond_x - doc_x0, diamond_y - doc_y0).  That
                    # is non-positive exactly when there is a
                    # left/top clip, and 0 otherwise.  The opposite
                    # sign (doc_x0 - diamond_x) shifts the content
                    # DOWN/RIGHT by 2·|clip| in the doc, which is the
                    # "sometimes the paste is shifted" bug.
                    p.drawImage(diamond_x - doc_x0,
                                diamond_y - doc_y0, diamond)
                    p.end()

                print(f"[_paste_result]  output "
                      f"{o.get('name', '?')}: diamond@{diamond_x},"
                      f"{diamond_y} {dw}x{dh} → layer@{doc_x0},{doc_y0} "
                      f"{vis_w}x{vis_h}", flush=True)

                o2 = dict(o)
                o2["png_bytes"] = _save_png(vis)
                o2["_paste_x"] = doc_x0
                o2["_paste_y"] = doc_y0
                new_outputs.append(o2)
            outputs = new_outputs

            groups = {}
            for o in outputs:
                groups.setdefault(o["node_id"], []).append(o)

            if outputs:
                paste_x = outputs[0]["_paste_x"]
                paste_y = outputs[0]["_paste_y"]
            else:
                paste_x = configured_x
                paste_y = configured_y
            effective_rotation = 0.0
        else:
            paste_x = int(crop_bounds[0]) + configured_x
            paste_y = int(crop_bounds[1]) + configured_y
            effective_rotation = 0.0

        batch_mode = paste_config.get("batch_mode", "default")
        configured_name = paste_config.get("name") or ""

        pasted_layers: list = []
        pasted_frames = 0
        failures: list = []

        for node_id, group in groups.items():
            images = [o["png_bytes"] for o in group]

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
                    node = paste_image(
                        doc, images[0], paste_x, paste_y, base_name,
                        blend_mode=blend_mode,
                        rotation=effective_rotation,
                    )
                    pasted_layers.append(node.name())
                elif batch_mode == "layers" and len(images) > 1:
                    nodes = paste_batch_as_layers(
                        doc, images, paste_x, paste_y, base_name,
                        blend_mode=blend_mode,
                        rotation=effective_rotation,
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
                            rotation=effective_rotation,
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

    # ------------------------------------------------------------------
    #  Recent layer aliases
    # ------------------------------------------------------------------

    def _record_used_layers(self, config: Optional[dict]) -> None:
        if not config:
            return
        resources = config.get("resources") or {}
        if not isinstance(resources, dict):
            return
        workflow_cmds = self.parent._workflow_cmds
        for _name, spec in resources.items():
            if not isinstance(spec, dict):
                continue
            if spec.get("source") != "layer":
                continue
            layer_name = spec.get("layer_name")
            if not layer_name:
                continue
            workflow_cmds._record_recent_alias(str(layer_name))