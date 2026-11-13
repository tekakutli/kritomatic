"""HTTP routes and ComfyUI/daemon bridge for the inpaint playground.

Routes:

  POST /comfy/upload_image     raw body, X-Filename header → {hash, width, height}
  POST /comfy/inpaint          run the active workflow with inline pixel blobs
  POST /comfy/interrupt        forward POST /interrupt to ComfyUI
  GET  /comfy/status           preflight (ComfyUI reachability)
  POST /comfy/active_state     daemon comfy_status (workflow + config)
  POST /krita/resource_image   resolve a `{source, layer_name}` spec to
                               a PNG data URL (used by the @thecanvas
                               auto-sync poller)

  GET  /krita/status           kritomatic daemon reachable?
  POST /krita/apply            send a mask to Krita (selection / transparency / extract / paint layer)
  POST /krita/import           fetch active document composite from Krita
  POST /krita/import_masks     fetch selected mask or paint layer(s) as base64 masks

WORKFLOW RESOLUTION
===================
The inpaint workflow is whatever the daemon currently has loaded — the
one the docker's Workflow picker shows, set via `comfy_load`.  The
playground does NOT hardcode a workflow name.  Before each inpaint it
queries `comfy_status` to learn the current name, then:

  * If the request body carries an explicit `workflow` field naming a
    *different* workflow, the batch includes a `comfy_load` to switch
    to it.
  * Otherwise (explicit name matches current, or no explicit name),
    no reload happens.

@thecanvas auto-sync
====================
The playground's displayed image tracks the daemon's
`config.resources["@thecanvas"]` spec.  The browser-side poller in
`pg_autosync.py` queries `/comfy/active_state` every 500 ms; on any
change to `config.resources["@thecanvas"]` it calls
`/krita/resource_image` with the new spec and swaps in the returned
pixels via `applyCanvasImage`.  There is no generation-triggered
refresh; only an explicit @thecanvas spec change fires it.

`/krita/resource_image` dispatches:
  source == "active"  → GET_DOCUMENT_COMPOSITE_B64 (the composite)
  source == "layer"   → GET_LAYER_PIXELS_B64 (that layer only)

`source == "global"` is a mask, not a colour image, and is not
displayed here — the inpaint workflow reads it directly.
"""

from __future__ import annotations

import base64
import json
import time
import traceback
from io import BytesIO
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from PIL import Image

from pg_comfy import (
    ComfyClient, ComfyError, ComfyTimeout, ComfyCancelled,
    EncodedImage, KritaResources, expand_custom, get_client, reset_client,
)
from pg_comfy.image import make_encoded_image

from . import krita_client


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

INPAINT_TIMEOUT = 180.0


def _log(msg: str) -> None:
    print(f"[inpaint] {msg}", flush=True)


_IMAGE_CACHE_MAX = 8
_image_cache: dict = {}
_image_cache_lock = __import__("threading").Lock()


def _cache_image(hash_hex, raw, width, height, filename):
    with _image_cache_lock:
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            oldest = next(iter(_image_cache))
            _image_cache.pop(oldest, None)
        _image_cache[hash_hex] = {
            "raw": raw, "width": width, "height": height,
            "filename": filename,
        }


def _decode_b64_png(data_url_or_b64: str) -> Image.Image:
    s = data_url_or_b64
    if "," in s:
        s = s.split(",", 1)[1]
    return Image.open(BytesIO(base64.b64decode(s)))


