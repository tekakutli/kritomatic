"""HTTP routes for the layout playground.

  POST /state                  set (from browser) or get (from node)
  POST /version                version only (for IS_CHANGED and daemon sweep)
  POST /import_image           fetch active document composite from Krita
  POST /krita_status           daemon reachable?
  POST /active_state           daemon comfy_status + playground registry
  POST /resource_image         resolve a {source, layer_name} spec to a PNG
  POST /upload_image           raw body (image file)
  POST /playground_reregister  single-shot re-register with the daemon

Lock discipline
===============
`_state_lock` is an `RLock`.  The canonical accessor is
`_read_state_snapshot()`, which acquires the lock once and returns a
plain dict copy.  Handlers build their response from the copy; no
handler holds `_state_lock` while calling another function that might
also want it.

Model is fixed
==============
`viewer.model` is pinned to `PixArtMS_Sigma_XL_2_512` on this server
regardless of what the browser POSTs.  drawatoon is a 512-px model;
there is no user-facing model choice.  The panel doesn't send a model
field at all.  If a future fine-tune needs a different preset, change
`_DEFAULT_VIEWER["model"]` here (and the corresponding hardcoded
value in `pg_state.js`, only used for display).
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


PLAYGROUND_ID   = "layout"
PLAYGROUND_NAME = "Layout Editor"
PLAYGROUND_PORT = 8777

LAYER_SLOTS = [
    {
        "id":                "@thelayout",
        "auto_select_layer": "thelayout",
    },
]

SLOT_ID = "@thelayout"

_DEFAULT_VIEWER = {
    "x": 0.05, "y": 0.05, "w": 0.9, "h": 0.9,
    "model": "PixArtMS_Sigma_XL_2_512",
    "ratio": "1.00",
}

_state_lock = threading.RLock()
_state      = None
_version    = ""


def _log(msg):
    print(f"[layout] {msg}", flush=True)


def _default_state():
    return {
        "text_bboxes":          [],
        "character_bboxes":     [],
        "reference_embeddings": [],
        "image_dims":           [0, 0],
        "viewer":               dict(_DEFAULT_VIEWER),
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


def _normalize_viewer(raw):
    raw = raw if isinstance(raw, dict) else {}
    try:
        vx = max(0.0, min(1.0, float(raw.get("x", _DEFAULT_VIEWER["x"]))))
        vy = max(0.0, min(1.0, float(raw.get("y", _DEFAULT_VIEWER["y"]))))
        vw = max(0.01, min(1.0, float(raw.get("w", _DEFAULT_VIEWER["w"]))))
        vh = max(0.01, min(1.0, float(raw.get("h", _DEFAULT_VIEWER["h"]))))
    except (TypeError, ValueError):
        vx, vy, vw, vh = (
            _DEFAULT_VIEWER["x"], _DEFAULT_VIEWER["y"],
            _DEFAULT_VIEWER["w"], _DEFAULT_VIEWER["h"],
        )
    if vx + vw > 1.0:
        vw = max(0.01, 1.0 - vx)
    if vy + vh > 1.0:
        vh = max(0.01, 1.0 - vy)

    # Model is pinned — see module docstring.  Ratio is user-driven.
    model = _DEFAULT_VIEWER["model"]
    ratio = str(raw.get("ratio") or _DEFAULT_VIEWER["ratio"])
    return {"x": vx, "y": vy, "w": vw, "h": vh,
            "model": model, "ratio": ratio}


def _normalize_state(raw):
    text_bboxes = [_safe_bbox(b) for b in (raw.get("text_bboxes") or [])]
    text_bboxes = [b for b in text_bboxes if b is not None]

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
        "text_bboxes":          text_bboxes,
        "character_bboxes":     char_bboxes,
        "reference_embeddings": refs,
        "image_dims":           dims,
        "viewer":               _normalize_viewer(raw.get("viewer")),
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
        v = normalized["viewer"]
        _log(f"state set: text={len(normalized['text_bboxes'])} "
             f"char={len(normalized['character_bboxes'])} "
             f"viewer={v['ratio']} "
             f"v{new_version[:8]}")
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
}

RAW_ROUTES = {
    "/upload_image": handle_upload_image,
}