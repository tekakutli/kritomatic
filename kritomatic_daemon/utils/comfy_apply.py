"""comfy_apply.py — prepare a native-v2 workflow for submission.

Reads pixels from a Krita document per a panel config's `[resources]`
spec, uploads each blob to the comfy-api-proxy via POST /api/v2/assets,
and returns a transformed graph.

CROP FRAME
==========
Before reading anything, `prepare_workflow` queries the inpaint
playground's `/crop_frame` endpoint.  That endpoint is the single
source of truth for what to crop — it returns the definitive
`{theta, W, H, Wp, Hp, cropX, cropY, cropW, cropH}` that the daemon
must apply to `@thecanvas` (and, by extension, to `@theselection`
and `@thecontext`) before the workflow runs.  Multimask's `/mask`
endpoint queries the same frame and applies the same rotate+crop to
each area mask, so every mask the workflow sees is at cropW×cropH.

Cases:
  · `/crop_frame` returns null (playground down or empty state) →
    legacy un-rotated path (`_prepare_unrotated`), which does its own
    `_determine_crop_bounds` fallback for `@thecontext` layer bbox.
  · `/crop_frame` says theta=0, whole canvas → same as legacy,
    `_prepare_unrotated`.
  · `/crop_frame` says theta=0 with context crop → `_prepare_cropped`
    (no rotation, but crop to context rect).
  · `/crop_frame` says theta≠0 → `_prepare_cropped` (rotate then crop).

`PreparedWorkflow.rotation = theta`.  The paste stage un-rotates by
`-theta` and pastes at `(paste_config.x, paste_config.y)` when theta
≠ 0; otherwise pastes at `(crop_bounds[0]+x, crop_bounds[1]+y)` (the
existing un-rotated semantics).

EXTENDED CANVAS
===============
`cropX`/`cropY` returned by `/crop_frame` may be negative, or
`cropX+cropW` may exceed `Wp` (and likewise for Y) when the user
drags the context rect past the un-rotated canvas bounds — the
daemon is expected to be able to crop any region of the rotated
frame, including regions past `Wp×Hp`, with the outside-of-frame
region rendered as transparent (canvas) / black (mask).  The
`_extend_and_crop` helper draws the rotated image at a negative
offset into a `cropW×cropH` blank image; QPainter clips the
negative coordinates so the parts of the crop outside the frame
stay transparent."""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
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

INPAINT_URL = "http://127.0.0.1:8774"


@dataclass
class PreparedWorkflow:
    graph: dict
    crop_bounds: Bounds
    assets: dict[str, dict]
    seed_applied: Optional[int]
    warnings: list[str] = field(default_factory=list)
    rotation: float = 0.0


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
    if config is None:
        raise ValueError(
            "No panel config applied. Run comfy_apply_config first."
        )
    if doc is None:
        raise ValueError("No active document.")

    frame = _query_inpaint_crop_frame()
    print(f"[comfyui] prepare: crop_frame={frame}", flush=True)

    if frame is None:
        print("[comfyui] prepare: no crop_frame (inpaint down or empty); "
              "legacy unrotated path", flush=True)
        return _prepare_unrotated(
            doc, graph, resource_slots, param_slots,
            config, proxy_client, seed=seed,
        )

    frame_W = int(frame.get("W", 0))
    frame_H = int(frame.get("H", 0))
    doc_W = int(doc.width())
    doc_H = int(doc.height())
    if frame_W != doc_W or frame_H != doc_H:
        print(f"[comfyui] prepare: frame dims {frame_W}x{frame_H} "
              f"!= doc {doc_W}x{doc_H}; legacy fallback", flush=True)
        return _prepare_unrotated(
            doc, graph, resource_slots, param_slots,
            config, proxy_client, seed=seed,
        )

    theta = float(frame.get("theta", 0.0))
    cropW = int(frame.get("cropW", doc_W))
    cropH = int(frame.get("cropH", doc_H))
    cropX = int(frame.get("cropX", 0))
    cropY = int(frame.get("cropY", 0))

    if theta == 0 and cropW == doc_W and cropH == doc_H:
        # No-op frame — legacy un-rotated pipeline.  Same behavior as
        # before /crop_frame existed.
        return _prepare_unrotated(
            doc, graph, resource_slots, param_slots,
            config, proxy_client, seed=seed,
        )

    return _prepare_cropped(
        doc, graph, resource_slots, param_slots,
        config, proxy_client, frame=frame, seed=seed,
    )


