"""HTTP routes for the multimask playground.

  POST /state               set (from browser) or get (from node)
  POST /version             version only (for IS_CHANGED)
  POST /mask                fetch a mask by hash; transformed by
                            inpaint's crop frame if available
  POST /export_graph        build the API-format JSON
  POST /fetch_history       fetch the last ComfyUI run
  POST /import_masks        fetch selected mask or paint layer(s)
  POST /krita_status        daemon reachable?
  POST /active_state        daemon comfy_status
  POST /resource_image      resolve a spec to a PNG data URL

The image enters the browser exclusively via `/resource_image`
(called by autosync and the panel's `Refresh @thecanvas` button).
The `+ Pick image…`, `Clear`, `+ From Krita (image)` shortcuts and
their server routes (`/import_image`, `/upload_image`) were removed
along with the drag-drop overlay — the autosync poller and the
manual refresh cover every install path those buttons provided.

The shared daemon_route_handlers() factory's import_image handler —
used internally by resource_image for source="active" — covers the
composite-fetch path.

MASK TRANSFORM
==============
When inpaint's playground is running, the daemon rotates and crops
`@thecanvas`, `@theselection`, and `@thecontext` before submitting to
ComfyUI.  To stay in the same coordinate frame, the area masks that
`MultiAreaConditioning` receives must undergo the same rotate+crop.
That transformation happens HERE, not in the browser (which must keep
un-rotated state for display) and not in the node (which is
playground-agnostic).

Mechanism: `/mask` queries inpaint's `/crop_frame` (cached for 1 s),
then applies `rotate(-theta) + crop(cropX, cropY, cropW, cropH)` via
PIL.  Cache key is `(hash, theta, cropX, cropY, cropW, cropH)` so a
given mask at a given frame isn't re-transformed.

When inpaint is down (or `/crop_frame` returns null), no transform
happens — the mask is served as stored.  Matches the daemon's
behavior, which also falls back to un-rotated when inpaint is
unreachable.

PUSH CHANNELS
=============
The inpaint playground's daemon publishes two events on the daemon's
event bus (port 12347) whenever a batch pastes into Krita:

  · `paste_applied` — per paste, with the pasted PNG + position.
    Forwarded to browsers as `patch` on `paste.applied`.  The
    browser composites it onto the current canvas for optimistic
    display (~20-90 ms).

  · `canvas_update` — at batch end, with the authoritative
    `doc.projection()` composite.  Forwarded as `patch` on
    `canvas.pushed`.  The browser replaces the canvas with ground
    truth (~200-500 ms later).

The subscriber thread (`_canvas_subscriber_loop`) connects to the
daemon event stream and forwards both.  Recovery on disconnect is
automatic — 2 s reconnect delay.  A daemon without an event stream
(older build) means the subscriber retries forever without
succeeding; the browser's autosync poll fallback still detects
batch-end via `active_batches` and re-fetches `@thecanvas`, so
post-paste updates arrive on the poll cadence instead of via push.

`set_browser_hub(hub)` must be called once, before
`start_canvas_subscriber()`, so the subscriber has somewhere to
forward to.  `multimask_playground.py` does both — it creates a
`pgkit.remote_hub.Hub`, passes it to `kit_serve(hub=...)` so the
`/remote` WebSocket endpoint uses it, and calls `set_browser_hub`
with the same instance.  This is the same wiring shape inpaint
uses; see `playgrounds/inpaint/inpaint_server.py`'s module
docstring, CANVAS_PUSH SUBSCRIBER.
"""

import base64
import hashlib
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from io import BytesIO

from PIL import Image

from pgruntime.daemon_routes import daemon_route_handlers
from pgruntime.ports import port_for, base_url_for


_state_lock = threading.Lock()
_state     = None
_masks     = {}
_version   = ""

PLAYGROUND_PORT = port_for("multimask")
COMFY_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")

INPAINT_URL = os.environ.get(
    "MULTIMASK_INPAINT_URL", base_url_for("inpaint"),
).rstrip("/")


# Crop-frame cache — one query per second at most.
_crop_frame_lock = threading.Lock()
_crop_frame_cache = {"frame": None, "at": 0.0}
CROP_FRAME_TTL = 1.0


# Mask transform cache — keyed by (hash, frame signature).
_mask_transform_lock = threading.Lock()
_mask_transform_cache: dict = {}
_MASK_TRANSFORM_CACHE_MAX = 64


# ---------------------------------------------------------------------------
#  Canvas-push subscriber
# ---------------------------------------------------------------------------

