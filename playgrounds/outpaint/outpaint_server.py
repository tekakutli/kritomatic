"""HTTP routes for the outpaint playground.

  POST /state                 set (from browser) or get
  POST /version               version only
  POST /params                current pad values (playground-owned params)
  POST /krita_status          daemon reachable?
  POST /active_state          daemon comfy_status + playground registry
  POST /resource_image        resolve a {source, layer_name} spec to a PNG
  POST /playground_reregister single-shot re-register with the daemon
  POST /generate              trigger generation (no_paste); return PNG b64
  POST /send_to_krita         create a new Krita doc from the given PNG

No magi integration, no bboxes.  State is just the area dims and the
viewer rect.

PARAM VALUES
============
`/params` is what makes the playground's "stealing" of the workflow
params work end-to-end.  The daemon's
`comfyui_generation._fetch_playground_params` POSTs here at generation
time and merges the returned values into the config's `params` dict
before calling `prepare_workflow`.  See `kritomatic_daemon/handlers/
playgrounds.py`'s PARAM SLOTS for the registration side.

The playground claims three groups of params (see PARAM_SLOTS):

  · Four ints derived from `_state.viewer` (which the browser keeps
    current via `/state` on every viewer change) and `_state.area_dims`:

        OUTPAINT_LEFT_PAD   = viewer.pxX
        OUTPAINT_TOP_PAD    = viewer.pxY
        OUTPAINT_RIGHT_PAD  = area_dims[0] - (viewer.pxX + viewer.pxW)
        OUTPAINT_BOTTOM_PAD = area_dims[1] - (viewer.pxY + viewer.pxH)

    That is: how much empty area lies between each outer edge of the
    outpaint area and the corresponding side of the viewer rect.  All
    values are rounded to int and clamped to `>= 0` (defensively — the
    browser's clamp should make the padding non-negative, but a stale
    state on the server should still produce usable numbers rather
    than negative paddings the workflow might choke on).

  · Two boolean flags forced to `True`:

        APIv2    = True   (outpaint always uses the v2 proxy path)
        OUTPAINT = True   (this workflow is an outpaint workflow)

    The corresponding Primitive nodes are hidden from the docker's
    Parameters section (their names are in PARAM_SLOTS), so the user
    cannot switch them off.

  · Two hidden-but-not-owned params (`denoise`, `OUTPAINT_DENOISE`)
    appear in PARAM_SLOTS so the docker hides their fields, but the
    playground never returns a value for them.  At generation time
    the daemon falls back to `_active_config` for missing claimed
    params; the workflow Primitive default is used when nothing else
    has been set.

GENERATION AND SEND-TO-KRITA
============================
Generation is triggered by the docker's global Run button
(`comfy_panel_run`).  The playground is no longer a generation
initiator; it is a receiver.  The `/generate` route is kept for
external callers / debugging (it still POSTs `comfy_generate
--no_paste` on the daemon), but is not invoked by the browser UI.

The daemon publishes `paste_applied` after each successful paste
(see `comfyui_generation._publish_paste_applied`).  When the
outpaint workflow is set to `paste.resize_canvas=true` — or when
the daemon is updated to detect `OUTPAINT=True` and skip paste
entirely — those events carry the outpaint PNG.  The event-bus
subscriber below captures the latest paste in `_last_pasted`, which
`handle_active_state` exposes; the browser's autosync poll
(`pg_autosync.js`) picks it up and calls `applyGeneratedImage`.

    /send_to_krita   materializes the currently displayed PNG as a
                     brand-new Krita document.  Two existing daemon
                     commands are chained: `create_new_with_dimensions`
                     (creates a doc at the PNG's exact size) then
                     `comfy_paste_image_b64` (pastes the PNG at (0,0)
                     into the now-active doc).  The user's original
                     document is untouched.

EVENT BUS SUBSCRIPTION
======================
`start_event_bus_subscriber()` starts a daemon thread that connects
to the daemon's event server (`127.0.0.1:12347`), reads
newline-delimited JSON events, and stores the latest
`paste_applied` payload in `_last_pasted`.  Reconnects on
disconnect with a 2 s backoff.

If the daemon is not running, or if its build predates the
`paste_applied` event, the subscriber simply reconnects and never
stores anything — the browser falls back to showing only the
source (`@thecanvas`).  Send to Krita will refuse to run in that
case, which is correct: no generation has been received.

The `last_pasted` payload:

    {
      "hash":     str,   # content hash from paste_image
      "b64":      str,   # base64 PNG of the layer Krita received
      "width":    int,
      "height":   int,
      "paste_x":  int,
      "paste_y":  int,
      "ts":       float, # unix time when the event arrived
    }

The browser distinguishes new pastes by `hash:ts` — a stable
`ts` is required so a repeated poll for the same paste does not
re-trigger `applyGeneratedImage`.
"""

