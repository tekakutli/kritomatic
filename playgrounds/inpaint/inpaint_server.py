"""HTTP routes for the inpaint playground.

The playground is a state synchronizer.  It no longer submits
generation — the docker's Run button does.

Routes
======

    POST /sync              push pixel state (canvas + selection) into
                            Krita scratch layers; also store rotation,
                            context rect, and canvas dims for
                            `/rotation` and `/crop_frame`
    POST /rotation          return the last rotation pushed via /sync
    POST /crop_frame        authoritative crop frame the daemon applies
                            to @thecanvas before submission; multimask
                            consumes the same frame to transform its
                            masks.  Everything downstream (canvas,
                            selection, context mask, multimask area
                            masks) ends up at the same cropW × cropH.
    POST /comfy/status      ComfyUI preflight
    POST /comfy/active_state  proxy the daemon's `comfy_status`

    POST /krita/status      kritomatic daemon reachable?
    POST /krita/import      fetch active document composite from Krita
    POST /krita/import_masks  fetch selected mask or paint layer(s)
    POST /krita/resource_image  resolve a `{source, layer_name}` spec
                                to a PNG data URL
    POST /rebind_document   clear the pinned target document and
                            re-capture from the daemon's activeDocument()

PINNED TARGET DOCUMENT
======================
Every daemon call that touches Krita document state (writing scratch
layers, reading the composite, reading a named layer, reading mask
layers) falls back to `Krita.instance().activeDocument()` when no
document is named.  That fallback is fine for a single-client
workflow, but breaks when another playground steals the active tab —
notably controlnets' Push-to-Krita, which calls
`create_new_with_dimensions` → `addView`, activating
`kritomatic_controlnets` mid-session.

This module pins the target document on the first daemon response
that reports one, and passes the pin on every subsequent call:

    · `_capture_document_from_response` pins once, from
      `data.document` (every doc-targeting command returns it).
    · `_send_with_pin` injects `command[doc_arg] = pin` on every
      subsequent call.
    · A "document … not found" response clears the pin and retries
      unpinned once, re-capturing from `activeDocument()` — the
      self-healing case: user closed the pinned doc, opened another.
    · `/rebind_document` is the manual escape hatch: user switched
      Krita tabs back to the doc they want inpaint to follow, clicks
      "⟲ Rebind doc", the pin re-captures.

Layer-missing errors ("Layer not found: …") do NOT trigger a rebind —
those are real content errors, not stale-doc errors, and rebinding on
them would mask them.

CROP FRAME
==========
`/crop_frame` returns `{theta, W, H, Wp, Hp, cropX, cropY, cropW,
cropH, inpaintEnabled, contextRect}`.  It is the definitive answer
to "what does the daemon crop @thecanvas to."  The math:

  · theta = 0: crop = the effective context rect clamped to the
    canvas.  The effective context rect is the user's drawn rect when
    the checkbox is on; when the checkbox is off, `handle_sync` has
    synthesized a full-canvas rect `(0, 0, W, H)`, so the crop is the
    whole canvas.

  · theta ≠ 0: rotate the frame to W'×H'; translate the effective
    context rect into the rotated frame by
    ((W'-W)//2 + cx, (H'-H)//2 + cy).  The translated rect's
    axis-aligned position in the rotated frame matches the *visual*
    position of the rect over the rotated image, so corners of the
    diamond are fully coverable by a crop that reaches them.  Crops
    that extend past the W'×H' frame are honoured — the daemon
    extends the canvas to cover them.  This is a pure translation,
    NOT a rotation of the context rect.

    When the checkbox is off, the effective context rect is the full
    canvas (0, 0, W, H), so the crop is the central W×H region of the
    W'×H' rotated frame — the same as if the user had drawn a
    context rect covering the whole canvas at θ=0 and rotated.

`inpaintEnabled` is always True once a canvas has been synced — the
synthesized full-canvas rect makes the "disabled" branch
structurally identical to the "enabled" branch.  Before any canvas
has been synced (empty state), `/crop_frame` returns
`{success: true, frame: null}` and the daemon falls back to its
legacy un-rotated path.

CONTEXT RECT QUERY
==================
The context rect used for cropping is:

  · The user's rect, when the checkbox is on.
  · A synthesized full-canvas rect (0, 0, W, H) when the checkbox
    is off — i.e. "inpaint the whole canvas."  This makes the
    checkbox a pure "restrict the inpaint area" toggle: off means
    "inpaint everything," not "fall back to whatever @thecontext
    layer happens to contain."
  · Before any canvas has been synced, W and H are 0, so no rect
    can be synthesized; `/crop_frame` returns None and the daemon
    falls back to its legacy path.

Both `/sync` and `/crop_frame` are liveness-gated: kill the playground
server, and the daemon's calls fail, falls back to legacy behavior
(no rotation, no context rect).

SYNC FAILURE LOGGING
====================
Every `handle_sync` failure path produces one `[inpaint] sync error:
<reason>` line on the server's stdout, alongside the JSON error
response that goes back to the browser:

  · transport exception       → `sync error: <exception>` (pre-existing)
  · daemon did not respond    → `sync error: daemon did not respond`
  · daemon returned error     → `sync error: daemon reported: <msg>`
  · one or more sub-commands  → `sync error: <cmd>: <msg>[; ...]`
    failed

The success path already logs `sync: pushed [...] rotation=...
context=...`.  Failure logging completes the picture so a terminal
watching the inpaint server sees *why* pushes stopped landing —
previously the transport-exception path was the only one that logged,
and the three response-side failure modes were silent.

The browser mirrors the same error via `flashStatus(..., "bad")` in
`pg_sync.js` on the first failure of a streak, so both sides agree on
what went wrong and neither side depends on the other for
observability.  See `pg_sync.py`'s module docstring, FAILURE
MIRRORING."""

