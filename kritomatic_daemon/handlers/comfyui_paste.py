"""comfyui_paste.py — output-handling helpers shared by the normal and
intercept generation pipelines.

Extracted from `comfyui_generation.py` to keep the normal-pipeline
file focused on the load-bearing pieces (batch record, poll loop,
concurrency snapshot) and to give the paste-rotation logic its own
home.  Nothing here has state beyond what the caller passes in;
every function is a pure module-level helper.

Contents:

  · `download_outputs(proxy, job)` — pull every retrievable asset
    referenced by a terminal `Job` dict.  Used by both pipelines.

  · `paste_result(doc, outputs, ...)` — the paste stage.  Handles
    rotation (un-rotate the crop, place at target, clip to doc),
    batch modes (default / layers / animation), per-node grouping,
    and the various failure paths.  Called with the parent's
    `_paste_lock` held.  Also returns an `applied` list — one entry
    per pasted layer, carrying the exact PNG bytes the Krita layer
    received — which `comfyui_generation` publishes as
    `paste_applied` events for optimistic display in the inpaint
    playground.  See the "APPLIED LIST" section below.

  · `write_history_record(...)` — append a history JSON for a
    finished batch.  Best-effort; swallows I/O errors.

  · `record_used_layers(workflow_cmds, config)` — record every
    `source == "layer"` resource name as a recent alias.

None of these touch handler state directly.  Callers pass in the
specific values the function needs (workflow name, workflow_cmds
reference, etc.), so the module has no dependency on
`ComfyUIHandler` or `GenerationCommands`.

Rotation approach (unchanged from the pre-split implementation)
===============================================================
When `output_rotation != 0`, the workflow ran on a crop from the
*rotated* frame at `(cropX, cropY, cw, ch)`.  The output is a
`cw × ch` image whose content, when un-rotated by `-output_rotation`,
becomes a diamond.  The diamond's bbox is `dw × dh`; we place the
diamond so its center sits at the un-rotated position of the crop
center `(target_cx, target_cy)`, then clip to the document bounds
and paste the visible fragment at `(doc_x0, doc_y0)` with
`effective_rotation = 0`.

That keeps the pasted *layer* bounds aligned with the visible
content (`doc_x0, doc_y0, vis_w, vis_h`), not a full-document-sized
rectangle at `(0, 0)`.  The `_paste_result` log line names the
diamond position and the pasted-region size so the numbers can be
sanity-checked against the log of the crop frame from
`prepare_workflow`.

APPLIED LIST
============
`paste_result`'s return dict now carries an `applied` list.  Each
entry is::

    {
      "hash":    str,   # 16-char hex SHA-256 of the applied PNG
      "b64":     str,   # base64 PNG of the layer that was pasted
      "paste_x": int,   # document-space x of the pasted region
      "paste_y": int,
      "width":   int,
      "height":  int,
    }

The list is populated for `batch_mode="default"` (one entry per
image, in paste order).  For `batch_mode="layers"` and
`"animation"` it is empty — `paste_batch_as_layers` and
`paste_animation` do not return per-image applied bytes, and
computing them separately would duplicate the paste work.  Callers
should treat an empty `applied` as "optimistic display unavailable
for this batch" and rely on the end-of-batch `canvas_update`
instead.

The daemon's `_publish_paste_applied` iterates `applied` to publish
one `paste_applied` event per pasted layer.  An `applied` entry's
`b64` is exactly the bytes the Krita layer received — for
`rotation == 0` it is the input PNG unchanged, and for
`rotation != 0` it is the rotated-and-cropped version.  This is
what lets the browser's optimistic composite match the daemon's
final projection to the pixel, not just approximately.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Optional

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
from .comfyui_document import _png_dimensions
from .comfyui_types import _HISTORY_DIR


# ---------------------------------------------------------------------------
#  Download
# ---------------------------------------------------------------------------

def download_outputs(proxy: ProxyV2, job: dict) -> list:
    """Pull every retrievable asset referenced by a terminal `Job` dict.

    `job["outputs"]` is the proxy's list of outputs.  Each entry with a
    non-`"temp"` type and a non-empty `id` gets its bytes fetched via
    `proxy.download_asset`.  Failed downloads are silently skipped —
    a partial download set is strictly better than an all-or-nothing
    failure here, because the paste stage tolerates missing outputs.
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


