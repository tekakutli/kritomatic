"""HTTP routes for the inpaint playground.

The playground is a state synchronizer and a pixel source.  It no
longer submits generation and no longer writes Krita scratch layers.

Routes
======

    POST /sync              update the playground's in-memory state
                            (canvas_b64, selection_b64, rotation,
                            context rect, dims).  No Krita writes.
    POST /resources         atomic fetch of the whole prepare-time
                            state: `{frame, canvas, selection,
                            rotation}` in one round-trip.
    POST /canvas_b64        return the cached canvas blob
                            (`{success, b64, width, height}`)
    POST /selection_b64     return the cached selection blob
                            (`{success, b64, width, height}` with
                            `b64` possibly null when no mask is
                            selected — the caller uses all-white)

    POST /version           liveness probe.  The daemon's playground
                            sweep (see `kritomatic_daemon/handlers/
                            playgrounds.py`) POSTs this every 300 s
                            and removes our registry entry if it does
                            not get an HTTP response.  Registered
                            here so the playground answers even on
                            older daemon builds that predate pgkit's
                            built-in `/version` route.  Response is
                            `{"success": true, "version": ""}` — we
                            have no version state worth advertising
                            (all mutable state is queried via
                            `/resources`, `/rotation`, and
                            `/crop_frame`).
    POST /playground_reregister  single-shot re-registration with
                            the daemon.  Called by `pg_autosync.js`
                            when it detects that the daemon has
                            removed our registry entry — the sweep
                            can misjudge liveness (see
                            `kritomatic_daemon/handlers/playgrounds.py`'s
                            SWEEP: WHAT "DEAD" MEANS), and the
                            browser's autosync is the fastest path to
                            recovery.  Response is `{"success": bool}`.
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

CANVAS_PUSH SUBSCRIBER (Slices D + E)
=====================================
Besides answering HTTP requests, this module runs a background thread
(`_canvas_subscriber_loop`) that connects to the daemon's event stream
(port 12347) and forwards two event types to every browser client
connected to the inpaint server's `/remote` WebSocket:

  · `paste_applied` (per paste, Slice E) — forwarded as a `patch` on
    the `paste.applied` field:

        {"type": "patch",
         "values": {"paste.applied": {hash, b64, paste_x, paste_y,
                                      width, height, doc_name}}}

    The browser's `applyPastedCanvas` composites the pasted PNG over
    its pre-paste canvas for optimistic display (~20-90 ms).  For the
    common inpaint case (checkbox off, no rotation) the pasted PNG
    IS the full new canvas, so the browser takes a fast-path that
    just replaces `board.imageEl` — ~20 ms, no compositing.

  · `canvas_update` (once at batch end, Slice D) — forwarded as a
    `patch` on the `canvas.pushed` field:

        {"type": "patch",
         "values": {"canvas.pushed": {hash, b64, width, height,
                                      doc_name}}}

    The browser's `applyPushedCanvas` replaces the optimistic
    composite with the authoritative `doc.projection()` output.  For
    inpaint this is visually identical to the optimistic version
    (same pixels, different PNG encoding), so no flicker; for edge
    cases (blend modes, user painting mid-batch) it corrects drift.

The two-event structure is what makes "playground updates at the
same time as Krita" possible without paying the `doc.projection()`
cost on the perceived-latency path.  See
`kritomatic_daemon/handlers/comfyui_generation.py`'s CANVAS_PUSH
module docstring section for the daemon side.

`set_browser_hub(hub)` must be called once, before
`start_canvas_subscriber()`, so the subscriber has somewhere to
forward to.  `inpaint_playground.py` does both — it creates a
`pgkit.remote_hub.Hub`, passes it to `kit_serve(hub=...)` so the
`/remote` WebSocket endpoint uses it, and calls `set_browser_hub`
with the same instance.

WHY NO KRITA SCRATCH LAYERS
===========================
The canvas and selection used to round-trip through Krita scratch
layers (`thecanvas`, `theselection`) via `comfy_set_layer_pixels` on
`/sync`, and the daemon read them from Krita.  That had three
problems:

  · On boot, autosync's first-canvas-arrival would write an
    all-white `theselection` paint layer onto the user's stack
    (visible garbage).
  · The round-trip was pure overhead; the daemon knows the doc and
    crop bounds already.
  · The scratch layers shadowed real Krita layers of the same name.

Now `/sync` is a pure memory write.  The daemon queries
`/resources` at `prepare_workflow` time and substitutes the pixels
into the graph directly (via the same `core/ASSET` rewrite
`controlnets_playground` uses) — no pull node, no Krita layer, no
boot-time artifact.

PINNED TARGET DOCUMENT
======================
Every `/krita/*` daemon call that touches Krita document state
(reading the composite, reading a named layer, reading mask layers)
falls back to `Krita.instance().activeDocument()` when no document is
named.  That fallback is fine for a single-client workflow, but breaks
when another playground steals the active tab — notably controlnets'
Push-to-Krita, which calls `create_new_with_dimensions` → `addView`,
activating `kritomatic_controlnets` mid-session.

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
Every `handle_sync` failure path (bad payload, etc.) produces one
`[inpaint] sync error: <reason>` line on the server's stdout,
alongside the JSON error response that goes back to the browser.

The success path logs `sync: cached [...] rotation=...
context=...`.  Since `/sync` no longer contacts the daemon, the only
failure modes left are payload-shape errors; transport failures are
gone.  The browser mirrors any `/sync` failure via
`flashStatus(..., "bad")` in `pg_sync.js` on the first failure of a
streak, so both sides agree on what went wrong and neither side
depends on the other for observability.  See `pg_sync.py`'s module
docstring, FAILURE MIRRORING."""

