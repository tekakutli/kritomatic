#!/usr/bin/env python3
"""
Nautilus right-click: open all selected images in Krita as either
one document (one layer per image) or one document per image.

Three independent knobs (see CONFIGURABLE SETTINGS below):

  • MULTI_DOCUMENT
        False → all images layered into ONE document whose canvas is the
                bounding box of the (possibly scaled) images.
        True  → ONE document per image; each document's canvas equals that
                image's own (possibly scaled) size. CENTER_IMAGES becomes
                a no-op in this mode; SCALE_TO_SAME_SIZE still applies, so
                every resulting document ends up with the same longest side.

  • CENTER_IMAGES
        True  → center each image on the canvas.
        False → place each image at (0, 0).
        (Ignored when MULTI_DOCUMENT = True.)

  • SCALE_TO_SAME_SIZE
        True  → shrink every image so its longest side equals the smallest
                "longest side" among all selected images. Never upscales;
                keeps aspect ratio.
        False → keep native sizes.

Builds OpenRaster (.ora) files (plain PNG layers) and opens them with
`krita` in one call, so they all land in the running Krita instance
(requires Settings > Configure Krita > General > Multiple instances
= "Reuse existing").

Place in ~/.local/share/nautilus/scripts/ (or a submenu folder).

Requires Pillow:  pip install --user pillow
"""

import io
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    print("❌ Pillow is required: pip install --user pillow")
    sys.exit(1)

# ===== CONFIGURABLE SETTINGS =====
# False → all images as layers in one document
# True  → one separate document per image
MULTI_DOCUMENT = False

# True  → center each image on the canvas
# False → place each image at (0, 0)
# (Ignored when MULTI_DOCUMENT = True.)
CENTER_IMAGES = True

# True  → scale every image down so its longest side equals the smallest
#         "longest side" across the selection. Never upscales.
# False → keep native sizes.
SCALE_TO_SAME_SIZE = True

# Adjust if Krita isn't on PATH, e.g.:
#   KRITA_CMD = ["flatpak", "run", "org.kde.krita"]
#   KRITA_CMD = ["snap", "run", "krita"]
KRITA_CMD = ["krita"]
# =================================


def compute_layout(paths: list[str],
                   scale_to_same_size: bool
                   ) -> tuple[int, int, list[tuple[str, tuple[int, int], tuple[int, int]]]]:
    """Return (canvas_w, canvas_h, layout).

    layout is a list of (path, (orig_w, orig_h), (target_w, target_h)).
    """
    dims: list[tuple[int, int]] = []
    for p in paths:
        with Image.open(p) as im:
            dims.append(im.size)

    if scale_to_same_size and dims:
        target = min(max(w, h) for (w, h) in dims)
    else:
        target = None

    sizes: list[tuple[int, int]] = []
    for (w, h) in dims:
        if target is None:
            sizes.append((w, h))
        else:
            longest = max(w, h)
            if longest <= target:
                sizes.append((w, h))
            else:
                s = target / longest
                sizes.append((max(1, round(w * s)),
                              max(1, round(h * s))))

    canvas_w = max(w for (w, _) in sizes)
    canvas_h = max(h for (_, h) in sizes)

    layout = [(p, dims[i], sizes[i]) for i, p in enumerate(paths)]
    return canvas_w, canvas_h, layout


