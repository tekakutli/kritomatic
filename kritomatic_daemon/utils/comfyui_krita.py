"""comfyui_krita.py — read pixels from a Krita document, paste ComfyUI
                   results back into it.

The document adapter for the `comfyui` handler. The v2 pipeline (Stage
5 onwards) uses:

  * ``read_canvas_composite`` / ``read_selection_mask`` /
    ``read_selection_bounds`` / ``read_layer_pixels`` / ``read_layer_mask``
    to fetch pixels for ``prepare_workflow``;
  * ``compute_context_bounds`` to derive a crop rect from the selection
    and the ``[crop]`` config;
  * ``paste_image`` / ``paste_batch_as_layers`` / ``paste_animation`` /
    ``resize_canvas`` to write downloaded outputs back into the document.

It knows nothing about ComfyUI, nothing about graph rewriting, nothing
about the wire protocol.

=============================================================================
PIXEL FORMAT
=============================================================================
Krita's `Document.pixelData` / `Node.projectionPixelData` return raw
bytes in `Format_ARGB32` layout for RGBA/U8 documents — 4 bytes per
pixel, BGRA in little-endian memory, which is the same byte order
QImage's `Format_ARGB32` uses. `Selection.pixelData` and mask-node
`pixelData` return 1 byte per pixel (grayscale 0-255).

There is no conversion step: wrap the Krita bytes in a QImage of the
matching format, save as PNG, and hand the PNG to the caller. On the
way back, decode the PNG to `Format_ARGB32` and hand the raw bytes to
`Node.setPixelData`.

=============================================================================
LAYER RESOLUTION
=============================================================================
Layer lookups go through the scope-aware resolver in `layer_resolve.py`,
not `Document.nodeByName`. That means a bundle that creates a layer
named `X` and later references `X` resolves to the layer it created,
even if another layer called `X` exists in the document.
"""

from __future__ import annotations

import tempfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage

from .layer_resolve import resolve as resolve_node
from .layer_types import IMAGE_LAYER_TYPES, MASK_LAYER_TYPES


Bounds = tuple[int, int, int, int]


# ============================================================================
#  EncodedImage
# ============================================================================
#
# One PNG-encoded image plus its pixel extent. Moved here from
# comfyui_tooling.py in Stage 6 (that module's expansion logic is gone;
# this dataclass is what survives and what the reads return and the
# uploads consume).

@dataclass
class EncodedImage:
    """One PNG-encoded image plus its pixel extent.

    `data` is the raw PNG bytes. `is_mask` is a hint for logging and for
    choosing the load path; it does not affect the wire format.
    """
    data: bytes
    width: int
    height: int
    is_mask: bool = False

    @property
    def content_hash(self) -> str:
        """8-character lowercase hex CRC32 of the PNG bytes.

        Kept for compatibility with callers that used it as a cache key
        under the old tooling-nodes path. The proxy hashes uploaded
        bytes server-side (blake3), so the daemon no longer needs a
        client-side hash — this property is informational.
        """
        return f"{zlib.crc32(self.data) & 0xFFFFFFFF:08x}"


# ============================================================================
#  Reading
# ============================================================================

def read_canvas_composite(doc, bounds: Bounds) -> EncodedImage:
    """Read the composite of every visible layer, cropped to `bounds`."""
    x, y, w, h = bounds
    raw = bytes(doc.pixelData(x, y, w, h))
    expected = w * h * 4
    if len(raw) != expected:
        raise ValueError(
            f"Canvas pixel data size {len(raw)} does not match "
            f"{w}x{h} RGBA ({expected} bytes). "
            f"Document color model: {doc.colorModel()} "
            f"depth: {doc.colorDepth()}."
        )
    return EncodedImage(
        data=_encode_argb32_png(raw, w, h),
        width=w, height=h, is_mask=False,
    )