from __future__ import annotations

import base64
import json
import math
import socket
import struct
import threading
import time
from io import BytesIO
from typing import Optional

from PIL import Image

from pg_comfy import ComfyError, get_client, reset_client
from pg_comfy.image import make_encoded_image
from pgruntime.ports import port_for

from . import krita_client
from . import playground_registry


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

SYNC_TIMEOUT = 30.0

# Playground identity, matching the daemon-side registration contract.
# These live here (not in inpaint_playground.py) because
# `handle_playground_reregister` needs them, and having the server
# import from its own entry point would create a cycle.
# `inpaint_playground.py` re-imports these for its own use — see the
# module docstring, PLAYGROUND IDENTITY.
PLAYGROUND_ID   = "inpaint"
PLAYGROUND_NAME = "Inpaint"
PLAYGROUND_PORT = port_for(PLAYGROUND_ID)

LAYER_SLOTS = [
    # User-editable via the docker's Playgrounds section.
    {"id": "@thecanvas"},
    # Managed internally by the playground.  Claimed from Resources
    # so the user cannot edit them out of sync with what the
    # playground derives; not shown in the Playgrounds section.
    {"id": "@thecontext", "managed_by_playground": True},
    {"id": "@theselection", "managed_by_playground": True},
]


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
#  Canvas-push subscriber (Slices D + E)
# ---------------------------------------------------------------------------

_browser_hub = None
_subscriber_running = False
_subscriber_thread = None


def set_browser_hub(hub) -> None:
    """Register the pgkit `Hub` the canvas/paste subscribers broadcast
    through.  Must be called once, before `start_canvas_subscriber()`.

    `inpaint_playground.py` creates a `Hub()` instance, passes it to
    `kit_serve(hub=...)` (so the `/remote` WS endpoint uses it), and
    calls this with the same instance.  See the module docstring,
    CANVAS_PUSH SUBSCRIBER.
    """
    global _browser_hub
    _browser_hub = hub


def start_canvas_subscriber() -> None:
    """Spawn the background thread that subscribes to the daemon's
    event stream and forwards canvas/paste events to browsers.

    Idempotent.  See the module docstring, CANVAS_PUSH SUBSCRIBER.
    """
    global _subscriber_running, _subscriber_thread
    if _subscriber_thread is not None and _subscriber_thread.is_alive():
        return
    _subscriber_running = True
    _subscriber_thread = threading.Thread(
        target=_canvas_subscriber_loop,
        name="inpaint-canvas-subscriber",
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
    docstring, CANVAS_PUSH SUBSCRIBER.
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

    The browser's `pgkitRemoteApplyPatch` (in
    `pgkit/remote_client.js`) routes the field update to
    `canvas.pushed`'s registered `set` handler, which calls
    `applyPushedCanvas` in `pg_autosync.js`.  See the module
    docstring, CANVAS_PUSH SUBSCRIBER.
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
#  /version — liveness probe for the daemon's registry sweep
# ---------------------------------------------------------------------------

def handle_version(_payload) -> dict:
    """Liveness probe answered with no state worth versioning.

    The daemon's sweep thread POSTs `/version` at `_SWEEP_INTERVAL_S`
    (300 s) intervals to decide whether each registered playground
    is still alive.  Any 2xx response keeps the registry entry alive.
    See `kritomatic_daemon/handlers/playgrounds.py`'s SWEEP: WHAT
    "DEAD" MEANS.

    Inpaint has no version state worth advertising — all mutable
    state is queried via `/resources`, `/rotation`, `/crop_frame`.
    Controlnets registers its own `/version` that returns the
    workflow file's content hash, because its
    `ControlNetsConditioning` ComfyUI node uses that hash as an
    `IS_CHANGED` value.  Inpaint does not need anything equivalent.
    """
    return {"success": True, "version": ""}


# ---------------------------------------------------------------------------
#  /playground_reregister — recovery path when the sweep drops us
# ---------------------------------------------------------------------------

