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
    ``resize_canvas`` to write downloaded outputs back into the document;
  * ``pad_png`` to expand a pasted PNG's frame before un-rotation.

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

=============================================================================
ASSET UPLOAD CACHE
=============================================================================
`_ASSET_UPLOAD_CACHE` deduplicates proxy asset uploads across concurrent
`prepare_workflow` calls. Keyed by `(proxy_pid, content_hash)`:

  · **content_hash** comes from `EncodedImage.content_hash` (crc32 of
    the PNG bytes). Two runs that read the same `@thecanvas` pixels
    produce the same PNG bytes, get the same hash, and share one
    upload. A canvas that changed between runs produces different
    bytes, gets a different hash, and uploads fresh. Invalidation is
    automatic — the key changes when the content changes.

  · **proxy_pid** is the OS pid of the proxy process the client is
    bound to (`ProxyV2.pid`, populated by `from_pid_file()`). Proxy
    restart means new pid, meaning the old cache entries are
    unreachable. The pid component is what makes proxy restart safe:
    without it, a live daemon with a restarted proxy would submit
    asset references the new proxy doesn't know about.

The cache is bypassed entirely when `ProxyV2.pid` is None (client
constructed directly with `(host, port)`, no pid file consulted) —
in that case the caller is either a diagnostic or a non-daemon path
where the cache's "shared across runs" property is not useful.

A 5-minute TTL bounds cache growth when the daemon outlives many
proxies. A per-key `threading.Lock` serializes concurrent uploads of
the *same* bytes (the second waiter finds the cached entry after the
first completes); different hashes upload in parallel.

