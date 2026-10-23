"""HTTP routes and ComfyUI bridge for the SAM3 playground.

The playground's browser talks only to this server; this server talks
to ComfyUI. That decision comes from three constraints:

  1. ComfyUI does not send CORS headers on /prompt by default, so the
     browser cannot POST directly to it from a different origin.
  2. WS preview frames are routed by client_id, and the client that
     submitted the prompt must be the same client with the open WS.
     The browser's WS and a server-side POST are different clients.
  3. Image bytes never leave localhost; the extra hop is ~5ms.

Routes:

  POST /comfy/upload_image   raw body, X-Filename header → {hash, width, height}
  POST /comfy/segment        JSON body → {success, masks: [b64_png], timing}
  POST /comfy/interrupt      forward POST /interrupt to ComfyUI
  GET  /comfy/status         preflight (ComfyUI reachable, nodes present)
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


# -- constants --------------------------------------------------------------

COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
GENERATE_TIMEOUT = 120.0

WORKFLOW_PATH = Path(__file__).parent / "workflows" / "sam3_segment.json"

_IMAGE_CACHE_MAX = 8


# -- module state -----------------------------------------------------------

_image_cache: dict = {}
_image_cache_lock = Lock()

_workflow_lock = Lock()
_workflow: Optional[dict] = None


def _log(msg: str) -> None:
    print(f"[sam3] {msg}", flush=True)


# -- ComfyUI client ---------------------------------------------------------

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


# -- workflow loading -------------------------------------------------------

def _load_workflow() -> Optional[dict]:
    global _workflow
    with _workflow_lock:
        if _workflow is not None:
            return _workflow
        if not WORKFLOW_PATH.is_file():
            _log(f"workflow file not found: {WORKFLOW_PATH}")
            return None
        try:
            _workflow = json.loads(WORKFLOW_PATH.read_text())
            _log(f"workflow loaded ({len(_workflow)} nodes)")
            return _workflow
        except Exception as e:
            _log(f"failed to load workflow: {e}")
            return None


# -- image cache ------------------------------------------------------------

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


# -- preflight --------------------------------------------------------------

_SAM3_NODE = "SAM3_Detect"
_TOOLING_NODES = ("ETN_LoadImageCache", "ETN_KritaOutput")


def handle_status(_payload) -> dict:
    """Reachability + node presence. Called by the browser on load and
    when the user presses "Check connection" in settings."""
    client = _get_client()
    try:
        info = client.object_info()
    except ComfyError as e:
        # Print to the playground's terminal so the failure survives the
        # browser's flashStatus auto-clear (1.8s) and is greppable.
        _log(f"status check: unreachable: {e}")
        _reset_client()
        return {
            "success": True,
            "reachable": False,
            "message": f"ComfyUI unreachable at {COMFY_HOST}:{COMFY_PORT}: {e}",
        }

    has_sam3 = _SAM3_NODE in info
    missing_tooling = [n for n in _TOOLING_NODES if n not in info]
    workflow = _load_workflow()
    return {
        "success": True,
        "reachable": True,
        "node_count": len(info),
        "has_sam3": has_sam3,
        "has_tooling": not missing_tooling,
        "missing_tooling": missing_tooling,
        "workflow_loaded": workflow is not None,
        "workflow_path": str(WORKFLOW_PATH),
    }


# -- upload -----------------------------------------------------------------

def handle_upload(headers: dict, body: bytes) -> dict:
    """Cache the uploaded image, return its hash and dimensions.

    The hash is used as the `ETN_LoadImageCache.id` value; the tooling
    module deduplicates uploads by content hash, so a second segment
    call with the same image only uploads once."""
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


# -- segment ----------------------------------------------------------------

def _build_krita_resources(image_entry: dict) -> KritaResources:
    enc = make_encoded_image(image_entry["raw"])
    return KritaResources(
        canvas_image=enc,
        canvas_extent=(enc.width, enc.height),
    )


def _build_params(payload: dict) -> dict:
    def coords_json(points):
        # SAM3 expects [{"x": int, "y": int}, ...] per docs.
        # The browser already sends this shape (see pg_core.js
        # _recount), so the only work is int coercion + serialisation.
        return json.dumps([{"x": int(p["x"]), "y": int(p["y"])}
                           for p in points])

    return {
        "positive_coords":   coords_json(payload.get("positive") or []),
        "negative_coords":   coords_json(payload.get("negative") or []),
        "segment_text":      str(payload.get("text") or ""),
        "threshold":         float(payload.get("threshold", 0.5)),
        "refine_iterations": int(payload.get("refine_iterations", 2)),
        "individual_masks":  bool(payload.get("individual_masks", True)),
    }


def handle_segment(payload: dict) -> dict:
    hash_hex = payload.get("hash")
    if not hash_hex:
        return {"success": False, "message": "no image hash in request"}

    image_entry = _get_image(hash_hex)
    if image_entry is None:
        return {"success": False,
                "message": "image not in cache; re-upload the file"}

    workflow = _load_workflow()
    if workflow is None:
        return {"success": False,
                "message": f"workflow could not be loaded from {WORKFLOW_PATH}"}

    client = _get_client()

    try:
        object_info = client.object_info()
    except ComfyError as e:
        _log(f"segment: unreachable: {e}")
        _reset_client()
        return {"success": False,
                "message": f"ComfyUI unreachable: {e}"}

    if _SAM3_NODE not in object_info:
        return {"success": False,
                "message": f"ComfyUI has no {_SAM3_NODE} node. "
                           f"Update to a ComfyUI version that includes it."}
    for n in _TOOLING_NODES:
        if n not in object_info:
            return {"success": False,
                    "message": f"comfyui-tooling-nodes missing {n}. "
                               f"Install the tooling-nodes package."}

    resources = _build_krita_resources(image_entry)
    params = _build_params(payload)

    try:
        expanded = expand_custom(
            workflow, params, resources, node_defs=object_info,
        )
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
    _log(f"segment: {len(masks_b64)} mask(s) in {elapsed:.2f}s "
         f"(text={params['segment_text'][:30]!r}, "
         f"pos={len(payload.get('positive') or [])}, "
         f"neg={len(payload.get('negative') or [])})")

    if not masks_b64:
        return {"success": False,
                "message": "no masks produced (check the prompt / threshold)"}

    return {
        "success": True,
        "masks": masks_b64,
        "timing": round(elapsed, 3),
    }


# -- interrupt --------------------------------------------------------------

def handle_interrupt(_payload) -> dict:
    client = _get_client()
    try:
        client.interrupt()
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}


# -- route table ------------------------------------------------------------

ROUTES = {
    "/comfy/status":       handle_status,
    "/comfy/segment":      handle_segment,
    "/comfy/interrupt":    handle_interrupt,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
