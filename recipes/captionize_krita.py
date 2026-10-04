#!/usr/bin/env python3
"""
captionize_krita.py — Captionize an image by driving Krita via the
Kritomatic daemon.

Every interaction with Krita goes through the daemon. The only things
this script does locally are: inspect .kra files as zip archives (to
read their dimensions), measure the caption with Pillow (to pick a font
size), and copy the input .kra to a temp location so we never mutate
the user's file.

EMBED_MODE selects how the source image makes it into the new document:

    "file_layer"  (default)
        External PNG reference. Smallest .kra, but the output depends
        on that PNG staying where it is when the .kra is reopened.

    "baked_png"
        Pixels baked into a paint layer. Self-contained .kra.

    "kra_group"
        Source .kra imported as a group. Preserves layers. Self-
        contained. Skips the flatten-to-PNG stage.

KEEP_IMAGE_NATIVE_SIZE selects which dimension anchors the layout:

    False (default)
        The canvas anchors. Strip is carved from the top, source
        shrinks into the area below it.

    True
        The source anchors. It keeps its native dimensions and the
        canvas grows downward by the strip height.

ROTATE_BEFORE_PROCESSING mirrors captionize.py's flag of the same name,
but is implemented differently. Where captionize.py rotates the pixel
buffer CW before sizing and CCW after, this script does not rotate any
image data at all — it computes the layout in the "as if rotated" frame
and then transforms the resulting geometry into the final frame. The
practical result is the same: the label strip ends up as a vertical band
along the LEFT edge of the final image, with the text reading
bottom-to-top, and the source in its original orientation.

Why not rotate the document in Krita: Krita's rotate-image action
rotates each layer's pixel data but leaves transform-mask parameters in
their original coordinate system. A file layer with a transform mask
ends up shifted in the wrong direction after the rotation. By keeping
the source in its original orientation and laying out the final frame
directly, there is no transform mask on the source at all, and the
problem disappears. The label is the only rotated element, and it is
rotated via the SVG transform attribute, which Krita renders correctly
because it is part of the shape rather than a mask.

Pipeline (each stage is a self-contained batch):
  0.    (bitmap only) convert the input bitmap to a .kra
  0.5   flatten the .kra to a PNG (skipped in "kra_group" mode)
  1.    create the target canvas at the computed size
  2.    embed the source per EMBED_MODE
  3.    add the vector label, save as .kra
"""

import argparse
import json
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


# ========== CONFIGURATION — mirrors captionize.py ==========
INPUT_IMAGE = None
TEXT = None

OUTPUT_SUFFIX = "_labeled"

LABEL_HEIGHT_RATIO = 0.3
SHRINK_RATIO = 1.0

KEEP_IMAGE_NATIVE_SIZE = False

TRIM_SIDE_MARGINS = True

LABEL_COLOR = "#000000"
LABEL_BACKGROUND = "#ffffff"
LABEL_PADDING_X = 20

LABEL_VERTICAL_PADDING_FRACTION = 0.15

DEFAULT_RESOLUTION = 300

LABEL_FONT_CANDIDATES = [
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]

LABEL_FONT_FAMILY = "DejaVu Sans"

# "file_layer" | "baked_png" | "kra_group"
EMBED_MODE = "file_layer"

# Mirror of captionize.py's flag. When True, the strip appears as a
# vertical band along the LEFT edge of the output, with the text
# reading bottom-to-top. See the module docstring for why we don't
# actually rotate anything in Krita.
ROTATE_BEFORE_PROCESSING = True
# ==========================================================


_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE / "src", _HERE.parent / "src"):
    if (_cand / "kritomatic").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

try:
    from kritomatic.batch import BatchExecutor
    from kritomatic.client import KritaClient
except ImportError:
    print("Error: the 'kritomatic' Python package is not importable.")
    print("Install it with:  pipx install -e .    (from the repo root)")
    sys.exit(1)


VALID_EMBED_MODES = ("file_layer", "baked_png", "kra_group")


# --------------------------------------------------------------------------
#  Reading the source (local, no Krita)
# --------------------------------------------------------------------------