`invalidate_asset_cache(pid)` drops every entry for one pid.  The
caller (in `comfyui_generation.py`) invokes it on a submit-time
`ProxyHTTPError`, so a stale asset reference in the proxy's view
(self-healing case: proxy evicted assets within one pid lifetime)
forces the *next* run to re-upload.
"""

from __future__ import annotations

import tempfile
import threading
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage

from .comfyui_proxy_client import ProxyHTTPError, ProxyUnreachable
from .layer_resolve import resolve as resolve_node
from .layer_types import IMAGE_LAYER_TYPES, MASK_LAYER_TYPES


Bounds = tuple[int, int, int, int]


# ============================================================================
#  EncodedImage
# ============================================================================

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
        """8-character lowercase hex CRC32 of the PNG bytes."""
        return f"{zlib.crc32(self.data) & 0xFFFFFFFF:08x}"


# ============================================================================
#  Asset upload cache
# ============================================================================

class _AssetUploadCache:
    """Dedup of proxy asset uploads keyed by (proxy_pid, content_hash).

    See the module docstring, ASSET UPLOAD CACHE, for the full design.
    """

    def __init__(self, ttl_sec: float = 300.0):
        self._ttl = ttl_sec
        self._lock = threading.Lock()
        self._entries: dict[tuple[int, str], tuple[dict, float]] = {}
        self._key_locks: dict[tuple[int, str], threading.Lock] = {}

    def get(self, proxy_pid: int, content_hash: str) -> Optional[dict]:
        key = (proxy_pid, content_hash)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            asset, stored_at = entry
            if time.monotonic() - stored_at > self._ttl:
                self._entries.pop(key, None)
                return None
            return asset

    def get_or_upload(
        self,
        proxy_pid: int,
        blob: EncodedImage,
        proxy,
        file_path: str,
    ) -> dict:
        h = blob.content_hash
        cached = self.get(proxy_pid, h)
        if cached is not None:
            return cached
        key = (proxy_pid, h)
        with self._lock:
            lk = self._key_locks.get(key)
            if lk is None:
                lk = threading.Lock()
                self._key_locks[key] = lk
        with lk:
            # Recheck under the per-key lock: another thread may have
            # uploaded while this one waited for the lock.
            cached = self.get(proxy_pid, h)
            if cached is not None:
                return cached
            asset = proxy.upload_asset(blob.data, file_path, "image/png")
            with self._lock:
                self._entries[key] = (asset, time.monotonic())
            return asset

    def invalidate_pid(self, proxy_pid: int) -> None:
        with self._lock:
            for k in [k for k in self._entries if k[0] == proxy_pid]:
                del self._entries[k]
            for k in [k for k in self._key_locks if k[0] == proxy_pid]:
                del self._key_locks[k]


_ASSET_UPLOAD_CACHE = _AssetUploadCache()


def invalidate_asset_cache(proxy_pid: Optional[int]) -> None:
    """Drop every cached upload for `proxy_pid`.  No-op for None/0.

    Called by the generation pipeline when a submit attempt fails with
    a `ProxyHTTPError` (proxy-side rejection — could be a stale asset
    reference, e.g. the proxy evicted assets within one pid lifetime).
    Invalidation is cheap; the *next* run re-uploads and succeeds.
    The current run has already failed and is reported as such.
    """
    if proxy_pid:
        _ASSET_UPLOAD_CACHE.invalidate_pid(int(proxy_pid))


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
    that covers the entire document.
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

    Mirrors krita-ai-diffusion's `CustomWorkspace.prepare_mask`.
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
#  Padding (un-rotation fix)
# ============================================================================

def pad_png(png_bytes: bytes, pad_x: int, pad_y: int,
            new_w: int, new_h: int) -> bytes:
    """Decode PNG, place at (pad_x, pad_y) in a new_w × new_h canvas,
    re-encode as PNG.

    Used by the paste path when un-rotating a rotated workflow output.
    The workflow was prepared by rotating the canvas by θ (giving a
    W' × H' frame), then cropping the central (cropW × cropH).  When
    the workflow returns the same cropW × cropH output, un-rotating it
    directly clips the four tips of the rotated diamond, leaving
    transparent corners in the final content.  Padding back to W' × H'
    at ((W' - cropW) / 2, (H' - cropH) / 2) reconstructs the full
    rotated frame, so un-rotation recovers the full un-rotated canvas
    (edge-to-edge, no clipping).
    """
    from PyQt6.QtGui import QPainter

    img = QImage()
    if not img.loadFromData(png_bytes, "PNG"):
        raise ValueError("could not decode PNG for padding")
    img = img.convertToFormat(QImage.Format.Format_ARGB32)

    padded = QImage(new_w, new_h, QImage.Format.Format_ARGB32)
    padded.fill(0)
    painter = QPainter(padded)
    painter.drawImage(pad_x, pad_y, img)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    padded.save(buf, "PNG")
    out = bytes(buf.data())
    buf.close()
    return out


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
    on, then set the blending mode back to itself.
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


# ============================================================================
#  Upload
# ============================================================================

def _upload_resource_blobs(blobs: dict[str, EncodedImage],
                           proxy_client) -> dict[str, dict]:
    """Upload every blob to the proxy, returning {name: asset_dict}.

    Uses `_ASSET_UPLOAD_CACHE` (keyed by `(proxy_pid, content_hash)`)
    when the client exposes a `.pid`, so concurrent prepare calls
    within one proxy lifetime skip duplicate uploads.  See the module
    docstring, ASSET UPLOAD CACHE.

    `ProxyUnreachable` and `ProxyHTTPError` are propagated unchanged
    (not wrapped in ValueError) so callers can invalidate the client /
    asset cache and treat the run as failed.  Only unexpected local
    errors (e.g. missing bytes) are wrapped as ValueError.
    """
    pid = getattr(proxy_client, "pid", None)
    refs: dict[str, dict] = {}
    for name, blob in blobs.items():
        slug = name.lstrip("@") or "resource"
        file_path = f"input/{slug}.png"
        try:
            if pid:
                asset = _ASSET_UPLOAD_CACHE.get_or_upload(
                    pid, blob, proxy_client, file_path,
                )
            else:
                asset = proxy_client.upload_asset(
                    blob.data, file_path, "image/png",
                )
        except (ProxyUnreachable, ProxyHTTPError):
            raise
        except Exception as e:
            raise ValueError(
                f"upload of {name!r} failed: {e}"
            ) from e
        refs[name] = asset
    return refs