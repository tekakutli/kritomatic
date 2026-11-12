"""HTTP routes for the multimask playground.

  POST /state               set (from browser) or get (from node)
  POST /version             version only (for IS_CHANGED)
  POST /mask                fetch a mask by hash
  POST /export_graph        build the API-format JSON the node would emit
  POST /fetch_history       fetch the last ComfyUI run from native /history
  POST /generation_started  hint from MultiAreaConditioning; opens polling window
  POST /image_status        cheap poll; fetches composite only during active window
  POST /cached_image        return current composite (cache-first)
  POST /import_image        fetch active document composite from Krita
  POST /import_masks        fetch selected mask or paint layer(s) as base64 masks
  POST /krita_status        daemon reachable?
  POST /upload_image        raw body (image file), returns dims + data_url

STATE OWNERSHIP
===============
The browser is the source of truth.  It POSTs `{op: "set", state, masks}`
after every change; the server keeps the last-pushed state in memory
and serves it.  If the playground process restarts, the state is
lost and the browser re-pushes it on its next change (or on boot).

The node fetches `/state` on every execution.  `/version` is cheap
enough for `IS_CHANGED` on every queue.

MASK STORAGE
============
Masks are content-addressed by a hash the browser computes.  The
`/state` POST includes `{hash → b64}` for every mask referenced in
the state, and the server merges them into a growing dict.  Old
masks are not evicted; the dict is bounded by the number of distinct
masks the user has ever imported in the current process.

GRAPH EXPORT
============
`/export_graph` returns the API-format graph the node would build
from the current state.  It mirrors the node's expansion logic; the
two implementations must stay in sync when either side changes.
The export runs entirely in the playground, so it works with
ComfyUI closed.

Bbox crop — the export cannot show the actual crop coordinates.
The node computes them at execute time from the wired context_mask
and selection_mask.  The export emits `RUNTIME_CROP_X` etc. as
placeholders and a note explaining that.

HISTORY FETCH
=============
`/fetch_history` asks ComfyUI's *native* `/history` endpoint (port
8188) for the most recent run and returns it with a diagnostic
summary.  The native endpoint is the right source: expansion happens
at ComfyUI's execution time, not through comfy-api-proxy's
`/api/v2/jobs` API, so the proxy would never see these runs."""

import base64
import hashlib
import json
import os
import re
import threading
import urllib.error
import urllib.request
from io import BytesIO
from urllib.parse import unquote

from PIL import Image

from . import krita_client


_state_lock = threading.Lock()
_state     = None
_masks     = {}
_version   = ""

COMFY_URL = os.environ.get("COMFYUI_URL", "http://127.0.0.1:8188").rstrip("/")


# ---------------------------------------------------------------------------
#  Composite cache + generation-active window
# ---------------------------------------------------------------------------
#
# `/generation_started` (called by MultiAreaConditioning.execute before
# fetching state) opens a 120 s window during which `/image_status`
# fetches Krita's active-document composite and hashes it.  The browser
# polls `/image_status` every 3 s (1.5 s while active); on a hash
# mismatch it calls `/cached_image` to fetch the actual data.
#
# Outside the window, `/image_status` returns `{active: false}` without
# touching Krita.  Idle cost is one round trip on the playground's
# own HTTP server.

_generation_active_until = 0.0
_generation_lock         = threading.Lock()

_composite_cache = {
    "hash":     None,
    "data_url": None,
    "width":    0,
    "height":   0,
    "at":       0.0,
}
_composite_lock = threading.Lock()


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
#  /state, /version, /mask
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


def handle_mask(payload):
    h = (payload or {}).get("hash", "")
    with _state_lock:
        b64 = _masks.get(h)
    if b64 is None:
        return {"success": False, "message": f"unknown mask hash {h!r}"}
    return {"success": True, "hash": h, "b64": b64}


# ---------------------------------------------------------------------------
#  /generation_started, /image_status, /cached_image
# ---------------------------------------------------------------------------

