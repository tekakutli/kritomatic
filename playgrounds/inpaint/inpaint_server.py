"""HTTP routes for the inpaint playground.

The playground is a state synchronizer.  It no longer submits
generation — the docker's Run button does.

Routes
======

    POST /sync              push pixel state (canvas + selection) into
                            Krita scratch layers; also store rotation,
                            context rect, and canvas dims for
                            `/rotation` and `/crop_frame`
    POST /rotation          return the last rotation pushed via /sync
    POST /crop_frame        authoritative crop frame the daemon applies
                            to @thecanvas before submission; multimask
                            consumes the same frame to transform its
                            masks.  Everything downstream (canvas,
                            selection, context mask, multimask area
                            masks) ends up at the same cropW × cropH.
    POST /comfy/status      ComfyUI preflight
    POST /comfy/upload_image  upload an image file; returns hash + dims
    POST /comfy/active_state  proxy the daemon's `comfy_status`

    POST /krita/status      kritomatic daemon reachable?
    POST /krita/import      fetch active document composite from Krita
    POST /krita/import_masks  fetch selected mask or paint layer(s)
    POST /krita/resource_image  resolve a `{source, layer_name}` spec
                                to a PNG data URL

CROP FRAME
==========
`/crop_frame` returns `{theta, W, H, Wp, Hp, cropX, cropY, cropW,
cropH, inpaintEnabled, contextRect?}`.  It is the definitive answer
to "what does the daemon crop @thecanvas to."  The math:

  · theta = 0, context enabled: crop = user's context rect clamped to
    the canvas.
  · theta = 0, context disabled: crop = @thecontext layer's tight
    bbox (queried from the daemon), or whole canvas if the layer has
    no content.
  · theta ≠ 0: rotate frame to W'×H'; `_project_crop_dims_py` picks a
    centered cropW×cropH based on the selection bbox ∩ context rect
    in the rotated frame; crop at ((W'-cropW)//2, (H'-cropH)//2).

The daemon queries `/crop_frame` once per generation, applies those
exact numbers, and hands the workflow cropW×cropH blobs.  Multimask's
`/mask` handler queries the same frame and applies the same
rotate+crop to each area mask.  No duplication of the projection
math.

If the playground has no image (empty state), `/crop_frame` returns
`{success: true, frame: null}` — the daemon falls back to its legacy
un-rotated path.

CONTEXT RECT QUERY
==================
The context rect used for cropping is derived from:
  · The user's checkbox + rect on `/sync` when the checkbox is on
    (inpaintEnabled = True).
  · Otherwise from the daemon's `@thecontext` layer, per its
    configured source (default `{source: "layer"}`).  Inpaint queries
    the daemon, decodes the layer's mask, and takes its tight bbox.
  · If neither exists, whole canvas.

Both `/sync` and `/crop_frame` are liveness-gated: kill the playground
server, and the daemon's calls fail, falls back to legacy behavior
(no rotation, no context rect)."""

from __future__ import annotations

import base64
import math
import struct
import threading
import time
from io import BytesIO
from typing import Optional
from urllib.parse import unquote

from PIL import Image

from pg_comfy import ComfyError, get_client, reset_client
from pg_comfy.image import make_encoded_image

from . import krita_client


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

SYNC_TIMEOUT = 30.0


