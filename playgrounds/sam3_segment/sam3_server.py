"""HTTP routes and ComfyUI bridge for the SAM3/SAM2 playground.

Routes:

  POST /comfy/upload_image   raw body, X-Filename header → {hash, width, height}
  POST /comfy/segment        Segment All  → SAM3 detection, no mask
  POST /comfy/refine         Refine mask → SAM2 (true refine) or SAM3 (re-detect)
  POST /comfy/interrupt      forward POST /interrupt to ComfyUI
  GET  /comfy/status         preflight

  GET  /krita/status         kritomatic daemon reachable?
  POST /krita/apply          send a mask to Krita (selection / transparency mask / layer extraction)
  POST /krita/import         fetch active document composite from Krita
"""

from __future__ import annotations

import base64
import json
import time
import traceback
from pathlib import Path
from threading import Lock
from typing import Optional
from urllib.parse import unquote

from pg_comfy import (
    ComfyClient, ComfyError, ComfyTimeout, ComfyCancelled,
    EncodedImage, KritaResources, expand_custom, get_client, reset_client,
)
from pg_comfy.image import make_encoded_image

import krita_client


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
GENERATE_TIMEOUT = 120.0

WORKFLOW_SEGMENT_PATH = Path(__file__).parent / "workflows" / "sam3_segment.json"
WORKFLOW_REFINE_PATH  = Path(__file__).parent / "workflows" / "sam2_refine.json"

_IMAGE_CACHE_MAX = 8


_image_cache: dict = {}
_image_cache_lock = Lock()

_workflow_lock = Lock()
_workflow_segment: Optional[dict] = None
_workflow_refine:  Optional[dict] = None


def _log(msg: str) -> None:
    print(f"[sam3] {msg}", flush=True)


_client: Optional[ComfyClient] = None


def _get_client() -> ComfyClient:
    global _client
    if _client is None:
        _client = get_client(COMFY_HOST, COMFY_PORT)
    return _client


def _reset_client() -> None:
    global _client
    reset_client()
    _client = None