def handle_generation_started(_payload):
    """Called by MultiAreaConditioning.execute() before fetching state.

    Opens a window during which /image_status will actively poll
    Krita's composite.  ComfyUI's SaveImage fires *after* the node's
    execute(), the daemon's paste-to-Krita fires shortly after that,
    and the composite becomes visible on the next /image_status poll.

    120 s is generous — most runs complete in under 60 s; if the user
    queues back-to-back generations, each /generation_started refreshes
    the window.
    """
    global _generation_active_until
    import time as _time
    with _generation_lock:
        _generation_active_until = _time.monotonic() + 120.0
    _log("generation_started; polling Krita composite for 120s")
    return {"success": True}


def _fetch_composite():
    """Fetch the active Krita document's composite and cache it."""
    import time as _time
    resp = krita_client.send(
        {"type": "get_document_composite_b64"}, timeout=15.0,
    )
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return None
    data_url = (resp.get("data") or {}).get("data_url")
    if not data_url or not data_url.startswith("data:image/png;base64,"):
        return None
    try:
        png_bytes = base64.b64decode(data_url.split(",", 1)[1])
        img = Image.open(BytesIO(png_bytes))
        w, h = img.size
    except Exception:
        return None
    hash_hex = hashlib.sha256(png_bytes).hexdigest()[:16]
    with _composite_lock:
        _composite_cache["hash"]     = hash_hex
        _composite_cache["data_url"] = data_url
        _composite_cache["width"]    = w
        _composite_cache["height"]   = h
        _composite_cache["at"]       = _time.monotonic()
    return {
        "hash": hash_hex, "data_url": data_url,
        "width": w, "height": h,
    }


def handle_image_status(_payload):
    """Cheap check the browser polls every few seconds.

    Idle: `{active: false}`, no Krita call.
    Active: fetch the composite, return its hash.  Browser compares
    against `board.imageHash`; on mismatch it calls `/cached_image`.
    """
    import time as _time
    with _generation_lock:
        active = _time.monotonic() < _generation_active_until
    if not active:
        return {"success": True, "active": False}
    result = _fetch_composite()
    if result is None:
        return {"success": False, "active": True,
                "message": "could not fetch composite from Krita"}
    return {"success": True, "active": True, "hash": result["hash"]}


def handle_cached_image(payload):
    """Return the current composite's data.  Prefers the cache.

    `payload.hash` is optional: when provided and matching a fresh
    cache entry, the response is served from cache (no Krita fetch).
    On cache miss or stale hash, refetches from Krita.
    """
    import time as _time
    want = (payload or {}).get("hash") if isinstance(payload, dict) else None

    with _composite_lock:
        cached = dict(_composite_cache)
    age = (_time.monotonic() - cached["at"]) if cached["at"] else 1e9

    if (cached["hash"] and age < 5.0
            and (want is None or want == cached["hash"])):
        return {
            "success":  True,
            "hash":     cached["hash"],
            "data_url": cached["data_url"],
            "width":    cached["width"],
            "height":   cached["height"],
        }

    result = _fetch_composite()
    if result is None:
        return {"success": False, "message": "could not fetch composite"}
    return {"success": True, **result}


# ---------------------------------------------------------------------------
#  Graph export — mirrors kritomatic_nodes/__init__.py's expansion
# ---------------------------------------------------------------------------
#
# The playground keeps a copy of the expansion logic so it can serve
# /export_graph without ComfyUI being present.  The two implementations
# must stay in sync; when you change one, change the other.  The
# duplication is bounded (one function, one dict shape) and the payoff
# is that the panel can export the graph while ComfyUI is closed.
#
# External inputs (clip, base_positive, context_mask, image) are
# emitted as placeholder stubs with reconnectable PLACEHOLDER__ strings,
# matching what the node's old subgraph_json output produced.

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
    """Return (graph, final_conditioning, notes).

    `graph` is {node_id: {class_type, inputs}} in ComfyUI API format.
    `final_conditioning` is the [node_id, slot] of the last combine,
    or ["__base_positive__", 0] if no areas ran.
    `notes` is a list of human-readable caveats about the export.
    """
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

    # If any area needs the bbox crop, note it.  The actual crop
    # rect is computed at execute time from the wired context_mask
    # and selection_mask — the playground cannot know those values.
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
            "(context_mask \u222a selection_mask) + crop_padding, clamped "
            "to the mask canvas.  This export shows the structure with "
            "placeholder coordinates; the actual values are computed "
            "by the node at execute time from the wired context_mask "
            "and selection_mask."
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
            "Tagger chains use WD14Tagger|pysssss + ImageCropToMask(image). "
            "They only execute in the node if the `image` input is wired."
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
#  /fetch_history — last ComfyUI run from native /history
# ---------------------------------------------------------------------------