_browser_hub = None
_subscriber_running = False
_subscriber_thread = None


def set_browser_hub(hub) -> None:
    """Register the pgkit `Hub` the canvas/paste subscribers broadcast
    through.  Must be called once, before `start_canvas_subscriber()`.

    `multimask_playground.py` creates a `Hub()` instance, passes it
    to `kit_serve(hub=...)` (so the `/remote` WS endpoint uses it),
    and calls this with the same instance.  See the module docstring,
    PUSH CHANNELS.
    """
    global _browser_hub
    _browser_hub = hub


def start_canvas_subscriber() -> None:
    """Spawn the background thread that subscribes to the daemon's
    event stream and forwards canvas/paste events to browsers.

    Idempotent.  See the module docstring, PUSH CHANNELS.
    """
    global _subscriber_running, _subscriber_thread
    if _subscriber_thread is not None and _subscriber_thread.is_alive():
        return
    _subscriber_running = True
    _subscriber_thread = threading.Thread(
        target=_canvas_subscriber_loop,
        name="multimask-canvas-subscriber",
        daemon=True,
    )
    _subscriber_thread.start()


def _canvas_subscriber_loop() -> None:
    """Connect to the daemon event stream, forward canvas/paste events."""
    while _subscriber_running:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10.0)
            sock.connect(("127.0.0.1", 12347))
            sock.settimeout(None)
            _log("canvas subscriber: connected to daemon event stream")
            buf = b""
            while _subscriber_running:
                try:
                    chunk = sock.recv(65536)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    etype = event.get("type")
                    if etype == "paste_applied":
                        _forward_paste_applied(event.get("data") or {})
                    elif etype == "canvas_update":
                        _forward_canvas_update(event.get("data") or {})
        except OSError as e:
            _log(f"canvas subscriber: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass
        if _subscriber_running:
            time.sleep(2.0)


def _forward_paste_applied(data: dict) -> None:
    """Broadcast a `paste_applied` payload to every connected browser
    client as a `patch` message on the `paste.applied` field.

    The browser's `pgkitRemoteApplyPatch` routes the field update to
    `paste.applied`'s registered `set` handler, which calls
    `applyPastedCanvas` in `pg_autosync.js`.  See the module
    docstring, PUSH CHANNELS.
    """
    if _browser_hub is None:
        return
    if not isinstance(data, dict):
        return
    payload = {
        "hash":     data.get("hash"),
        "b64":      data.get("b64"),
        "paste_x":  int(data.get("paste_x") or 0),
        "paste_y":  int(data.get("paste_y") or 0),
        "width":    int(data.get("width") or 0),
        "height":   int(data.get("height") or 0),
        "doc_name": data.get("doc_name"),
    }
    if not payload["hash"] or not payload["b64"]:
        return
    try:
        _browser_hub.broadcast({
            "type": "patch",
            "values": {"paste.applied": payload},
        })
        _log(f"paste push forwarded: "
             f"{payload['width']}x{payload['height']} "
             f"at ({payload['paste_x']},{payload['paste_y']}) "
             f"hash={payload['hash']}")
    except Exception as e:
        _log(f"paste push forward failed: {e}")


def _forward_canvas_update(data: dict) -> None:
    """Broadcast a `canvas_update` payload to every connected browser
    client as a `patch` message on the `canvas.pushed` field.

    The browser's `pgkitRemoteApplyPatch` routes the field update to
    `canvas.pushed`'s registered `set` handler, which calls
    `applyPushedCanvas` in `pg_autosync.js`.  See the module
    docstring, PUSH CHANNELS.
    """
    if _browser_hub is None:
        return
    if not isinstance(data, dict):
        return
    payload = {
        "hash":     data.get("hash"),
        "b64":      data.get("b64"),
        "width":    data.get("width"),
        "height":   data.get("height"),
        "doc_name": data.get("doc_name"),
    }
    if not payload["hash"] or not payload["b64"]:
        return
    try:
        _browser_hub.broadcast({
            "type": "patch",
            "values": {"canvas.pushed": payload},
        })
        _log(f"canvas push forwarded: "
             f"{payload['width']}x{payload['height']} "
             f"hash={payload['hash']}")
    except Exception as e:
        _log(f"canvas push forward failed: {e}")


def _log(msg):
    print(f"[multimask] {msg}", flush=True)


def _default_globals():
    return {
        "combine_mode":            "combine",
        "use_base_setarea":        False,
        "base_area_width":         1024,
        "base_area_height":        1024,
        "default_strength":        0.9,
        "default_set_cond_area":   "default",
        "tagger_mode":             "replace",
        "tagger_model":            "wd-v1-4-moat-tagger-v2",
        "tagger_threshold":        0.35,
        "tagger_char_threshold":   0.85,
        "bbox":                    True,
        "resize":                  True,
        "mask_w":                  1024,
        "mask_h":                  1024,
        "mask_channel":            "red",
    }


def _compute_version(state):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _public_state():
    if _state is None:
        return {"globals": _default_globals(), "areas": []}
    return _state


# ---------------------------------------------------------------------------
#  /state, /version
# ---------------------------------------------------------------------------

def handle_state(payload):
    global _state, _version
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    if payload.get("op") == "set":
        raw_state = payload.get("state") or {}
        new_masks = payload.get("masks") or {}

        globs = _default_globals()
        globs.update(raw_state.get("globals") or {})
        state = {
            "globals": globs,
            "areas":   list(raw_state.get("areas") or []),
        }
        new_version = _compute_version(state)

        with _state_lock:
            _state   = state
            _version = new_version
            for h, b64 in new_masks.items():
                if isinstance(h, str) and isinstance(b64, str):
                    _masks[h] = b64
        _log(f"state set: {len(state['areas'])} area(s), "
             f"v{new_version[:8]}")
        return {"success": True, "version": new_version}

    with _state_lock:
        return {
            "success": True,
            "version": _version,
            **_public_state(),
        }


def handle_version(_payload):
    with _state_lock:
        return {"success": True, "version": _version}


# ---------------------------------------------------------------------------
#  Crop-frame + mask transform
# ---------------------------------------------------------------------------

def _fetch_crop_frame_uncached():
    try:
        req = urllib.request.Request(
            INPAINT_URL + "/crop_frame",
            data=b"{}", method="POST",
        )
        with urllib.request.urlopen(req, timeout=0.5) as r:
            data = json.loads(r.read())
        if not isinstance(data, dict) or not data.get("success"):
            return None
        return data.get("frame")
    except (urllib.error.URLError, OSError, ValueError):
        return None


def _fetch_crop_frame_cached():
    with _crop_frame_lock:
        now = time.monotonic()
        if now - _crop_frame_cache["at"] < CROP_FRAME_TTL:
            return _crop_frame_cache["frame"]
    frame = _fetch_crop_frame_uncached()
    with _crop_frame_lock:
        _crop_frame_cache["frame"] = frame
        _crop_frame_cache["at"] = time.monotonic()
    return frame


def _frame_signature(frame):
    if frame is None:
        return None
    return (
        round(float(frame.get("theta", 0.0)) % 360.0, 3),
        int(frame.get("cropX", 0)),
        int(frame.get("cropY", 0)),
        int(frame.get("cropW", 0)),
        int(frame.get("cropH", 0)),
    )


def _transform_mask_b64(b64: str, frame) -> str:
    if frame is None:
        return b64

    theta = float(frame.get("theta", 0.0)) % 360.0
    W = int(frame.get("W", 0))
    H = int(frame.get("H", 0))
    cropX = int(frame.get("cropX", 0))
    cropY = int(frame.get("cropY", 0))
    cropW = int(frame.get("cropW", 0))
    cropH = int(frame.get("cropH", 0))

    if cropW <= 0 or cropH <= 0:
        return b64

    try:
        raw = base64.b64decode(b64)
        img = Image.open(BytesIO(raw)).convert("L")
    except Exception as e:
        _log(f"mask decode failed: {e}")
        return b64

    if W > 0 and H > 0 and img.size != (W, H):
        img = img.resize((W, H), Image.LANCZOS)

    if theta != 0:
        img = img.rotate(
            -theta, expand=True,
            resample=Image.BICUBIC, fillcolor=0,
        )

    img = img.crop((cropX, cropY, cropX + cropW, cropY + cropH))

    buf = BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _transform_mask_cached(h: str, b64: str) -> str:
    frame = _fetch_crop_frame_cached()
    sig = _frame_signature(frame)
    if sig is None:
        return b64

    key = (h, sig)
    with _mask_transform_lock:
        cached = _mask_transform_cache.get(key)
        if cached is not None:
            return cached

    transformed = _transform_mask_b64(b64, frame)

    with _mask_transform_lock:
        if len(_mask_transform_cache) >= _MASK_TRANSFORM_CACHE_MAX:
            oldest = next(iter(_mask_transform_cache))
            _mask_transform_cache.pop(oldest, None)
        _mask_transform_cache[key] = transformed

    return transformed


def handle_mask(payload):
    h = (payload or {}).get("hash", "")
    with _state_lock:
        b64 = _masks.get(h)
    if b64 is None:
        return {"success": False, "message": f"unknown mask hash {h!r}"}

    transformed = _transform_mask_cached(h, b64)
    return {"success": True, "hash": h, "b64": transformed}


# ---------------------------------------------------------------------------
#  Graph export
# ---------------------------------------------------------------------------

_STUB_NODES = {
    "clip": {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {"ckpt_name": "PLACEHOLDER__reconnect_CLIP.safetensors"},
    },
    "base_positive": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "clip": ["__clip__", 1],
            "text": "PLACEHOLDER__reconnect_base_positive",
        },
    },
    "context_mask": {
        "class_type": "SolidMask",
        "inputs": {"value": 0.0, "width": 1024, "height": 1024},
    },
    "image": {
        "class_type": "EmptyImage",
        "inputs": {"width": 1024, "height": 1024,
                   "batch_size": 1, "color": 0},
    },
}


