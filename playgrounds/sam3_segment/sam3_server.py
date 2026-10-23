"""HTTP routes and ComfyUI bridge for the SAM3/SAM2 playground.

Routes:

  POST /comfy/upload_image   raw body, X-Filename header → {hash, width, height}
  POST /comfy/segment        Segment All  → SAM3 detection, no mask
  POST /comfy/refine         Refine mask → SAM2 (true refine) or SAM3 (re-detect)
  POST /comfy/interrupt      forward POST /interrupt to ComfyUI
  GET  /comfy/status         preflight
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


# ---------- Segment All (SAM3, no mask) -----------------------------------

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


# ---------- Refine --------------------------------------------------------

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
    workflow = _get_segment_workflow()
    if workflow is None:
        return {"success": False, "message": "no segment workflow for SAM3 refine"}

    image_enc = make_encoded_image(image_entry["raw"])
    resources = KritaResources(
        canvas_image=image_enc,
        canvas_extent=(image_enc.width, image_enc.height),
    )
    # SAM3 refine path: force individual_masks=true so all detections
    # in the mask's bbox can contribute. Client sends mask.textPrompt
    # as `text`, and `bbox` = mask bbox expanded by positive points.
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

    # User explicitly wants SAM3.
    if model == "sam3":
        return _run_refine_sam3(payload, image_entry, client)

    # User wants SAM2: try SAM2, fall back to SAM3 on any failure.
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


ROUTES = {
    "/comfy/status":    handle_status,
    "/comfy/segment":   handle_segment,
    "/comfy/refine":    handle_refine,
    "/comfy/interrupt": handle_interrupt,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
