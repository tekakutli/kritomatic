"""HTTP routes for the magi playground.

The magi playground is a reference-image administrator: it displays a
single image (pulled from Krita via `@thecharacter`, or uploaded),
lets the user draw character bounding boxes over it, and posts
`{image, bboxes}` to a MAGI analysis endpoint to obtain 768-d
reference embeddings per character.  It exposes the resulting
`character_bboxes` + `reference_embeddings` at `/state` for the
`DrawatoonSetIP` node (or any future consumer) to pick up.

Routes
======

    POST /state                  set (from browser) or get (from node)
    POST /version                version only (for IS_CHANGED sweeps)
    POST /import_image           fetch active document composite from Krita
    POST /krita_status           daemon reachable?
    POST /active_state           daemon comfy_status + playground registry
    POST /resource_image         resolve a {source, layer_name} spec to a PNG
    POST /upload_image           raw body (image file)
    POST /playground_reregister  single-shot re-register with the daemon
    POST /analyze_characters     send image + bboxes to MAGI; get 768-d
                                 reference embeddings back

State shape (POST /state):

    {
      "op": "set",
      "state": {
        "character_bboxes":     [[x1, y1, x2, y2], ...],
        "reference_embeddings": [null | [768 floats], ...],
        "image_dims":           [W, H]
      }
    }

All bboxes are normalized [0,1] of image, in (x1, y1, x2, y2) order.
`reference_embeddings` length always matches `character_bboxes`.

MAGI INTEGRATION
================
`/analyze_characters` forwards to ComfyUI's `POST /magi/crop_embed`
route, which is provided by `kritomatic_nodes/Magi/`.  That route
lazy-loads ViT-MAE-base (`facebook/vit-mae-base` — the exact backbone
MAGI was trained against) on first call and returns the 768-d CLS
token per bbox.

Wire contract (playground → ComfyUI):

    request:
      {
        "image_b64":  "<base64 PNG, no data URL prefix>",
        "bboxes_px":  [[x1, y1, x2, y2], ...]   # pixel coords
      }

    response (success):
      {
        "success": true,
        "reference_embeddings": [[768 floats], ...]  # one per bbox
      }

    response (failure):
      { "success": false, "message": "<reason>" }

The ComfyUI endpoint URL is hardcoded, matching the convention used
by the layout playground for its `/state` consumer (see
`kritomatic_nodes/PixArt/nodes.py::_LAYOUT_STATE_URL`).  If ComfyUI
runs on a non-default port, update `_COMFY_CROP_EMBED_URL` below.
"""

import base64
import hashlib
import json
import threading
import urllib.error
import urllib.request
from io import BytesIO
from urllib.parse import unquote

from PIL import Image

from . import krita_client, playground_registry


PLAYGROUND_ID   = "magi"
PLAYGROUND_NAME = "MAGI Embedding Editor"
PLAYGROUND_PORT = 8778

LAYER_SLOTS = [
    {
        "id":                "@thecharacter",
        "auto_select_layer": "thecharacter",
    },
]

SLOT_ID = "@thecharacter"


# ComfyUI endpoint that serves ViT-MAE crop embedding.  The timeout is
# generous because the very first call may trigger an HF Hub download
# (~350 MB) plus model init (~3-5 s).  Subsequent calls return in
# tens of milliseconds per crop.
_COMFY_HOST              = "127.0.0.1"
_COMFY_PORT              = 8188
_COMFY_CROP_EMBED_URL    = f"http://{_COMFY_HOST}:{_COMFY_PORT}/magi/crop_embed"
_COMFY_TIMEOUT_S         = 120.0


_state_lock = threading.RLock()
_state      = None
_version    = ""


def _log(msg):
    print(f"[magi] {msg}", flush=True)


def _default_state():
    return {
        "character_bboxes":     [],
        "reference_embeddings": [],
        "image_dims":           [0, 0],
    }


def _read_state_snapshot():
    with _state_lock:
        if _state is None:
            return _default_state(), _version
        return dict(_state), _version