def read_selection_mask(doc, bounds: Bounds) -> Optional[EncodedImage]:
    """Read the active selection, cropped to `bounds`.

    Returns None when the document has no selection, or has a selection
    that covers the entire document (Krita creates one of those when the
    user runs Edit > Select All, and downstream nodes should treat it
    the same as "no selection").
    """
    selection = doc.selection()
    if selection is None:
        return None

    x, y, w, h = bounds
    raw = bytes(selection.pixelData(x, y, w, h))
    if len(raw) != w * h:
        raise ValueError(
            f"Selection pixel data size {len(raw)} does not match "
            f"{w}x{h} grayscale ({w * h} bytes)."
        )

    if _is_entirely_opaque(raw):
        return None

    return EncodedImage(
        data=_encode_grayscale_png(raw, w, h),
        width=w, height=h, is_mask=True,
    )


def read_selection_bounds(doc) -> Optional[Bounds]:
    """Return the selection's bounding box, or None if there is none."""
    selection = doc.selection()
    if selection is None:
        return None
    x, y = int(selection.x()), int(selection.y())
    w, h = int(selection.width()), int(selection.height())
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def read_layer_pixels(doc, layer_name: str, bounds: Bounds) -> EncodedImage:
    """Read a named layer's projection, cropped to `bounds`."""
    node = resolve_node(doc, layer_name)
    if node is None:
        raise ValueError(f"Layer not found: {layer_name!r}")

    node_type = node.type()
    if node_type not in IMAGE_LAYER_TYPES:
        raise ValueError(
            f"Layer {layer_name!r} is type {node_type!r}, which does not "
            f"contain color pixels. Use read_layer_mask for mask-only "
            f"layers."
        )

    x, y, w, h = bounds
    raw = bytes(node.projectionPixelData(x, y, w, h))
    expected = w * h * 4
    if len(raw) != expected:
        raise ValueError(
            f"Layer {layer_name!r} pixel data size {len(raw)} does not "
            f"match {w}x{h} RGBA ({expected} bytes)."
        )
    return EncodedImage(
        data=_encode_argb32_png(raw, w, h),
        width=w, height=h, is_mask=False,
    )


def read_layer_mask(doc, layer_name: str, bounds: Bounds) -> EncodedImage:
    """Read a named layer as a mask, cropped to `bounds`."""
    node = resolve_node(doc, layer_name)
    if node is None:
        raise ValueError(f"Layer not found: {layer_name!r}")

    x, y, w, h = bounds
    node_type = node.type()

    if node_type in MASK_LAYER_TYPES:
        raw = bytes(node.pixelData(x, y, w, h))
        if len(raw) != w * h:
            raise ValueError(
                f"Mask layer {layer_name!r} pixel data size {len(raw)} "
                f"does not match {w}x{h} grayscale ({w * h} bytes)."
            )
        return EncodedImage(
            data=_encode_grayscale_png(raw, w, h),
            width=w, height=h, is_mask=True,
        )

    if node_type in IMAGE_LAYER_TYPES:
        raw = bytes(node.projectionPixelData(x, y, w, h))
        if len(raw) != w * h * 4:
            raise ValueError(
                f"Layer {layer_name!r} projection size {len(raw)} does "
                f"not match {w}x{h} RGBA ({w * h * 4} bytes)."
            )
        return EncodedImage(
            data=_encode_alpha8_png(raw, w, h),
            width=w, height=h, is_mask=True,
        )

    raise ValueError(
        f"Layer {layer_name!r} is type {node_type!r}, which cannot be "
        f"read as a mask."
    )