def _comfy_get(path, timeout=10.0):
    url = COMFY_URL + path
    req = urllib.request.Request(url, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def handle_fetch_history(payload):
    """Fetch the last ComfyUI run from the native /history endpoint.

    Uses ComfyUI native (default port 8188), not comfy-api-proxy.
    MultiAreaConditioning nodes are queued through ComfyUI's browser
    UI, which goes through the native `/prompt` endpoint; comfy-api-
    proxy's `/api/v2/jobs` never sees them.

    Note: `/history` returns the SUBMITTED graph, not the EXPANDED
    graph.  The submitted graph contains MultiAreaConditioning and
    the base CLIPTextEncode (base_positive), but not the per-area
    CLIPTextEncode nodes that expansion creates.  The `diagnostic`
    field in the response explains this so the result is not misread.
    """
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
                "message": f"ComfyUI returned HTTP {e.code} "
                           f"from {COMFY_URL}{e.url}"}
    except urllib.error.URLError as e:
        return {"success": False,
                "message": f"could not reach ComfyUI at {COMFY_URL}: "
                           f"{e.reason}"}
    except Exception as e:
        return {"success": False,
                "message": f"unexpected error: {e}"}


def _diagnose_history(details):
    """Summarise one /history entry without claiming to show the
    expanded graph."""
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
            "EXPANDED one.  Per-area CLIPTextEncode nodes created by "
            "expansion will not appear in `prompt`.  The single "
            "CLIPTextEncode in the submitted graph is the base_positive "
            "input.  To see the expanded graph, start ComfyUI with "
            "--verbose, or add the _dump_finalized helper to the node."
        ),
    }


# ---------------------------------------------------------------------------
#  /krita_status, /import_image, /import_masks
# ---------------------------------------------------------------------------

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


def handle_import_masks(payload):
    cmd = {"type": "import_krita_masks_b64"}
    if isinstance(payload, dict):
        if payload.get("layer_names"):
            cmd["layer_names"] = str(payload["layer_names"])
        if "include_paint_alpha" in payload:
            cmd["include_paint_alpha"] = bool(payload["include_paint_alpha"])

    response = krita_client.send(cmd, timeout=60.0)
    if response.get("status") != "success":
        return {"success": False,
                "message": response.get("message") or "daemon rejected"}

    data = response.get("data") or {}
    return {
        "success":        True,
        "masks":          data.get("masks") or [],
        "skipped":        data.get("skipped") or [],
        "selection_path": data.get("selection_path") or "unknown",
        "message":        response.get("message") or "",
    }


# ---------------------------------------------------------------------------
#  /upload_image (raw body)
# ---------------------------------------------------------------------------

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
    "/state":              handle_state,
    "/version":            handle_version,
    "/mask":               handle_mask,
    "/export_graph":       handle_export_graph,
    "/fetch_history":      handle_fetch_history,
    "/generation_started": handle_generation_started,
    "/image_status":       handle_image_status,
    "/cached_image":       handle_cached_image,
    "/import_image":       handle_import_image,
    "/import_masks":       handle_import_masks,
    "/krita_status":       handle_krita_status,
}

RAW_ROUTES = {
    "/upload_image": handle_upload_image,
}