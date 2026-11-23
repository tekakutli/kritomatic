"""Shared Krita-daemon proxy route handlers for the playgrounds.

Every playground that talks to the daemon exposes some subset of
these routes to its browser client:

    /active_state       — daemon `comfy_status` (config, active_batches,
                          playground registry)
    /krita_status       — daemon reachable?  Cheap TCP probe
    /import_image       — active document composite as PNG data URL
    /resource_image     — resolve a `{source, layer_name}` spec to a
                          PNG data URL
    /import_masks       — selected mask or paint layer(s) as base64
                          masks

The URL prefix varies (controlnets/layout/magi/multimask use flat
`/active_state`, `/krita_status`, `/resource_image`, `/import_image`;
inpaint and segment prefix with `/krita/` and `/comfy/`).  The shared
handlers are therefore returned keyed by *semantic name*, not URL.
Each playground composes its own `ROUTES` dict from them:

    from pgruntime.daemon_routes import daemon_route_handlers

    _h = daemon_route_handlers()
    ROUTES = {
        "/active_state":   _h["active_state"],
        "/krita_status":   _h["krita_status"],
        "/resource_image": _h["resource_image"],
        # + playground-specific routes
    }

Or with a custom `send`:

    from pgruntime.daemon_routes import daemon_route_handlers
    from .krita_pin import send_with_pin

    _h = daemon_route_handlers(send=send_with_pin)
    ROUTES = {
        "/comfy/active_state":   _h["active_state"],
        "/krita/status":         _h["krita_status"],
        "/krita/resource_image": _h["resource_image"],
        "/krita/import":         _h["import_image"],
        "/krita/import_masks":   _h["import_masks"],
        # + playground-specific routes
    }

UNION OF COPIES THIS REPLACES
=============================
Controlnets (part 8), layout (part 4), magi (part 11), multimask
(part 6), inpaint (part 2), and segment (part 5) each had their own
`_fetch_active_state`, `handle_active_state`, `handle_krita_status`,
`handle_import_image`, `handle_resource_image`, and (in the latter
three) `handle_krita_import` / `handle_krita_import_masks`.  Six
copies of the first five, three copies of the last two.

The only per-playground differences were:

  · **The `send` callable.**  inpaint used a `_send_with_pin` wrapper
    that injects a pinned-document name into every daemon command;
    the other five used `daemon_client.send` directly.  Parameterized
    here as the `send` kwarg.

  · **Extra fields in `handle_import_image`.**  inpaint and segment
    returned `encoded.content_hash` (via
    `pg_comfy.image.make_encoded_image`); controlnets, layout, magi,
    and multimask used PIL directly and `sha256(png_bytes)[:16]`.  For
    PNG inputs both produce the same 16-char hex; for non-PNG inputs
    they differ, but `get_document_composite_b64` always returns PNG,
    so this difference is unobservable in practice.  The shared
    implementation uses the simpler PIL path — see the migration note
    on hash-tracker reset.

  · **Segment's `_cache_image` side effect.**  Segment cached the
    imported image bytes for later `/comfy/segment` calls.  Not
    included in the shared handler; segment wraps it locally.  See
    the module's MIGRATION NOTES below.

MIGRATION NOTES
===============
When a playground switches from its local copy to this module:

  · **Hash-tracker reset.**  A playground that previously used
    `pg_comfy.image.make_encoded_image` (inpaint, segment) will
    observe one extra state push after the migration, because the
    hash string changes format.  The tracker resets once, the canvas
    re-pushes once, normal operation resumes.  Not a correctness
    issue.

  · **Segment's `_cache_image` side effect.**  Wrap the shared
    handler:

        _import_base = _h["import_image"]

        def handle_krita_import(payload):
            res = _import_base(payload)
            if res.get("success") and res.get("hash"):
                import base64
                png_bytes = base64.b64decode(
                    res["data_url"].split(",", 1)[1]
                )
                _cache_image(res["hash"], png_bytes,
                             res["width"], res["height"],
                             res["filename"])
            return res

        ROUTES["/krita/import"] = handle_krita_import

    The cache write is segment's own concern and stays in segment.

  · **Inpaint's `_capture_document_from_response` side effect.**
    Already handled by passing `send=_send_with_pin` — the wrapper
    itself captures the pin from the response before returning.  No
    wrap of `handle_import_image` is needed.

  · **`/playground_reregister`.**  Not in this module on purpose:
    the URL/port/name triple that the handler returns is per-playground
    state, not shared.  Each playground keeps its own (usually a
    three-line wrapper around `daemon_registry.reregister_once`).

Non-goal: this module does not talk to the daemon on import.  All
daemon interaction is deferred to per-request handler bodies so that
the module can be imported by a playground that is not yet registered
or whose daemon is not yet running.
"""