def _safe_bbox(raw):
    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
        return None
    try:
        x1, y1, x2, y2 = (float(v) for v in raw)
    except (TypeError, ValueError):
        return None
    x1 = max(0.0, min(1.0, x1))
    y1 = max(0.0, min(1.0, y1))
    x2 = max(0.0, min(1.0, x2))
    y2 = max(0.0, min(1.0, y2))
    if x2 <= x1 or y2 <= y1:
        return None
    return [x1, y1, x2, y2]


def _normalize_state(raw):
    char_bboxes = [_safe_bbox(b) for b in (raw.get("character_bboxes") or [])]
    char_bboxes = [b for b in char_bboxes if b is not None]

    refs_raw = raw.get("reference_embeddings") or []
    refs = []
    for r in refs_raw:
        if r is None:
            refs.append(None)
        elif isinstance(r, (list, tuple)) and len(r) == 768:
            try:
                refs.append([float(v) for v in r])
            except (TypeError, ValueError):
                refs.append(None)
        else:
            refs.append(None)

    while len(refs) < len(char_bboxes):
        refs.append(None)
    if len(refs) > len(char_bboxes):
        refs = refs[:len(char_bboxes)]

    try:
        w, h = raw.get("image_dims") or [0, 0]
        dims = [max(0, int(w)), max(0, int(h))]
    except (TypeError, ValueError):
        dims = [0, 0]

    return {
        "character_bboxes":     char_bboxes,
        "reference_embeddings": refs,
        "image_dims":           dims,
    }


def _compute_version(state):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
#  /state, /version, /playground_reregister
# ---------------------------------------------------------------------------

def handle_state(payload):
    global _state, _version
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    if payload.get("op") == "set":
        raw = payload.get("state") or {}
        normalized = _normalize_state(raw)
        new_version = _compute_version(normalized)
        with _state_lock:
            _state = normalized
            _version = new_version
        _log(
            f"state set: char={len(normalized['character_bboxes'])} "
            f"emb={sum(1 for r in normalized['reference_embeddings'] if r)} "
            f"v{new_version[:8]}"
        )
        return {"success": True, "version": new_version}

    state_snapshot, version_snapshot = _read_state_snapshot()
    return {
        "success": True,
        "version": version_snapshot,
        **state_snapshot,
    }


def handle_version(_payload):
    with _state_lock:
        version = _version
    return {"success": True, "version": version}


def handle_playground_reregister(_payload):
    url = f"http://127.0.0.1:{PLAYGROUND_PORT}"
    ok = playground_registry.reregister_once(
        PLAYGROUND_ID, PLAYGROUND_NAME, url, PLAYGROUND_PORT,
        layer_slots=LAYER_SLOTS,
    )
    return {"success": bool(ok)}


# ---------------------------------------------------------------------------
#  /analyze_characters — forwards to ComfyUI's /magi/crop_embed
# ---------------------------------------------------------------------------

def handle_analyze_characters(payload):
    """Forward {image_b64, bboxes_px} to ComfyUI's /magi/crop_embed.

    ComfyUI lazy-loads ViT-MAE on the first call (~3-5s + possibly a
    HF Hub download on a fresh install); subsequent calls return in
    tens of milliseconds per crop.  The route's response shape is
    identical to what this handler returns, so it is forwarded
    verbatim on success.
    """
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    image_b64 = payload.get("image_b64")
    bboxes_px = payload.get("bboxes_px")

    if not image_b64:
        return {"success": False, "message": "image_b64 required"}
    if not isinstance(bboxes_px, list) or not bboxes_px:
        return {"success": False,
                "message": "bboxes_px must be a non-empty list"}
    for i, b in enumerate(bboxes_px):
        if not isinstance(b, (list, tuple)) or len(b) != 4:
            return {"success": False,
                    "message": f"bboxes_px[{i}] is not a 4-tuple"}

    body = json.dumps({
        "image_b64": image_b64,
        "bboxes_px": bboxes_px,
    }).encode("utf-8")
    req = urllib.request.Request(
        _COMFY_CROP_EMBED_URL,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=_COMFY_TIMEOUT_S) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        _log(f"crop_embed HTTP {e.code}: {detail[:200]}")
        return {
            "success": False,
            "message": f"ComfyUI /magi/crop_embed HTTP {e.code}: "
                       f"{detail[:200] or e.reason}",
        }
    except urllib.error.URLError as e:
        _log(f"crop_embed unreachable: {e.reason}")
        return {
            "success": False,
            "message": f"ComfyUI unreachable at "
                       f"{_COMFY_HOST}:{_COMFY_PORT}: {e.reason}",
        }
    except Exception as e:
        _log(f"crop_embed transport error: {e}")
        return {
            "success": False,
            "message": f"crop_embed transport error: {e}",
        }

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        _log(f"crop_embed bad response: {e}")
        return {
            "success": False,
            "message": f"ComfyUI returned non-JSON: {e}",
        }

    if not isinstance(data, dict):
        return {"success": False,
                "message": "ComfyUI returned unexpected payload"}

    n = len(data.get("reference_embeddings") or [])
    if data.get("success"):
        _log(f"analyzed {n} character(s) via ComfyUI")
    else:
        _log(f"crop_embed reported failure: {data.get('message')}")
    return data