def _encode_b64_png(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _context_bbox(context_b64: str) -> tuple[int, int, int, int]:
    img = _decode_b64_png(context_b64).convert("L")
    bbox = img.getbbox()
    if bbox is None:
        _log(f"context_bbox: empty context ({img.width}x{img.height}); "
             f"falling back to full canvas")
        return (0, 0, img.width, img.height)
    x0, y0, x1, y1 = bbox
    if (x0, y0, x1, y1) == (0, 0, img.width, img.height):
        _log(f"context_bbox: context fills canvas ({img.width}x{img.height}); "
             f"no crop possible")
    return (x0, y0, x1 - x0, y1 - y0)


def _crop_b64_png(png_b64: str, bbox: tuple[int, int, int, int]) -> str:
    x, y, w, h = bbox
    img = _decode_b64_png(png_b64)
    return _encode_b64_png(img.crop((x, y, x + w, y + h)))


def _fetch_active_state() -> dict:
    """Return the daemon's `comfy_status` data payload, or {} on failure."""
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception:
        return {}
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return {}
    return resp.get("data") or {}


def _fetch_active_workflow() -> tuple[Optional[str], str]:
    data = _fetch_active_state()
    name = data.get("workflow")
    if not isinstance(name, str) or not name:
        return None, "no workflow loaded"
    return name, ""


# ---------------------------------------------------------------------------
#  /comfy/status, /comfy/active_state
# ---------------------------------------------------------------------------

def handle_status(_payload) -> dict:
    try:
        client = get_client(COMFY_HOST, COMFY_PORT)
        info = client.object_info()
    except ComfyError as e:
        reset_client()
        return {
            "success": True,
            "reachable": False,
            "message": f"ComfyUI unreachable at {COMFY_HOST}:{COMFY_PORT}: {e}",
        }

    has_tooling = "ETN_LoadImageCache" in info
    return {
        "success": True,
        "reachable": True,
        "node_count": len(info),
        "has_tooling": has_tooling,
    }


def handle_active_state(_payload) -> dict:
    data = _fetch_active_state()
    return {"success": True, "data": data}


# ---------------------------------------------------------------------------
#  /comfy/upload_image
# ---------------------------------------------------------------------------

def handle_upload(headers: dict, body: bytes) -> dict:
    if not body:
        return {"success": False, "message": "empty upload"}

    raw_filename = headers.get("X-Filename", "") or headers.get("x-filename", "")
    filename = unquote(raw_filename) if raw_filename else "image"

    try:
        encoded = make_encoded_image(body)
    except Exception as e:
        return {"success": False, "message": f"could not decode image: {e}"}

    _cache_image(encoded.content_hash, body, encoded.width, encoded.height, filename)
    return {
        "success": True,
        "hash": encoded.content_hash,
        "width": encoded.width,
        "height": encoded.height,
        "filename": filename,
    }


# ---------------------------------------------------------------------------
#  /comfy/inpaint
# ---------------------------------------------------------------------------

def handle_inpaint(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")
    context_b64   = payload.get("context_b64")
    prompt        = payload.get("prompt", "") or ""

    explicit = payload.get("workflow")
    current, workflow_err = _fetch_active_workflow()

    if explicit:
        workflow = explicit
        need_load = (explicit != current)
    elif current:
        workflow = current
        need_load = False
    else:
        return {
            "success": False,
            "message": (
                f"Cannot determine active workflow ({workflow_err}).  "
                f"Load one first: in the docker's Workflow picker, or "
                f"via `kritomatic comfyui comfy_load --name <name>`.  "
                f"Or pass an explicit `workflow` in the request body."
            ),
        }

    try:
        timeout = float(payload.get("timeout", INPAINT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = INPAINT_TIMEOUT

    try:
        output_rotation = float(payload.get("output_rotation") or 0.0)
    except (TypeError, ValueError):
        output_rotation = 0.0

    if not canvas_b64 or not selection_b64 or not context_b64:
        return {"success": False,
                "message": "canvas_b64, selection_b64, context_b64 are required"}

    try:
        bbox = _context_bbox(context_b64)
        canvas_b64    = _crop_b64_png(canvas_b64, bbox)
        selection_b64 = _crop_b64_png(selection_b64, bbox)
    except Exception as e:
        return {"success": False, "message": f"could not crop to context: {e}"}

    _log(f"context bbox={bbox}")
    _log(f"cropped canvas={bbox[2]}x{bbox[3]} at ({bbox[0]},{bbox[1]})")

    batch_id = f"inpaint_{int(time.time()*1000)}"

    captured = {"image_b64": None, "message": ""}

    def on_progress(index, result):
        if result.get("command") != "comfy_generate":
            return
        if result.get("status") != "success":
            captured["message"] = result.get("message", "")
            return
        data = result.get("data") or {}
        images = data.get("images") or []
        if images:
            captured["image_b64"] = images[0]

    generate_cmd = {
        "type": "comfy_generate",
        "timeout": 55.0,
        "output_rotation": output_rotation,
        "output_offset_x": bbox[0],
        "output_offset_y": bbox[1],
    }

    commands = [
        {"type": "comfy_connect", "check": False},
    ]
    if need_load:
        commands.append(
            {"type": "comfy_load", "name": workflow, "exclusive": True}
        )
    commands.extend([
        {"type": "comfy_set_layer_pixels", "name": "thecanvas",
         "image_b64": canvas_b64, "is_mask": False},
        {"type": "comfy_set_layer_pixels", "name": "theselection",
         "image_b64": selection_b64, "is_mask": True},
        {"type": "comfy_set_layer_pixels", "name": "thecontext",
         "image_b64": context_b64, "is_mask": True},
        {"type": "comfy_set_param", "name": "prompt", "value": prompt},
        generate_cmd,
    ])
    batch = {"id": batch_id, "commands": commands}

    _log(f"inpaint batch {batch_id}: workflow={workflow!r} "
         f"reload={need_load} prompt={prompt[:40]!r} "
         f"timeout={timeout}s")

    t0 = time.monotonic()
    summary = krita_client.send_batch(
        batch, on_progress=on_progress, timeout=timeout,
    )
    elapsed = time.monotonic() - t0

    if summary is None:
        return {"success": False,
                "message": "daemon did not respond",
                "timing": round(elapsed, 3)}

    if summary.get("status") == "error":
        return {"success": False,
                "message": summary.get("message", "daemon error"),
                "timing": round(elapsed, 3)}

    if captured["image_b64"] is None:
        for r in summary.get("results", []) or []:
            if r.get("command") != "comfy_generate":
                continue
            if r.get("status") != "success":
                continue
            images = ((r.get("data") or {}).get("images") or [])
            if images:
                captured["image_b64"] = images[0]
                break

    if captured["image_b64"] is None:
        detail = captured.get("message") or "no image produced"
        return {"success": False,
                "message": detail,
                "timing": round(elapsed, 3)}

    _log(f"inpaint batch {batch_id}: done in {elapsed:.2f}s")
    return {
        "success": True,
        "image_b64": captured["image_b64"],
        "timing": round(elapsed, 3),
    }


def handle_interrupt(_payload) -> dict:
    try:
        client = get_client(COMFY_HOST, COMFY_PORT)
        client.interrupt()
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
#  /krita/*
# ---------------------------------------------------------------------------

def handle_krita_status(_payload) -> dict:
    reachable = krita_client.is_reachable()
    return {"success": True, "reachable": reachable}


def handle_krita_apply(payload: dict) -> dict:
    mask_b64 = payload.get("mask_b64")
    if not mask_b64:
        return {"success": False, "message": "no mask_b64"}
    return {"success": False, "message": "krita/apply not implemented in this playground"}


def handle_krita_import(payload: dict) -> dict:
    name = payload.get("name") if isinstance(payload, dict) else None
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
        return {"success": False, "message": f"could not decode base64: {e}"}

    try:
        encoded = make_encoded_image(png_bytes)
    except Exception as e:
        return {"success": False, "message": f"could not decode image: {e}"}

    h = encoded.content_hash
    filename = data.get("name") or "krita_composite"
    _cache_image(h, png_bytes, encoded.width, encoded.height, filename)

    return {
        "success": True,
        "hash": h,
        "width": encoded.width,
        "height": encoded.height,
        "data_url": data_url,
        "filename": filename,
    }


def handle_krita_import_masks(payload: dict) -> dict:
    command = {"type": "import_krita_masks_b64"}
    if isinstance(payload, dict):
        if "layer_names" in payload and payload["layer_names"]:
            command["layer_names"] = str(payload["layer_names"])
        if "include_paint_alpha" in payload:
            command["include_paint_alpha"] = bool(payload["include_paint_alpha"])
        if "prefer_selection_mask_children" in payload:
            command["prefer_selection_mask_children"] = bool(
                payload["prefer_selection_mask_children"]
            )

    response = krita_client.send(command, timeout=60.0)
    if response.get("status") != "success":
        return {
            "success": False,
            "message": response.get("message") or "daemon rejected",
        }

    data = response.get("data") or {}
    masks = data.get("masks") or []
    skipped = data.get("skipped") or []
    selection_path = data.get("selection_path") or "unknown"

    return {
        "success": True,
        "masks": masks,
        "skipped": skipped,
        "selection_path": selection_path,
        "message": response.get("message") or f"{len(masks)} mask(s)",
    }


def handle_resource_image(payload: dict) -> dict:
    """Resolve a `{source, layer_name}` resource spec to a PNG data URL.

    Used by the @thecanvas auto-sync poller.  `source == "active"`
    fetches the document composite; `source == "layer"` fetches that
    specific layer's pixels via `get_layer_pixels_b64`.
    """
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    source = payload.get("source", "active")
    layer_name = payload.get("layer_name")

    if source == "active":
        return handle_krita_import({})
    if source == "layer":
        if not layer_name:
            return {"success": False,
                    "message": "layer_name is required when source=layer"}
        response = krita_client.send(
            {"type": "get_layer_pixels_b64", "name": str(layer_name)},
            timeout=30.0,
        )
        if response.get("status") != "success":
            return {"success": False,
                    "message": response.get("message") or "daemon rejected"}
        data = response.get("data") or {}
        data_url = data.get("data_url")
        if not data_url:
            return {"success": False,
                    "message": "daemon returned no data_url for layer"}
        return {
            "success": True,
            "hash": "layer_" + str(layer_name),
            "width": data.get("width"),
            "height": data.get("height"),
            "data_url": data_url,
            "filename": str(layer_name),
        }
    return {
        "success": False,
        "message": (
            f"source={source!r} cannot be displayed as an image "
            f"(@thecanvas expects 'active' or 'layer')."
        ),
    }


ROUTES = {
    "/comfy/status":         handle_status,
    "/comfy/active_state":   handle_active_state,
    "/comfy/inpaint":        handle_inpaint,
    "/comfy/interrupt":      handle_interrupt,
    "/krita/status":         handle_krita_status,
    "/krita/apply":          handle_krita_apply,
    "/krita/import":         handle_krita_import,
    "/krita/import_masks":   handle_krita_import_masks,
    "/krita/resource_image": handle_resource_image,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