import base64
import hashlib
from io import BytesIO

from PIL import Image

from . import daemon_client


_TIMEOUT_IMPORT = 60.0
_TIMEOUT_LAYER  = 30.0
_TIMEOUT_MASKS  = 60.0
_TIMEOUT_STATUS = 10.0


def daemon_route_handlers(
    *,
    send=None,
    timeout_import: float = _TIMEOUT_IMPORT,
    timeout_layer:  float = _TIMEOUT_LAYER,
    timeout_masks:  float = _TIMEOUT_MASKS,
    timeout_status: float = _TIMEOUT_STATUS,
) -> dict:
    """Return the standard set of daemon proxy handlers.

    Returns a dict keyed by *semantic name* (not URL):

        active_state, krita_status, import_image, resource_image,
        import_masks

    `send`, if provided, is called instead of `daemon_client.send`
    for every daemon command.  Use this to inject a pinned-document
    wrapper (inpaint's `_send_with_pin`) or a playground-specific
    timeout policy.  The signature is `send(command, timeout=...)`,
    matching `daemon_client.send`.
    """
    if send is None:
        send = daemon_client.send

    # ---- /active_state --------------------------------------------------

    def _fetch_active_state():
        """Return `(data, err)`.  `data` is None on failure, `err` is
        None on success.

        Wrapped in try/except because `send` is guaranteed not to
        raise, but a custom `send` a caller injects (e.g. a mock in a
        future test) might.  Defensive `except Exception` keeps the
        route from 500-ing on a transport wobble.
        """
        try:
            resp = send({"type": "comfy_status"}, timeout=timeout_status)
        except Exception as e:
            return None, str(e)
        if not isinstance(resp, dict):
            return None, "bad daemon response"
        if resp.get("status") != "success":
            return None, (
                resp.get("message") or "daemon rejected comfy_status"
            )
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

    # ---- /krita_status --------------------------------------------------

    def handle_krita_status(_payload):
        return {
            "success": True,
            "reachable": daemon_client.is_reachable(),
        }

    # ---- /import_image --------------------------------------------------

    def handle_import_image(payload):
        """Fetch the active document's composite as a PNG data URL.

        `payload["name"]` selects a specific document; when absent the
        daemon uses `activeDocument()`.  This handler is also called
        internally by `handle_resource_image` for `source="active"`,
        so it must accept an empty dict.
        """
        name = (
            payload.get("name") if isinstance(payload, dict) else None
        )
        cmd = {"type": "get_document_composite_b64"}
        if name:
            cmd["name"] = name

        response = send(cmd, timeout=timeout_import)
        if response.get("status") != "success":
            return {
                "success": False,
                "message": (
                    response.get("message") or "daemon rejected"
                ),
            }

        data = response.get("data") or {}
        data_url = data.get("data_url")
        if not data_url:
            return {
                "success": False,
                "message": "daemon returned no data_url",
            }
        if not data_url.startswith("data:image/png;base64,"):
            return {
                "success": False,
                "message": (
                    f"unexpected data_url prefix: "
                    f"{data_url[:30]!r}"
                ),
            }

        try:
            png_bytes = base64.b64decode(data_url.split(",", 1)[1])
            img = Image.open(BytesIO(png_bytes))
            w, h = img.size
        except Exception as e:
            return {
                "success": False,
                "message": f"decode failed: {e}",
            }

        hash_hex = hashlib.sha256(png_bytes).hexdigest()[:16]
        return {
            "success":  True,
            "hash":     hash_hex,
            "width":    w,
            "height":   h,
            "data_url": data_url,
            "filename": data.get("name") or "krita_composite",
        }

    # ---- /resource_image ------------------------------------------------

    def handle_resource_image(payload):
        """Resolve a `{source, layer_name}` spec to a PNG data URL.

        `source="active"` delegates to `handle_import_image`.
        `source="layer"` requires `layer_name` and fetches that
        specific layer's pixels.
        """
        if not isinstance(payload, dict):
            return {
                "success": False,
                "message": "expected a JSON object",
            }

        source = payload.get("source", "active")
        layer_name = payload.get("layer_name")

        if source == "active":
            return handle_import_image({})

        if source == "layer":
            if not layer_name:
                return {
                    "success": False,
                    "message": (
                        "layer_name is required when source=layer"
                    ),
                }
            response = send(
                {"type": "get_layer_pixels_b64", "name": str(layer_name)},
                timeout=timeout_layer,
            )
            if response.get("status") != "success":
                return {
                    "success": False,
                    "message": (
                        response.get("message") or "daemon rejected"
                    ),
                }
            data = response.get("data") or {}
            data_url = data.get("data_url")
            if not data_url:
                return {
                    "success": False,
                    "message": "daemon returned no data_url for layer",
                }
            return {
                "success":  True,
                "hash":     "layer_" + str(layer_name),
                "width":    data.get("width"),
                "height":   data.get("height"),
                "data_url": data_url,
                "filename": str(layer_name),
            }

        return {
            "success": False,
            "message": (
                f"source={source!r} cannot be displayed as an image."
            ),
        }

    # ---- /import_masks --------------------------------------------------

    def handle_import_masks(payload):
        """Fetch selected mask or paint layer(s) as base64 masks.

        Accepts three optional filters, all forwarded verbatim to the
        daemon:
            layer_names                      explicit layer selection
            include_paint_alpha              paint-layer alpha policy
            prefer_selection_mask_children   selection-mask preference

        Return shape matches inpaint/segment/multimask's existing
        client contract: `masks`, `skipped`, `selection_path`,
        `message`.
        """
        cmd = {"type": "import_krita_masks_b64"}
        if isinstance(payload, dict):
            if payload.get("layer_names"):
                cmd["layer_names"] = str(payload["layer_names"])
            if "include_paint_alpha" in payload:
                cmd["include_paint_alpha"] = bool(
                    payload["include_paint_alpha"]
                )
            if "prefer_selection_mask_children" in payload:
                cmd["prefer_selection_mask_children"] = bool(
                    payload["prefer_selection_mask_children"]
                )

        response = send(cmd, timeout=timeout_masks)
        if response.get("status") != "success":
            return {
                "success": False,
                "message": (
                    response.get("message") or "daemon rejected"
                ),
            }

        data = response.get("data") or {}
        masks = data.get("masks") or []
        skipped = data.get("skipped") or []
        selection_path = data.get("selection_path") or "unknown"

        return {
            "success":        True,
            "masks":          masks,
            "skipped":        skipped,
            "selection_path": selection_path,
            "message":        (
                response.get("message") or f"{len(masks)} mask(s)"
            ),
        }

    return {
        "active_state":   handle_active_state,
        "krita_status":   handle_krita_status,
        "import_image":   handle_import_image,
        "resource_image": handle_resource_image,
        "import_masks":   handle_import_masks,
    }