def composited_layer(path: str,
                     target_size: tuple[int, int],
                     canvas_w: int, canvas_h: int,
                     center: bool) -> Image.Image:
    tw, th = target_size
    with Image.open(path) as src:
        src = src.convert("RGBA")
        if src.size != (tw, th):
            src = src.resize((tw, th), Image.LANCZOS)
        canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
        if center:
            x = (canvas_w - tw) // 2
            y = (canvas_h - th) // 2
        else:
            x = y = 0
        canvas.alpha_composite(src, (x, y))
        return canvas


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_ora(dest_path: str,
              canvas_w: int, canvas_h: int,
              layout: list[tuple[str, tuple[int, int], tuple[int, int]]],
              center: bool) -> None:
    layers: list[tuple[str, Image.Image]] = []
    used_names: set[str] = set()
    for i, (path, _orig, target_size) in enumerate(layout):
        base = Path(path).stem or f"Layer {i + 1}"
        name, n = base, 1
        while name in used_names:
            name = f"{base} ({n})"
            n += 1
        used_names.add(name)
        layers.append((name, composited_layer(path, target_size,
                                              canvas_w, canvas_h, center)))

    layer_xml = []
    for name, _ in layers:
        safe = (name.replace("&", "&amp;")
                    .replace("<", "&lt;")
                    .replace(">", "&gt;")
                    .replace('"', "&quot;"))
        layer_xml.append(
            f'    <layer name="{safe}" src="data/{safe}.png" '
            f'x="0" y="0" opacity="1.0" visibility="visible"/>'
        )

    stack = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<image version="0.0.3" w="{}" h="{}">\n'
        '  <stack>\n'
        '{}'
        '  </stack>\n'
        '</image>\n'
    ).format(canvas_w, canvas_h, "\n".join(layer_xml) + "\n")

    merged = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    for _, img in reversed(layers):
        merged.alpha_composite(img)

    thumb = merged.copy()
    thumb.thumbnail((256, 256))

    with zipfile.ZipFile(dest_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zi = zipfile.ZipInfo("mimetype")
        zi.compress_type = zipfile.ZIP_STORED
        zf.writestr(zi, b"image/openraster")

        zf.writestr("stack.xml", stack)
        zf.writestr("mergedimage.png", png_bytes(merged))
        zf.writestr("Thumbnails/thumbnail.png", png_bytes(thumb))

        for name, img in layers:
            safe = (name.replace("&", "&amp;")
                        .replace("<", "&lt;")
                        .replace(">", "&gt;")
                        .replace('"', "&quot;"))
            zf.writestr(f"data/{safe}.png", png_bytes(img))


def collect_paths(argv: list[str]) -> list[str]:
    paths: list[str] = []
    for raw in argv:
        p = Path(raw).expanduser()
        if p.is_file():
            paths.append(str(p))
        else:
            print(f"⚠️  Skipping (not found): {raw}")
    return paths


def launch_krita(ora_paths: list[str]) -> None:
    try:
        subprocess.Popen(KRITA_CMD + ora_paths)
    except FileNotFoundError:
        print(f"❌ `{' '.join(KRITA_CMD)}` not found.")
        sys.exit(1)


def run_multi_document(layout) -> None:
    """One .ora per image; each canvas equals that image's (scaled) size."""
    ora_paths: list[str] = []
    for i, (path, (ow, oh), (tw, th)) in enumerate(layout):
        fd, ora_path = tempfile.mkstemp(
            suffix=".ora", prefix=f"krita_single_{i}_")
        os.close(fd)
        try:
            # Single-layer doc: canvas == target size, center flag irrelevant.
            build_ora(ora_path, tw, th,
                      [(path, (ow, oh), (tw, th))], center=True)
            ora_paths.append(ora_path)
            if (ow, oh) != (tw, th):
                print(f"📦 {ora_path}  ({ow}×{oh} → {tw}×{th})")
            else:
                print(f"📦 {ora_path}  ({tw}×{th})")
        except Exception:
            try:
                os.unlink(ora_path)
            except OSError:
                pass
            raise
    print(f"✅ Opening {len(ora_paths)} document(s) in Krita")
    launch_krita(ora_paths)


def run_single_document(canvas_w: int, canvas_h: int, layout) -> None:
    """All images layered into one .ora document."""
    fd, ora_path = tempfile.mkstemp(suffix=".ora", prefix="krita_layers_")
    os.close(fd)
    try:
        build_ora(ora_path, canvas_w, canvas_h, layout, center=CENTER_IMAGES)
        print(f"📦 Built OpenRaster: {ora_path}")
    except Exception:
        try:
            os.unlink(ora_path)
        except OSError:
            pass
        raise
    print("✅ Opening in Krita")
    launch_krita([ora_path])


def main() -> None:
    if len(sys.argv) < 2:
        print("No files selected.")
        return

    paths = collect_paths(sys.argv[1:])
    if not paths:
        print("No valid image files.")
        return

    try:
        canvas_w, canvas_h, layout = compute_layout(paths, SCALE_TO_SAME_SIZE)
    except Exception as e:
        print(f"❌ Could not read one or more images: {e}")
        sys.exit(1)

    mode = "one document per image" if MULTI_DOCUMENT else "one layered document"
    print(f"Mode:      {mode}")
    print(f"Placement: {'centered' if (CENTER_IMAGES and not MULTI_DOCUMENT) else 'top-left (0,0)'}")
    print(f"Scale:     {'same longest side' if SCALE_TO_SAME_SIZE else 'native'}")
    if not MULTI_DOCUMENT:
        print(f"Canvas:    {canvas_w} × {canvas_h}")
    for path, (ow, oh), (tw, th) in layout:
        if (ow, oh) != (tw, th):
            print(f"  • {path}  ({ow}×{oh} → {tw}×{th})")
        else:
            print(f"  • {path}  ({tw}×{th})")

    if MULTI_DOCUMENT:
        run_multi_document(layout)
    else:
        run_single_document(canvas_w, canvas_h, layout)


if __name__ == "__main__":
    main()