# ---------------------------------------------------------------------------
#  /crop_frame probe
# ---------------------------------------------------------------------------

def _query_inpaint_crop_frame(timeout: float = 0.5) -> Optional[dict]:
    try:
        req = urllib.request.Request(
            INPAINT_URL + "/crop_frame",
            data=b"{}", method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
        if not isinstance(data, dict) or not data.get("success"):
            return None
        return data.get("frame")
    except (urllib.error.URLError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
#  Un-rotated path — preserved as-is for legacy / no-op cases
# ---------------------------------------------------------------------------

def _prepare_unrotated(doc, graph, resource_slots, param_slots,
                       config, proxy_client, *, seed=None) -> PreparedWorkflow:
    warnings: list[str] = []
    crop_bounds, boundary_name, boundary_bbox = _determine_crop_bounds(
        doc, resource_slots, config, warnings,
    )
    blobs = _read_resource_blobs(
        doc, resource_slots, config, crop_bounds, warnings,
        boundary_name=boundary_name,
        boundary_bbox=boundary_bbox,
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
        rotation=0.0,
    )


# ---------------------------------------------------------------------------
#  Cropped path — unified for theta=0-with-crop and theta≠0
# ---------------------------------------------------------------------------

def _prepare_cropped(doc, graph, resource_slots, param_slots,
                     config, proxy_client, *, frame, seed=None
                     ) -> PreparedWorkflow:
    from PyQt6.QtGui import QImage

    W = int(frame["W"])
    H = int(frame["H"])
    Wp = int(frame["Wp"])
    Hp = int(frame["Hp"])
    theta = float(frame["theta"])
    cropX = int(frame["cropX"])
    cropY = int(frame["cropY"])
    cropW = int(frame["cropW"])
    cropH = int(frame["cropH"])
    inpaint_enabled = bool(frame.get("inpaintEnabled", False))

    warnings: list[str] = []
    warnings.append(
        f"cropped prepare: theta={theta} W={W} H={H} "
        f"frame={Wp}x{Hp} crop=({cropX},{cropY},{cropW},{cropH}) "
        f"inpaintEnabled={inpaint_enabled}"
    )
    print(f"[comfyui] cropped prepare: theta={theta} "
          f"W={W} H={H} frame={Wp}x{Hp} "
          f"crop=({cropX},{cropY},{cropW},{cropH}) "
          f"inpaintEnabled={inpaint_enabled}", flush=True)

    # ---- Read canvas + selection at native W×H ----
    canvas_enc = read_layer_pixels(doc, "thecanvas", (0, 0, W, H))
    if canvas_enc is None:
        raise ValueError("thecanvas scratch layer is missing")

    try:
        selection_enc = read_layer_mask(doc, "theselection", (0, 0, W, H))
    except ValueError:
        selection_enc = None

    canvas_img = _decode_png_to_qimage(canvas_enc.data)

    if theta != 0:
        rotated_canvas = _rotate_qimage(canvas_img, theta)
    else:
        rotated_canvas = canvas_img

    if selection_enc is not None:
        sel_img = _decode_png_to_qimage(selection_enc.data)
        rotated_selection = (
            _rotate_qimage(sel_img, theta) if theta != 0 else sel_img
        )
    else:
        rotated_selection = _blank_qimage(
            Wp if theta != 0 else W,
            Hp if theta != 0 else H,
        )

    # ---- Crop to (cropX, cropY, cropW, cropH) ----
    #
    # `_extend_and_crop` draws the rotated image at (-cropX, -cropY)
    # inside a blank cropW×cropH image, so a crop that extends past
    # the rotated frame boundary (cropX<0, or cropX+cropW>Wp, or the
    # Y analogue) is filled with transparent (ARGB) / black
    # (grayscale) where the frame has no data.  In-bounds crops
    # produce byte-identical output to the old `.copy()` call.
    cropped_canvas    = _extend_and_crop(rotated_canvas,    cropX, cropY, cropW, cropH)
    cropped_selection = _extend_and_crop(rotated_selection, cropX, cropY, cropW, cropH)

    # ---- Context mask ----
    #
    # The daemon crop IS the user's context rect (translated into the
    # rotated frame).  So the whole crop is context; the workflow's
    # InpaintCropImproved sees a full-white context mask, which means
    # "the entire crop is context" and its own crop is
    # selection ∪ full_crop = full_crop.  The model sees the whole
    # crop, restricted only by the selection mask.
    ctx_mask = _all_white_qimage(cropW, cropH)

    # ---- Encode + upload ----
    canvas_enc_out = _encode_qimage_png(cropped_canvas)
    sel_enc_out    = _encode_qimage_png(cropped_selection)
    ctx_enc_out    = _encode_qimage_png(ctx_mask)

    blobs: dict[str, EncodedImage] = {}
    for name in resource_slots:
        if name == "@thecanvas":
            blobs[name] = canvas_enc_out
        elif name == "@theselection":
            blobs[name] = sel_enc_out
        elif name == "@thecontext":
            blobs[name] = ctx_enc_out

    assets = _upload_resource_blobs(blobs, proxy_client)
    prepared_graph = substitute_into_graph(
        graph, resource_slots, param_slots,
        assets, config["params"], seed=seed,
    )

    return PreparedWorkflow(
        graph=prepared_graph,
        crop_bounds=(cropX, cropY, cropW, cropH),
        assets=assets,
        seed_applied=(
            seed if seed is not None and "seed" in param_slots else None
        ),
        warnings=warnings,
        rotation=theta,
    )


# ---------------------------------------------------------------------------
#  Crop with extension
# ---------------------------------------------------------------------------

def _extend_and_crop(img, cropX, cropY, cropW, cropH):
    """Extract a cropW × cropH region from img starting at (cropX, cropY).

    Regions outside img's bounds are filled with transparent (ARGB32)
    or black (Grayscale8).  Handles cropX/cropY < 0 and crops that
    extend past img's right/bottom edge — the rotated frame may be
    smaller than the requested crop when the user drags the context
    rect past the frame boundary.  For in-bounds crops this produces
    exactly the same output as `img.copy(cropX, cropY, cropW, cropH)`.
    """
    from PyQt6.QtGui import QImage, QPainter

    fmt = img.format()
    out = QImage(int(cropW), int(cropH), fmt)
    out.fill(0)
    painter = QPainter(out)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    painter.drawImage(-int(cropX), -int(cropY), img)
    painter.end()
    return out


# ---------------------------------------------------------------------------
#  Rotation helpers
# ---------------------------------------------------------------------------

def _rotated_bbox_in_crop(rect, theta, W, H, Wp, Hp, cropX, cropY):
    """Return the axis-aligned bbox of the rect (in W×H) transformed
    into the rotated frame (W'×H'), translated into the crop frame.

    Kept for future use; the current cropped path uses a full-white
    context mask since the daemon crop already IS the context rect.
    """
    cx, cy, cw, ch = rect
    rad = math.radians(theta)
    cos_t, sin_t = math.cos(rad), math.sin(rad)
    origin_x, origin_y = W / 2.0, H / 2.0
    dest_cx, dest_cy = Wp / 2.0, Hp / 2.0

    corners = [
        (cx, cy),
        (cx + cw, cy),
        (cx + cw, cy + ch),
        (cx, cy + ch),
    ]
    rotated = []
    for (px, py) in corners:
        dx = px - origin_x
        dy = py - origin_y
        rx = dx * cos_t - dy * sin_t
        ry = dx * sin_t + dy * cos_t
        rotated.append((dest_cx + rx, dest_cy + ry))

    min_x = min(p[0] for p in rotated)
    min_y = min(p[1] for p in rotated)
    max_x = max(p[0] for p in rotated)
    max_y = max(p[1] for p in rotated)

    return (
        int(round(min_x)) - cropX,
        int(round(min_y)) - cropY,
        int(round(max_x - min_x)),
        int(round(max_y - min_y)),
    )


# ---------------------------------------------------------------------------
#  QImage helpers
# ---------------------------------------------------------------------------

def _decode_png_to_qimage(png_bytes: bytes):
    from PyQt6.QtGui import QImage
    img = QImage()
    if not img.loadFromData(png_bytes, "PNG"):
        raise ValueError("could not decode PNG")
    return img.convertToFormat(QImage.Format.Format_ARGB32)


def _rotate_qimage(qimg, theta):
    from PyQt6.QtGui import QTransform
    from PyQt6.QtCore import Qt
    return qimg.transformed(
        QTransform().rotate(float(theta)),
        Qt.TransformationMode.SmoothTransformation,
    )


def _encode_qimage_png(qimg):
    from PyQt6.QtCore import QBuffer, QIODevice
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    if not qimg.save(buf, "PNG"):
        raise ValueError("could not encode PNG")
    png = bytes(buf.data())
    buf.close()
    return EncodedImage(
        data=png, width=qimg.width(), height=qimg.height(),
    )


def _blank_qimage(W, H):
    from PyQt6.QtGui import QImage
    q = QImage(W, H, QImage.Format.Format_ARGB32)
    q.fill(0)
    return q


def _all_white_qimage(W, H):
    from PyQt6.QtGui import QImage
    q = QImage(W, H, QImage.Format.Format_Grayscale8)
    q.fill(255)
    return q


def _make_rect_qimage(bbox, W, H):
    """Kept for future use; no longer called from the cropped path."""
    from PyQt6.QtGui import QImage
    q = QImage(W, H, QImage.Format.Format_Grayscale8)
    q.fill(0)
    x, y, w, h = bbox
    x0 = max(0, x)
    y0 = max(0, y)
    x1 = min(W, x + w)
    y1 = min(H, y + h)
    if x1 > x0 and y1 > y0:
        for yy in range(y0, y1):
            for xx in range(x0, x1):
                q.setPixel(xx, yy, 0xFFFFFF)
    return q


def _multiply_qimages(a, b):
    from PyQt6.QtGui import QImage
    W, H = a.width(), a.height()
    if (W, H) != (b.width(), b.height()):
        return a
    a = a.convertToFormat(QImage.Format.Format_ARGB32)
    b = b.convertToFormat(QImage.Format.Format_Grayscale8)
    out = QImage(W, H, QImage.Format.Format_ARGB32)
    out.fill(0)
    for yy in range(H):
        for xx in range(W):
            mask = b.pixel(xx, yy) & 0xFF
            if mask == 0:
                continue
            src = a.pixel(xx, yy)
            r = (src >> 16) & 0xFF
            g = (src >> 8) & 0xFF
            b_ch = src & 0xFF
            a_ch = (src >> 24) & 0xFF
            out.setPixel(xx, yy,
                         (a_ch << 24) | (r << 16) | (g << 8) | b_ch)
    return out


# ---------------------------------------------------------------------------
#  Legacy crop bounds (used only when inpaint is unreachable)
# ---------------------------------------------------------------------------

def _determine_crop_bounds(doc, resource_slots: dict,
                           config: dict, warnings: list
                           ) -> tuple[Bounds, Optional[str], Optional[Bounds]]:
    canvas_w, canvas_h = int(doc.width()), int(doc.height())
    full: Bounds = (0, 0, canvas_w, canvas_h)

    try:
        padding = int(config["crop"].get("padding", 0))
    except (TypeError, ValueError):
        padding = 0

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
                f"non-zero pixels; falling through"
            )
            continue

        x0, y0, w, h = bbox
        cx0 = max(0, x0 - padding)
        cy0 = max(0, y0 - padding)
        cx1 = min(canvas_w, x0 + w + padding)
        cy1 = min(canvas_h, y0 + h + padding)
        clamped = (cx0, cy0, cx1 - cx0, cy1 - cy0)
        print(f"[comfyui] legacy unrotated crop from @thecontext layer: {clamped}",
              flush=True)
        return clamped, name, bbox

    has_global = any(
        config["resources"].get(name, {}).get("source") == "global"
        for name in resource_slots
    )
    if not has_global:
        return full, None, None

    sel = read_selection_bounds(doc)
    if sel is None:
        warnings.append(
            "no active selection; source='global' resources use the "
            "full canvas"
        )
        return full, None, None

    bounds = compute_context_bounds(
        canvas_extent=(canvas_w, canvas_h),
        mask_bounds=sel,
        context=config["crop"]["context"],
        padding=padding,
    )
    return bounds, None, None


def _read_layer_bbox(doc, layer_name: str,
                     canvas_w: int, canvas_h: int) -> Optional[Bounds]:
    from .layer_resolve import resolve as resolve_node
    from .layer_types import IMAGE_LAYER_TYPES, MASK_LAYER_TYPES
    node = resolve_node(doc, layer_name)
    if node is None:
        return None
    node_type = node.type()
    try:
        if node_type in MASK_LAYER_TYPES:
            raw = bytes(node.pixelData(0, 0, canvas_w, canvas_h))
            if len(raw) != canvas_w * canvas_h:
                return None
            return _grayscale_bytes_bbox(raw, canvas_w, canvas_h)
        if node_type in IMAGE_LAYER_TYPES:
            raw = bytes(node.projectionPixelData(0, 0, canvas_w, canvas_h))
            if len(raw) != canvas_w * canvas_h * 4:
                return None
            alpha = raw[3::4]
            return _grayscale_bytes_bbox(alpha, canvas_w, canvas_h)
    except Exception:
        return None
    return None


def _grayscale_bytes_bbox(data, W, H):
    min_x, min_y = W, H
    max_x, max_y = -1, -1
    for yy in range(H):
        base = yy * W
        for xx in range(W):
            if data[base + xx]:
                if xx < min_x: min_x = xx
                if xx > max_x: max_x = xx
                if yy < min_y: min_y = yy
                if yy > max_y: max_y = yy
    if max_x < min_x or max_y < min_y:
        return None
    return (min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)


# ---------------------------------------------------------------------------
#  Legacy resource reads + upload
# ---------------------------------------------------------------------------

def _read_resource_blobs(doc, resource_slots: dict, config: dict,
                         crop_bounds: Bounds, warnings: list,
                         boundary_name: Optional[str] = None,
                         boundary_bbox: Optional[Bounds] = None,
                         ) -> dict[str, EncodedImage]:
    out: dict[str, EncodedImage] = {}

    for name, slot in resource_slots.items():
        spec = config["resources"].get(name)
        if spec is None:
            raise ValueError(
                f"resource slot {name!r} exists in the graph but is not "
                f"covered by [resources] in the config."
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
                        data=_encode_grayscale_png(
                            b"\xff" * (w * h), w, h,
                        ),
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
                    if (name == boundary_name
                            and boundary_bbox is not None):
                        blob = _make_max_bounded_mask(
                            crop_bounds, boundary_bbox,
                        )
                    else:
                        blob = read_layer_mask(doc, layer_name, crop_bounds)
            else:
                raise ValueError(f"unknown source {source!r}")
        except ValueError as e:
            raise ValueError(f"resource {name!r}: {e}") from e

        if (blob.width, blob.height) != (crop_bounds[2], crop_bounds[3]):
            raise ValueError(
                f"resource {name!r}: read {blob.width}\u00d7{blob.height} "
                f"but crop is {crop_bounds[2]}\u00d7{crop_bounds[3]}"
            )
        out[name] = blob
    return out


def _make_max_bounded_mask(crop_bounds: Bounds,
                           bbox: Bounds) -> EncodedImage:
    cx, cy, cw, ch = crop_bounds
    bx, by, bw, bh = bbox
    inner_x = bx - cx
    inner_y = by - cy
    x0 = max(0, inner_x)
    y0 = max(0, inner_y)
    x1 = min(cw, inner_x + bw)
    y1 = min(ch, inner_y + bh)

    if x1 <= x0 or y1 <= y0:
        raw = bytes(cw * ch)
        return EncodedImage(
            data=_encode_grayscale_png(raw, cw, ch),
            width=cw, height=ch, is_mask=True,
        )

    buf = bytearray(cw * ch)
    for yy in range(y0, y1):
        base = yy * cw
        for xx in range(x0, x1):
            buf[base + xx] = 255

    return EncodedImage(
        data=_encode_grayscale_png(bytes(buf), cw, ch),
        width=cw, height=ch, is_mask=True,
    )


def _upload_resource_blobs(blobs: dict[str, EncodedImage],
                           proxy_client) -> dict[str, dict]:
    refs: dict[str, dict] = {}
    for name, blob in blobs.items():
        slug = name.lstrip("@") or "resource"
        file_path = f"input/{slug}.png"
        try:
            asset = proxy_client.upload_asset(
                blob.data, file_path, "image/png",
            )
        except Exception as e:
            raise ValueError(
                f"upload of {name!r} failed: {e}"
            ) from e
        refs[name] = asset
    return refs