"""Minimal image utilities for the SAM3 playground.

Replaces the Krita-specific parts of kritomatic's comfyui_krita.py. Only
what a browser-driven workflow needs: raw bytes → PNG, PNG → raw bytes,
and PNG dimensions. Uses PIL for encode/decode.

`EncodedImage` is re-exported from `tooling.py` (which is copied verbatim
from kritomatic); no new type is defined here.
"""

from io import BytesIO
from typing import Tuple

from PIL import Image

from .tooling import EncodedImage


PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_png(data: bytes) -> bool:
    return data[:8] == PNG_MAGIC


def png_dimensions(png_bytes: bytes) -> Tuple[int, int]:
    with Image.open(BytesIO(png_bytes)) as im:
        return im.width, im.height


def encode_png_from_rgba(raw: bytes, width: int, height: int) -> bytes:
    img = Image.frombytes("RGBA", (width, height), raw)
    buf = BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def decode_png_to_rgba(png_bytes: bytes) -> Tuple[bytes, int, int]:
    with Image.open(BytesIO(png_bytes)) as im:
        im = im.convert("RGBA")
        return im.tobytes(), im.width, im.height


def make_encoded_image(raw_bytes: bytes) -> EncodedImage:
    """Turn arbitrary uploaded bytes into an EncodedImage.

    The workflow's `ETN_LoadImageCache` decodes with PIL and expects a
    PNG or one of PIL's other readable formats. We normalize to PNG
    here so the tooling never sees JPEG/WebP/etc; the cost is one
    decode/re-encode on non-PNG uploads.
    """
    if is_png(raw_bytes):
        w, h = png_dimensions(raw_bytes)
        return EncodedImage(data=raw_bytes, width=w, height=h, is_mask=False)

    with Image.open(BytesIO(raw_bytes)) as im:
        im = im.convert("RGBA")
        w, h = im.width, im.height
        png = encode_png_from_rgba(im.tobytes(), w, h)
        return EncodedImage(data=png, width=w, height=h, is_mask=False)
