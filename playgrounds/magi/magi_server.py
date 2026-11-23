"""HTTP routes for the magi playground.

The magi playground is a reference-image administrator: it displays a
single image (pulled from Krita via `@thecharacter`), lets the user
draw character bounding boxes over it, and posts `{image, bboxes}` to
a MAGI analysis endpoint to obtain 768-d reference embeddings per
character.  It exposes the resulting `character_bboxes` +
`character_labels` + `reference_embeddings` at `/state` for the
layout playground (or any other consumer) to pick up.

Routes
======

    POST /state                  set (from browser) or get (from node)
    POST /version                version only (for IS_CHANGED sweeps)
    POST /krita_status           daemon reachable?
    POST /active_state           daemon comfy_status + playground registry
    POST /resource_image         resolve a {source, layer_name} spec to a PNG
    POST /playground_reregister  single-shot re-register with the daemon
    POST /analyze_characters     send image + bboxes to MAGI; get 768-d
                                 reference embeddings back

IMAGE INSTALL
=============
The only image-install path is `/resource_image`, resolved from the
`@thecharacter` slot's current selector by `pg_autosync.js` (autosync)
or `Refresh @thecharacter` (manual).  The former `/import_image` and
`/upload_image` routes, which the pick/clear/import-image buttons used,
are gone.  `handle_import_image` survives as an internal helper for
`handle_resource_image`'s `source="active"` branch.  The save/load
JSON flow (client-side) remains the way to rehydrate a full state.

State shape (POST /state):

    {
      "op": "set",
      "state": {
        "character_bboxes":     [[x1, y1, x2, y2], ...],
        "character_labels":     ["Alice", "Bob", ...],
        "reference_embeddings": [null | [768 floats], ...],
        "image_dims":           [W, H]
      }
    }

All bboxes are normalized [0,1] of image, in (x1, y1, x2, y2) order.
`character_labels` and `reference_embeddings` lengths always match
`character_bboxes`.  Labels are user-editable, browser-authoritative,
and serve as the pairing key for downstream consumers (layout pairs
its position boxes to magi's identities by label).

MAGI INTEGRATION
================
`/analyze_characters` forwards to ComfyUI's `POST /magi/crop_embed`
route, provided by `kritomatic_nodes/Magi/`.  That route lazy-loads
ViT-MAE-base (`facebook/vit-mae-base`) on first call and returns the
768-d CLS token per bbox.
"""

import hashlib
import json
import threading
import urllib.error
import urllib.request

from pgruntime.daemon_routes import daemon_route_handlers
from pgruntime import daemon_registry as playground_registry


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
        "character_labels":     [],
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

    labels_raw = raw.get("character_labels") or []
    char_labels = []
    for lbl in labels_raw:
        char_labels.append(lbl if isinstance(lbl, str) else "")
    while len(char_labels) < len(char_bboxes):
        char_labels.append("")
    if len(char_labels) > len(char_bboxes):
        char_labels = char_labels[:len(char_bboxes)]

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
        "character_labels":     char_labels,
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
    """Forward {image_b64, bboxes_px} to ComfyUI's /magi/crop_embed."""
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


_h = daemon_route_handlers()

ROUTES = {
    "/state":                  handle_state,
    "/version":                handle_version,
    "/krita_status":           _h["krita_status"],
    "/active_state":           _h["active_state"],
    "/resource_image":         _h["resource_image"],
    "/playground_reregister":  handle_playground_reregister,
    "/analyze_characters":     handle_analyze_characters,
}

RAW_ROUTES = {}