def _log(msg: str) -> None:
    print(f"[inpaint] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  State — rotation, context rect, canvas dims, selection b64.
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_rotation: float = 0.0
_context_state: dict = {
    "enabled": False, "x": 0, "y": 0, "w": 0, "h": 0,
}
_canvas_w: int = 0
_canvas_h: int = 0
_canvas_b64: str = ""
_selection_b64: str = ""


# ---------------------------------------------------------------------------
#  Image cache — used by the upload handler.
# ---------------------------------------------------------------------------

_IMAGE_CACHE_MAX = 8
_image_cache: dict = {}
_image_cache_lock = threading.Lock()


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
#  PNG helpers
# ---------------------------------------------------------------------------

def _png_dims_from_b64(b64: str) -> tuple[int, int]:
    """Return (W, H) from a base64 PNG, using only the IHDR header."""
    try:
        raw = base64.b64decode(b64[:64] if len(b64) > 64 else b64)
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        w, h = struct.unpack(">II", raw[16:24])
        return (int(w), int(h))
    except Exception:
        return (0, 0)


def _decode_bbox(b64: str) -> Optional[tuple[int, int, int, int]]:
    """Return the tight bbox of non-zero pixels in a grayscale PNG."""
    try:
        raw = base64.b64decode(b64)
        img = Image.open(BytesIO(raw)).convert("L")
        bbox = img.getbbox()
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        return (x0, y0, x1 - x0, y1 - y0)
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Daemon probes
# ---------------------------------------------------------------------------

def _fetch_active_state() -> dict:
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception:
        return {}
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return {}
    return resp.get("data") or {}


def handle_active_state(_payload) -> dict:
    data = _fetch_active_state()
    return {"success": True, "data": data}


def _query_thecontext_layer_bbox() -> Optional[tuple[int, int, int, int]]:
    """Read `@thecontext`'s bbox via the daemon, per its config source.

    Only handles source="layer".  Any other source (active / global)
    returns None; the caller falls back to whole-canvas context.  See
    the module docstring.
    """
    data = _fetch_active_state()
    if not data:
        return None
    config = data.get("config") or {}
    resources = config.get("resources") or {}
    spec = resources.get("@thecontext")
    if not isinstance(spec, dict):
        return None
    if spec.get("source") != "layer":
        return None
    layer_name = spec.get("layer_name")
    if not layer_name:
        return None

    try:
        resp = krita_client.send({
            "type": "import_krita_masks_b64",
            "layer_names": layer_name,
        }, timeout=10.0)
    except Exception:
        return None
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return None
    data = resp.get("data") or {}
    masks = data.get("masks") or []
    if not masks:
        return None
    m = masks[0]
    b64 = m.get("mask_b64")
    if not b64:
        return None
    return _decode_bbox(b64)


# ---------------------------------------------------------------------------
#  /sync — push pixel state + rotation + context
# ---------------------------------------------------------------------------

def handle_sync(payload: dict) -> dict:
    global _rotation, _context_state, _canvas_w, _canvas_h
    global _canvas_b64, _selection_b64

    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    try:
        rotation = float(payload.get("rotation", 0.0)) % 360.0
    except (TypeError, ValueError):
        rotation = 0.0

    context_state = {
        "enabled": bool(payload.get("context_enabled", False)),
        "x": int(payload.get("context_x", 0)),
        "y": int(payload.get("context_y", 0)),
        "w": int(payload.get("context_w", 0)),
        "h": int(payload.get("context_h", 0)),
    }

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")

    with _state_lock:
        _rotation = rotation
        _context_state = context_state
        if canvas_b64:
            _canvas_b64 = canvas_b64
            _canvas_w, _canvas_h = _png_dims_from_b64(canvas_b64)
        if selection_b64:
            _selection_b64 = selection_b64

    commands = []
    if canvas_b64:
        commands.append({
            "type": "comfy_set_layer_pixels",
            "name": "thecanvas",
            "image_b64": canvas_b64,
            "is_mask": False,
        })
    if selection_b64:
        commands.append({
            "type": "comfy_set_layer_pixels",
            "name": "theselection",
            "image_b64": selection_b64,
            "is_mask": True,
        })

    if not commands:
        return {"success": True,
                "rotation": rotation,
                "context": context_state,
                "pushed": []}

    batch_id = f"sync_{int(time.time() * 1000)}"
    batch = {"id": batch_id, "commands": commands}

    try:
        summary = krita_client.send_batch(batch, timeout=SYNC_TIMEOUT)
    except Exception as e:
        _log(f"sync error: {e}")
        return {"success": False,
                "message": f"daemon send failed: {e}",
                "rotation": rotation,
                "context": context_state}

    if summary is None:
        return {"success": False,
                "message": "daemon did not respond",
                "rotation": rotation,
                "context": context_state}

    if summary.get("status") == "error":
        return {"success": False,
                "message": summary.get("message", "daemon error"),
                "rotation": rotation,
                "context": context_state}

    failed = int(summary.get("failed", 0) or 0)
    if failed:
        msgs = []
        for r in summary.get("results", []) or []:
            if r.get("status") == "error":
                msgs.append(f"{r.get('command')}: {r.get('message')}")
        return {"success": False,
                "message": "; ".join(msgs) or f"{failed} command(s) failed",
                "rotation": rotation,
                "context": context_state}

    pushed = [c["name"] for c in commands]
    _log(f"sync: pushed {pushed} rotation={rotation} "
         f"context={context_state}")
    return {"success": True,
            "rotation": rotation,
            "context": context_state,
            "pushed": pushed}


# ---------------------------------------------------------------------------
#  /rotation — liveness-gated value source for the daemon
# ---------------------------------------------------------------------------

def handle_rotation(_payload) -> dict:
    with _state_lock:
        rotation = _rotation
    return {"rotation": rotation}


# ---------------------------------------------------------------------------
#  /crop_frame — authoritative crop window
# ---------------------------------------------------------------------------

def _project_crop_dims_py(W, H, maskBBoxOrig, theta, c, inpaintEnabled):
    """Port of the pre-refactor `projectCropDims`.  Kept identical so
    daemon and multimask can rely on it without divergence."""
    base = (W, H)
    if maskBBoxOrig is None:
        return base
    if not theta or theta % 360 == 0:
        return base

    rad = (theta % 360) * math.pi / 180.0
    cosA, sinA = abs(math.cos(rad)), abs(math.sin(rad))
    Wp = max(1, round(W * cosA + H * sinA))
    Hp = max(1, round(W * sinA + H * cosA))

    cosR, sinR = math.cos(rad), math.sin(rad)
    cx, cy = W / 2.0, H / 2.0
    cxp, cyp = Wp / 2.0, Hp / 2.0

    mx0, my0, mw, mh = maskBBoxOrig
    mcorners = [
        (mx0, my0),
        (mx0 + mw, my0),
        (mx0 + mw, my0 + mh),
        (mx0, my0 + mh),
    ]
    rminX = rminY = float("inf")
    rmaxX = rmaxY = float("-inf")
    for px, py in mcorners:
        dx = px - cx
        dy = py - cy
        rx = dx * cosR - dy * sinR
        ry = dx * sinR + dy * cosR
        wx = cxp + rx
        wy = cyp + ry
        if wx < rminX: rminX = wx
        if wy < rminY: rminY = wy
        if wx > rmaxX: rmaxX = wx
        if wy > rmaxY: rmaxY = wy

    if inpaintEnabled:
        ctxL = (Wp - W) / 2.0 + c[0]
        ctxT = (Hp - H) / 2.0 + c[1]
        ctxR = ctxL + c[2]
        ctxB = ctxT + c[3]
    else:
        ctxL = ctxT = float("-inf")
        ctxR = ctxB = float("inf")

    effL = max(rminX, ctxL)
    effT = max(rminY, ctxT)
    effR = min(rmaxX, ctxR)
    effB = min(rmaxY, ctxB)
    if effR <= effL or effB <= effT:
        return base

    baseL = (Wp - W) / 2.0
    baseT = (Hp - H) / 2.0
    baseR = baseL + W
    baseB = baseT + H
    if effL >= baseL and effT >= baseT and effR <= baseR and effB <= baseB:
        return base

    halfW = max(Wp / 2.0 - effL, effR - Wp / 2.0, W / 2.0)
    halfH = max(Hp / 2.0 - effT, effB - Hp / 2.0, H / 2.0)
    return (
        min(Wp, math.ceil(halfW * 2)),
        min(Hp, math.ceil(halfH * 2)),
    )


def _compute_crop_frame() -> Optional[dict]:
    with _state_lock:
        W = _canvas_w
        H = _canvas_h
        theta = _rotation
        ctx = dict(_context_state)
        sel_b64 = _selection_b64

    if W <= 0 or H <= 0:
        return None

    inpaint_enabled = False
    context_rect = (0, 0, W, H)

    if ctx.get("enabled") and ctx.get("w", 0) > 0 and ctx.get("h", 0) > 0:
        inpaint_enabled = True
        context_rect = (ctx["x"], ctx["y"], ctx["w"], ctx["h"])
    else:
        # Fallback: @thecontext layer bbox
        layer_bbox = _query_thecontext_layer_bbox()
        if layer_bbox is not None:
            inpaint_enabled = True
            context_rect = layer_bbox

    sel_bbox = _decode_bbox(sel_b64) if sel_b64 else None

    if theta == 0:
        if inpaint_enabled:
            x0 = max(0, int(context_rect[0]))
            y0 = max(0, int(context_rect[1]))
            x1 = min(W, int(context_rect[0]) + int(context_rect[2]))
            y1 = min(H, int(context_rect[1]) + int(context_rect[3]))
            if x1 > x0 and y1 > y0:
                result = {
                    "theta": 0, "W": W, "H": H, "Wp": W, "Hp": H,
                    "cropX": x0, "cropY": y0,
                    "cropW": x1 - x0, "cropH": y1 - y0,
                    "inpaintEnabled": True,
                    "contextRect": [int(context_rect[0]),
                                     int(context_rect[1]),
                                     int(context_rect[2]),
                                     int(context_rect[3])],
                }
                return result
        return {
            "theta": 0, "W": W, "H": H, "Wp": W, "Hp": H,
            "cropX": 0, "cropY": 0, "cropW": W, "cropH": H,
            "inpaintEnabled": False,
        }

    rad = math.radians(theta)
    cos_a, sin_a = abs(math.cos(rad)), abs(math.sin(rad))
    Wp = max(1, round(W * cos_a + H * sin_a))
    Hp = max(1, round(W * sin_a + H * cos_a))

    cropW, cropH = _project_crop_dims_py(
        W, H, sel_bbox, theta, context_rect, inpaint_enabled,
    )
    cropX = round((Wp - cropW) / 2)
    cropY = round((Hp - cropH) / 2)

    result = {
        "theta": theta, "W": W, "H": H, "Wp": Wp, "Hp": Hp,
        "cropX": cropX, "cropY": cropY, "cropW": cropW, "cropH": cropH,
        "inpaintEnabled": inpaint_enabled,
    }
    if inpaint_enabled:
        result["contextRect"] = [int(context_rect[0]),
                                  int(context_rect[1]),
                                  int(context_rect[2]),
                                  int(context_rect[3])]
    return result


def handle_crop_frame(_payload) -> dict:
    frame = _compute_crop_frame()
    return {"success": True, "frame": frame}


# ---------------------------------------------------------------------------
#  /comfy/status — unchanged
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
#  /comfy/upload_image — unchanged
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
#  /krita/* — unchanged
# ---------------------------------------------------------------------------

def handle_krita_status(_payload) -> dict:
    reachable = krita_client.is_reachable()
    return {"success": True, "reachable": reachable}


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
    "/sync":                 handle_sync,
    "/rotation":             handle_rotation,
    "/crop_frame":           handle_crop_frame,
    "/comfy/status":         handle_status,
    "/comfy/active_state":   handle_active_state,
    "/krita/status":         handle_krita_status,
    "/krita/import":         handle_krita_import,
    "/krita/import_masks":   handle_krita_import_masks,
    "/krita/resource_image": handle_resource_image,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