import base64
import hashlib
import json
import socket
import threading
import time

from io import BytesIO

from PIL import Image

from . import krita_client, playground_registry


PLAYGROUND_ID   = "outpaint"
PLAYGROUND_NAME = "Outpaint"
PLAYGROUND_PORT = 8779

LAYER_SLOTS = [
    {
        "id":                "@thecanvas",
        "auto_select_layer": "thecanvas",
    },
    # Claimed only so the docker hides them from the Resources
    # section.  `managed_by_playground: True` also suppresses the
    # Playground Input row (see docker.py::_update_playgrounds).
    # The daemon still populates both unconditionally in
    # comfy_apply.py::_prepare_cropped / _prepare_unrotated — no
    # playground involvement.
    {
        "id":                    "@theselection",
        "managed_by_playground": True,
    },
    {
        "id":                    "@thecontext",
        "managed_by_playground": True,
    },
]

# The four parameter names the playground owns.  Registered with the
# daemon so they disappear from the docker's Parameters section (same
# rule as @-resources disappearing from Resources), and computed
# fresh at generation time from the current viewer/area state via
# /params.  See the module docstring, PARAM VALUES.
PARAM_SLOTS = [
    # Playground-owned, computed fresh from viewer/area state at
    # generation time.  See the module docstring, PARAM VALUES.
    "OUTPAINT_LEFT_PAD",
    "OUTPAINT_RIGHT_PAD",
    "OUTPAINT_TOP_PAD",
    "OUTPAINT_BOTTOM_PAD",
    # Hidden from the docker's Parameters section.
    #   · APIv2 and OUTPAINT are forced True — the outpaint path is
    #     always v2 + outpaint mode, so the workflow's corresponding
    #     Primitive nodes are locked on.
    #   · denoise and OUTPAINT_DENOISE are hidden but NOT owned — the
    #     playground never returns a value for them.  At generation
    #     time the daemon falls back to _active_config; the workflow
    #     Primitive default is used when nothing else has been set.
    "denoise",
    "OUTPAINT_DENOISE",
    "APIv2",
    "OUTPAINT",
]

SLOT_ID = "@thecanvas"

_DEFAULT_AREA_DIMS = [1024, 1024]
_DEFAULT_VIEWER = {
    "pxX": 51, "pxY": 51, "pxW": 922, "pxH": 922,
    "ratio": "1.00",
}


_state_lock = threading.RLock()
_state      = None
_event_bus_lock = threading.Lock()
_last_pasted: dict | None = None

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
#  /state, /version, /params, /playground_reregister
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