def compute_context_bounds(canvas_extent: tuple[int, int],
                           mask_bounds: Optional[Bounds],
                           context: str,
                           padding: int) -> Bounds:
    """Compute the crop region to read for a workflow.

    Mirrors krita-ai-diffusion's `CustomWorkspace.prepare_mask`:

        "entire_image"  the whole canvas
        "automatic"     the selection's bounds, padded by `padding`
        "mask_bounds"   the selection's bounds, padded, rounded to 1px

    When there is no selection, everything reduces to the whole canvas.
    """
    w, h = canvas_extent
    canvas_bounds: Bounds = (0, 0, w, h)

    if mask_bounds is None:
        return canvas_bounds

    if context == "entire_image":
        return canvas_bounds

    mx, my, mw, mh = mask_bounds
    pad = max(0, int(padding))

    if context == "mask_bounds":
        multiple = 1
    else:  # "automatic"
        multiple = 8

    padded = _pad_bounds((mx, my, mw, mh), pad, multiple=multiple)
    return _clamp_bounds(padded, canvas_bounds)


# ============================================================================
#  Writing
# ============================================================================

def paste_image(doc,
                image_png: bytes,
                x: int, y: int,
                name: str,
                *,
                blend_mode: str = "normal",
                make_active: bool = True,
                rotation: float = 0.0):
    """Decode a PNG and write it as a new paint layer at (x, y).

    `rotation` is degrees, positive = clockwise on screen. When
    nonzero, the image is rotated about its center, then center-cropped
    to the document's current dimensions before being written.

    Refresh strategy: the caller is inside a batch, the document is in
    batchmode, and this runs on the server's worker thread. All three
    suppress the widget-level repaint that would normally make a fresh
    layer appear. Three direct steps nudge Krita into repainting:
    recompute the composite, toggle the new layer's visibility off and
    on, then set the blending mode back to itself (a no-op Krita treats
    as a layer modification).
    """
    img = _decode_png(image_png)

    if rotation and rotation % 360 != 0:
        img = _rotate_about_center(img, rotation)
        doc_w, doc_h = int(doc.width()), int(doc.height())
        img = _center_crop(img, doc_w, doc_h)

    w, h = img.width(), img.height()
    doc_w, doc_h = int(doc.width()), int(doc.height())
    if x < 0 or y < 0 or x + w > doc_w or y + h > doc_h:
        raise ValueError(
            f"paste_image: region ({x},{y}) {w}x{h} extends beyond "
            f"canvas {doc_w}x{doc_h}"
        )

    node = doc.createNode(name, "paintlayer")
    doc.rootNode().addChildNode(node, None)

    pixel_bytes = _qimage_to_argb32_bytes(img)
    node.setPixelData(pixel_bytes, x, y, w, h)

    if blend_mode and blend_mode != "normal":
        node.setBlendingMode(blend_mode)

    if make_active:
        doc.setActiveNode(node)

    try:
        doc.refreshProjection()
    except Exception:
        pass
    try:
        node.setVisible(False)
        node.setVisible(True)
    except Exception:
        pass
    try:
        node.setBlendingMode(node.blendingMode())
    except Exception:
        pass

    return node


def paste_batch_as_layers(doc,
                          images: list[bytes],
                          x: int, y: int,
                          base_name: str,
                          *,
                          blend_mode: str = "normal",
                          rotation: float = 0.0) -> list:
    nodes = []
    for i, png in enumerate(images):
        name = f"[Layer {i + 1}] {base_name}"
        nodes.append(paste_image(
            doc, png, x, y, name,
            blend_mode=blend_mode,
            make_active=(i == 0),
            rotation=rotation,
        ))
    return nodes


def paste_animation(doc, images: list[bytes], start_frame: int = 0) -> bool:
    """Import a sequence of PNGs as a Krita animation."""
    if not images:
        return False

    with tempfile.TemporaryDirectory(prefix="kritomatic_anim_") as tmp:
        paths = []
        for i, png in enumerate(images):
            p = Path(tmp) / f"{i:04d}.png"
            p.write_bytes(png)
            paths.append(str(p))

        try:
            ok = doc.importAnimation(paths, int(start_frame), 1)
        except Exception:
            return False

        return bool(ok)


def resize_canvas(doc, width: int, height: int) -> None:
    """Grow (or shrink) the canvas to the given size."""
    doc.resizeImage(0, 0, int(width), int(height))


# ============================================================================
#  Encoding helpers
# ============================================================================