def _load_workflow(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        wf = json.loads(path.read_text())
        _log(f"workflow loaded ({len(wf)} nodes): {path.name}")
        return wf
    except Exception as e:
        _log(f"failed to load {path}: {e}")
        return None


def _get_segment_workflow() -> Optional[dict]:
    global _workflow_segment
    with _workflow_lock:
        if _workflow_segment is None:
            _workflow_segment = _load_workflow(WORKFLOW_SEGMENT_PATH)
        return _workflow_segment


def _get_refine_workflow() -> Optional[dict]:
    global _workflow_refine
    with _workflow_lock:
        if _workflow_refine is None:
            _workflow_refine = _load_workflow(WORKFLOW_REFINE_PATH)
        return _workflow_refine


def _cache_image(hash_hex: str, raw_bytes: bytes,
                 width: int, height: int, filename: str) -> None:
    with _image_cache_lock:
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            oldest = next(iter(_image_cache))
            _image_cache.pop(oldest, None)
        _image_cache[hash_hex] = {
            "raw": raw_bytes, "width": width, "height": height,
            "filename": filename,
        }


def _get_image(hash_hex: str) -> Optional[dict]:
    with _image_cache_lock:
        return _image_cache.get(hash_hex)


_SAM3_NODE = "SAM3_Detect"
_SAM2_NODE = "Sam2Segmentation"
_TOOLING_NODES = ("ETN_LoadImageCache", "ETN_KritaOutput")


def handle_status(_payload) -> dict:
    client = _get_client()
    try:
        info = client.object_info()
    except ComfyError as e:
        _log(f"status check: unreachable: {e}")
        _reset_client()
        return {
            "success": True,
            "reachable": False,
            "message": f"ComfyUI unreachable at {COMFY_HOST}:{COMFY_PORT}: {e}",
        }

    missing_tooling = [n for n in _TOOLING_NODES if n not in info]
    seg_wf = _get_segment_workflow()
    ref_wf = _get_refine_workflow()
    return {
        "success": True,
        "reachable": True,
        "node_count": len(info),
        "has_sam3": _SAM3_NODE in info,
        "has_sam2": _SAM2_NODE in info,
        "has_tooling": not missing_tooling,
        "missing_tooling": missing_tooling,
        "segment_workflow_loaded": seg_wf is not None,
        "refine_workflow_loaded": ref_wf is not None,
        "segment_workflow_path": str(WORKFLOW_SEGMENT_PATH),
        "refine_workflow_path": str(WORKFLOW_REFINE_PATH),
    }


def handle_upload(headers: dict, body: bytes) -> dict:
    if not body:
        return {"success": False, "message": "empty upload"}

    raw_filename = headers.get("X-Filename", "") or headers.get("x-filename", "")
    filename = unquote(raw_filename) if raw_filename else "image"

    try:
        encoded = make_encoded_image(body)
    except Exception as e:
        _log(f"upload decode failed: {e}")
        return {"success": False, "message": f"could not decode image: {e}"}

    h = encoded.content_hash
    _cache_image(h, body, encoded.width, encoded.height, filename)
    _log(f"upload {filename!r} {encoded.width}x{encoded.height} → {h}")
    return {
        "success": True,
        "hash": h,
        "width": encoded.width,
        "height": encoded.height,
        "filename": filename,
    }


def _run_workflow(workflow: dict, resources: KritaResources,
                  params: dict, client: ComfyClient,
                  label: str) -> dict:
    try:
        object_info = client.object_info()
    except ComfyError as e:
        _reset_client()
        return {"success": False, "message": f"ComfyUI unreachable: {e}"}

    for n in _TOOLING_NODES:
        if n not in object_info:
            return {"success": False,
                    "message": f"comfyui-tooling-nodes missing {n}."}

    try:
        expanded = expand_custom(workflow, params, resources,
                                 node_defs=object_info)
    except Exception as e:
        traceback.print_exc()
        return {"success": False,
                "message": f"workflow rewrite failed: {e}"}

    for blob_hash, blob_bytes in expanded.image_data.items():
        try:
            client.upload_image(blob_hash, blob_bytes)
        except ComfyError as e:
            return {"success": False,
                    "message": f"image upload failed: {e}"}

    t0 = time.monotonic()
    try:
        result = client.generate(expanded.graph, timeout=GENERATE_TIMEOUT)
    except ComfyTimeout as e:
        return {"success": False, "message": f"timeout: {e}"}
    except ComfyCancelled:
        return {"success": False, "message": "cancelled"}
    except ComfyError as e:
        return {"success": False, "message": f"ComfyUI error: {e}"}
    except Exception as e:
        traceback.print_exc()
        return {"success": False, "message": f"unexpected error: {e}"}
    elapsed = time.monotonic() - t0

    masks_b64 = [base64.b64encode(png).decode("ascii")
                 for png in result.images if png]
    _log(f"{label}: {len(masks_b64)} mask(s) in {elapsed:.2f}s")

    if not masks_b64:
        return {"success": False, "message": "no masks produced"}

    return {"success": True, "masks": masks_b64,
            "timing": round(elapsed, 3)}


def _clamp_bbox(bbox, image_w: int, image_h: int) -> tuple:
    if not isinstance(bbox, dict):
        return 0, 0, image_w, image_h
    try:
        bx = int(bbox.get("x", 0))
        by = int(bbox.get("y", 0))
        bw = int(bbox.get("w", image_w))
        bh = int(bbox.get("h", image_h))
    except (TypeError, ValueError):
        return 0, 0, image_w, image_h
    bx = max(0, min(image_w - 1, bx))
    by = max(0, min(image_h - 1, by))
    bw = max(1, min(image_w - bx, bw))
    bh = max(1, min(image_h - by, bh))
    return bx, by, bw, bh


def _build_segment_params(payload: dict, image_w: int, image_h: int,
                          individual_masks_override: Optional[bool] = None
                          ) -> dict:
    def coords_json(points):
        return json.dumps([{"x": int(p["x"]), "y": int(p["y"])}
                           for p in points])

    bx, by, bw, bh = _clamp_bbox(payload.get("bbox"), image_w, image_h)
    individual = (individual_masks_override
                  if individual_masks_override is not None
                  else bool(payload.get("individual_masks", True)))
    return {
        "positive_coords":   coords_json(payload.get("positive") or []),
        "negative_coords":   coords_json(payload.get("negative") or []),
        "segment_text":      str(payload.get("text") or ""),
        "threshold":         float(payload.get("threshold", 0.5)),
        "refine_iterations": int(payload.get("refine_iterations", 2)),
        "individual_masks":  individual,
        "bbox_x":            bx,
        "bbox_y":            by,
        "bbox_w":            bw,
        "bbox_h":            bh,
    }


def handle_segment(payload: dict) -> dict:
    hash_hex = payload.get("hash")
    if not hash_hex:
        return {"success": False, "message": "no image hash"}

    image_entry = _get_image(hash_hex)
    if image_entry is None:
        return {"success": False, "message": "image not in cache; re-upload"}

    workflow = _get_segment_workflow()
    if workflow is None:
        return {"success": False,
                "message": f"segment workflow not loaded from "
                           f"{WORKFLOW_SEGMENT_PATH}"}

    client = _get_client()
    try:
        info = client.object_info()
    except ComfyError as e:
        _reset_client()
        return {"success": False, "message": f"ComfyUI unreachable: {e}"}

    if _SAM3_NODE not in info:
        return {"success": False,
                "message": f"ComfyUI has no {_SAM3_NODE} node."}

    image_enc = make_encoded_image(image_entry["raw"])
    resources = KritaResources(
        canvas_image=image_enc,
        canvas_extent=(image_enc.width, image_enc.height),
    )
    params = _build_segment_params(
        payload, image_entry["width"], image_entry["height"],
    )

    result = _run_workflow(workflow, resources, params, client, "segment")
    if result.get("success"):
        result["backend"] = "sam3"
    return result


def _build_refine_sam2_params(payload: dict) -> dict:
    """SAM2 coordinate format: [{"x": int, "y": int}, ...].

    Empty sign → single dummy (0, 0) point, same format.
    Without this, kijai's Sam2Segmentation crashes on np.concatenate
    because np.array("") is 0-D and np.array([]) is 1-D, neither
    concatenable with the (N, 2) array from the populated sign."""
    def coords_json(points):
        if not points:
            return '[{"x": 0, "y": 0}]'
        return json.dumps([{"x": int(p["x"]), "y": int(p["y"])}
                           for p in points])

    return {
        "positive_coords": coords_json(payload.get("positive") or []),
        "negative_coords": coords_json(payload.get("negative") or []),
    }


def _run_refine_sam2(payload: dict, image_entry: dict,
                     workflow: dict, client: ComfyClient) -> dict:
    mask_b64 = payload.get("mask_b64")
    if not mask_b64:
        return {"success": False, "message": "no mask_b64 for SAM2 refine"}

    try:
        mask_bytes = base64.b64decode(mask_b64)
        mask_enc   = make_encoded_image(mask_bytes)
    except Exception as e:
        return {"success": False, "message": f"bad mask_b64: {e}"}

    image_enc = make_encoded_image(image_entry["raw"])
    resources = KritaResources(
        canvas_image=image_enc,
        canvas_extent=(image_enc.width, image_enc.height),
        layer_images={"current_mask": mask_enc},
    )
    params = _build_refine_sam2_params(payload)

    result = _run_workflow(workflow, resources, params, client, "refine-sam2")
    if result.get("success"):
        result["backend"] = "sam2"
    return result


def _run_refine_sam3(payload: dict, image_entry: dict,
                     client: ComfyClient) -> dict:
    """SAM3 refine re-detects.

    Unlike SAM2 refine, SAM3's detect node has no `mask` input — the
    workflow can only pass points, prompt, and bbox. So each click
    produces a fresh detection rather than an incremental refinement
    of the previous mask. This is a semantic difference the UI documents
    via the model radio tooltip; the handler itself just routes.
    """
    workflow = _get_segment_workflow()
    if workflow is None:
        return {"success": False, "message": "no segment workflow for SAM3 refine"}

    image_enc = make_encoded_image(image_entry["raw"])
    resources = KritaResources(
        canvas_image=image_enc,
        canvas_extent=(image_enc.width, image_enc.height),
    )
    params = _build_segment_params(
        payload, image_entry["width"], image_entry["height"],
        individual_masks_override=True,
    )

    result = _run_workflow(workflow, resources, params, client,
                           "refine-sam3")
    if result.get("success"):
        result["backend"] = "sam3"
    return result


def handle_refine(payload: dict) -> dict:
    hash_hex = payload.get("hash")
    if not hash_hex:
        return {"success": False, "message": "no image hash"}

    image_entry = _get_image(hash_hex)
    if image_entry is None:
        return {"success": False,
                "message": "image not in cache; re-upload"}

    model = payload.get("model", "sam2")
    if model not in ("sam2", "sam3"):
        return {"success": False,
                "message": f"unknown refine model: {model!r}"}

    client = _get_client()
    try:
        info = client.object_info()
    except ComfyError as e:
        _reset_client()
        return {"success": False,
                "message": f"ComfyUI unreachable: {e}"}

    if model == "sam3":
        return _run_refine_sam3(payload, image_entry, client)

    refine_wf = _get_refine_workflow()
    if refine_wf is not None and _SAM2_NODE in info:
        try:
            result = _run_refine_sam2(payload, image_entry, refine_wf, client)
            if result.get("success"):
                return result
            _log(f"sam2 refine failed: {result.get('message')}")
        except Exception as e:
            _log(f"sam2 refine raised: {e}")

    _log("sam2 unavailable or failed; falling back to sam3 refine")
    return _run_refine_sam3(payload, image_entry, client)


def handle_interrupt(_payload) -> dict:
    client = _get_client()
    try:
        client.interrupt()
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}