def get_kra_dimensions(path):
    """Parse width and height out of a .kra's maindoc.xml."""
    with zipfile.ZipFile(path) as zf:
        main = next((n for n in zf.namelist()
                     if n.endswith("maindoc.xml") and "/" not in n), None)
        if main is None:
            main = next((n for n in zf.namelist()
                         if n.endswith("maindoc.xml")), None)
        if main is None:
            raise ValueError(f"No maindoc.xml found in {path}")
        with zf.open(main) as f:
            tree = ET.parse(f)
        for elem in tree.getroot().iter():
            if "width" in elem.attrib and "height" in elem.attrib:
                return int(elem.attrib["width"]), int(elem.attrib["height"])
    raise ValueError(f"Could not locate width/height in {path}")


def get_image_dimensions(path):
    """Dimensions of any Krita-readable bitmap, via Pillow."""
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def is_kra(path):
    return path.suffix.lower() == ".kra"


# --------------------------------------------------------------------------
#  Geometry — mirrors captionize.py
# --------------------------------------------------------------------------

def compute_geometry(src_w, src_h):
    """
    Compute layout. If ROTATE_BEFORE_PROCESSING is True, first computes
    everything in the "rotated frame" (as if the source had been rotated
    90 degrees CW), then transforms the geometry into the final frame by
    applying the inverse rotation (90 degrees CCW).

    The returned fields are always in the frame that will actually be
    drawn — that is, if ROTATE_BEFORE_PROCESSING is True, the canvas,
    content position, and label position are all expressed in the final
    (rotated) frame, and the source is expected to be embedded in its
    original orientation.

    Fields:
      canvas_w, canvas_h   final canvas dimensions
      strip_h              strip thickness (height when horizontal, width
                           when vertical)
      image_area_h         height of the content area in the rotated
                           frame (informational only)
      scaled_w, scaled_h   content size in the final frame
      img_x, img_y         content top-left in the final frame
      label_x, label_y     label anchor in the final frame
      label_rotation       degrees; 0 for horizontal, -90 for reading
                           bottom-to-top
      label_axis           length of the strip's long axis; used as the
                           text-width constraint when sizing the font
      rotated              True if ROTATE_BEFORE_PROCESSING was applied
    """
    if ROTATE_BEFORE_PROCESSING:
        rot_w, rot_h = src_h, src_w
    else:
        rot_w, rot_h = src_w, src_h

    strip_h = max(0, int(rot_h * LABEL_HEIGHT_RATIO))

    if KEEP_IMAGE_NATIVE_SIZE:
        canvas_w = rot_w
        canvas_h = rot_h + strip_h
        image_area_h = rot_h
        scaled_w = rot_w
        scaled_h = rot_h
        img_x = 0
        img_y = strip_h
    else:
        t = max(0.0, min(1.0, SHRINK_RATIO))
        growth = round(strip_h * (1.0 - t))
        canvas_h = rot_h + growth
        image_area_h = canvas_h - strip_h

        s = min(1.0, image_area_h / rot_h) if rot_h > 0 else 1.0
        scaled_w = max(1, int(round(rot_w * s)))
        scaled_h = max(1, int(round(rot_h * s)))

        if TRIM_SIDE_MARGINS and 0 < scaled_w < rot_w:
            canvas_w = scaled_w
        else:
            canvas_w = rot_w

        img_x = (canvas_w - scaled_w) // 2
        img_y = strip_h + (image_area_h - scaled_h) // 2

    if not ROTATE_BEFORE_PROCESSING:
        return {
            "canvas_w": canvas_w,
            "canvas_h": canvas_h,
            "strip_h": strip_h,
            "image_area_h": image_area_h,
            "scaled_w": scaled_w,
            "scaled_h": scaled_h,
            "img_x": img_x,
            "img_y": img_y,
            "label_x": canvas_w / 2,
            "label_y": strip_h / 2,
            "label_rotation": 0,
            "label_axis": canvas_w,
            "rotated": False,
        }

    # Transform to the final frame by rotating the rotated-frame layout
    # 90 degrees CCW. The map (x, y) -> (y, W - 1 - x) on a WxH canvas
    # gives an HxW output. Applied to the content rect and the label
    # anchor in turn.
    final_canvas_w = canvas_h
    final_canvas_h = canvas_w
    final_img_x = img_y
    final_img_y = canvas_w - img_x - scaled_w
    final_scaled_w = scaled_h
    final_scaled_h = scaled_w
    final_label_x = strip_h / 2
    final_label_y = canvas_w / 2

    return {
        "canvas_w": final_canvas_w,
        "canvas_h": final_canvas_h,
        "strip_h": strip_h,
        "image_area_h": image_area_h,
        "scaled_w": final_scaled_w,
        "scaled_h": final_scaled_h,
        "img_x": final_img_x,
        "img_y": final_img_y,
        "label_x": final_label_x,
        "label_y": final_label_y,
        "label_rotation": -90,
        "label_axis": final_canvas_h,
        "rotated": True,
    }