def _bool(v, d=False):
    if isinstance(v, bool): return v
    if isinstance(v, str):  return v.lower() in ("true", "yes", "on", "1")
    if isinstance(v, (int, float)): return bool(v)
    return d

def _float(v, d):
    try: return float(v)
    except (TypeError, ValueError): return d

def _int(v, d):
    try: return int(v)
    except (TypeError, ValueError): return d

def _str(v, d=""):
    return d if v is None else str(v)

def _safe_id(s):
    return re.sub(r"[^A-Za-z0-9_]", "_", str(s or "x"))


def _build_expansion_json(state, area_filter=""):
    globs = dict(state.get("globals") or {})
    areas = list(state.get("areas") or [])
    want = {n.strip() for n in (area_filter or "").split(",") if n.strip()}
    if want:
        areas = [a for a in areas if a.get("name") in want]

    notes = []
    graph = {}
    for key, stub in _STUB_NODES.items():
        graph["__" + key + "__"] = dict(stub)

    if not areas:
        notes.append(
            "No areas in the playground state; the node would pass "
            "base_positive through unchanged."
        )
        return graph, ["__base_positive__", 0], notes

    combine_mode          = _str(globs.get("combine_mode"), "combine")
    use_base_setarea      = _bool(globs.get("use_base_setarea"), False)
    base_w                = _int(globs.get("base_area_width"), 1024)
    base_h                = _int(globs.get("base_area_height"), 1024)
    default_strength      = _float(globs.get("default_strength"), 0.9)
    default_set_cond_area = _str(globs.get("default_set_cond_area"), "default")

    tagger_mode        = _str(globs.get("tagger_mode"), "replace")
    if tagger_mode not in ("replace", "concat"):
        tagger_mode = "replace"
    tagger_model       = _str(globs.get("tagger_model"), "wd-v1-4-moat-tagger-v2")
    tagger_threshold   = _float(globs.get("tagger_threshold"), 0.35)
    tagger_char_thresh = _float(globs.get("tagger_char_threshold"), 0.85)

    g_bbox       = _bool(globs.get("bbox"), False)
    g_resize     = _bool(globs.get("resize"), True)
    mask_w       = _int(globs.get("mask_w"), 1024)
    mask_h       = _int(globs.get("mask_h"), 1024)
    mask_channel = _str(globs.get("mask_channel"), "red")

    current = ["__base_positive__", 0]
    if use_base_setarea:
        graph["base_setarea"] = {
            "class_type": "ConditioningSetArea",
            "inputs": {
                "conditioning": current,
                "width": base_w, "height": base_h,
                "x": 0, "y": 0, "strength": 1.0,
            },
        }
        current = ["base_setarea", 0]

    needs_bbox = False
    for a in areas:
        eff_bbox = a.get("bbox")
        if eff_bbox is None:
            eff_bbox = g_bbox
        if _bool(eff_bbox, g_bbox):
            needs_bbox = True
            break

    if needs_bbox:
        notes.append(
            "The node crops each area's mask to the runtime bbox of "
            "(context_mask \u222a selection_mask) + crop_padding."
        )

    any_tagger = False
    combine_idx = 0

    for area in areas:
        aid = area.get("id", "unknown")
        base = "area_" + _safe_id(aid)

        prompt = _str(area.get("prompt"))

        strength = _float(
            area.get("strength") if area.get("strength") is not None
                                  else default_strength,
            default_strength,
        )
        set_cond_area = _str(
            area.get("set_cond_area") if area.get("set_cond_area") is not None
                                      else default_set_cond_area,
            default_set_cond_area,
        )
        area_channel = _str(
            area.get("mask_channel") if area.get("mask_channel") is not None
                                     else mask_channel,
            mask_channel,
        )
        area_bbox = _bool(
            area.get("bbox") if area.get("bbox") is not None else g_bbox,
            g_bbox,
        )
        area_resize = _bool(
            area.get("resize") if area.get("resize") is not None else g_resize,
            g_resize,
        )

        area_use_tagger = _bool(area.get("use_tagger"), False)
        area_tagger_mode = _str(
            area.get("tagger_mode") if area.get("tagger_mode") is not None
                                     else tagger_mode,
            tagger_mode,
        )
        if area_tagger_mode not in ("replace", "concat"):
            area_tagger_mode = "replace"
        area_tagger_model = _str(
            area.get("tagger_model") if area.get("tagger_model") is not None
                                      else tagger_model,
            tagger_model,
        )
        area_tagger_thresh = _float(
            area.get("tagger_threshold") if area.get("tagger_threshold") is not None
                                          else tagger_threshold,
            tagger_threshold,
        )
        area_tagger_char_th = _float(
            area.get("tagger_char_threshold")
                if area.get("tagger_char_threshold") is not None
                else tagger_char_thresh,
            tagger_char_thresh,
        )

        load_id = base + "_load_mask"
        graph[load_id] = {
            "class_type": "LoadImageMask",
            "inputs": {
                "image": f"_kritomatic_{aid}.png",
                "channel": area_channel,
            },
        }
        cond_mask = [load_id, 0]

        if area_bbox:
            crop_id = base + "_crop"
            graph[crop_id] = {
                "class_type": "CropMask",
                "inputs": {
                    "mask":   cond_mask,
                    "x":      "RUNTIME_CROP_X",
                    "y":      "RUNTIME_CROP_Y",
                    "width":  "RUNTIME_CROP_W",
                    "height": "RUNTIME_CROP_H",
                },
            }
            cond_mask = [crop_id, 0]

        if area_resize:
            resize_id = base + "_resize"
            graph[resize_id] = {
                "class_type": "ResizeImageMaskNode",
                "inputs": {
                    "input":              cond_mask,
                    "resize_type":        "scale dimensions",
                    "resize_type.width":  mask_w,
                    "resize_type.height": mask_h,
                    "resize_type.crop":   "center",
                    "scale_method":       "area",
                },
            }
            cond_mask = [resize_id, 0]

        text_input = prompt
        if area_use_tagger:
            any_tagger = True
            crop_to_mask_id = base + "_imgcrop"
            graph[crop_to_mask_id] = {
                "class_type": "ImageCropToMask",
                "inputs": {
                    "images":     ["__image__", 0],
                    "masks":      [load_id, 0],
                    "width":      mask_w,
                    "height":     mask_h,
                    "pad_factor": 1,
                    "grow_mask":  0,
                    "background": "#000000",
                },
            }
            tagger_id = base + "_tagger"
            graph[tagger_id] = {
                "class_type": "WD14Tagger|pysssss",
                "inputs": {
                    "image":               [crop_to_mask_id, 0],
                    "model":               area_tagger_model,
                    "threshold":           area_tagger_thresh,
                    "character_threshold": area_tagger_char_th,
                    "replace_underscore":  False,
                    "trailing_comma":      False,
                    "exclude_tags":        "",
                },
            }
            if area_tagger_mode == "concat" and prompt:
                fmt_id = base + "_fmt"
                graph[fmt_id] = {
                    "class_type": "StringFormat",
                    "inputs": {
                        "values.a": [tagger_id, 0],
                        "values.b": prompt,
                        "f_string": "{a} {b}",
                    },
                }
                text_input = [fmt_id, 0]
            else:
                text_input = [tagger_id, 0]

        encode_id = base + "_encode"
        graph[encode_id] = {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "clip": ["__clip__", 1],
                "text": text_input,
            },
        }

        set_id = base + "_set_mask"
        graph[set_id] = {
            "class_type": "ConditioningSetMask",
            "inputs": {
                "conditioning":   [encode_id, 0],
                "mask":           cond_mask,
                "strength":       strength,
                "set_cond_area":  set_cond_area,
            },
        }

        combine_idx += 1
        combine_id = f"combine_{combine_idx}"
        if combine_mode == "concat":
            graph[combine_id] = {
                "class_type": "ConditioningConcat",
                "inputs": {
                    "conditioning_to":   current,
                    "conditioning_from": [set_id, 0],
                },
            }
        else:
            graph[combine_id] = {
                "class_type": "ConditioningCombine",
                "inputs": {
                    "conditioning_1": current,
                    "conditioning_2": [set_id, 0],
                },
            }
        current = [combine_id, 0]

    if any_tagger:
        notes.append(
            "Tagger chains use WD14Tagger|pysssss + ImageCropToMask(image)."
        )

    return graph, current, notes