def handle_params(_payload):
    """Return current param values.

    See the module docstring, PARAM VALUES.  Reads `_state` under
    `_state_lock` so it never sees a half-written state, and copies
    the two pieces it cares about (`area_dims`, `viewer`) — reading
    them under the lock is what makes this safe even though the
    response construction happens afterwards.

    Returns:
      · OUTPAINT_*_PAD — four ints derived from viewer/area state.
      · APIv2, OUTPAINT — booleans forced to True.  The outpaint
        workflow always runs on the v2 proxy in outpaint mode; the
        docker hides the corresponding Primitive fields (see
        PARAM_SLOTS), so the user cannot switch them off.

    `denoise` and `OUTPAINT_DENOISE` are in PARAM_SLOTS (hidden from
    the docker) but intentionally NOT returned here — the playground
    does not own a value for them.  At generation time the daemon
    falls back to `_active_config` for missing claimed params.
    """
    with _state_lock:
        if _state is None:
            viewer = dict(_DEFAULT_VIEWER)
            area_w, area_h = _DEFAULT_AREA_DIMS
        else:
            viewer = dict(_state["viewer"])
            area_w, area_h = _state["area_dims"]

    try:
        pxX = float(viewer["pxX"])
        pxY = float(viewer["pxY"])
        pxW = float(viewer["pxW"])
        pxH = float(viewer["pxH"])
    except (TypeError, ValueError, KeyError):
        pxX = float(_DEFAULT_VIEWER["pxX"])
        pxY = float(_DEFAULT_VIEWER["pxY"])
        pxW = float(_DEFAULT_VIEWER["pxW"])
        pxH = float(_DEFAULT_VIEWER["pxH"])

    values = {
        "OUTPAINT_LEFT_PAD":   max(0, int(round(pxX))),
        "OUTPAINT_TOP_PAD":    max(0, int(round(pxY))),
        "OUTPAINT_RIGHT_PAD":  max(0, int(round(float(area_w) - pxX - pxW))),
        "OUTPAINT_BOTTOM_PAD": max(0, int(round(float(area_h) - pxY - pxH))),
        "APIv2":               True,
        "OUTPAINT":            True,
    }

    return {"success": True, "values": values}


def handle_playground_reregister(_payload):
    url = f"http://127.0.0.1:{PLAYGROUND_PORT}"
    ok = playground_registry.reregister_once(
        PLAYGROUND_ID, PLAYGROUND_NAME, url, PLAYGROUND_PORT,
        layer_slots=LAYER_SLOTS,
        param_slots=PARAM_SLOTS,
    )
    return {"success": bool(ok)}


# ---------------------------------------------------------------------------
#  Event bus subscriber
# ---------------------------------------------------------------------------

def _event_bus_subscriber_loop():
    """Connect to the daemon's event bus and stream events.

    See the module docstring, EVENT BUS SUBSCRIPTION.  Reconnects on
    any failure with a 2 s backoff.  Only `paste_applied` events are
    stored (in `_last_pasted`); everything else is discarded.
    """
    global _last_pasted
    daemon_host = "127.0.0.1"
    daemon_port = 12347

    while True:
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5.0)
            sock.connect((daemon_host, daemon_port))
            sock.settimeout(None)
            _log(f"event bus subscriber connected on "
                 f"{daemon_host}:{daemon_port}")

            buf = b""
            while True:
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
                    except Exception:
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") != "outpaint_result":
                        continue
                    payload = event.get("data") or {}
                    b64 = payload.get("b64")
                    if not b64:
                        continue
                    entry = {
                        "hash":    payload.get("hash"),
                        "b64":     b64,
                        "width":   int(payload.get("width") or 0),
                        "height":  int(payload.get("height") or 0),
                        "paste_x": int(payload.get("paste_x") or 0),
                        "paste_y": int(payload.get("paste_y") or 0),
                        "ts":      time.time(),
                    }
                    with _event_bus_lock:
                        _last_pasted = entry
                    _log(
                        f"paste_applied: "
                        f"{entry['width']}\u00d7{entry['height']} "
                        f"hash={str(entry['hash'])[:8]} "
                        f"at ({entry['paste_x']},{entry['paste_y']})"
                    )
        except (OSError, socket.timeout) as e:
            _log(f"event bus subscriber: {e}")
        finally:
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        time.sleep(2.0)


def start_event_bus_subscriber() -> None:
    """Start the event-bus subscriber thread.  Idempotent."""
    if getattr(start_event_bus_subscriber, "_started", False):
        return
    start_event_bus_subscriber._started = True
    threading.Thread(
        target=_event_bus_subscriber_loop,
        name="outpaint-event-bus-subscriber",
        daemon=True,
    ).start()


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
    if not _is_success(resp):
        return None, resp.get("message") or "daemon rejected comfy_status"
    return resp.get("data") or {}, None


def _is_success(resp):
    """Normalize a daemon response into a success boolean.

    The daemon's socket layer may return either
    `{"status": "success", ...}` or `{"success": true, ...}` depending
    on the command's return shape.  Both are treated as success.
    """
    if not isinstance(resp, dict):
        return False
    if resp.get("status") == "success":
        return True
    if resp.get("success") is True:
        return True
    return False