def compute_font_size(text, strip_h, canvas_w):
    max_w = canvas_w - 2 * LABEL_PADDING_X
    max_h = int(strip_h * (1 - 2 * LABEL_VERTICAL_PADDING_FRACTION))
    if max_w <= 0 or max_h <= 0 or not text:
        return 12

    font_path = next((p for p in LABEL_FONT_CANDIDATES if os.path.exists(p)), None)
    if font_path is not None:
        try:
            from PIL import ImageFont
            lo, hi = 1, max(2, int(max_h * 2))
            best = 8
            while lo <= hi:
                mid = (lo + hi) // 2
                font = ImageFont.truetype(font_path, mid)
                try:
                    w = font.getlength(text)
                except AttributeError:
                    w = font.getsize(text)[0]
                ascent, descent = font.getmetrics()
                line_h = ascent + descent
                if w <= max_w and line_h <= max_h:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            return best
        except Exception:
            pass

    by_width = max_w / max(1, len(text)) / 0.55
    by_height = max_h / 1.2
    return max(8, int(min(by_width, by_height)))


# --------------------------------------------------------------------------
#  Batch builders
# --------------------------------------------------------------------------

def build_convert_bundle(bitmap_path, kra_path, src_w, src_h):
    """
    Stage 0 (bitmap inputs only): turn the bitmap into a .kra and close
    it. Closing is important — the pipeline opens the source .kra again
    later (for flatten or group import), and a doc left open by this
    stage would collide with those opens.
    """
    return {
        "id": "captionize_convert",
        "commands": [
            {
                "type": "create_new_with_dimensions",
                "name": bitmap_path.stem,
                "width": src_w,
                "height": src_h,
                "resolution": DEFAULT_RESOLUTION,
            },
            {
                "type": "create_file_layer",
                "name": "source",
                "file_path": str(bitmap_path),
            },
            {
                "type": "save_document",
                "file_path": str(kra_path),
            },
            {
                "type": "close_document",
            },
        ],
    }


def build_flatten_bundle(kra_path, png_path):
    """Stage 0.5: flatten the .kra to a PNG, entirely inside Krita."""
    return {
        "id": "captionize_flatten",
        "commands": [{
            "type": "export_file_to_image",
            "input_path": str(kra_path),
            "output_path": str(png_path),
        }],
    }


def build_canvas_bundle(geo, doc_name):
    return {
        "id": "captionize_canvas",
        "commands": [{
            "type": "create_new_with_dimensions",
            "name": doc_name,
            "width": geo["canvas_w"],
            "height": geo["canvas_h"],
            "resolution": DEFAULT_RESOLUTION,
        }],
    }


def build_embed_bundle(geo, src_png_path, src_kra_path):
    common = [
        {
            "type": "create_layer",
            "name": "background",
            "layer_type": "paintlayer",
            "position": "bottom",
        },
        {
            "type": "fill_layer",
            "layer_name": "background",
            "color": LABEL_BACKGROUND,
        },
    ]

    if EMBED_MODE == "file_layer":
        common.append({
            "type": "create_file_layer",
            "name": "source",
            "file_path": str(src_png_path),
            "width": geo["scaled_w"],
            "height": geo["scaled_h"],
            "x": geo["img_x"],
            "y": geo["img_y"],
        })

    elif EMBED_MODE == "baked_png":
        common.append({
            "type": "embed_image_as_layer",
            "name": "source",
            "file_path": str(src_png_path),
            "width": geo["scaled_w"],
            "height": geo["scaled_h"],
            "x": geo["img_x"],
            "y": geo["img_y"],
        })

    elif EMBED_MODE == "kra_group":
        common.append({
            "type": "import_kra_as_group",
            "name": "source",
            "file_path": str(src_kra_path),
        })
        common.append({
            "type": "create_transform_mask",
            "layer_name": "source",
            "mask_name": "source_transform",
        })
        common.append({
            "type": "transform_mask",
            "mask_name": "source_transform",
            "scale_x": _KRA_GROUP_SCALE[0],
            "scale_y": _KRA_GROUP_SCALE[1],
            "translate_x": geo["img_x"],
            "translate_y": geo["img_y"],
        })

    return {"id": "captionize_embed", "commands": common}


