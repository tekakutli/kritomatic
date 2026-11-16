"""HTTP routes for the controlnets playground.

The playground is a state host.  It exposes `{image_hash, image_dims,
controlnets}` at `/state` for a future ComfyUI node to pull at
generation time, plus Krita-proxy routes for the browser.

Routes
======

    POST /state                  set (from browser) or get (from node)
    POST /version                version only (for IS_CHANGED sweeps)
    POST /krita_status           daemon reachable?
    POST /active_state           daemon comfy_status + playground registry
    POST /resource_image         resolve a {source, layer_name} spec to a PNG
    POST /playground_reregister  single-shot re-register with the daemon

There is no `/import_image` or `/upload_image` — the image can only
enter `/state` through `@thecontrolnet`, so a browser-side "import
composite" or "pick a file" path would let the canvas drift from
what the future node will actually fetch.  `handle_import_image`
still exists as an internal helper for the `source="active"` branch
of `handle_resource_image`.

State shape (POST /state):

    {
      "op": "set",
      "state": {
        "image_hash": "abc123...",
        "image_dims": [W, H],
        "controlnets": []
      }
    }

`controlnets` is currently always `[]`.  Each future entry will have
shape `{id, name, strength, image: {hash, width, height}}`.  The
corresponding `MultiControlNetConditioning` node will iterate the
list, fetch each item's image via a future `/controlnet` route by
hash, and expand into ControlNetApply branches — mirroring how
`MultiAreaConditioning` iterates `areas` and fetches masks via
`/mask`.
"""

import base64
import hashlib
import json
import threading
from io import BytesIO

from PIL import Image

from . import krita_client, playground_registry


PLAYGROUND_ID   = "controlnets"
PLAYGROUND_NAME = "Controlnets"
PLAYGROUND_PORT = 8779

LAYER_SLOTS = [
    {
        "id":                "@thecontrolnet",
        "auto_select_layer": "thecontrolnet",
    },
]


_state_lock = threading.RLock()
_state      = None
_version    = ""


def _log(msg):
    print(f"[controlnets] {msg}", flush=True)


def _default_state():
    return {
        "image_hash": "",
        "image_dims": [0, 0],
        "controlnets": [],
    }


def _read_state_snapshot():
    with _state_lock:
        if _state is None:
            return _default_state(), _version
        return dict(_state), _version


def _normalize_state(raw):
    hash_val = raw.get("image_hash") or ""
    if not isinstance(hash_val, str):
        hash_val = ""

    try:
        w, h = raw.get("image_dims") or [0, 0]
        dims = [max(0, int(w)), max(0, int(h))]
    except (TypeError, ValueError):
        dims = [0, 0]

    controlnets = []
    for cn in (raw.get("controlnets") or []):
        if isinstance(cn, dict):
            controlnets.append(cn)

    return {
        "image_hash":  hash_val,
        "image_dims":  dims,
        "controlnets": controlnets,
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
            f"state set: hash={normalized['image_hash'][:8]} "
            f"dims={normalized['image_dims']} "
            f"controlnets={len(normalized['controlnets'])} "
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
        return {"success": True, "version": _version}


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
    """Fetch the active document composite.

    Kept as an internal helper — `handle_resource_image` calls it for
    the `source="active"` branch.  Not exposed as a route: the only
    image-load path is `@thecontrolnet`.
    """
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
    "/state":                  handle_state,
    "/version":                handle_version,
    "/krita_status":           handle_krita_status,
    "/active_state":           handle_active_state,
    "/resource_image":         handle_resource_image,
    "/playground_reregister":  handle_playground_reregister,
}

RAW_ROUTES = {}