def handle_export_graph(payload):
    area_filter = ""
    if isinstance(payload, dict):
        area_filter = payload.get("area_filter") or ""

    with _state_lock:
        state   = _public_state()
        version = _version

    graph, final, notes = _build_expansion_json(state, area_filter)
    return {
        "success":            True,
        "version":            version,
        "area_count":         len(state.get("areas") or []),
        "area_filter":        area_filter,
        "final_conditioning": final,
        "graph":              graph,
        "notes":              notes,
    }


# ---------------------------------------------------------------------------
#  /fetch_history
# ---------------------------------------------------------------------------

def _comfy_get(path, timeout=10.0):
    url = COMFY_URL + path
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def handle_fetch_history(payload):
    try:
        try:
            history = _comfy_get("/history?max_items=1")
        except Exception:
            history = _comfy_get("/history")

        if not history:
            return {"success": False, "message": "ComfyUI history is empty"}

        if len(history) > 1:
            last_id = list(history.keys())[-1]
            history = {last_id: history[last_id]}

        prompt_id = next(iter(history))
        details   = history[prompt_id]

        diag = _diagnose_history(details)

        return {
            "success":    True,
            "comfy_url":  COMFY_URL,
            "prompt_id":  prompt_id,
            "prompt":     details.get("prompt"),
            "outputs":    details.get("outputs"),
            "status":     details.get("status"),
            "meta":       details.get("meta"),
            "diagnostic": diag,
        }
    except urllib.error.HTTPError as e:
        return {"success": False,
                "message": f"ComfyUI returned HTTP {e.code}"}
    except urllib.error.URLError as e:
        return {"success": False,
                "message": f"could not reach ComfyUI at {COMFY_URL}: "
                           f"{e.reason}"}
    except Exception as e:
        return {"success": False,
                "message": f"unexpected error: {e}"}