def handle_active_state(_payload):
    data, err = _fetch_active_state()
    if err is not None:
        return {
            "success": False,
            "daemon_reachable": False,
            "message": err,
        }
    with _event_bus_lock:
        last_pasted = dict(_last_pasted) if _last_pasted else None
    return {
        "success": True,
        "daemon_reachable": True,
        "data": data,
        "last_pasted": last_pasted,
    }


def handle_krita_status(_payload):
    return {"success": True, "reachable": krita_client.is_reachable()}


def handle_import_image(payload):
    name = (payload or {}).get("name") if isinstance(payload, dict) else None
    cmd = {"type": "get_document_composite_b64"}
    if name:
        cmd["name"] = name

    response = krita_client.send(cmd, timeout=60.0)
    if not _is_success(response):
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
        if not _is_success(response):
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


# ---------------------------------------------------------------------------
#  Generation + send-to-Krita (playground-owned receipt path)
# ---------------------------------------------------------------------------

# Generous budget: comfy_generate's poll loop has its own internal
# budgets (_MIN_EXEC_TIMEOUT_S = 300s, _MAX_TOTAL_POLL_S = 600s).  The
# socket timeout here just needs to be at least that long, otherwise
# the browser would give up before the daemon does.  We give the
# socket a 700s ceiling so it always outlasts the daemon's own cap.
_GENERATE_SOCKET_TIMEOUT_S = 700.0
_GENERATE_EXEC_TIMEOUT_S   = 600.0


def _call_generate_once():
    """One comfy_generate call, no connection preflight.

    Returns the raw daemon response dict (which may be a success or
    an error — the caller normalizes via `_is_success`).  Exceptions
    are trapped and converted to an error-shaped dict so the caller
    can uniformly inspect `message`.
    """
    try:
        return krita_client.send(
            {
                "type":     "comfy_generate",
                "timeout":  _GENERATE_EXEC_TIMEOUT_S,
                "count":    1,
                "no_paste": True,
            },
            timeout=_GENERATE_SOCKET_TIMEOUT_S,
        )
    except Exception as e:
        return {"status": "error",
                "message": f"daemon call raised: {e}"}


def _preflight_connection():
    """Try to (re)establish the daemon's ComfyUI client.

    Called from `handle_generate` only after `comfy_generate` reports
    "Not connected".  Issues `comfy_proxy_start` (idempotent — starts
    the proxy child process if not already up) then `comfy_connect`
    (idempotent — sets `_client`).

    Returns None on success, or an error string on failure.  A
    `comfy_connect` response containing "already" is treated as
    success: some daemon builds report an already-connected state
    that way, and the retry will succeed since `_client` was already
    set.
    """
    _log("preflighting connection (comfy_proxy_start + comfy_connect)")

    # Idempotent — "already running" is fine, and if it genuinely
    # fails the comfy_connect below surfaces the clearer error.
    krita_client.send({"type": "comfy_proxy_start"}, timeout=60.0)

    r = krita_client.send({"type": "comfy_connect"}, timeout=30.0)
    if _is_success(r):
        _log("preflight: comfy_connect succeeded")
        return None

    msg = ""
    if isinstance(r, dict):
        msg = (r.get("message") or "")
    if "already" in msg.lower():
        _log("preflight: comfy_connect reports 'already'; proceeding")
        return None

    _log(f"preflight: comfy_connect failed: {r}")
    return "connection preflight failed: " + (msg or "unknown error")