_KRA_GROUP_SCALE = (1.0, 1.0)


def build_label_bundle(geo, caption, font_size, out_path):
    cmds = []
    if caption and geo["strip_h"] > 0:
        cmds.append({
            "type": "create_layer",
            "name": "label",
            "layer_type": "vectorlayer",
            "position": "top",
        })
        cmds.append({
            "type": "set_active_layer",
            "name": "label",
        })
        cmds.append({
            "type": "add_vector_text",
            "layer_name": "label",
            "text": caption,
            "font_family": LABEL_FONT_FAMILY,
            "font_size": int(font_size),
            "x": geo["label_x"],
            "y": geo["label_y"],
            "rotation": geo["label_rotation"],
            "alignment": "center",
            "color": LABEL_COLOR,
        })
    cmds.append({
        "type": "save_document",
        "file_path": str(out_path),
    })
    return {"id": "captionize_label", "commands": cmds}


# --------------------------------------------------------------------------
#  Execution helper
# --------------------------------------------------------------------------

def run_batch(bundle, label):
    print(f"\n── {label} ──────────────────────────────────────")
    print(json.dumps(bundle, indent=2))
    executor = BatchExecutor()
    try:
        results = executor.execute(bundle)
    finally:
        executor.close()
    failed = [r for r in results if r["status"] == "error"]
    for r in results:
        icon = "✓" if r["status"] == "success" else "✗"
        print(f"  {icon} {r['command']}: {r['message']}")
    if failed:
        raise RuntimeError(
            f"{len(failed)} command(s) failed in {label}: "
            + "; ".join(f"{r['command']} -> {r['message']}" for r in failed)
        )
    return results


# --------------------------------------------------------------------------
#  CLI
# --------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        prog="captionize_krita.py",
        description=(
            "Embed a caption into an image by driving Krita through the "
            "Kritomatic daemon. Mirrors captionize.py's canvas geometry."
        ),
    )
    p.add_argument("input", nargs="?", default=None,
                   help="Input image path. Falls back to INPUT_IMAGE.")
    p.add_argument("-o", "--output", default=None,
                   help=f"Output .kra path (default: <input>{OUTPUT_SUFFIX}.kra).")
    p.add_argument("--text", default=None,
                   help="Caption text. Falls back to TEXT, then to the filename stem.")
    return p.parse_args()


def resolve_paths(args):
    in_str = args.input if args.input is not None else INPUT_IMAGE
    if in_str is None:
        print("Error: no input image given.")
        print("  Pass one on the command line, or set INPUT_IMAGE.")
        sys.exit(1)

    in_path = Path(in_str).expanduser().resolve()
    if not in_path.is_file():
        print(f"Error: input not found: {in_path}")
        sys.exit(1)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = in_path.with_name(f"{in_path.stem}{OUTPUT_SUFFIX}.kra")
    if out_path.suffix.lower() != ".kra":
        out_path = out_path.with_suffix(".kra")

    if out_path == in_path:
        print("Error: output path would overwrite the input.")
        sys.exit(1)
    return in_path, out_path


def resolve_caption(args, in_path):
    if args.text is not None:
        return args.text, "custom --text"
    if TEXT is not None:
        return TEXT, "custom TEXT"
    return in_path.stem, "filename"


