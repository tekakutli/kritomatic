"""comfy_apply.py — prepare a native-v2 workflow for submission.

Reads pixels from a Krita document per a panel config's `[resources]`
spec, uploads each blob to the comfy-api-proxy via POST /api/v2/assets,
and returns a transformed graph with:

  * each `LoadImage` / `LoadImageMask` resource input replaced by a
    ``core/ASSET`` reference pointing at the uploaded blob;
  * each `Primitive*` param input replaced by the config's `[params]`
    value (and, if present and requested, by the batch's seed).

The output is what Stage 5's generation pipeline submits via
``POST /api/v2/jobs``. ComfyUI sees only native nodes; the proxy
resolves ``core/ASSET`` references back to filenames before passing the
graph to ComfyUI.

This module is pure read + upload + substitute. It imports from
``comfyui_krita`` for the reads and takes a ``ProxyV2`` instance for
the upload. No Krita state mutation, no daemon state mutation, no
paste.

Design notes
============
  * Every blob is read at the *same* crop bounds. If any resource has
    ``source = "global"``, the crop is derived from the selection
    + ``[crop]`` config; otherwise the full canvas is used. Uniform
    dimensions are required because the graph's `LoadImage` /
    `LoadImageMask` nodes will feed each other with a shared latent
    shape — mixed sizes fail ComfyUI's validation.

  * The graph is deep-copied at the node level (fresh ``inputs`` dict
    per node) before substitution, so the caller's
    ``_active_workflow_graph`` is never mutated by a single
    substitution's failure.  (The copy lives in the shared
    `substitute_into_graph` function; this module does not touch it.)

  * A resource slot with no config entry is an error, not a warning:
    leaving a ``@``-prefixed literal in the graph means ComfyUI tries
    to load a file named ``@thecanvas`` and fails at execute time with
    a message that hides the real cause. Erroring at prepare time names
    the missing key.

  * A resource with ``source = "global"`` and no active selection
    synthesizes a full-white mask — matches the old ``ETN_KritaSelection``
    behavior where "no selection = everything selected". A warning is
    added to the result so the caller can surface it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from .comfyui_krita import (
    _encode_grayscale_png,
    compute_context_bounds,
    read_canvas_composite,
    read_layer_mask,
    read_layer_pixels,
    read_selection_bounds,
    read_selection_mask,
)
from .comfyui_krita import EncodedImage
from .comfyui_substitute import substitute_into_graph


Bounds = tuple[int, int, int, int]


@dataclass
class PreparedWorkflow:
    """Output of `prepare_workflow`.

    `graph` is the fully substituted API-format workflow, ready to
    submit. `crop_bounds` is what the daemon will use to place the
    pasted result: the same `(x, y, w, h)` used for reads. `assets` is
    ``{@-name: {id, hash, ...}}`` (the proxy's asset dict, minus bytes)
    for provenance and any post-submission reconciliation. `warnings`
    collects non-fatal diagnostics like the synthesized-selection case.
    """
    graph: dict
    crop_bounds: Bounds
    assets: dict[str, dict]
    seed_applied: Optional[int]
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
#  Public entry point
# ---------------------------------------------------------------------------

def prepare_workflow(doc,
                     graph: dict,
                     resource_slots: dict,
                     param_slots: dict,
                     config: dict,
                     proxy_client,
                     *,
                     seed: Optional[int] = None) -> PreparedWorkflow:
    """Read pixels, upload as assets, substitute into the graph.

    `config` is the ``_active_config`` dict produced by
    ``comfy_apply_config`` — it has ``resources``, ``params``, ``crop``,
    and ``paste`` keys. ``paste`` is not consumed here; it is Stage 5's
    concern.

    `proxy_client` is a ``ProxyV2`` instance pointed at the running
    proxy. Every blob is uploaded via its ``upload_asset`` method.

    ``seed`` overrides ``config["params"]["seed"]`` when the workflow
    has a ``Primitive*`` titled ``seed``. Ignored when no such slot
    exists.
    """
    if config is None:
        raise ValueError(
            "No panel config applied. Run comfy_apply_config first."
        )
    if doc is None:
        raise ValueError("No active document.")

    warnings: list[str] = []
    crop_bounds = _determine_crop_bounds(
        doc, resource_slots, config, warnings,
    )
    blobs = _read_resource_blobs(
        doc, resource_slots, config, crop_bounds, warnings,
    )
    asset_refs = _upload_resource_blobs(blobs, proxy_client)

    prepared_graph = substitute_into_graph(
        graph, resource_slots, param_slots,
        asset_refs, config["params"], seed=seed,
    )

    return PreparedWorkflow(
        graph=prepared_graph,
        crop_bounds=crop_bounds,
        assets=asset_refs,
        seed_applied=(
            seed if seed is not None and "seed" in param_slots else None
        ),
        warnings=warnings,
    )


# ---------------------------------------------------------------------------
#  Internals
# ---------------------------------------------------------------------------

def _determine_crop_bounds(doc, resource_slots: dict,
                           config: dict, warnings: list) -> Bounds:
    """Decide the read window for every resource.

    Resolution order:

      1. Mask-kind, source="layer" resources.  The first one whose
         layer has any non-zero pixels defines the crop: its
         non-zero bbox, padded by [crop].padding and clamped to the
         canvas.  Conventionally @thecontext.  This is the rule the
         multimask playground relies on: it sets @thecontext to a
         layer whose painted rectangle is the region of interest,
         and expects the workflow to see only what is inside that
         rectangle, with any @theselection spill clipped to it.

      2. source="global" resources: crop derived from the live
         Krita selection bounds and [crop] config.  Falls back to
         full canvas (with a warning) when no selection exists,
         matching the old "no selection = everything selected"
         behaviour.

      3. Otherwise: full canvas.

    Every resource is then read at the resulting crop, so the canvas,
    every mask, and every named layer share one dimension — required
    because LoadImage / LoadImageMask feed each other with a shared
    latent shape.
    """
    canvas_w, canvas_h = int(doc.width()), int(doc.height())
    full: Bounds = (0, 0, canvas_w, canvas_h)

    try:
        padding = int(config["crop"].get("padding", 0))
    except (TypeError, ValueError):
        padding = 0

    # ---- Rule 1: a mask-kind, source="layer" resource defines the crop. ----
    #
    # Conventionally @thecontext. The layer's non-zero bbox — the tight
    # rectangle around its painted / alpha-bearing content — is the
    # region the workflow should see. Everything else (canvas,
    # selection, other masks) gets read at that same crop, so a
    # selection that spills outside @thecontext is clipped before it
    # ever reaches ComfyUI.
    for name, slot in resource_slots.items():
        if name == "@theselection":
            continue
        if slot.get("kind") != "mask":
            continue
        spec = config["resources"].get(name) or {}
        if spec.get("source") != "layer":
            continue
        layer_name = spec.get("layer_name")
        if not layer_name:
            continue

        bbox = _read_layer_bbox(doc, layer_name, canvas_w, canvas_h)
        if bbox is None:
            warnings.append(
                f"crop boundary {name!r} (layer {layer_name!r}) has no "
                f"non-zero pixels; falling through to other crop rules"
            )
            continue

        x0, y0, w, h = bbox
        cx0 = max(0, x0 - padding)
        cy0 = max(0, y0 - padding)
        cx1 = min(canvas_w, x0 + w + padding)
        cy1 = min(canvas_h, y0 + h + padding)
        clamped = (cx0, cy0, cx1 - cx0, cy1 - cy0)
        warnings.append(
            f"crop boundary from {name!r} (layer {layer_name!r}): "
            f"bbox={bbox} padded={padding} -> {clamped}"
        )
        return clamped

    # ---- Rule 2: source="global" resources use live selection bounds. ----
    has_global = any(
        config["resources"].get(name, {}).get("source") == "global"
        for name in resource_slots
    )
    if not has_global:
        return full

    sel = read_selection_bounds(doc)
    if sel is None:
        warnings.append(
            "no active selection; source='global' resources use the "
            "full canvas"
        )
        return full

    return compute_context_bounds(
        canvas_extent=(canvas_w, canvas_h),
        mask_bounds=sel,
        context=config["crop"]["context"],
        padding=padding,
    )


def _read_layer_bbox(doc, layer_name: str,
                     canvas_w: int, canvas_h: int) -> Optional[Bounds]:
    """Non-zero bbox of a layer's mask rendering, or None if empty.

    Reads the whole canvas once and computes the tight rectangle around
    every non-zero mask pixel.  The shape of the mask depends on the
    layer type:

      * mask layers (transparencymask, selectionmask): pixelData is
        8-bit grayscale; bbox is the tight rect around every pixel
        with value > 0.

      * image-bearing layers (paintlayer, grouplayer, filelayer,
        vectorlayer, clonelayer, filllayer, filterlayer): the alpha
        channel of projectionPixelData; bbox is the tight rect around
        every pixel with alpha > 0.  This matches what read_layer_mask
        encodes (alpha -> grayscale) so the bbox the daemon uses for
        cropping is the same bbox the workflow will see on the mask
        the daemon uploaded.

    Returns None for an empty layer (no pixels above 0) and for layer
    types that cannot be read as masks.  The caller treats None as
    "this layer cannot define a crop" and tries the next rule.

    numpy is used when available (it is in any Krita that ships the
    kritomatic daemon, because handlers/mask.py already imports it).
    If it is missing, falls back to a pure-Python scan — slower, but
    same answer.
    """
    from .layer_resolve import resolve as resolve_node
    from .layer_types import IMAGE_LAYER_TYPES, MASK_LAYER_TYPES

    node = resolve_node(doc, layer_name)
    if node is None:
        return None

    if canvas_w <= 0 or canvas_h <= 0:
        return None

    node_type = node.type()

    if node_type in MASK_LAYER_TYPES:
        raw = bytes(node.pixelData(0, 0, canvas_w, canvas_h))
        if len(raw) != canvas_w * canvas_h:
            return None
        return _grayscale_bbox(raw, canvas_w, canvas_h)

    if node_type in IMAGE_LAYER_TYPES:
        raw = bytes(node.projectionPixelData(0, 0, canvas_w, canvas_h))
        if len(raw) != canvas_w * canvas_h * 4:
            return None
        try:
            import numpy as np
        except ImportError:
            return None
        arr = np.frombuffer(raw, dtype=np.uint8).reshape(
            (canvas_h, canvas_w, 4),
        )
        return _grayscale_bbox_arr(arr[:, :, 3], canvas_w, canvas_h)

    return None


def _grayscale_bbox(raw: bytes, W: int, H: int) -> Optional[Bounds]:
    try:
        import numpy as np
    except ImportError:
        return _grayscale_bbox_pure(raw, W, H)
    arr = np.frombuffer(raw, dtype=np.uint8).reshape((H, W))
    return _grayscale_bbox_arr(arr, W, H)


def _grayscale_bbox_arr(arr, W: int, H: int) -> Optional[Bounds]:
    import numpy as np
    if arr.size == 0:
        return None
    rows = np.any(arr != 0, axis=1)
    cols = np.any(arr != 0, axis=0)
    if not rows.any() or not cols.any():
        return None
    y0 = int(np.argmax(rows))
    y1 = int(H - 1 - np.argmax(rows[::-1]))
    x0 = int(np.argmax(cols))
    x1 = int(W - 1 - np.argmax(cols[::-1]))
    return (x0, y0, x1 - x0 + 1, y1 - y0 + 1)


def _grayscale_bbox_pure(raw: bytes, W: int, H: int) -> Optional[Bounds]:
    min_x, min_y, max_x, max_y = W, H, -1, -1
    for y in range(H):
        base = y * W
        for x in range(W):
            if raw[base + x]:
                if x < min_x: min_x = x
                if x > max_x: max_x = x
                if y < min_y: min_y = y
                if y > max_y: max_y = y
    if max_x < min_x:
        return None
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


def _read_resource_blobs(doc, resource_slots: dict, config: dict,
                        crop_bounds: Bounds,
                        warnings: list) -> dict[str, EncodedImage]:
    """Read every resource slot's pixels at `crop_bounds`."""
    out: dict[str, EncodedImage] = {}

    for name, slot in resource_slots.items():
        spec = config["resources"].get(name)
        if spec is None:
            raise ValueError(
                f"resource slot {name!r} exists in the graph but is not "
                f"covered by [resources] in the config. Add "
                f"`\"{name}\" = {{ source = ... }}` and re-apply."
            )

        source = spec["source"]
        kind = slot["kind"]

        try:
            if source == "active":
                blob = read_canvas_composite(doc, crop_bounds)
            elif source == "global":
                blob = read_selection_mask(doc, crop_bounds)
                if blob is None:
                    w, h = crop_bounds[2], crop_bounds[3]
                    blob = EncodedImage(
                        data=_encode_grayscale_png(b"\xff" * (w * h), w, h),
                        width=w, height=h, is_mask=True,
                    )
            elif source == "layer":
                layer_name = spec.get("layer_name")
                if not layer_name:
                    raise ValueError(
                        f"source='layer' requires layer_name"
                    )
                if kind == "image":
                    blob = read_layer_pixels(doc, layer_name, crop_bounds)
                else:
                    blob = read_layer_mask(doc, layer_name, crop_bounds)
            else:
                raise ValueError(f"unknown source {source!r}")
        except ValueError as e:
            raise ValueError(f"resource {name!r}: {e}") from e

        # Every blob must have the exact crop dimensions. A mismatch
        # means an individual read used different bounds (e.g. a
        # sub-canvas layer with `projectionPixelData` clamping at its
        # own bounds) — ComfyUI would reject the eventual submission
        # with an opaque latent-shape error.
        if (blob.width, blob.height) != (crop_bounds[2], crop_bounds[3]):
            raise ValueError(
                f"resource {name!r}: read {blob.width}\u00d7{blob.height} "
                f"but crop is {crop_bounds[2]}\u00d7{crop_bounds[3]}"
            )

        out[name] = blob
    return out


def _upload_resource_blobs(blobs: dict[str, EncodedImage],
                           proxy_client) -> dict[str, dict]:
    """Upload every blob via `POST /api/v2/assets`.

    The file_path is ``input/<slug>.png`` where ``<slug>`` is the
    @-name minus the ``@``. The proxy classifies ``input/...`` as a
    workflow-input upload and routes it through ComfyUI's
    ``/upload/image``; the exact filename does not surface in the graph
    (the proxy resolves core/ASSET references to filenames internally).
    """
    refs: dict[str, dict] = {}
    for name, blob in blobs.items():
        slug = name.lstrip("@") or "resource"
        file_path = f"input/{slug}.png"
        try:
            asset = proxy_client.upload_asset(
                blob.data, file_path, "image/png",
            )
        except Exception as e:  # noqa: BLE001 — proxy errors are ProxyError subclasses
            raise ValueError(
                f"upload of {name!r} failed: {e}"
            ) from e
        refs[name] = asset
    return refs