# -- Krita export / import ------------------------------------------------

def handle_krita_status(_payload) -> dict:
    reachable = krita_client.is_reachable()
    return {"success": True, "reachable": reachable}


def _coerce_edge_args(payload: dict) -> dict:
    """Pull the shared edge-cleanup args out of an apply payload.

    Shared between all four apply modes. Missing keys default to 0/False
    so an old client that doesn't send them still gets the pre-cleanup
    behaviour (no dilate, no erode, no smooth, no feather, no invert).
    """
    def _int(key):
        v = payload.get(key, 0)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return {
        "expand_pixels":     _int("expand_pixels"),
        "contract_pixels":   _int("contract_pixels"),
        "smooth_iterations": _int("smooth_iterations"),
        "feather_radius":    _int("feather_radius"),
        "invert":            bool(payload.get("invert", False)),
    }


def _normalize_response(response: dict) -> dict:
    if response.get("status") == "success":
        return {
            "success": True,
            "message": response.get("message") or "applied",
            "data": response.get("data") or {},
        }
    return {
        "success": False,
        "message": response.get("message") or "daemon rejected the command",
    }


def _extract_with_post_apply(payload: dict, edge: dict) -> dict:
    """Orchestrate extract_mask_to_layer + optional post-apply.

    The daemon stays primitive: extract does extraction only, blend and
    opacity live on their own commands, grouping lives on
    move_layer_to_group. This function chains them server-side because
    each follow-up needs the layer NAME returned by the extract step.

    Order:
      1. extract_mask_to_layer — hard failure if this fails; nothing
         else runs and the caller gets the daemon's error verbatim.
      2. set_layer_blend_mode — soft failure: extraction succeeded, so
         report the post-apply error as a warning and continue.
      3. set_layer_opacity    — same soft-fail policy.
      4. move_layer_to_group  — same soft-fail policy. If the group
         doesn't exist, the error is reported but the extracted layer
         stays where it landed.
    """
    daemon_mode = ("new_layer" if payload.get("mode") == "extract_new_layer"
                   else "duplicate")

    extract_cmd = {
        "type": "extract_mask_to_layer",
        "mask_b64": payload.get("mask_b64"),
        "layer_name": payload.get("layer_name") or "",
        "new_layer_name": payload.get("new_layer_name") or "",
        "mode": daemon_mode,
        **edge,
    }
    extract_resp = krita_client.send(extract_cmd, timeout=60.0)
    if extract_resp.get("status") != "success":
        return {
            "success": False,
            "message": extract_resp.get("message") or "extraction failed",
        }

    new_layer = ((extract_resp.get("data") or {}).get("new_layer") or "")
    if not new_layer:
        return {
            "success": False,
            "message": "extraction succeeded but daemon returned no layer name",
        }

    post_warnings: list = []

    blend_mode = payload.get("blend_mode") or ""
    if blend_mode:
        r = krita_client.send({
            "type": "set_layer_blend_mode",
            "layer_name": new_layer,
            "blend_mode": blend_mode,
        }, timeout=30.0)
        if r.get("status") != "success":
            post_warnings.append(
                f"blend mode: {r.get('message') or 'unknown error'}"
            )

    opacity = payload.get("opacity")
    if opacity is not None and opacity != "":
        try:
            op_int = int(opacity)
        except (TypeError, ValueError):
            post_warnings.append(f"opacity not an integer: {opacity!r}")
        else:
            if 0 <= op_int <= 255:
                r = krita_client.send({
                    "type": "set_layer_opacity",
                    "layer_name": new_layer,
                    "opacity": op_int,
                }, timeout=30.0)
                if r.get("status") != "success":
                    post_warnings.append(
                        f"opacity: {r.get('message') or 'unknown error'}"
                    )
            else:
                post_warnings.append(
                    f"opacity {op_int} out of range (0-255); skipped"
                )

    group_name = payload.get("group_name") or ""
    if group_name:
        r = krita_client.send({
            "type": "move_layer_to_group",
            "layer_name": new_layer,
            "group_name": group_name,
            "position": "inside",
        }, timeout=30.0)
        if r.get("status") != "success":
            post_warnings.append(
                f"group '{group_name}': "
                f"{r.get('message') or 'move failed'}"
            )

    result_data = dict(extract_resp.get("data") or {})
    if post_warnings:
        result_data["post_warnings"] = post_warnings

    verb = "Extracted" if daemon_mode == "new_layer" else "Duplicated"
    msg = f"{verb} to '{new_layer}'"
    if post_warnings:
        msg += " (" + "; ".join(post_warnings) + ")"

    return {
        "success": True,
        "message": msg,
        "data": result_data,
    }


