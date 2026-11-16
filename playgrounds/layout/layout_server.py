"""HTTP routes for the layout playground.

  POST /state                  set (from browser) or get (from node)
  POST /version                version only (for IS_CHANGED and daemon sweep)
  POST /import_image           fetch active document composite from Krita
  POST /krita_status           daemon reachable?
  POST /active_state           daemon comfy_status + playground registry
  POST /resource_image         resolve a {source, layer_name} spec to a PNG
  POST /upload_image           raw body (image file)
  POST /playground_reregister  single-shot re-register with the daemon
  POST /generate               start a Drawatoon generation job
  POST /generate_status        poll a generation job

Lock discipline
===============
`_state_lock` is an `RLock`.  The canonical accessor is
`_read_state_snapshot()`, which acquires the lock once, merges magi's
embeddings, and returns a plain dict copy plus the merged version
hash.  No handler holds `_state_lock` while calling another function
that might also want it.

MAGI INTEGRATION
================
Layout's `character_bboxes` are *positions* — where the sampler should
place a character in the generated output.  Magi's `character_bboxes`
are *crops* — where in the reference image an embedding was taken
from.  They are different arrays for different purposes; layout never
touches magi's bboxes and magi never touches layout's.

What crosses the boundary is *identities*: layout pairs each of its
position boxes to one of magi's characters via a per-row label
(`character_labels` in the state dict).  The server merges by looking
up that label in magi's `/state`, resolving to the 768-d vector at the
same index as magi's `character_bboxes`.

Layout keeps ownership of:
  · character_bboxes     (positions, browser-authoritative)
  · character_labels     (magi pairing, browser-authoritative)
  · text_bboxes, viewer, image_dims

Magi owns:
  · character_bboxes     (crop metadata, unused by layout)
  · character_labels     (identity names)
  · reference_embeddings (768-d vectors)

Merge is at READ time: on every GET `/state` (and every `/version`),
the server pulls magi's `/state` (with negative-only caching so magi
being down doesn't add latency to every poll), matches labels, and
injects the resolved `reference_embeddings` array plus two
informational fields (`magi_reachable`, `magi_character_labels`) that
the browser uses to populate its dropdowns.

Fallback: magi unreachable (or unreachable-for-this-pull) → all
`reference_embeddings` null → `DrawatoonSetIP` sees the same
all-null array it has always seen → each position uses
`character_cls_embedding`.  Behaviour is identical to the current
implementation before the merge existed.  No error, no warning to
the user, no delay beyond the 1 s magi timeout on the first miss
(cached for 2 s thereafter).

Version invalidation is automatic: `_compute_version` hashes the
*merged* dict, so any magi change (add, delete, rename, re-analyze)
changes the version that `DrawatoonSetIP.IS_CHANGED` reads, and the
next queue picks up the new embeddings without any node or workflow
change.
"""

import base64
import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from io import BytesIO
from urllib.parse import unquote

from PIL import Image

from . import krita_client, playground_registry, layout_comfy


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
    "ratio": "1.00",
}

# Magi integration — pull endpoint + negative-cache timing.  Positive
# results are not cached: magi's `/state` is a pure in-memory dict read
# on its side, so pulling every layout read costs ~1 ms and gives
# always-fresh data.  Negative results are cached because the failure
# mode (connect timeout) costs the full `_MAGI_TIMEOUT_S` each attempt.
_MAGI_STATE_URL  = "http://127.0.0.1:8778/state"
_MAGI_TIMEOUT_S  = 1.0
_MAGI_ERR_TTL_S  = 2.0


_state_lock = threading.RLock()
_state      = None


# ---------------------------------------------------------------------------
#  Magi pull + merge
# ---------------------------------------------------------------------------

_magi_cache_lock = threading.Lock()
_magi_cache = {"err": False, "at": 0.0}


def _pull_magi():
    """Fetch magi's /state.  Returns None on any failure.

    Positive results are returned fresh (no cache).  Negative results
    are cached for `_MAGI_ERR_TTL_S` so an unreachable magi does not
    add a 1-second timeout to every `/state` and `/version` read.
    """
    now = time.monotonic()
    with _magi_cache_lock:
        if _magi_cache["err"] and now - _magi_cache["at"] < _MAGI_ERR_TTL_S:
            return None

    try:
        req = urllib.request.Request(
            _MAGI_STATE_URL,
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_MAGI_TIMEOUT_S) as r:
            data = json.loads(r.read())
        if isinstance(data, dict) and data.get("success"):
            with _magi_cache_lock:
                _magi_cache["err"] = False
                _magi_cache["at"] = now
            return data
    except Exception:
        pass

    with _magi_cache_lock:
        _magi_cache["err"] = True
        _magi_cache["at"] = now
    return None