def _encode_argb32_png(data: bytes, w: int, h: int) -> bytes:
    img = QImage(data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    return _save_png(img)


def _encode_grayscale_png(data: bytes, w: int, h: int) -> bytes:
    img = QImage(data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    return _save_png(img)


def _encode_alpha8_png(rgba_data: bytes, w: int, h: int) -> bytes:
    if len(rgba_data) != w * h * 4:
        raise ValueError(
            f"_encode_alpha8_png: expected {w * h * 4} bytes, "
            f"got {len(rgba_data)}"
        )
    alpha = bytearray(w * h)
    for i in range(w * h):
        alpha[i] = rgba_data[i * 4 + 3]
    return _encode_grayscale_png(bytes(alpha), w, h)


def _save_png(img: QImage) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    if not ok or not data:
        raise RuntimeError("Failed to encode image as PNG")
    return data


def _decode_png(png_bytes: bytes) -> QImage:
    img = QImage()
    if not img.loadFromData(png_bytes, "PNG"):
        raise ValueError(
            f"Failed to decode PNG from ComfyUI output "
            f"({len(png_bytes)} bytes)"
        )
    if img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    return img


def _rotate_about_center(img: QImage, angle_deg: float) -> QImage:
    from PyQt6.QtGui import QTransform
    from PyQt6.QtCore import Qt
    return img.transformed(
        QTransform().rotate(float(angle_deg)),
        Qt.TransformationMode.SmoothTransformation,
    )


def _center_crop(img: QImage, target_w: int, target_h: int) -> QImage:
    w, h = img.width(), img.height()
    if w == target_w and h == target_h:
        return img
    crop_x = max(0, (w - target_w) // 2)
    crop_y = max(0, (h - target_h) // 2)
    crop_w = min(target_w, w)
    crop_h = min(target_h, h)
    if crop_w <= 0 or crop_h <= 0:
        return img
    return img.copy(crop_x, crop_y, crop_w, crop_h)


def _qimage_to_argb32_bytes(img: QImage) -> bytes:
    if img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)

    width = img.width()
    height = img.height()
    expected = width * height * 4

    if img.bytesPerLine() == width * 4:
        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        return bytes(ptr)

    out = bytearray()
    for y in range(height):
        row_ptr = img.scanLine(y)
        row_ptr.setsize(width * 4)
        out.extend(bytes(row_ptr))
    if len(out) != expected:
        raise RuntimeError(
            f"Pixel extraction produced {len(out)} bytes, "
            f"expected {expected}"
        )
    return bytes(out)


# ============================================================================
#  Bounds arithmetic
# ============================================================================

def _is_entirely_opaque(raw: bytes) -> bool:
    if not raw:
        return False
    return all(b == 0xff for b in raw)


def _pad_bounds(bounds: Bounds, pad: int, multiple: int = 1) -> Bounds:
    x, y, w, h = bounds
    px, pw = _pad_scalar(x, w, pad, multiple)
    py, ph = _pad_scalar(y, h, pad, multiple)
    return (px, py, pw, ph)


def _pad_scalar(x: int, size: int, pad: int, multiple: int) -> tuple[int, int]:
    padded = size + 2 * pad
    new_size = _multiple_of(padded, multiple)
    new_x = x - (new_size - size) // 2
    return new_x, new_size


def _multiple_of(n: int, m: int) -> int:
    if m <= 1:
        return n
    return ((n + m - 1) // m) * m


def _clamp_bounds(bounds: Bounds, container: Bounds) -> Bounds:
    x, y, w, h = bounds
    cx, cy, cw, ch = container

    if w >= cw:
        x, w = cx, cw
    else:
        x = max(x, cx)
        excess = max((x + w) - (cx + cw), 0)
        x -= excess

    if h >= ch:
        y, h = cy, ch
    else:
        y = max(y, cy)
        excess = max((y + h) - (cy + ch), 0)
        y -= excess

    return (x, y, w, h)