def handle_krita_apply(payload: dict) -> dict:
    """Forward a mask to Krita via the kritomatic daemon.

    Modes:
      'selection'           → daemon command set_selection_from_mask_b64
      'transparency'        → daemon command apply_transparency_mask_b64
      'extract_new_layer'   → daemon command extract_mask_to_layer (new_layer)
      'extract_duplicate'   → daemon command extract_mask_to_layer (duplicate)

    All four accept the shared edge-cleanup args (expand_pixels,
    contract_pixels, smooth_iterations, feather_radius, invert).

    Extract modes additionally accept --blend_mode, --opacity, and
    --group_name. These are NOT passed to extract_mask_to_layer (which
    stays single-purpose); this handler composes them by calling
    set_layer_blend_mode, set_layer_opacity, and move_layer_to_group
    against the new layer's name after extraction succeeds.
    """
    mask_b64 = payload.get("mask_b64")
    if not mask_b64:
        return {"success": False, "message": "no mask_b64"}

    mode = payload.get("mode")
    if mode not in ("selection", "transparency",
                    "extract_new_layer", "extract_duplicate"):
        return {"success": False, "message": f"unknown mode: {mode!r}"}

    edge = _coerce_edge_args(payload)

    if mode == "selection":
        sel_mode = payload.get("sel_mode") or "replace"
        if sel_mode not in ("replace", "add", "subtract", "intersect"):
            return {"success": False,
                    "message": f"unknown sel_mode: {sel_mode!r}"}
        command = {
            "type": "set_selection_from_mask_b64",
            "mask_b64": mask_b64,
            "mode": sel_mode,
            **edge,
        }
        return _normalize_response(krita_client.send(command, timeout=60.0))

    if mode == "transparency":
        command = {
            "type": "apply_transparency_mask_b64",
            "mask_b64": mask_b64,
            "layer_name": payload.get("layer_name") or "",
            **edge,
        }
        return _normalize_response(krita_client.send(command, timeout=60.0))

    # Extract modes: orchestrate extract + post-apply composition
    return _extract_with_post_apply(payload, edge)