# ---------------------------------------------------------------------------
#  Applied-list helper
# ---------------------------------------------------------------------------

def _applied_entry(png_bytes: bytes,
                   paste_x: int, paste_y: int) -> dict:
    """Build one entry for `paste_result`'s `applied` list.

    `png_bytes` is the exact PNG that was handed to `paste_image` as
    the layer content.  `paste_x` / `paste_y` are the document-space
    coordinates the layer was placed at.  See the module docstring,
    APPLIED LIST.
    """
    try:
        w, h = _png_dimensions(png_bytes)
    except Exception:
        w, h = 0, 0
    return {
        "hash":    hashlib.sha256(png_bytes).hexdigest()[:16],
        "b64":     base64.b64encode(png_bytes).decode("ascii"),
        "paste_x": int(paste_x),
        "paste_y": int(paste_y),
        "width":   int(w),
        "height":  int(h),
    }


# ---------------------------------------------------------------------------
#  Paste
# ---------------------------------------------------------------------------

def paste_result(
    doc,
    outputs: list,
    *,
    crop_bounds: tuple,
    paste_config: dict,
    job_id: str,
    seed_suffix: Optional[int] = None,
    output_rotation: float = 0.0,
    blend_mode: str = "normal",
) -> dict:
    """Paste downloaded outputs into `doc` per `paste_config`.

    Handles:

      · rotation (`output_rotation != 0`): un-rotate the crop by
        `-output_rotation` to get a diamond; compute the diamond's
        center as the un-rotated position of the crop center; clip
        the diamond to the document bounds; paste the visible
        fragment at its actual document position.  The resulting
        layer bounds match the visible content, not the whole
        document.
      · no rotation: pastes at `(crop_bounds[0]+x, crop_bounds[1]+y)`.
      · batch_mode `default`: one layer per output.
      · batch_mode `layers`: all images from a node in stacked layers.
      · batch_mode `animation`: import as a Krita animation when there
        are multiple images; falls back to a single layer for one.
      · `resize_canvas`: grows the canvas to the first output's
        dimensions before pasting.

    Returns a dict with `success`, `message`, and a `data` block
    carrying `layer_names`, `frame_count`, `output_count`, and
    `applied` (see the module docstring, APPLIED LIST).

    Caller is expected to hold `parent._paste_lock` for the whole
    call — two concurrent pastes on the same document corrupt its
    layer tree (interleaved `rootNode().addChildNode` calls, lost
    node, inconsistent `refreshProjection` state).
    """
    if not outputs:
        return {"success": True,
                "message": "No outputs to paste",
                "data": {"layer_names": [],
                         "output_count": 0, "frame_count": 0,
                         "applied": []}}

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
        # Un-rotate each output crop by -theta to get a diamond, place
        # its center at the un-rotated position of the crop center,
        # clip to doc bounds, and paste the visible fragment at its
        # actual doc position.  This is the pre-split behaviour — the
        # padding-to-frame approach that replaced it put the layer
        # bounds at (0, 0, doc_w, doc_h), which broke downstream
        # operations that depend on the layer's actual position.
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

        # Rotated frame size — same formula as the OLD code; used to
        # convert the crop center (in rotated frame coords) into the
        # un-rotated doc frame.
        rad = math.radians(theta)
        cos_t = math.cos(rad)
        sin_t = math.sin(rad)
        cos_abs = abs(cos_t)
        sin_abs = abs(sin_t)
        Wp = max(1, int(round(W_doc * cos_abs + H_doc * sin_abs)))
        Hp = max(1, int(round(W_doc * sin_abs + H_doc * cos_abs)))

        # Un-rotated position of the crop center in doc space.
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

            diamond_x = int(round(target_cx - dw / 2.0)) + configured_x
            diamond_y = int(round(target_cy - dh / 2.0)) + configured_y

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
    applied: list = []

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
                    # No per-frame applied bytes from paste_animation;
                    # the browser will get the end-of-batch
                    # canvas_update instead.  See module docstring,
                    # APPLIED LIST.
                    continue
                node, applied_png = paste_image(
                    doc, images[0], paste_x, paste_y, base_name,
                    blend_mode=blend_mode,
                    rotation=effective_rotation,
                    return_png=True,
                )
                pasted_layers.append(node.name())
                applied.append(_applied_entry(applied_png, paste_x, paste_y))
            elif batch_mode == "layers" and len(images) > 1:
                nodes = paste_batch_as_layers(
                    doc, images, paste_x, paste_y, base_name,
                    blend_mode=blend_mode,
                    rotation=effective_rotation,
                )
                pasted_layers.extend(n.name() for n in nodes)
                # paste_batch_as_layers does not surface per-node
                # applied bytes; skip optimistic-display entries for
                # this mode.  See module docstring, APPLIED LIST.
            else:
                for idx, png in enumerate(images):
                    sub_name = (
                        base_name if len(images) == 1
                        else f"{base_name} #{idx + 1}"
                    )
                    node, applied_png = paste_image(
                        doc, png, paste_x, paste_y, sub_name,
                        blend_mode=blend_mode,
                        rotation=effective_rotation,
                        return_png=True,
                    )
                    pasted_layers.append(node.name())
                    applied.append(
                        _applied_entry(applied_png, paste_x, paste_y)
                    )
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
            "applied": applied,
        },
    }