def _diagnose_history(details):
    prompt = details.get("prompt") or []
    graph = {}
    if isinstance(prompt, list) and len(prompt) >= 3:
        if isinstance(prompt[2], dict):
            graph = prompt[2]

    by_class    = {}
    cliptexts   = []
    multi_area  = None
    for nid, ndef in graph.items():
        if not isinstance(ndef, dict):
            continue
        ct = ndef.get("class_type", "?")
        by_class[ct] = by_class.get(ct, 0) + 1
        if ct == "CLIPTextEncode":
            txt = (ndef.get("inputs") or {}).get("text", "")
            if isinstance(txt, str):
                cliptexts.append({"node": nid, "text": txt[:300]})
        if ct == "MultiAreaConditioning":
            multi_area = {
                "node":   nid,
                "inputs": ndef.get("inputs") or {},
            }

    output_node_ids = list((details.get("outputs") or {}).keys())
    status          = details.get("status") or {}
    status_str      = status.get("status_str")
    status_messages = status.get("messages") or []

    return {
        "submitted_node_count": len(graph),
        "by_class":             by_class,
        "cliptexts":            cliptexts,
        "multi_area_node":      multi_area,
        "output_node_ids":      output_node_ids,
        "status_str":           status_str,
        "status_messages":      status_messages,
        "note": (
            "ComfyUI's /history returns the SUBMITTED graph, not the "
            "EXPANDED one."
        ),
    }


# ---------------------------------------------------------------------------
#  /krita_status, /import_masks, /resource_image
# ---------------------------------------------------------------------------


_h = daemon_route_handlers()


ROUTES = {
    "/state":              handle_state,
    "/version":            handle_version,
    "/mask":               handle_mask,
    "/export_graph":       handle_export_graph,
    "/fetch_history":      handle_fetch_history,
    "/import_masks":       _h["import_masks"],
    "/krita_status":       _h["krita_status"],
    "/active_state":       _h["active_state"],
    "/resource_image":     _h["resource_image"],
}

RAW_ROUTES = {}