def handle_krita_import(payload: dict) -> dict:
    """Fetch the active document's composite from Krita and cache it.

    Returns the same {hash, width, height, data_url} shape as the
    upload endpoint, so the browser can call setImage() exactly as it
    would after a file upload.
    """
    name = None
    if isinstance(payload, dict):
        name = payload.get("name")
    command = {"type": "get_document_composite_b64"}
    if name:
        command["name"] = name

    response = krita_client.send(command, timeout=60.0)
    if response.get("status") != "success":
        return {"success": False,
                "message": response.get("message") or "daemon rejected"}

    data = response.get("data") or {}
    data_url = data.get("data_url")
    if not data_url:
        return {"success": False, "message": "daemon returned no data_url"}
    if not data_url.startswith("data:image/png;base64,"):
        return {"success": False,
                "message": f"unexpected data_url prefix: {data_url[:30]!r}"}

    try:
        png_bytes = base64.b64decode(data_url.split(",", 1)[1])
    except Exception as e:
        return {"success": False,
                "message": f"could not decode base64: {e}"}

    try:
        encoded = make_encoded_image(png_bytes)
    except Exception as e:
        return {"success": False,
                "message": f"could not decode image: {e}"}

    h = encoded.content_hash
    filename = data.get("name") or "krita_composite"
    _cache_image(h, png_bytes, encoded.width, encoded.height, filename)
    _log(f"import {filename!r} {encoded.width}x{encoded.height} → {h}")

    return {
        "success": True,
        "hash": h,
        "width": encoded.width,
        "height": encoded.height,
        "data_url": data_url,
        "filename": filename,
    }


ROUTES = {
    "/comfy/status":    handle_status,
    "/comfy/segment":   handle_segment,
    "/comfy/refine":    handle_refine,
    "/comfy/interrupt": handle_interrupt,
    "/krita/status":    handle_krita_status,
    "/krita/apply":     handle_krita_apply,
    "/krita/import":    handle_krita_import,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