# ---------------------------------------------------------------------------
#  Krita proxies
# ---------------------------------------------------------------------------

def _fetch_active_state():
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception as e:
        return None, str(e)
    if not isinstance(resp, dict):
        return None, "bad daemon response"
    if resp.get("status") != "success":
        return None, resp.get("message") or "daemon rejected comfy_status"
    return resp.get("data") or {}, None


def handle_active_state(_payload):
    data, err = _fetch_active_state()
    if err is not None:
        return {
            "success": False,
            "daemon_reachable": False,
            "message": err,
        }
    return {
        "success": True,
        "daemon_reachable": True,
        "data": data,
    }


def handle_krita_status(_payload):
    return {"success": True, "reachable": krita_client.is_reachable()}


def handle_import_image(payload):
    name = (payload or {}).get("name") if isinstance(payload, dict) else None
    cmd = {"type": "get_document_composite_b64"}
    if name:
        cmd["name"] = name

    response = krita_client.send(cmd, timeout=60.0)
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
        img = Image.open(BytesIO(png_bytes))
        w, h = img.size
    except Exception as e:
        return {"success": False, "message": f"decode failed: {e}"}

    hash_hex = hashlib.sha256(png_bytes).hexdigest()[:16]
    return {
        "success":   True,
        "hash":      hash_hex,
        "width":     w,
        "height":    h,
        "data_url":  data_url,
        "filename":  data.get("name") or "krita_composite",
    }


def handle_resource_image(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    source = payload.get("source", "active")
    layer_name = payload.get("layer_name")

    if source == "active":
        return handle_import_image({})
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
        "message": f"source={source!r} cannot be displayed as an image.",
    }


def handle_upload_image(headers, body):
    if not body:
        return {"success": False, "message": "empty upload"}

    raw_filename = (headers.get("X-Filename", "")
                    or headers.get("x-filename", ""))
    filename = unquote(raw_filename) if raw_filename else "image"

    try:
        img = Image.open(BytesIO(body))
        w, h = img.size
    except Exception as e:
        return {"success": False,
                "message": f"could not decode image: {e}"}

    if img.format and img.format.upper() != "PNG":
        buf = BytesIO()
        img.save(buf, "PNG")
        png_bytes = buf.getvalue()
    else:
        png_bytes = body

    hash_hex = hashlib.sha256(png_bytes).hexdigest()[:16]
    data_url = ("data:image/png;base64,"
                + base64.b64encode(png_bytes).decode("ascii"))
    return {
        "success":  True,
        "hash":     hash_hex,
        "width":    w,
        "height":   h,
        "data_url": data_url,
        "filename": filename,
    }


ROUTES = {
    "/state":                  handle_state,
    "/version":                handle_version,
    "/import_image":           handle_import_image,
    "/krita_status":           handle_krita_status,
    "/active_state":           handle_active_state,
    "/resource_image":         handle_resource_image,
    "/playground_reregister":  handle_playground_reregister,
    "/analyze_characters":     handle_analyze_characters,
}

RAW_ROUTES = {
    "/upload_image": handle_upload_image,
}
