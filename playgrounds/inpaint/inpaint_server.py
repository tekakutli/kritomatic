"""HTTP routes and ComfyUI/daemon bridge for the inpaint playground.

Routes:

  POST /comfy/upload_image   raw body, X-Filename header → {hash, width, height}
  POST /comfy/inpaint        run NEWTekakutli45 with inline pixel blobs
  POST /comfy/interrupt      forward POST /interrupt to ComfyUI
  GET  /comfy/status         preflight (ComfyUI reachability)

  GET  /krita/status         kritomatic daemon reachable?
  POST /krita/apply          send a mask to Krita (selection / transparency / extract / paint layer)
  POST /krita/import         fetch active document composite from Krita
  POST /krita/import_masks   fetch selected mask or paint layer(s) as base64 masks

DEPENDENCY NOTE
===============
`handle_inpaint` sends a daemon batch that includes
`comfy_set_layer_pixels` — a daemon command that does not yet exist.
Until the daemon has it (along with `comfy_generate --no_paste` and
the `comfy_load` inline-map clear), `/comfy/inpaint` will fail with
"Unknown command 'comfy_set_layer_pixels'".  The rest of the routes
are functional today."""

from __future__ import annotations

import base64
import json
import time
import traceback
from pathlib import Path
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

DEFAULT_WORKFLOW = "NEWTekakutli45"

# Per-run wall clock allowance for the workflow.  ComfyUI can take tens
# of seconds for one SDXL inpaint with ControlNets; a 55 s ComfyUI
# timeout is the daemon-side default; the server-side wrapper adds a
# generous margin for upload + batch overhead.
INPAINT_TIMEOUT = 180.0


def _log(msg: str) -> None:
    print(f"[inpaint] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  Image cache — the raw-body upload path still needs a place to stash
#  the bytes between upload and use.  For inpaint we only send blobs
#  inline, so the cache is really only used by status checks and by
#  the (unwired) fallback path.
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
#  /comfy/status
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


# ---------------------------------------------------------------------------
#  /comfy/upload_image — kept for parity with sam3_segment; not used by
#  the current inpaint flow (which sends blobs inline) but harmless.
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
#  /comfy/inpaint — the main route
# ---------------------------------------------------------------------------

def handle_inpaint(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")
    context_b64   = payload.get("context_b64")
    prompt        = payload.get("prompt", "") or ""
    workflow      = payload.get("workflow") or DEFAULT_WORKFLOW

    try:
        timeout = float(payload.get("timeout", INPAINT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = INPAINT_TIMEOUT

    if not canvas_b64 or not selection_b64 or not context_b64:
        return {"success": False,
                "message": "canvas_b64, selection_b64, context_b64 are required"}

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

    batch = {
        "id": batch_id,
        "commands": [
            {"type": "comfy_load", "name": workflow, "exclusive": True},
            {"type": "comfy_set_layer_pixels", "name": "thecanvas",
             "image_b64": canvas_b64, "is_mask": False},
            {"type": "comfy_set_layer_pixels", "name": "theselection",
             "image_b64": selection_b64, "is_mask": True},
            {"type": "comfy_set_layer_pixels", "name": "thecontext",
             "image_b64": context_b64, "is_mask": True},
            {"type": "comfy_set_param", "name": "prompt", "value": prompt},
            {"type": "comfy_generate", "timeout": 55.0, "no_paste": True},
        ],
    }

    _log(f"inpaint batch {batch_id}: workflow={workflow} "
         f"prompt={prompt[:40]!r} timeout={timeout}s")

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
        # Fall back to the summary's per-command results if the stream
        # didn't include one (older daemon, or the batch_complete
        # summary's results array wasn't slimmed).
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


# ---------------------------------------------------------------------------
#  /comfy/interrupt — forward to ComfyUI's /interrupt
# ---------------------------------------------------------------------------

def handle_interrupt(_payload) -> dict:
    try:
        client = get_client(COMFY_HOST, COMFY_PORT)
        client.interrupt()
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
#  /krita/* — thin wrappers over the daemon TCP socket
# ---------------------------------------------------------------------------

def handle_krita_status(_payload) -> dict:
    reachable = krita_client.is_reachable()
    return {"success": True, "reachable": reachable}


def handle_krita_apply(payload: dict) -> dict:
    # Not wired by the inpaint playground; kept for parity and for the
    # (future) "export this mask back to Krita" flow.
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


ROUTES = {
    "/comfy/status":        handle_status,
    "/comfy/inpaint":       handle_inpaint,
    "/comfy/interrupt":     handle_interrupt,
    "/krita/status":        handle_krita_status,
    "/krita/apply":         handle_krita_apply,
    "/krita/import":        handle_krita_import,
    "/krita/import_masks":  handle_krita_import_masks,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
