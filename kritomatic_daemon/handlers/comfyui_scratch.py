"""Scratch blobs: transient image data pushed to the workflow without
appearing as Krita layers.

The inpaint playground writes three blobs per run (canvas, selection,
context) into `_scratch_blobs` via `comfy_set_layer_pixels`. The next
`comfy_generate` call reads them through `build_krita_resources` (see
`comfyui_krita.py::build_krita_resources`). No Krita nodes are
created; the blobs live only in memory and are lost on daemon reload.

Actively worked on — this subsystem is new and likely to grow.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Optional

from krita import Krita
from PyQt6.QtGui import QImage
from PyQt6.QtCore import Qt

from ..decorators import command, AutoDispatchHandler
from ..utils.comfyui_krita import (
    _encode_argb32_png,
    _encode_grayscale_png,
)
from ..utils.comfyui_tooling import EncodedImage


def _png_bytes_to_encoded(png_bytes: bytes,
                          is_mask: bool,
                          target_w: Optional[int] = None,
                          target_h: Optional[int] = None) -> EncodedImage:
    """Decode a playground PNG into an EncodedImage ready for upload.

    Images keep RGBA bytes as-is. Masks have their R channel extracted
    into a Format_Grayscale8 PNG — same shape `_encode_alpha8_png`
    produces when reading a mask from a Krita layer, so the workflow
    sees the same thing either way.

    If `target_w` / `target_h` are given and differ, the image is
    scaled. That mirrors the persist-in-doc path so a scratch blob is
    always the same size as the document.
    """
    img = QImage()
    if not img.loadFromData(png_bytes, "PNG"):
        raise ValueError("could not decode PNG")
    if img.format() != QImage.Format.Format_ARGB32:
        img = img.convertToFormat(QImage.Format.Format_ARGB32)

    if target_w and target_h and (
        img.width() != target_w or img.height() != target_h
    ):
        img = img.scaled(
            target_w, target_h,
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        img = img.convertToFormat(QImage.Format.Format_ARGB32)

    w, h = img.width(), img.height()
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    src = bytes(ptr)

    if is_mask:
        gray = bytearray(w * h)
        for i in range(w * h):
            gray[i] = src[i * 4 + 2]
        return EncodedImage(
            data=_encode_grayscale_png(bytes(gray), w, h),
            width=w, height=h, is_mask=True,
        )
    return EncodedImage(
        data=_encode_argb32_png(src, w, h),
        width=w, height=h, is_mask=False,
    )


class ScratchCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Store an image blob for use by the next comfy_generate "
                  "call. By default the PNG is kept in memory (a "
                  "'scratch blob') and does not appear in the document. "
                  "Pass --persist_in_doc true to instead create/update a "
                  "hidden Krita paint layer with that name (old "
                  "behavior).",
        args={
            "--name": {"type": "str", "required": True,
                       "help": "Blob name (matches a workflow's "
                               "ETN_KritaImageLayer / "
                               "ETN_KritaMaskLayer `name` widget)."},
            "--image_b64": {"type": "str", "required": True,
                            "help": "Base64-encoded PNG."},
            "--is_mask": {"type": "bool", "default": False,
                          "help": "If true, the PNG's R channel is the "
                                  "mask value."},
            "--persist_in_doc": {"type": "bool", "default": False,
                                 "help": "If true, create/update a Krita "
                                         "paint layer with that name."},
        },
    )
    def comfy_set_layer_pixels(self, params):
        from PyQt6.QtCore import QByteArray
        parent = self.parent

        name = params.get("name") or ""
        image_b64 = params.get("image_b64") or ""
        is_mask = bool(params.get("is_mask", False))
        persist = bool(params.get("persist_in_doc", False))

        if not name:
            return {"success": False, "message": "name is required"}
        if not image_b64:
            return {"success": False, "message": "image_b64 is required"}

        doc = Krita.instance().activeDocument()
        if doc is None:
            return {"success": False, "message": "no active document"}

        try:
            png_bytes = base64.b64decode(image_b64)
        except Exception as e:
            return {"success": False,
                    "message": f"could not decode base64: {e}"}

        W, H = int(doc.width()), int(doc.height())

        if not persist:
            try:
                encoded = _png_bytes_to_encoded(
                    png_bytes, is_mask, target_w=W, target_h=H,
                )
            except ValueError as e:
                return {"success": False, "message": str(e)}
            parent._scratch_blobs[name] = {"encoded": encoded}
            print(f"[comfyui] comfy_set_layer_pixels: stored scratch blob "
                  f"{name!r} ({encoded.width}x{encoded.height}, "
                  f"is_mask={encoded.is_mask}, {len(encoded.data)} bytes)",
                  flush=True)
            return {
                "success": True,
                "message": f"Stored scratch blob {name!r}",
                "data": {"name": name,
                         "width": encoded.width,
                         "height": encoded.height,
                         "is_mask": encoded.is_mask,
                         "persist_in_doc": False},
            }

        # Persist-in-doc path (unchanged from before the split).
        img = QImage()
        if not img.loadFromData(png_bytes, "PNG"):
            return {"success": False, "message": "could not decode PNG"}
        if img.format() != QImage.Format.Format_ARGB32:
            img = img.convertToFormat(QImage.Format.Format_ARGB32)

        if img.width() != W or img.height() != H:
            img = img.scaled(
                W, H,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            img = img.convertToFormat(QImage.Format.Format_ARGB32)

        ptr = img.constBits()
        ptr.setsize(img.sizeInBytes())
        src = bytes(ptr)

        if is_mask:
            out = bytearray(W * H * 4)
            for i in range(W * H):
                r = src[i * 4 + 2]
                out[i * 4]     = 255
                out[i * 4 + 1] = 255
                out[i * 4 + 2] = 255
                out[i * 4 + 3] = r
            pixel_bytes = bytes(out)
        else:
            pixel_bytes = src

        removed_types = []
        while True:
            existing = doc.nodeByName(name)
            if existing is None:
                break
            removed_types.append(existing.type())
            try:
                existing.remove()
            except Exception as e:
                return {"success": False,
                        "message": f"could not remove existing layer "
                                   f"{name!r} (type={existing.type()!r}): "
                                   f"{e}"}
        if removed_types:
            print(f"[comfyui] comfy_set_layer_pixels: removed "
                  f"pre-existing {name!r} of type(s) {removed_types}",
                  flush=True)

        node = doc.createNode(name, "paintlayer")
        if node is None:
            return {"success": False,
                    "message": f"could not create layer {name!r}"}
        doc.rootNode().addChildNode(node, None)
        node.setPixelData(QByteArray(pixel_bytes), 0, 0, W, H)
        node.setVisible(False)
        doc.refreshProjection()
        print(f"[comfyui] comfy_set_layer_pixels: created {name!r} "
              f"(paintlayer, is_mask={is_mask})", flush=True)
        return {
            "success": True,
            "message": f"Created {name!r} (hidden)",
            "data": {"name": name, "created": True,
                     "persist_in_doc": True},
        }

    @command(
        category="comfyui",
        help_text="List the in-memory scratch blobs.",
        args={},
    )
    def comfy_list_scratch_blobs(self, params):
        parent = self.parent
        items = []
        for name, entry in sorted(parent._scratch_blobs.items()):
            enc = entry["encoded"]
            items.append({
                "name": name,
                "width": enc.width,
                "height": enc.height,
                "is_mask": enc.is_mask,
                "size_bytes": len(enc.data),
            })
        return {
            "success": True,
            "message": f"{len(items)} scratch blob(s)",
            "data": {"blobs": items},
        }

    @command(
        category="comfyui",
        help_text="Write the current scratch blobs to a directory as "
                  "PNG files (one per blob).",
        args={
            "--dir": {"type": "str", "required": True,
                      "help": "Output directory."},
        },
    )
    def comfy_dump_scratch_blobs(self, params):
        parent = self.parent
        out_dir = params.get("dir") or ""
        if not out_dir:
            return {"success": False, "message": "dir is required"}
        path = Path(out_dir).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"success": False,
                    "message": f"could not create dir: {e}"}
        written = []
        for name, entry in sorted(parent._scratch_blobs.items()):
            enc = entry["encoded"]
            safe = "".join(
                ch if ch.isalnum() or ch in "-_." else "_" for ch in name
            ) or "blob"
            p = path / f"{safe}.png"
            try:
                p.write_bytes(enc.data)
                written.append(str(p))
            except Exception as e:
                return {"success": False,
                        "message": f"write failed for {name!r}: {e}"}
        return {
            "success": True,
            "message": f"Dumped {len(written)} blob(s)",
            "data": {"paths": written},
        }

    @command(
        category="comfyui",
        help_text="Clear one in-memory scratch blob.",
        args={
            "--name": {"type": "str", "required": True,
                       "help": "Blob name."},
        },
    )
    def comfy_clear_scratch_blob(self, params):
        parent = self.parent
        name = params.get("name") or ""
        if not name:
            return {"success": False, "message": "name is required"}
        if name not in parent._scratch_blobs:
            return {"success": False,
                    "message": f"No scratch blob {name!r}",
                    "data": {"available":
                             sorted(parent._scratch_blobs.keys())}}
        del parent._scratch_blobs[name]
        return {"success": True, "message": f"Cleared {name!r}"}

    @command(
        category="comfyui",
        help_text="Clear every in-memory scratch blob.",
        args={},
    )
    def comfy_clear_all_scratch_blobs(self, params):
        parent = self.parent
        n = len(parent._scratch_blobs)
        parent._scratch_blobs.clear()
        return {"success": True,
                "message": f"Cleared {n} scratch blob(s)"}