def handle_generate(_payload):
    """Trigger generation via the daemon with no_paste=True.

    Uses `comfy_generate`'s existing `no_paste` branch: the daemon
    prepares the workflow, submits the job, polls to completion, and
    returns the downloaded PNGs as `data.images` — WITHOUT attempting
    to paste into Krita.  This is the key difference from the docker's
    `comfy_panel_run`, which always pastes.

    Requires:
      · the outpaint workflow to be loaded in the daemon (via the
        docker, which auto-applies the config), and
      · the daemon to have a connected ComfyUI client (`_client`
        non-None).

    The second requirement is handled transparently: if the first
    `comfy_generate` fails with "Not connected", we issue
    `comfy_proxy_start` + `comfy_connect` and retry once.  See the
    module docstring, CONNECTION PREFLIGHT.
    """
    resp = _call_generate_once()

    if not _is_success(resp):
        msg = ""
        if isinstance(resp, dict):
            msg = (resp.get("message") or "")
        if "not connected" in msg.lower():
            err = _preflight_connection()
            if err is not None:
                return {"success": False, "message": err}
            _log("preflight complete; retrying comfy_generate")
            resp = _call_generate_once()

    if not _is_success(resp):
        msg = ""
        if isinstance(resp, dict):
            msg = (resp.get("message") or "")
        return {
            "success": False,
            "message": msg or "generation failed",
        }

    data = resp.get("data") or {}
    images = data.get("images") or []
    if not images:
        return {
            "success": False,
            "message": "generation succeeded but returned no images",
        }

    return {
        "success": True,
        "b64":     images[0],
        "count":   len(images),
        "message": resp.get("message") or f"generated {len(images)} image(s)",
    }


def handle_send_to_krita(payload):
    """Create a NEW Krita document from the given PNG and paste it.

    Two existing daemon commands are chained:

      1. `create_new_with_dimensions` — creates a fresh document at
         the PNG's exact pixel dimensions and adds a view for it
         (which makes it the active document).  Defaults to RGBA/U8
         at 300 DPI.

      2. `comfy_paste_image_b64` — pastes the PNG as a paint layer at
         (0, 0) into the (now-active) new document.  Since the
         document dims equal the image dims, `paste_image`'s
         bounds check passes.

    The user's original document is untouched: only the new document
    receives the paste.

    Payload: `{b64: str, width: int, height: int, name?: str}`.  `name`
    defaults to `"Outpaint <unix-ts>"` if omitted.
    """
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    b64    = payload.get("b64") or ""
    width  = int(payload.get("width")  or 0)
    height = int(payload.get("height") or 0)
    name   = (payload.get("name") or "").strip()
    if not name:
        name = f"Outpaint {int(time.time())}"

    if not b64:
        return {"success": False, "message": "b64 is required"}
    if width <= 0 or height <= 0:
        return {"success": False,
                "message": "width and height must be positive ints"}

    # Sanity: the b64 payload should actually decode.
    try:
        base64.b64decode(b64, validate=True)
    except Exception as e:
        return {"success": False,
                "message": f"b64 did not decode: {e}"}

    # ---- 1) Create the new document ----
    r_create = krita_client.send(
        {
            "type":         "create_new_with_dimensions",
            "name":         name,
            "width":        width,
            "height":       height,
            "resolution":   300,
            "color_model":  "RGBA",
            "color_depth":  "U8",
            "profile":      "",
        },
        timeout=30.0,
    )
    if not _is_success(r_create):
        return {
            "success": False,
            "message": ("create document failed: "
                        + (r_create.get("message") or "daemon rejected")),
        }

    # ---- 2) Paste into the (now-active) new document ----
    r_paste = krita_client.send(
        {
            "type":       "comfy_paste_image_b64",
            "name":       name,
            "image_b64":  b64,
            "x":          0,
            "y":          0,
            "rotation":   0.0,
            "blend_mode": "normal",
        },
        timeout=60.0,
    )
    if not _is_success(r_paste):
        return {
            "success": False,
            "message": ("paste into new document failed: "
                        + (r_paste.get("message") or "daemon rejected")),
        }

    return {
        "success":  True,
        "doc_name": name,
        "width":    width,
        "height":   height,
        "message":  f"created '{name}' ({width}\u00d7{height}) and pasted",
    }


ROUTES = {
    "/state":                 handle_state,
    "/version":               handle_version,
    "/params":                handle_params,
    "/krita_status":          handle_krita_status,
    "/active_state":          handle_active_state,
    "/resource_image":        handle_resource_image,
    "/playground_reregister": handle_playground_reregister,
    "/generate":              handle_generate,
    "/send_to_krita":         handle_send_to_krita,
}

RAW_ROUTES = {}