from __future__ import annotations

import base64
import math
import struct
import threading
import time
from io import BytesIO
from typing import Optional

from PIL import Image

from pg_comfy import ComfyError, get_client, reset_client
from pg_comfy.image import make_encoded_image

from . import krita_client


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

SYNC_TIMEOUT = 30.0


def _log(msg: str) -> None:
    print(f"[inpaint] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  State — rotation, context rect, canvas dims, selection b64,
#          pinned target document.
# ---------------------------------------------------------------------------

_state_lock = threading.Lock()
_rotation: float = 0.0
_context_state: dict = {
    "enabled": False, "x": 0, "y": 0, "w": 0, "h": 0,
}
_canvas_w: int = 0
_canvas_h: int = 0
_canvas_b64: str = ""
_selection_b64: str = ""

# Pinned target document.  Empty string means "not yet captured".
# Set on the first daemon response that reports a document name;
# cleared on "document … not found" (stale pin — doc was closed) so
# the next call re-captures from activeDocument().
#
# Without this, every inpaint → daemon call falls back to Krita's
# activeDocument(), which controlnets Push-to-Krita steals by calling
# create_new_with_dimensions → addView.  See the module docstring,
# PINNED TARGET DOCUMENT.
_target_document_name: str = ""


# ---------------------------------------------------------------------------
#  Pinned-document helpers
# ---------------------------------------------------------------------------

def _capture_document_from_response(resp) -> None:
    """Pin `_target_document_name` from a successful daemon response.

    Only fires once — subsequent responses are ignored so a later doc
    switch (e.g. controlnets Push) does not move the pin.  Reads the
    unambiguous `data.document` field; every document-targeting
    daemon command returns it.
    """
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
    """Send a daemon command targeting the pinned document.

    - No pin yet: send `command` unchanged (daemon uses activeDocument);
      capture the pin from the response.
    - Pin set: inject `command[doc_arg] = pin`; on a "document … not
      found" / "matching document" error, clear the pin and retry once
      without the hint.  The retry's response re-pins against
      activeDocument().
    """
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
        # Fall through to unpinned retry.

    resp = krita_client.send(command, timeout=timeout)
    _capture_document_from_response(resp)
    return resp


def _send_sync_batch(base_commands: list) -> "dict | None":
    """Send a sync batch with the pinned document injected into every
    command.  Fresh dicts each call so a stale-pin retry does not
    reuse previously-injected commands.
    """
    with _state_lock:
        doc_name = _target_document_name
    if doc_name:
        cmds = [{**c, "document": doc_name} for c in base_commands]
    else:
        cmds = [{**c} for c in base_commands]
    batch = {"id": f"sync_{int(time.time() * 1000)}", "commands": cmds}
    return krita_client.send_batch(batch, timeout=SYNC_TIMEOUT)


def _batch_failed_on_stale_document(summary) -> bool:
    """True iff `summary` reports an error whose message contains the
    word 'document' — the doc-missing signature from
    comfy_set_layer_pixels / get_layer_pixels_b64 /
    get_document_composite_b64.  Layer-missing errors ("Layer not
    found: …") do not contain 'document', so a legitimate layer
    miss will not trigger a rebind.
    """
    if not isinstance(summary, dict):
        return False
    if (summary.get("status") == "batch_complete"
            and not summary.get("failed")):
        return False
    top = (summary.get("message") or "").lower()
    if "document" in top:
        return True
    for r in summary.get("results", []) or []:
        if r.get("status") != "error":
            continue
        if "document" in (r.get("message") or "").lower():
            return True
    return False


# ---------------------------------------------------------------------------
#  PNG helpers
# ---------------------------------------------------------------------------

def _png_dims_from_b64(b64: str) -> tuple[int, int]:
    """Return (W, H) from a base64 PNG, using only the IHDR header."""
    try:
        raw = base64.b64decode(b64[:64] if len(b64) > 64 else b64)
        if len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n":
            return (0, 0)
        w, h = struct.unpack(">II", raw[16:24])
        return (int(w), int(h))
    except Exception:
        return (0, 0)


def _decode_bbox(b64: str) -> Optional[tuple[int, int, int, int]]:
    """Return the tight bbox of non-zero pixels in a grayscale PNG."""
    try:
        raw = base64.b64decode(b64)
        img = Image.open(BytesIO(raw)).convert("L")
        bbox = img.getbbox()
        if bbox is None:
            return None
        x0, y0, x1, y1 = bbox
        return (x0, y0, x1 - x0, y1 - y0)
    except Exception:
        return None


# ---------------------------------------------------------------------------
#  Daemon probes
# ---------------------------------------------------------------------------

def _fetch_active_state() -> dict:
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception:
        return {}
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return {}
    return resp.get("data") or {}


def handle_active_state(_payload) -> dict:
    data = _fetch_active_state()
    return {"success": True, "data": data}


# ---------------------------------------------------------------------------
#  /sync — push pixel state + rotation + context
# ---------------------------------------------------------------------------

def handle_sync(payload: dict) -> dict:
    global _rotation, _context_state, _canvas_w, _canvas_h
    global _canvas_b64, _selection_b64

    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    try:
        rotation = float(payload.get("rotation", 0.0)) % 360.0
    except (TypeError, ValueError):
        rotation = 0.0

    context_state = {
        "enabled": bool(payload.get("context_enabled", False)),
        "x": int(payload.get("context_x", 0)),
        "y": int(payload.get("context_y", 0)),
        "w": int(payload.get("context_w", 0)),
        "h": int(payload.get("context_h", 0)),
    }

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")

    with _state_lock:
        _rotation = rotation
        if canvas_b64:
            _canvas_b64 = canvas_b64
            _canvas_w, _canvas_h = _png_dims_from_b64(canvas_b64)
        if selection_b64:
            _selection_b64 = selection_b64

        # Effective context state.
        #
        # When the checkbox is on, the user's rect is authoritative.
        # When it is off, synthesize a full-canvas rect (0, 0, W, H)
        # so the disabled branch behaves identically to "checkbox on
        # with a context rectangle covering the whole canvas."  This
        # makes the checkbox a pure "restrict the inpaint area"
        # toggle: off means "inpaint everything", not "fall back to
        # whatever @thecontext layer happens to contain".
        #
        # Before the first canvas has been synced, _canvas_w/_canvas_h
        # are 0, so no rect can be synthesized; _compute_crop_frame
        # returns None in that case and the daemon falls back to its
        # legacy path.
        if context_state["enabled"]:
            _context_state = context_state
        elif _canvas_w > 0 and _canvas_h > 0:
            _context_state = {
                "enabled": True,
                "x": 0, "y": 0,
                "w": _canvas_w, "h": _canvas_h,
            }
        else:
            _context_state = context_state

    base_commands = []
    if canvas_b64:
        base_commands.append({
            "type": "comfy_set_layer_pixels",
            "name": "thecanvas",
            "image_b64": canvas_b64,
            "is_mask": False,
        })
    if selection_b64:
        base_commands.append({
            "type": "comfy_set_layer_pixels",
            "name": "theselection",
            "image_b64": selection_b64,
            "is_mask": True,
        })

    if not base_commands:
        return {"success": True,
                "rotation": rotation,
                "context": context_state,
                "pushed": []}

    try:
        summary = _send_sync_batch(base_commands)
    except Exception as e:
        _log(f"sync error: {e}")
        return {"success": False,
                "message": f"daemon send failed: {e}",
                "rotation": rotation,
                "context": context_state}

    if _batch_failed_on_stale_document(summary):
        with _state_lock:
            _target_document_name = ""
        _log("sync: pin was stale; retried against activeDocument()")
        try:
            summary = _send_sync_batch(base_commands)
        except Exception as e:
            _log(f"sync error: {e}")
            return {"success": False,
                    "message": f"daemon send failed: {e}",
                    "rotation": rotation,
                    "context": context_state}

    if summary is None:
        _log("sync error: daemon did not respond")
        return {"success": False,
                "message": "daemon did not respond",
                "rotation": rotation,
                "context": context_state}

    if summary.get("status") == "error":
        msg = summary.get("message", "daemon error")
        _log(f"sync error: daemon reported: {msg}")
        return {"success": False,
                "message": msg,
                "rotation": rotation,
                "context": context_state}

    failed = int(summary.get("failed", 0) or 0)
    if failed:
        msgs = []
        for r in summary.get("results", []) or []:
            if r.get("status") == "error":
                msgs.append(f"{r.get('command')}: {r.get('message')}")
        joined = "; ".join(msgs) or f"{failed} command(s) failed"
        _log(f"sync error: {joined}")
        return {"success": False,
                "message": joined,
                "rotation": rotation,
                "context": context_state}

    pushed = [c["name"] for c in base_commands]
    _log(f"sync: pushed {pushed} rotation={rotation} "
         f"context={context_state}")
    return {"success": True,
            "rotation": rotation,
            "context": context_state,
            "pushed": pushed}


# ---------------------------------------------------------------------------
#  /rotation — liveness-gated value source for the daemon
# ---------------------------------------------------------------------------

def handle_rotation(_payload) -> dict:
    with _state_lock:
        rotation = _rotation
    return {"rotation": rotation}


# ---------------------------------------------------------------------------
#  /crop_frame — authoritative crop window
# ---------------------------------------------------------------------------

def _compute_crop_frame() -> Optional[dict]:
    with _state_lock:
        W = _canvas_w
        H = _canvas_h
        theta = _rotation
        ctx = dict(_context_state)

    if W <= 0 or H <= 0:
        return None

    # The effective context is always populated after the first canvas
    # sync — handle_sync synthesizes a full-canvas rect when the
    # checkbox is off.  Defensive fallback: if it's somehow not set,
    # treat the whole canvas as the context, matching the synthesized
    # rect's semantics.
    if (ctx.get("enabled")
            and ctx.get("w", 0) > 0
            and ctx.get("h", 0) > 0):
        context_rect = (int(ctx["x"]), int(ctx["y"]),
                        int(ctx["w"]), int(ctx["h"]))
    else:
        context_rect = (0, 0, W, H)

    if theta == 0:
        x0 = max(0, context_rect[0])
        y0 = max(0, context_rect[1])
        x1 = min(W, context_rect[0] + context_rect[2])
        y1 = min(H, context_rect[1] + context_rect[3])
        if x1 > x0 and y1 > y0:
            return {
                "theta": 0, "W": W, "H": H, "Wp": W, "Hp": H,
                "cropX": x0, "cropY": y0,
                "cropW": x1 - x0, "cropH": y1 - y0,
                "inpaintEnabled": True,
                "contextRect": list(context_rect),
            }
        # Degenerate rect after clamping (shouldn't happen): fall back
        # to full canvas.
        return {
            "theta": 0, "W": W, "H": H, "Wp": W, "Hp": H,
            "cropX": 0, "cropY": 0, "cropW": W, "cropH": H,
            "inpaintEnabled": True,
            "contextRect": [0, 0, W, H],
        }

    rad = math.radians(theta)
    cos_a, sin_a = abs(math.cos(rad)), abs(math.sin(rad))
    Wp = max(1, round(W * cos_a + H * sin_a))
    Hp = max(1, round(W * sin_a + H * cos_a))

    cx, cy, cw, ch = context_rect
    cropX = round((Wp - W) / 2) + cx
    cropY = round((Hp - H) / 2) + cy
    cropW = cw
    cropH = ch

    return {
        "theta": theta, "W": W, "H": H, "Wp": Wp, "Hp": Hp,
        "cropX": cropX, "cropY": cropY, "cropW": cropW, "cropH": cropH,
        "inpaintEnabled": True,
        "contextRect": list(context_rect),
    }


def handle_crop_frame(_payload) -> dict:
    frame = _compute_crop_frame()
    return {"success": True, "frame": frame}


# ---------------------------------------------------------------------------
#  /comfy/status — unchanged
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
#  /krita/* — unchanged
# ---------------------------------------------------------------------------

def handle_krita_status(_payload) -> dict:
    reachable = krita_client.is_reachable()
    return {"success": True, "reachable": reachable}


def handle_krita_import(payload: dict) -> dict:
    name = payload.get("name") if isinstance(payload, dict) else None

    if name:
        # Explicit caller-supplied name wins over the pin.
        command = {"type": "get_document_composite_b64", "name": name}
        response = krita_client.send(command, timeout=60.0)
        _capture_document_from_response(response)
    else:
        # `get_document_composite_b64` uses `--name` for the doc arg,
        # not `--document`.
        response = _send_with_pin(
            {"type": "get_document_composite_b64"},
            doc_arg="name",
            timeout=60.0,
        )

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
    except Exception as e:
        return {"success": False, "message": f"could not decode base64: {e}"}

    try:
        encoded = make_encoded_image(png_bytes)
    except Exception as e:
        return {"success": False, "message": f"could not decode image: {e}"}

    h = encoded.content_hash
    filename = data.get("name") or "krita_composite"

    return {
        "success": True,
        "hash": h,
        "width": encoded.width,
        "height": encoded.height,
        "data_url": data_url,
        "filename": filename,
    }


def handle_krita_import_masks(payload: dict) -> dict:
    command = {"type": "import_krita_masks_b64"}
    if isinstance(payload, dict):
        if "layer_names" in payload and payload["layer_names"]:
            command["layer_names"] = str(payload["layer_names"])
        if "include_paint_alpha" in payload:
            command["include_paint_alpha"] = bool(payload["include_paint_alpha"])
        if "prefer_selection_mask_children" in payload:
            command["prefer_selection_mask_children"] = bool(
                payload["prefer_selection_mask_children"]
            )

    response = _send_with_pin(command, timeout=60.0)
    if response.get("status") != "success":
        return {
            "success": False,
            "message": response.get("message") or "daemon rejected",
        }

    data = response.get("data") or {}
    masks = data.get("masks") or []
    skipped = data.get("skipped") or []
    selection_path = data.get("selection_path") or "unknown"

    return {
        "success": True,
        "masks": masks,
        "skipped": skipped,
        "selection_path": selection_path,
        "message": response.get("message") or f"{len(masks)} mask(s)",
    }


def handle_resource_image(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    source = payload.get("source", "active")
    layer_name = payload.get("layer_name")

    if source == "active":
        return handle_krita_import({})
    if source == "layer":
        if not layer_name:
            return {"success": False,
                    "message": "layer_name is required when source=layer"}
        response = _send_with_pin(
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
        "message": (
            f"source={source!r} cannot be displayed as an image "
            f"(@thecanvas expects 'active' or 'layer')."
        ),
    }


# ---------------------------------------------------------------------------
#  /rebind_document — manual escape hatch
# ---------------------------------------------------------------------------

def handle_rebind_document(_payload) -> dict:
    """Clear the pinned target document and re-capture from the
    daemon's activeDocument().

    Use case: user had controlnets active when inpaint booted, so the
    pin points at `kritomatic_controlnets`.  After manually switching
    back to their real doc, this command re-pins to the doc they are
    now looking at.

    Proactively does one fetch so the pin is populated immediately —
    otherwise the user would have to trigger an autosync spec change
    or a manual Refresh before the pin lands.
    """
    global _target_document_name
    with _state_lock:
        old = _target_document_name
        _target_document_name = ""

    try:
        # get_document_composite_b64 returns data.document on success,
        # which _capture_document_from_response will pin.
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


ROUTES = {
    "/sync":                 handle_sync,
    "/rotation":             handle_rotation,
    "/crop_frame":           handle_crop_frame,
    "/comfy/status":         handle_status,
    "/comfy/active_state":   handle_active_state,
    "/krita/status":         handle_krita_status,
    "/krita/import":         handle_krita_import,
    "/krita/import_masks":   handle_krita_import_masks,
    "/krita/resource_image": handle_resource_image,
    "/rebind_document":      handle_rebind_document,
}

RAW_ROUTES = {}