def main():
    global _KRA_GROUP_SCALE

    args = parse_args()

    if EMBED_MODE not in VALID_EMBED_MODES:
        print(f"Error: EMBED_MODE is {EMBED_MODE!r}, "
              f"must be one of {VALID_EMBED_MODES}.")
        sys.exit(1)

    in_path, out_path = resolve_paths(args)
    caption, caption_source = resolve_caption(args, in_path)

    probe = KritaClient()
    if not probe.connect():
        print("Error: cannot reach the Kritomatic daemon at "
              f"{probe.host}:{probe.port}.")
        print("Make sure Krita is running and the Kritomatic Daemon plugin "
              "is enabled.")
        sys.exit(1)
    probe.close()

    assets_dir = out_path.parent / f".{out_path.stem}_assets"
    assets_dir.mkdir(exist_ok=True)

    src_kra = assets_dir / "source.kra"
    src_png = assets_dir / "source.png"

    try:
        # Stage 0: ensure we have a .kra to work with. Always work on a
        # copy in the assets dir so the user's input file is untouched.
        if is_kra(in_path):
            print(f"Input is a .kra: {in_path.name} -> copying to assets")
            shutil.copyfile(in_path, src_kra)
        else:
            src_w, src_h = get_image_dimensions(in_path)
            print(f"Input is a bitmap: {in_path.name}  ({src_w} x {src_h})")
            run_batch(
                build_convert_bundle(in_path, src_kra, src_w, src_h),
                f"Stage 0: convert {in_path.suffix or 'bitmap'} → .kra",
            )

        # Dimensions of the .kra we will embed. In the rotated case,
        # the .kra is still in its original orientation — the geometry
        # function performs the "as if rotated" reasoning internally.
        src_w, src_h = get_kra_dimensions(src_kra)
        print(f"Source:  {src_kra.name}  ({src_w} x {src_h})")

        # Stage 0.5: flatten the .kra to a PNG, through the daemon.
        if EMBED_MODE == "kra_group":
            print("Embed mode kra_group: skipping flatten-to-PNG stage.")
        else:
            run_batch(
                build_flatten_bundle(src_kra, src_png),
                "Stage 0.5: flatten .kra → PNG",
            )
            if not src_png.is_file():
                print(f"Error: flatten stage reported success but {src_png} "
                      f"is missing.")
                sys.exit(1)

        # Geometry + font size.
        geo = compute_geometry(src_w, src_h)
        print(f"Canvas:  {geo['canvas_w']} x {geo['canvas_h']}"
              f"   (strip {geo['strip_h']} px)")
        print(f"Image:   {geo['scaled_w']} x {geo['scaled_h']}"
              f" at ({geo['img_x']}, {geo['img_y']})")
        if KEEP_IMAGE_NATIVE_SIZE:
            print("Mode:    native-size source, canvas grown by strip")
        if ROTATE_BEFORE_PROCESSING:
            print("Rotate:  strip on LEFT edge, text reads bottom-to-top "
                  "(source stays in original orientation)")

        # For kra_group, the transform mask scale is relative to the
        # source .kra's own dimensions.
        _KRA_GROUP_SCALE = (
            geo["scaled_w"] / max(1, src_w),
            geo["scaled_h"] / max(1, src_h),
        )

        font_size = compute_font_size(
            caption, geo["strip_h"], geo["label_axis"]
        )
        print(f"Caption: {caption!r}  ({caption_source}, font size ~{font_size})")
        print(f"Embed:   {EMBED_MODE}")

        # Stages 1..3.
        run_batch(
            build_canvas_bundle(geo, f"Captionized {in_path.stem}"),
            "Stage 1: create canvas",
        )
        run_batch(
            build_embed_bundle(geo, src_png, src_kra),
            f"Stage 2: embed source ({EMBED_MODE})",
        )
        run_batch(
            build_label_bundle(geo, caption, font_size, out_path),
            "Stage 3: label + save",
        )
    except RuntimeError as e:
        print(f"\n✗ {e}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("Caption embedded")
    print("=" * 60)
    print(f"  Input:    {in_path}")
    print(f"  Output:   {out_path}  ({geo['canvas_w']} x {geo['canvas_h']})")
    print(f"  Caption:  {caption!r}  ({caption_source})")
    print(f"  Strip:    {geo['strip_h']} px")
    print(f"  SHRINK_RATIO = {SHRINK_RATIO:.3f}")
    print(f"  Embed:    {EMBED_MODE}")
    if ROTATE_BEFORE_PROCESSING:
        print("  Rotate:   yes (strip on LEFT edge, text reads bottom-to-top)")
    else:
        print("  Rotate:   no (strip at top)")
    if KEEP_IMAGE_NATIVE_SIZE:
        print("  Anchor:   source (canvas grew by strip height)")
    else:
        print("  Anchor:   canvas (source shrunk into area below strip)")
    if (not KEEP_IMAGE_NATIVE_SIZE
            and TRIM_SIDE_MARGINS
            and geo["scaled_w"] < min(src_w, src_h)):
        print("  Trim:     side margins trimmed to scaled image width")
    if EMBED_MODE == "file_layer":
        print(f"  Asset:    {src_png}  (external reference; keep it around)")
    else:
        print("  Asset:    none — output .kra is self-contained")
    print("=" * 60)


if __name__ == "__main__":
    main()