def _merge_magi_into_state(base_state):
    """Resolve layout's per-position magi labels to 768-d embeddings.

    Returns a new dict: base_state + reference_embeddings +
    magi_reachable + magi_character_labels.  All fields the node
    consumes are present; the two `magi_*` fields are informational
    (used by the browser to populate dropdowns and disable when
    magi is down).
    """
    merged = dict(base_state)
    char_count = len(base_state.get("character_bboxes") or [])
    layout_labels = base_state.get("character_labels") or []

    magi_state = _pull_magi()
    if magi_state is None:
        merged["reference_embeddings"] = [None] * char_count
        merged["magi_reachable"] = False
        merged["magi_character_labels"] = []
        return merged

    magi_labels     = magi_state.get("character_labels") or []
    magi_embeddings = magi_state.get("reference_embeddings") or []

    # Only non-empty labels are pairable — an empty label is "unlabeled"
    # in magi's UI and means "no identity for this crop".  Duplicate
    # labels: last occurrence wins (documented; magi's UI does not
    # enforce uniqueness, so we pick a deterministic tiebreaker).
    label_to_embedding = {}
    for i, lbl in enumerate(magi_labels):
        if isinstance(lbl, str) and lbl and i < len(magi_embeddings):
            label_to_embedding[lbl] = magi_embeddings[i]

    resolved = []
    for lbl in layout_labels:
        if isinstance(lbl, str) and lbl and lbl in label_to_embedding:
            resolved.append(label_to_embedding[lbl])
        else:
            resolved.append(None)

    while len(resolved) < char_count:
        resolved.append(None)
    if len(resolved) > char_count:
        resolved = resolved[:char_count]

    merged["reference_embeddings"] = resolved
    merged["magi_reachable"] = True
    merged["magi_character_labels"] = [
        lbl for lbl in magi_labels
        if isinstance(lbl, str) and lbl
    ]
    return merged


# ---------------------------------------------------------------------------
#  State normalization + versioning
# ---------------------------------------------------------------------------

def _log(msg):
    print(f"[layout] {msg}", flush=True)


def _default_state():
    return {
        "text_bboxes":      [],
        "character_bboxes": [],
        "character_labels": [],
        "image_dims":       [0, 0],
        "viewer":           dict(_DEFAULT_VIEWER),
    }


def _read_state_snapshot():
    """Returns (merged_state_dict, merged_version)."""
    with _state_lock:
        base = _default_state() if _state is None else dict(_state)
    merged = _merge_magi_into_state(base)
    return merged, _compute_version(merged)


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

    ratio = str(raw.get("ratio") or _DEFAULT_VIEWER["ratio"])
    return {"x": vx, "y": vy, "w": vw, "h": vh, "ratio": ratio}


def _normalize_state(raw):
    text_bboxes = [_safe_bbox(b) for b in (raw.get("text_bboxes") or [])]
    text_bboxes = [b for b in text_bboxes if b is not None]

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

    # NOTE: browser-side `reference_embeddings` is deliberately ignored.
    # The server injects the merged array at read time from magi.

    try:
        w, h = raw.get("image_dims") or [0, 0]
        dims = [max(0, int(w)), max(0, int(h))]
    except (TypeError, ValueError):
        dims = [0, 0]

    return {
        "text_bboxes":      text_bboxes,
        "character_bboxes": char_bboxes,
        "character_labels": char_labels,
        "image_dims":       dims,
        "viewer":           _normalize_viewer(raw.get("viewer")),
    }


def _compute_version(state):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        merged, version = _read_state_snapshot()
        n_chars = len(merged.get("character_bboxes") or [])
        n_paired = sum(1 for r in merged.get("reference_embeddings") or []
                       if r is not None)
        v = merged["viewer"]
        _log(f"state set: text={len(merged['text_bboxes'])} "
             f"char={n_chars} paired={n_paired} "
             f"magi={'up' if merged['magi_reachable'] else 'down'} "
             f"viewer={v['ratio']} "
             f"v{version[:8]}")
        return {
            "success":                True,
            "version":                version,
            "magi_reachable":         merged["magi_reachable"],
            "magi_character_labels":  merged["magi_character_labels"],
            "reference_embeddings":   merged["reference_embeddings"],
        }

    state_snapshot, version_snapshot = _read_state_snapshot()
    return {
        "success": True,
        "version": version_snapshot,
        **state_snapshot,
    }


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


def handle_generate(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}
    try:
        job_id = layout_comfy.start_generation(payload)
    except (ValueError, RuntimeError) as e:
        return {"success": False, "message": str(e)}
    return {"success": True, "job_id": job_id}


def handle_generate_status(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}
    job_id = payload.get("job_id")
    if not job_id:
        return {"success": False, "message": "job_id required"}
    entry = layout_comfy.get_job_status(str(job_id))
    if not entry:
        return {"success": False,
                "message": f"unknown job {job_id!r}"}
    return {"success": True, **entry}


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
    "/generate":               handle_generate,
    "/generate_status":        handle_generate_status,
}

RAW_ROUTES = {
    "/upload_image": handle_upload_image,
}