def handle_playground_reregister(_payload) -> dict:
    """Single-shot re-registration with the daemon.

    Called by `pg_autosync.js` when its poll notices that our entry
    is missing from `comfy_status`'s `playgrounds` list — the sweep
    thread can misjudge liveness (see
    `kritomatic_daemon/handlers/playgrounds.py`'s SWEEP: WHAT
    "DEAD" MEANS), and the browser's autosync is the fastest path to
    recovery.  Before this route existed, the only way to recover
    was to restart the inpaint playground process.

    Recovery latency: the next autosync tick (≤500 ms) after the
    sweep removes us, plus the ~2 ms the daemon socket round-trip
    takes on localhost.

    Fails fast on daemon unreachability — autosync reschedules on
    its own cadence, so a slow retry loop here would just make
    `/playground_reregister` block for no benefit.  See
    `playgrounds/inpaint/playground_registry.py`'s
    RE-REGISTRATION section.
    """
    url = f"http://127.0.0.1:{PLAYGROUND_PORT}"
    ok = playground_registry.reregister_once(
        PLAYGROUND_ID, PLAYGROUND_NAME, url, PLAYGROUND_PORT,
        layer_slots=LAYER_SLOTS,
    )
    return {"success": bool(ok)}


# ---------------------------------------------------------------------------
#  /sync — pure memory write (no Krita round-trip)
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

    cached = []
    if canvas_b64:
        cached.append("canvas")
    if selection_b64:
        cached.append("selection")

    _log(f"sync: cached {cached} rotation={rotation} "
         f"context={_context_state}")

    return {
        "success": True,
        "rotation": rotation,
        "context": _context_state,
        "cached": cached,
    }


# ---------------------------------------------------------------------------
#  /resources — atomic multi-field fetch (Slice C)
# ---------------------------------------------------------------------------

def handle_resources(_payload) -> dict:
    """Return every field `prepare_workflow` needs, atomically.

    One `_state_lock` acquisition covers the frame, canvas, selection,
    and rotation.  No torn reads between "read frame" and "read
    canvas": a rotate-between-calls race that the three-call sequence
    (Slice B) had is impossible here.

    Return shape (see the module docstring, /resources):

        {
          "success":   True,
          "rotation":  float,
          "frame":     {...} or None,
          "canvas":    {"b64": str, "width": int, "height": int} or None,
          "selection": {"b64": str, "width": int, "height": int} or None,
        }

    `frame` is `None` before the first canvas sync — the caller must
    fall back to the legacy un-rotated path in that case.  `canvas`
    is `None` under the same condition (see the docstring for the
    invariant that ties the two together).  `selection` is `None`
    when no mask has been selected; the caller uses all-white.
    """
    with _state_lock:
        rotation = _rotation
        # Build the frame inside the same lock so it cannot observe
        # a `_canvas_w` / `_context_state` / `_rotation` triple that
        # was not true at some single moment in time.
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
        "rotation": rotation,
        "frame": frame,
        "canvas": canvas,
        "selection": selection,
    }


# ---------------------------------------------------------------------------
#  /canvas_b64 and /selection_b64 — legacy fallback (kept for older daemons)
# ---------------------------------------------------------------------------

def handle_canvas_b64(_payload) -> dict:
    """Return the cached canvas blob.

    `b64` is the raw base64 PNG (no data URL prefix).  `b64` is
    `None` when no canvas has been synced yet.

    Kept for backward compat with daemons that predate `/resources`.
    Current daemons call `/resources` exclusively.  See the module
    docstring, /resources.
    """
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
    """Return the cached selection blob.

    `b64` is the raw base64 PNG.  `b64` is `None` when no mask has
    been selected — the caller must interpret that as "select all"
    and use an all-white mask at the context rect's extent (see
    `pg_inpaint.js::buildWhiteMaskB64`).

    Kept for backward compat with daemons that predate `/resources`.
    Current daemons call `/resources` exclusively.  See the module
    docstring, /resources.
    """
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
#  /rotation — liveness-gated value source for the daemon
# ---------------------------------------------------------------------------

def handle_rotation(_payload) -> dict:
    with _state_lock:
        rotation = _rotation
    return {"rotation": rotation}


# ---------------------------------------------------------------------------
#  /crop_frame — authoritative crop window
# ---------------------------------------------------------------------------

def _compute_crop_frame_locked() -> Optional[dict]:
    """Compute the crop frame assuming `_state_lock` is already held.

    Pulled out of `_compute_crop_frame` so `handle_resources` can
    capture the frame atomically with the canvas and selection blobs
    under a single `_state_lock` acquisition.  Everything below
    assumes the caller holds `_state_lock` and reads the module-level
    state directly.
    """
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


def _compute_crop_frame() -> Optional[dict]:
    """Compute the crop frame under `_state_lock`.

    Thin wrapper that acquires the lock for callers that only need
    the frame (not the canvas or selection).  `handle_resources`
    calls `_compute_crop_frame_locked` directly so it can also read
    the blobs under the same lock.
    """
    with _state_lock:
        return _compute_crop_frame_locked()


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
    "/version":              handle_version,
    "/playground_reregister": handle_playground_reregister,
    "/sync":                 handle_sync,
    "/resources":            handle_resources,
    "/canvas_b64":           handle_canvas_b64,
    "/selection_b64":        handle_selection_b64,
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
