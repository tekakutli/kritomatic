"""HTTP routes for the rmbg playground.

The playground is a state synchronizer and a pixel source for
the daemon's rmbg workflow.  No rotation, no inpaint-specific
logic — just full-canvas pixels, a boundary rect that says
"where the non-transparent content lives", and a context rect
that says "where to operate within that boundary".

Routes
======

    POST /sync              update in-memory state
                            (canvas_b64, selection_b64, boundary_*,
                            context_*).  No Krita writes.
    POST /resources         atomic fetch of prepare-time state
                            `{frame, canvas, selection}`.
    POST /canvas_b64        cached canvas blob
    POST /selection_b64     cached selection blob
    POST /version           liveness probe for daemon registry sweep
    POST /playground_reregister  re-register with the daemon
    POST /crop_frame        authoritative crop frame the daemon
                            applies to @thecanvas before submission
    POST /comfy/status      ComfyUI preflight
    POST /comfy/active_state  daemon `comfy_status`
    POST /krita/status      daemon reachable?
    POST /krita/import      fetch active document composite
    POST /krita/import_masks fetch masks
    POST /krita/resource_image resolve a {source, layer_name} spec
    POST /rebind_document   clear pinned target document

CROP FRAME (THE KEY DIFFERENCE FROM INPAINT)
============================================
The frame is `boundary ∩ context_rect` (when context is
enabled) or `boundary` alone (when disabled).

  · `boundary` is the tight non-transparent bbox the client
    computed from the loaded canvas.

  · `context_rect` is the user's rectangle, translated to full-
    image coords.  It's guaranteed within the boundary by the
    client (drag math clamps), so the intersection reduces to
    the context rect in the common case — the intersection is
    defensive.

The frame's `cropX/cropY` become the daemon's paste position
after generation — results land exactly where the user drew
their context rectangle (or at the boundary's origin when the
checkbox is off).

NO ROTATION
===========
Frame always has `theta: 0`.  The daemon takes the non-rotated
path unconditionally.  Inpaint's rotated-prepare code path is
never exercised by rmbg's requests.

CANVAS_PUSH SUBSCRIBER
======================
Same as inpaint's — a background thread reads the daemon's
event bus and forwards `paste_applied` and `canvas_update`
events to the browser's `/remote` WebSocket.  See inpaint's
`inpaint_server.py` module docstring for the full rationale.

PINNED TARGET DOCUMENT
======================
Same as inpaint's — pin once from `data.document`, re-pin on
"document not found", `/rebind_document` escape hatch."""

from __future__ import annotations

import base64
import math
import struct
import threading
from io import BytesIO
from typing import Optional

from PIL import Image

from pg_comfy import ComfyError, get_client, reset_client
from pgruntime import daemon_client as krita_client
from pgruntime import event_bus
from pgruntime.daemon_registry import reregister_handler
from pgruntime.daemon_routes import daemon_route_handlers
from pgruntime.ports import port_for


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

SYNC_TIMEOUT = 30.0


PLAYGROUND_ID   = "rmbg"
PLAYGROUND_NAME = "RMBG"
PLAYGROUND_PORT = port_for(PLAYGROUND_ID)

LAYER_SLOTS = [
    {"id": "@thecanvas"},
    {"id": "@thecontext",   "managed_by_playground": True},
    {"id": "@theselection", "managed_by_playground": True},
]