def _publish_outpaint_result(out: dict, doc) -> None:
    """Publish one raw outpaint result as an `outpaint_result` event.

    Moved here from `comfyui_generation.py` so the intercept-resume
    path (`comfyui_intercept.py`) can call it without a circular
    import.  Called from both the normal path (when
    `DESTINATION=playground`) and from `comfy_resume_intercepted`
    when the intercepted workflow had `DESTINATION=playground` at
    intake time.

    The event type is `outpaint_result`, distinct from `paste_applied`,
    so the inpaint playground's subscriber never receives outpaint
    outputs (they are far larger than the canvas and would corrupt
    its optimistic composite).  Payload shape matches `paste_applied`
    with `paste_x`/`paste_y` set to 0 and an `"outpaint": True`
    marker.

    Best-effort.  A failure logs one line and continues.
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
#  History
# ---------------------------------------------------------------------------

def write_history_record(
    *,
    batch_id: str,
    workflow_name: Optional[str],
    base_seed: int,
    base_params: dict,
    run_records: list,
    warnings: list,
    duration_s: float,
) -> None:
    """Append a history JSON record for a finished batch.

    Best-effort; failures are silently swallowed so a full disk or a
    permission problem never fails the batch itself.  The path is
    `_HISTORY_DIR / <batch_id>.json`.
    """
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
            "workflow": workflow_name,
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


# ---------------------------------------------------------------------------
#  Recent layer aliases
# ---------------------------------------------------------------------------

def record_used_layers(workflow_cmds, config: Optional[dict]) -> None:
    """Record every `source == "layer"` resource name as a recent alias.

    `workflow_cmds` is the `WorkflowCommands` sub-handler; the caller
    passes `parent._workflow_cmds`.  Every `layer_name` in
    `config["resources"]` with `source == "layer"` is recorded via
    `workflow_cmds._record_recent_alias`, which moves the name to the
    front of the MRU list and persists it.
    """
    if not config:
        return
    resources = config.get("resources") or {}
    if not isinstance(resources, dict):
        return
    for _name, spec in resources.items():
        if not isinstance(spec, dict):
            continue
        if spec.get("source") != "layer":
            continue
        layer_name = spec.get("layer_name")
        if not layer_name:
            continue
        workflow_cmds._record_recent_alias(str(layer_name))
