"""
comfyui_krita.py — read pixels from a Krita document, paste ComfyUI
                   results back into it.

The document adapter for the `comfyui` handler. Everything in this module
is a thin wrapper around the Krita Python API, shaped so the handler can
turn a `WorkflowRequirements` into a `KritaResources`, feed that into
`comfyui_tooling.expand_custom()`, then turn the returned images back
into layers.

It knows nothing about ComfyUI, nothing about the tooling-nodes' ETN_*
semantics, and nothing about the wire protocol. It knows about Krita's
`Document`, `Node`, and `Selection` objects, and about the pixel formats
that the two ends of the pipeline agreed on (see "Pixel format" below).

=============================================================================
PIXEL FORMAT
=============================================================================
The tooling-nodes package reads images through `ETN_LoadImageCache`, which
decodes them with `PIL.Image` and interprets:
  * RGBA PNG  -> tensor (H, W, 4), mask from alpha
  * RGB  PNG  -> tensor (H, W, 3), mask derived from red channel
  * L    PNG  -> tensor (H, W, 1), used directly as mask

Krita's `Document.pixelData` / `Node.projectionPixelData` return raw bytes
in `Format_ARGB32` layout for RGBA/U8 documents — 4 bytes per pixel, BGRA
in little-endian memory, which is the same byte order QImage's
`Format_ARGB32` uses. `Selection.pixelData` and mask-node `pixelData`
return 1 byte per pixel (grayscale 0-255).

So there is no conversion step. We wrap the Krita bytes in a QImage of the
matching format, save as PNG, and hand the PNG to the tooling node. On the
way back, decode the PNG to `Format_ARGB32` and hand the raw bytes to
`Node.setPixelData`. The format agreement is what makes the round trip
work; if either Krita or the tooling nodes change their internal layout,
this is the first place to look.

=============================================================================
BOUNDS
=============================================================================
A "bounds" in this module is always `(x, y, width, height)` in pixels,
with (0, 0) being the canvas top-left. Reads accept a bounds that may be
a sub-region of the canvas; the returned `EncodedImage` is exactly that
sub-region. Writes take a bounds whose (x, y) is where on the canvas the
top-left of the image should land; the image's own size is used for width
and height.

=============================================================================
LAYER RESOLUTION
=============================================================================
Layer lookups go through the scope-aware resolver in `layer_resolve.py`,
not `Document.nodeByName`. That means a bundle that creates a layer named
`X` and later references `X` resolves to the layer it created, even if
another layer called `X` exists in the document. The resolver also
accepts a UUID string in place of a name, for cross-scope references.

In addition to scope resolution, `build_krita_resources` applies a
`layer_aliases` map supplied by the handler: a workflow's declared layer
name (the `name` widget on its `ETN_KritaImageLayer` / `ETN_KritaMaskLayer`
node) is translated through the map before the document lookup. The
workflow graph itself is not modified; only the read uses the alias. This
lets a user run a workflow on a document whose layer is named differently
from what the workflow expects, without editing the workflow file.

=============================================================================
WHAT THIS MODULE DOES NOT DO
=============================================================================
  * No HTTP, no WebSocket. See comfyui_client.py.
  * No graph rewriting. See comfyui_tooling.py.
  * No @command surface. See handlers/comfyui.py.
  * No workflow selection, no parameter handling, no history. The handler
    owns those; this module only touches pixels.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Optional

from krita import Krita
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice
from PyQt6.QtGui import QImage

from .comfyui_tooling import EncodedImage, KritaResources, WorkflowRequirements
from .layer_resolve import resolve as resolve_node
from .refresh import refresh


# A region of the canvas. (x, y, width, height) in pixels.
Bounds = tuple[int, int, int, int]


# ============================================================================
#  Reading
# ============================================================================

def read_canvas_composite(doc, bounds: Bounds) -> EncodedImage:
    """Read the composite of every visible layer, cropped to `bounds`.

    `doc.pixelData(x, y, w, h)` returns the flattened projection of the
    layer tree, in the same pixel format `Node.setPixelData` accepts on
    the way back. That symmetry is deliberate: what comes out of a
    canvas read is directly re-usable if a workflow ever needs to write
    it straight back (out of scope for now, but free).

    Raises ValueError if the byte count does not match the bounds, which
    would indicate a color-model mismatch — the handler is expected to
    have called `doc.colorModel()` / `doc.colorDepth()` first and
    refused to proceed on anything other than RGBA/U8.
    """
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
    the same as "no selection"). The tooling module's `_expand_selection`
    then synthesizes a SolidMask instead.

    `Selection.pixelData(x, y, w, h)` returns 1 byte per pixel, 0-255.
    Cropping is not clamping: if `bounds` extends beyond the canvas, the
    values outside the canvas are 0 (unselected), which is what we want
    for a masked generation.
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
    """Read a named layer's projection, cropped to `bounds`.

    `projectionPixelData` returns the layer composited with any of its
    child masks and filter masks — what the user sees for that layer,
    not the raw paint node.

    Resolution goes through the scope-aware resolver, so a bundle that
    created a layer named `X` earlier in the same scope reaches that
    layer even if another layer called `X` exists in the document.

    Raises ValueError if the layer is not found or is not an image-
    bearing layer type (paint, group, file, vector, clone, fill,
    filter). Mask-only layers (transparency, selection) must go
    through `read_layer_mask` instead.
    """
    node = resolve_node(doc, layer_name)
    if node is None:
        raise ValueError(f"Layer not found: {layer_name!r}")

    node_type = node.type()
    if node_type not in _IMAGE_LAYER_TYPES:
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
    """Read a named layer as a mask, cropped to `bounds`.

    Two cases:
      * Mask-only layer (transparency mask, selection mask): read the
        grayscale channel directly.
      * Image layer: take the alpha channel of the projection.

    Both produce 1 byte per pixel, matching what the tooling node's
    `ETN_KritaMaskLayer` expects.
    """
    node = resolve_node(doc, layer_name)
    if node is None:
        raise ValueError(f"Layer not found: {layer_name!r}")

    x, y, w, h = bounds
    node_type = node.type()

    if node_type in _MASK_LAYER_TYPES:
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

    if node_type in _IMAGE_LAYER_TYPES:
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


# ============================================================================
#  Assembling the resources bundle
# ============================================================================

def build_krita_resources(doc,
                          reqs: WorkflowRequirements,
                          *,
                          bounds: Optional[Bounds] = None,
                          selection_context: str = "entire_image",
                          selection_padding: int = 0,
                          layer_aliases: Optional[dict[str, str]] = None,
                          params: Optional[dict] = None) -> KritaResources:
    """Read exactly what `reqs` asked for and pack it into KritaResources.

    The caller obtains `reqs` by calling
    `comfyui_tooling.scan_requirements(workflow)` on the workflow before
    it is rewritten, then passes the workflow's `ETN_KritaSelection`
    widget values (`context`, `padding`) alongside so the crop region
    can be computed here. Doing the read here rather than in the handler
    keeps the handler focused on orchestration.

    `layer_aliases` maps a workflow's declared layer names to actual
    document layer names. Each name in `reqs.layer_images` and
    `reqs.layer_masks` is translated through the map before the document
    lookup; the key used in the returned `KritaResources` is still the
    workflow's declared name, so `expand_custom` continues to find the
    blob under the name the graph expects.

    `bounds` defaults to the full canvas. When `reqs.needs_selection` is
    set, the crop region is recomputed from the selection's bounding box
    and the node's `context`.

    Every read is skipped when the corresponding `reqs` flag is unset,
    so a workflow that only needs a named layer mask never triggers a
    full-canvas composite read.
    """
    canvas_bounds: Bounds = (
        bounds if bounds is not None
        else (0, 0, int(doc.width()), int(doc.height()))
    )

    if reqs.needs_selection:
        mask_bounds = read_selection_bounds(doc)
        crop = compute_context_bounds(
            canvas_extent=(int(doc.width()), int(doc.height())),
            mask_bounds=mask_bounds,
            context=selection_context,
            padding=selection_padding,
        )
    else:
        crop = canvas_bounds

    resources = KritaResources(canvas_extent=(crop[2], crop[3]))

    if reqs.needs_canvas:
        resources.canvas_image = read_canvas_composite(doc, crop)

    if reqs.needs_selection:
        resources.selection_mask = read_selection_mask(doc, crop)
        resources.selection_bounds = crop

    aliases = layer_aliases or {}

    for name in reqs.layer_images:
        actual = aliases.get(name, name)
        try:
            resources.layer_images[name] = read_layer_pixels(doc, actual, crop)
        except ValueError as e:
            available = [n.name() for n in _all_layers(doc)]
            raise ValueError(
                f"{e}"
                + (f"\n  (workflow declares {name!r}, "
                   f"alias resolves to {actual!r})"
                   if actual != name else "")
                + f"\nLayers in the document: {sorted(available)}"
            ) from e

    for name in reqs.layer_masks:
        actual = aliases.get(name, name)
        try:
            resources.layer_masks[name] = read_layer_mask(doc, actual, crop)
        except ValueError as e:
            available = [n.name() for n in _all_layers(doc)]
            raise ValueError(
                f"{e}"
                + (f"\n  (workflow declares {name!r}, "
                   f"alias resolves to {actual!r})"
                   if actual != name else "")
                + f"\nLayers in the document: {sorted(available)}"
            ) from e

    return resources


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
                make_active: bool = True):
    """Decode a PNG and write it as a new paint layer at (x, y).

    The PNG comes straight from ComfyUI. QImage decodes it to
    `Format_ARGB32`, which is the layout `Node.setPixelData` expects, so
    the handoff is a straight byte copy.

    `blend_mode` is Krita's blending-mode string ("normal", "multiply",
    "screen", ...).

    Refresh: the caller is inside a batch, the document is in
    batchmode, and this runs on the server's worker thread. All three
    suppress the widget-level repaint that would normally make a fresh
    layer appear. The `refresh()` helper defers during a batch and is a
    no-op at flush time, so calling it here would not help. Instead
    this method takes three direct steps:

      1. `doc.refreshProjection()` — recompute the document composite
         so the projection the canvas draws from is up to date.
      2. Toggle the new layer's visibility off and on — this is the
         same signal path a user's manual hide/show triggers, and it is
         what schedules the widget repaint on the main thread.
      3. `node.setBlendingMode(node.blendingMode())` — a cheap no-op
         that Krita treats as a layer modification. The AI-Diffusion
         plugin uses the same trick for the same reason.

    Order matters: recompute first, then nudge the widget, so the
    canvas draws from fresh pixels when it does repaint.

    Returns the new layer's `Node`.
    """
    img = _decode_png(image_png)
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

    # ---- Force the canvas to actually show the new layer. ----
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
                          blend_mode: str = "normal") -> list:
    """One layer per image in the batch, prefixed `[Layer i] `."""
    nodes = []
    for i, png in enumerate(images):
        name = f"[Layer {i + 1}] {base_name}"
        nodes.append(paste_image(
            doc, png, x, y, name,
            blend_mode=blend_mode,
            make_active=(i == 0),
        ))
    return nodes


def paste_animation(doc, images: list[bytes], start_frame: int = 0) -> bool:
    """Import a sequence of PNGs as a Krita animation.

    Krita's `Document.importAnimation` takes a list of file paths, so
    each PNG is written to a temp file first; the temp files are cleaned
    up after the import regardless of outcome.

    Returns True on success.
    """
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
    """Grow (or shrink) the canvas to the given size.

    `Document.resizeImage` takes (x, y, width, height); the offset is
    zero so existing content stays anchored at the top-left.
    """
    doc.resizeImage(0, 0, int(width), int(height))


# ============================================================================
#  Encoding helpers
# ============================================================================
#
# Krita's pixelData returns raw bytes; QImage's format constructors accept
# a buffer + dimensions + stride and (by default) reference the buffer
# without copying. We always call .copy() on the constructed QImage so
# the buffer can be garbage-collected freely after this function returns.

def _encode_argb32_png(data: bytes, w: int, h: int) -> bytes:
    """Wrap RGBA bytes as a PNG."""
    img = QImage(data, w, h, w * 4, QImage.Format.Format_ARGB32).copy()
    return _save_png(img)


def _encode_grayscale_png(data: bytes, w: int, h: int) -> bytes:
    """Wrap 1-byte-per-pixel mask data as a PNG."""
    img = QImage(data, w, h, w, QImage.Format.Format_Grayscale8).copy()
    return _save_png(img)


def _encode_alpha8_png(rgba_data: bytes, w: int, h: int) -> bytes:
    """Extract the alpha channel from RGBA bytes and encode it as a PNG."""
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
    """Encode a QImage as PNG bytes."""
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = img.save(buf, "PNG")
    data = bytes(buf.data())
    buf.close()
    if not ok or not data:
        raise RuntimeError("Failed to encode image as PNG")
    return data


def _decode_png(png_bytes: bytes) -> QImage:
    """Decode PNG bytes to a QImage in Format_ARGB32."""
    img = QImage()
    if not img.loadFromData(png_bytes, "PNG"):
        raise ValueError(
            f"Failed to decode PNG from ComfyUI output "
            f"({len(png_bytes)} bytes)"
        )
    if img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)
    return img


def _qimage_to_argb32_bytes(img: QImage) -> bytes:
    """Extract pixel bytes in Krita's `setPixelData` layout."""
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
#  Layer type sets
# ============================================================================

_IMAGE_LAYER_TYPES = frozenset({
    "paintlayer",
    "grouplayer",
    "filelayer",
    "vectorlayer",
    "clonelayer",
    "filllayer",
    "filterlayer",
})

_MASK_LAYER_TYPES = frozenset({
    "transparencymask",
    "selectionmask",
})


# ============================================================================
#  Bounds arithmetic
# ============================================================================

def _is_entirely_opaque(raw: bytes) -> bool:
    """True iff every byte is 0xff. Used to detect Krita's select-all."""
    if not raw:
        return False
    return all(b == 0xff for b in raw)


def _pad_bounds(bounds: Bounds, pad: int,
                multiple: int = 1) -> Bounds:
    """Grow a bounds by `pad` pixels on every side, then round the
    result to a multiple."""
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
    """Clamp a bounds to fit inside `container`."""
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
#  Layer tree walk (used only for error messages)
# ============================================================================

def _all_layers(doc):
    """Every named layer under the root, depth-first."""
    out = []
    def walk(node):
        for child in node.childNodes():
            if child.name():
                out.append(child)
            walk(child)
    walk(doc.rootNode())
    return out
