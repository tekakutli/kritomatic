"""HTTP routes for the outpaint playground.

  POST /state                 set (from browser) or get
  POST /version               version only
  POST /krita_status          daemon reachable?
  POST /active_state          daemon comfy_status + playground registry
  POST /resource_image        resolve a {source, layer_name} spec to a PNG
  POST /playground_reregister single-shot re-register with the daemon

No magi integration, no bboxes, no generation.  State is just the
area dims and the viewer rect.
"""

import base64
import hashlib
import json
import threading

from io import BytesIO

from PIL import Image

from . import krita_client, playground_registry


PLAYGROUND_ID   = "outpaint"
PLAYGROUND_NAME = "Outpaint"
PLAYGROUND_PORT = 8779

LAYER_SLOTS = [
    {
        "id":                "@theoutpainted",
        "auto_select_layer": "theoutpainted",
    },
]

SLOT_ID = "@theoutpainted"

_DEFAULT_AREA_DIMS = [1024, 1024]
_DEFAULT_VIEWER = {
    "pxX": 51, "pxY": 51, "pxW": 922, "pxH": 922,
    "ratio": "1.00",
}


_state_lock = threading.RLock()
_state      = None


def _log(msg):
    print(f"[outpaint] {msg}", flush=True)


def _default_state():
    return {
        "area_dims": list(_DEFAULT_AREA_DIMS),
        "viewer":    dict(_DEFAULT_VIEWER),
    }


def _safe_dims(raw):
    try:
        w, h = raw
        return [max(1, int(w)), max(1, int(h))]
    except (TypeError, ValueError):
        return list(_DEFAULT_AREA_DIMS)


def _normalize_viewer(raw):
    raw = raw if isinstance(raw, dict) else {}
    try:
        pxX = max(0.0, float(raw.get("pxX", _DEFAULT_VIEWER["pxX"])))
        pxY = max(0.0, float(raw.get("pxY", _DEFAULT_VIEWER["pxY"])))
        pxW = max(1.0, float(raw.get("pxW", _DEFAULT_VIEWER["pxW"])))
        pxH = max(1.0, float(raw.get("pxH", _DEFAULT_VIEWER["pxH"])))
    except (TypeError, ValueError):
        pxX = _DEFAULT_VIEWER["pxX"]; pxY = _DEFAULT_VIEWER["pxY"]
        pxW = _DEFAULT_VIEWER["pxW"]; pxH = _DEFAULT_VIEWER["pxH"]
    ratio = str(raw.get("ratio") or _DEFAULT_VIEWER["ratio"])
    return {"pxX": pxX, "pxY": pxY, "pxW": pxW, "pxH": pxH, "ratio": ratio}


def _normalize_state(raw):
    return {
        "area_dims": _safe_dims(raw.get("area_dims") or _DEFAULT_AREA_DIMS),
        "viewer":    _normalize_viewer(raw.get("viewer")),
    }


def _compute_version(state):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _read_state_snapshot():
    with _state_lock:
        base = _default_state() if _state is None else dict(_state)
    return base, _compute_version(base)


# ---------------------------------------------------------------------------
#  /state, /version, /playground_reregister
# ---------------------------------------------------------------------------

def handle_state(payload):
    global _state
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    if payload.get("op") == "set":
        raw = payload.get("state") or {}
        normalized = _normalize_state(raw)
        with _state_lock:
            _state = normalized
        version = _compute_version(normalized)
        _log(f"state set: area={normalized['area_dims']} "
             f"viewer={normalized['viewer']['ratio']} v{version[:8]}")
        return {"success": True, "version": version, **normalized}

    state_snapshot, version_snapshot = _read_state_snapshot()
    return {"success": True, "version": version_snapshot, **state_snapshot}


def handle_version(_payload):
    _, version = _read_state_snapshot()
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


ROUTES = {
    "/state":                 handle_state,
    "/version":               handle_version,
    "/krita_status":          handle_krita_status,
    "/active_state":          handle_active_state,
    "/resource_image":        handle_resource_image,
    "/playground_reregister": handle_playground_reregister,
}

RAW_ROUTES = {}