def _log(msg: str) -> None:
    print(f"[rmbg] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  State
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()

_canvas_b64:    str = ""
_canvas_w:      int = 0
_canvas_h:      int = 0
_selection_b64: str = ""
_boundary_state: dict = {"x": 0, "y": 0, "w": 0, "h": 0}
_context_state:  dict = {
    "enabled": False, "x": 0, "y": 0, "w": 0, "h": 0,
}

_target_document_name: str = ""


# ---------------------------------------------------------------------------
#  Canvas-push subscriber
# ---------------------------------------------------------------------------

_browser_hub = None


def set_browser_hub(hub) -> None:
    global _browser_hub
    _browser_hub = hub


def start_canvas_subscriber() -> None:
    event_bus.subscribe(
        {
            "paste_applied": _forward_paste_applied,
            "canvas_update": _forward_canvas_update,
        },
        log_prefix="rmbg",
    )


def _forward_paste_applied(data: dict) -> None:
    if _browser_hub is None or not isinstance(data, dict):
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
    if _browser_hub is None or not isinstance(data, dict):
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


# ---------------------------------------------------------------------------
#  Pinned-document helpers
# ---------------------------------------------------------------------------

def _capture_document_from_response(resp) -> None:
    global _target_document_name
    if not isinstance(resp, dict) or not resp.get("success"):
        return
    with _state_lock:
        if _target_document_name:
            return
        data = resp.get("data") or {}
        doc = data.get("document")
        if isinstance(doc, str) and doc:
            _target_document_name = doc
            _log(f"pinned target document: {doc!r}")


def _send_with_pin(command: dict, *,
                   doc_arg: str = "document",
                   timeout: float = 30.0) -> dict:
    global _target_document_name

    with _state_lock:
        pin = _target_document_name

    if pin:
        payload = {**command, doc_arg: pin}
        resp = krita_client.send(payload, timeout=timeout)
        msg = (resp.get("message") or "").lower()
        stale = (not resp.get("success")) and ("document" in msg)
        if not stale:
            _capture_document_from_response(resp)
            return resp
        with _state_lock:
            if _target_document_name == pin:
                _target_document_name = ""
                _log(f"pin {pin!r} stale; rebinding to active")

    resp = krita_client.send(command, timeout=timeout)
    _capture_document_from_response(resp)
    return resp


# ---------------------------------------------------------------------------
#  PNG helpers
# ---------------------------------------------------------------------------

def _png_dims_from_b64(b64: str) -> tuple[int, int]:
    try:
        raw = base64.b64decode(b64[:64] if len(b64) > 64 else b64)
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        w, h = struct.unpack(">II", raw[16:24])
        return (int(w), int(h))
    except Exception:
        return (0, 0)


# ---------------------------------------------------------------------------
#  /version
# ---------------------------------------------------------------------------

def handle_version(_payload) -> dict:
    return {"success": True, "version": ""}


handle_playground_reregister = reregister_handler(
    PLAYGROUND_ID, PLAYGROUND_NAME, PLAYGROUND_PORT,
    layer_slots=LAYER_SLOTS,
)


# ---------------------------------------------------------------------------
#  /sync
# ---------------------------------------------------------------------------

def handle_sync(payload: dict) -> dict:
    global _canvas_b64, _canvas_w, _canvas_h
    global _selection_b64, _boundary_state, _context_state

    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")

    try:
        boundary = {
            "x": int(payload.get("boundary_x", 0)),
            "y": int(payload.get("boundary_y", 0)),
            "w": int(payload.get("boundary_w", 0)),
            "h": int(payload.get("boundary_h", 0)),
        }
    except (TypeError, ValueError):
        boundary = {"x": 0, "y": 0, "w": 0, "h": 0}

    try:
        context_state = {
            "enabled": bool(payload.get("context_enabled", False)),
            "x": int(payload.get("context_x", 0)),
            "y": int(payload.get("context_y", 0)),
            "w": int(payload.get("context_w", 0)),
            "h": int(payload.get("context_h", 0)),
        }
    except (TypeError, ValueError):
        context_state = {
            "enabled": False, "x": 0, "y": 0, "w": 0, "h": 0,
        }

    with _state_lock:
        if canvas_b64:
            _canvas_b64 = canvas_b64
            _canvas_w, _canvas_h = _png_dims_from_b64(canvas_b64)
        if selection_b64:
            _selection_b64 = selection_b64

        _boundary_state = boundary
        _context_state  = context_state

    cached = []
    if canvas_b64:    cached.append("canvas")
    if selection_b64: cached.append("selection")

    _log(f"sync: cached {cached} "
         f"boundary={_boundary_state} context={_context_state}")

    return {
        "success": True,
        "boundary": _boundary_state,
        "context": _context_state,
        "cached": cached,
    }


# ---------------------------------------------------------------------------
#  /resources
# ---------------------------------------------------------------------------

def handle_resources(_payload) -> dict:
    with _state_lock:
        frame = _compute_crop_frame_locked()
        canvas_b64 = _canvas_b64 or None
        canvas_w = _canvas_w
        canvas_h = _canvas_h
        selection_b64 = _selection_b64 or None

    canvas = None
    if canvas_b64 is not None:
        canvas = {
            "b64": canvas_b64,
            "width": canvas_w,
            "height": canvas_h,
        }

    selection = None
    if selection_b64 is not None:
        selection = {
            "b64": selection_b64,
            "width": canvas_w,
            "height": canvas_h,
        }

    return {
        "success": True,
        "frame": frame,
        "canvas": canvas,
        "selection": selection,
    }


# ---------------------------------------------------------------------------
#  /canvas_b64 and /selection_b64
# ---------------------------------------------------------------------------

def handle_canvas_b64(_payload) -> dict:
    with _state_lock:
        b64 = _canvas_b64 or None
        w = _canvas_w
        h = _canvas_h
    return {
        "success": True,
        "b64": b64,
        "width": w,
        "height": h,
    }


def handle_selection_b64(_payload) -> dict:
    with _state_lock:
        b64 = _selection_b64 or None
        w = _canvas_w
        h = _canvas_h
    return {
        "success": True,
        "b64": b64,
        "width": w,
        "height": h,
    }


# ---------------------------------------------------------------------------
#  /crop_frame — authoritative crop window
# ---------------------------------------------------------------------------

def _frame_from_rect(x: int, y: int, w: int, h: int) -> dict:
    return {
        "theta": 0,
        "W": _canvas_w,
        "H": _canvas_h,
        "Wp": _canvas_w,
        "Hp": _canvas_h,
        "cropX": int(x),
        "cropY": int(y),
        "cropW": int(w),
        "cropH": int(h),
        "inpaintEnabled": True,
        "boundary": dict(_boundary_state),
        "contextRect": [
            _context_state.get("x", 0),
            _context_state.get("y", 0),
            _context_state.get("w", 0),
            _context_state.get("h", 0),
        ],
    }


def _compute_crop_frame_locked() -> Optional[dict]:
    """The frame the daemon crops @thecanvas to.

    - If no canvas yet: None (daemon falls back to legacy path).
    - If boundary is degenerate: full canvas.
    - If context enabled: boundary ∩ context_rect (context is
      normally within boundary, so this reduces to the context
      rect; the intersection is defensive).
    - If context disabled: boundary.
    """
    W = _canvas_w
    H = _canvas_h
    if W <= 0 or H <= 0:
        return None

    b = dict(_boundary_state)
    if b["w"] <= 0 or b["h"] <= 0:
        b = {"x": 0, "y": 0, "w": W, "h": H}

    ctx = dict(_context_state)
    use_ctx = (
        ctx.get("enabled")
        and ctx.get("w", 0) > 0
        and ctx.get("h", 0) > 0
    )

    if not use_ctx:
        return _frame_from_rect(b["x"], b["y"], b["w"], b["h"])

    # Intersect boundary ∩ context_rect
    x0 = max(b["x"], ctx["x"])
    y0 = max(b["y"], ctx["y"])
    x1 = min(b["x"] + b["w"], ctx["x"] + ctx["w"])
    y1 = min(b["y"] + b["h"], ctx["y"] + ctx["h"])

    if x1 <= x0 or y1 <= y0:
        # Empty intersection — fall back to boundary.  This should
        # never fire because the client clamps context to boundary,
        # but if it does, boundary is always a valid crop.
        return _frame_from_rect(b["x"], b["y"], b["w"], b["h"])

    return _frame_from_rect(x0, y0, x1 - x0, y1 - y0)


def handle_crop_frame(_payload) -> dict:
    with _state_lock:
        frame = _compute_crop_frame_locked()
    return {"success": True, "frame": frame}


# ---------------------------------------------------------------------------
#  /comfy/status
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
#  /rebind_document
# ---------------------------------------------------------------------------

def handle_rebind_document(_payload) -> dict:
    global _target_document_name
    with _state_lock:
        old = _target_document_name
        _target_document_name = ""

    try:
        resp = krita_client.send(
            {"type": "get_document_composite_b64"}, timeout=30.0,
        )
        _capture_document_from_response(resp)
    except Exception:
        pass

    with _state_lock:
        new = _target_document_name

    _log(f"rebind: was {old!r}, now {new!r}")
    return {
        "success": True,
        "message": f"Rebind: was {old!r}, now {new!r}",
        "data": {"previous": old, "current": new},
    }


# ---------------------------------------------------------------------------
#  Shared daemon routes with pinned-document dispatch
# ---------------------------------------------------------------------------

def _pinned_send(command: dict, *, timeout: float = 60.0) -> dict:
    ctype = command.get("type")
    if ctype == "get_document_composite_b64":
        if command.get("name"):
            resp = krita_client.send(command, timeout=timeout)
            _capture_document_from_response(resp)
            return resp
        return _send_with_pin(command, doc_arg="name", timeout=timeout)
    if ctype == "comfy_status":
        return krita_client.send(command, timeout=timeout)
    return _send_with_pin(command, doc_arg="document", timeout=timeout)


_h = daemon_route_handlers(send=_pinned_send)


ROUTES = {
    "/version":              handle_version,
    "/playground_reregister": handle_playground_reregister,
    "/sync":                 handle_sync,
    "/resources":            handle_resources,
    "/canvas_b64":           handle_canvas_b64,
    "/selection_b64":        handle_selection_b64,
    "/crop_frame":           handle_crop_frame,
    "/comfy/status":         handle_status,
    "/comfy/active_state":   _h["active_state"],
    "/krita/status":         _h["krita_status"],
    "/krita/import":         _h["import_image"],
    "/krita/import_masks":   _h["import_masks"],
    "/krita/resource_image": _h["resource_image"],
    "/rebind_document":      handle_rebind_document,
}

RAW_ROUTES = {}