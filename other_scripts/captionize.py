#!/usr/bin/env python3
"""
captionize.py - Embed a filename (or custom text) into an image
---------------------------------------------------------------
Reads one image, writes one image. Width and orientation are preserved
unless ROTATE_BEFORE_PROCESSING is True (see below). The canvas height is
preserved when SHRINK_RATIO = 1 and grows by up to one label-strip height
as SHRINK_RATIO approaches 0.

LABEL_MODE = "strip"  (default)
    A strip of height = LABEL_HEIGHT_RATIO * H sits at the top of the
    working canvas and holds the label. The image is scaled into the area
    below the strip using the same MAXIMIZE_PAGE_USAGE / AVOID_CROP rules
    as imgs2pdf.py, then centered.

LABEL_MODE = "overlay"
    The canvas is left at the input's exact size; a translucent banner is
    painted across the top and the label sits inside it. SHRINK_RATIO has
    no effect in this mode.

SHRINK_RATIO (strip mode only):
    1.0  -> canvas keeps its original height; image is scaled down to
            make room for the strip.
    0.0  -> canvas is expanded by exactly the strip height; the image is
            placed below at its original size.
    t    -> canvas grows by strip_h * (1 - t); image area shrinks by
            strip_h * t. Linear blend between the two extremes.

ROTATE_BEFORE_PROCESSING:
    If True, the image is rotated 90 degrees clockwise immediately after
    being loaded and before any sizing math happens, then the finished
    canvas is rotated 90 degrees counter-clockwise at the very end. Net
    effect: the label that would have been a top strip instead appears as
    a vertical strip along the LEFT edge of the final image (with the text
    running bottom-to-top). Set to False to keep the label at the top.
    Both rotations use Image.transpose, so no resampling occurs and the
    final dimensions are exactly the input's.

Usage:
    python3 captionize.py <input_image>
    python3 captionize.py <input_image> --text "some other caption"
"""

import argparse
import os
import sys
from PIL import Image, ImageDraw, ImageFont, ImageOps


# ========== CONFIGURATION - EDIT THESE VALUES ==========
# Router-patchable inputs. mcp.py rewrites these top-level assignments
# before exec'ing this module (see the AST patcher in mcp.py); when the
# script is run directly from the shell, the CLI arguments take
# precedence over them. Both are plain top-level `NAME = value` lines
# so the patcher can find and replace them.
INPUT_IMAGE = None
TEXT = None

# Suffix inserted between the input stem and extension
# (e.g. "photo.jpg" -> "photo_labeled.jpg").
OUTPUT_SUFFIX = "_labeled"

# "strip"   -> carve/append a strip at the top for the label.
# "overlay" -> keep the canvas, paint a translucent banner on top.
LABEL_MODE = "strip"

SHOW_LABEL = True

# Fraction of the (working) image height reserved for the label strip.
LABEL_HEIGHT_RATIO = 0.3

# 1.0 = shrink the image to make room for the strip (canvas unchanged).
# 0.0 = expand the canvas to make room for the strip (image untouched).
# Anything in between blends the two.
# SHRINK_RATIO = .3
SHRINK_RATIO = 1

# True -> rotate the image 90 deg CW before processing and rotate the
# finished canvas 90 deg CCW at the very end. The label ends up as a
# vertical strip along the LEFT edge of the final image.
ROTATE_BEFORE_PROCESSING = True

# Font used for the label. Must be a .ttf/.otf file readable by Pillow.
LABEL_FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
LABEL_COLOR = "black"
LABEL_BACKGROUND = "white"                # used only in "strip" mode
LABEL_OVERLAY_BG = (255, 255, 255, 200)   # RGBA banner in "overlay" mode
LABEL_PADDING_X = 20

# Scaling of the image inside its area (strip mode only).
#   MAXIMIZE_PAGE_USAGE = False                                  -> "fit"
#   MAXIMIZE_PAGE_USAGE = True,  AVOID_CROP = False              -> "maximize + crop"
#   MAXIMIZE_PAGE_USAGE = True,  AVOID_CROP = True               -> "maximize, no crop"
MAXIMIZE_PAGE_USAGE = True
AVOID_CROP = True

# Strip mode only. After the image is scaled into the area below the
# strip, shrink the canvas width down to the scaled image's width so the
# leftover side margins disappear. Set False to keep the old behavior
# (canvas keeps the input's full width, and the scaled image is centered
# in it, leaving background bands on either side).
TRIM_SIDE_MARGINS = True
# =======================================================


# --------------------------------------------------------------------------
#  Label rendering helpers (same logic as imgs2pdf.py)
# --------------------------------------------------------------------------

def _text_width(font, text):
    """Pixel width of `text` rendered with `font`."""
    try:
        return font.getlength(text)
    except AttributeError:
        return font.getsize(text)[0]


def _wrap_text(text, font, max_w):
    """Wrap `text` into lines that each fit within `max_w` pixels."""
    if not text:
        return [""]

    if _text_width(font, text) <= max_w:
        return [text]

    # Word-wrap if there are spaces.
    if ' ' in text:
        words = text.split(' ')
        lines = []
        current = words[0]
        for w in words[1:]:
            test = current + ' ' + w
            if _text_width(font, test) <= max_w:
                current = test
            else:
                lines.append(current)
                current = w
        lines.append(current)
        if all(_text_width(font, l) <= max_w for l in lines):
            return lines

    # Character-wrap fallback.
    lines = []
    current = ""
    for ch in text:
        test = current + ch
        if not current or _text_width(font, test) <= max_w:
            current = test
        else:
            lines.append(current)
            current = ch
    if current:
        lines.append(current)
    return lines


def _fit_label_to_box(text, max_w, max_h, font_path, max_lines=8):
    """
    Find the largest font size at which `text`, wrapped to fit `max_w`,
    still renders within `max_h` pixels of total height.
    Returns (font, lines, line_height).
    """
    if max_w <= 0 or max_h <= 0 or not text:
        return None, [], 0

    def try_font(fs):
        try:
            font = ImageFont.truetype(font_path, fs)
        except Exception:
            return None
        ascent, descent = font.getmetrics()
        line_h = ascent + descent
        lines = _wrap_text(text, font, max_w)
        if len(lines) <= max_lines and line_h * len(lines) <= max_h:
            return font, lines, line_h
        return None

    # Total rendered height grows monotonically with font size.
    lo, hi = 1, max(2, int(max_h * 2))
    best = (None, [], 0)
    while lo <= hi:
        mid = (lo + hi) // 2
        result = try_font(mid)
        if result is not None:
            best = result
            lo = mid + 1          # try bigger
        else:
            hi = mid - 1          # too big, back off

    if best[0] is None:
        result = try_font(8)
        if result is not None:
            return result
        return None, [], 0
    return best


def _compute_geometry(H):
    """
    Given the working (already rotated-in) height H, return
    (canvas_h, image_area_h, strip_h) according to SHOW_LABEL,
    LABEL_HEIGHT_RATIO, and SHRINK_RATIO.

    SHRINK_RATIO = 1 -> canvas_h = H,                 image_area_h = H - strip_h
    SHRINK_RATIO = 0 -> canvas_h = H + strip_h,       image_area_h = H
    In between:        canvas_h = H + strip_h*(1-t),  image_area_h = canvas_h - strip_h
    """
    if not SHOW_LABEL:
        return H, H, 0

    strip_h = max(0, int(H * LABEL_HEIGHT_RATIO))
    if strip_h == 0:
        return H, H, 0

    t = max(0.0, min(1.0, SHRINK_RATIO))
    growth = round(strip_h * (1.0 - t))
    canvas_h = H + growth
    image_area_h = canvas_h - strip_h
    return canvas_h, image_area_h, strip_h


def _draw_label(page, text, x0, y0, cell_w, strip_h, color):
    """Draw the label text centered inside the strip at (x0, y0)."""
    if strip_h <= 0 or not text:
        return
    max_w = cell_w - 2 * LABEL_PADDING_X
    if max_w <= 0:
        return

    font, lines, line_h = _fit_label_to_box(
        text, max_w, strip_h, LABEL_FONT_PATH
    )
    if font is None or not lines:
        return

    draw = ImageDraw.Draw(page)
    total_h = line_h * len(lines)
    y = y0 + max(0, (strip_h - total_h) // 2)
    for line in lines:
        w = _text_width(font, line)
        x = x0 + (cell_w - w) // 2
        draw.text((x, y), line, fill=color, font=font)
        y += line_h


# --------------------------------------------------------------------------
#  Image scaling (mirrors imgs2pdf.py)
# --------------------------------------------------------------------------

def _scale(img, box_w, box_h):
    """Scale `img` into (box_w, box_h) honoring the mode flags."""
    if MAXIMIZE_PAGE_USAGE:
        if AVOID_CROP:
            return ImageOps.contain(
                img, (box_w, box_h), method=Image.Resampling.LANCZOS
            )
        return ImageOps.fit(
            img, (box_w, box_h), method=Image.Resampling.LANCZOS
        )
    out = img.copy()
    out.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
    return out


# --------------------------------------------------------------------------
#  Core
# --------------------------------------------------------------------------

def _open_image(path):
    im = Image.open(path)
    if im.mode == 'P':
        im = im.convert('RGBA' if 'transparency' in im.info else 'RGB')
    return im.copy()


def _output_mode(img):
    return 'RGBA' if img.mode in ('RGBA', 'LA') else 'RGB'


def captionize(in_path, out_path, text):
    img = _open_image(in_path)

    # 1. Optional pre-rotation (no resampling, exact dimensions).
    if ROTATE_BEFORE_PROCESSING:
        img = img.transpose(Image.Transpose.ROTATE_270)   # 90 deg CW

    # 2. All size math happens in the (possibly rotated) working frame.
    W, H = img.size
    out_mode = _output_mode(img)

    canvas_h, image_area_h, strip_h = _compute_geometry(H)

    if LABEL_MODE == "overlay":
        canvas = Image.new(out_mode, (W, canvas_h), LABEL_BACKGROUND)
        src = img if img.mode == out_mode else img.convert(out_mode)
        if src.mode == 'RGBA':
            canvas.paste(src, (0, 0), src)
        else:
            canvas.paste(src, (0, 0))

        if strip_h > 0:
            if canvas.mode == 'RGBA':
                banner = Image.new('RGBA', (W, strip_h), LABEL_OVERLAY_BG)
                canvas.alpha_composite(banner, (0, 0))
            else:
                banner_rgb = tuple(LABEL_OVERLAY_BG[:3])
                alpha = LABEL_OVERLAY_BG[3] / 255.0
                top = canvas.crop((0, 0, W, strip_h))
                blended = Image.blend(
                    top, Image.new('RGB', top.size, banner_rgb), alpha
                )
                canvas.paste(blended, (0, 0))

        _draw_label(canvas, text, 0, 0, W, strip_h, LABEL_COLOR)

    else:
        # "strip" mode.
        src = img if img.mode == out_mode else img.convert(out_mode)

        # Scale first so we know the actual content width.
        scaled = None
        if image_area_h > 0:
            scaled = _scale(src, W, image_area_h)

        # Trim the canvas width to the scaled image's width. When the
        # image is height-limited by the area below the strip (the usual
        # case whenever SHRINK_RATIO > 0), scaled.width < W and the extra
        # width is pure background; dropping it removes the side bands.
        if TRIM_SIDE_MARGINS and scaled is not None and 0 < scaled.width < W:
            eff_W = scaled.width
        else:
            eff_W = W

        canvas = Image.new(out_mode, (eff_W, canvas_h), LABEL_BACKGROUND)
        _draw_label(canvas, text, 0, 0, eff_W, strip_h, LABEL_COLOR)

        if scaled is not None:
            x = (eff_W - scaled.width) // 2
            y = strip_h + (image_area_h - scaled.height) // 2
            if scaled.mode == 'RGBA' and canvas.mode == 'RGBA':
                canvas.paste(scaled, (x, y), scaled)
            else:
                canvas.paste(scaled, (x, y))

    # 3. Undo the pre-rotation on the finished canvas.
    if ROTATE_BEFORE_PROCESSING:
        canvas = canvas.transpose(Image.Transpose.ROTATE_90)   # 90 deg CCW

    # Respect the extension's capabilities.
    ext = os.path.splitext(out_path)[1].lower()
    to_save = canvas
    if ext in ('.jpg', '.jpeg', '.bmp') and canvas.mode == 'RGBA':
        to_save = canvas.convert('RGB')
    to_save.save(out_path)
    return out_path


# --------------------------------------------------------------------------
#  CLI
# --------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        prog="captionize.py",
        description=(
            "Embed an image's filename (or custom text) into the image "
            "itself. Behavior is controlled by script variables; only the "
            "input path and an optional --text override are on the CLI."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 captionize.py photo.jpg\n"
            "  python3 captionize.py photo.jpg --text \"Figure 3\"\n"
        ),
    )
    # nargs="?" so argparse does not hard-exit when the router in mcp.py
    # invokes this script with an empty sys.argv (sys.argv = [script]).
    # In that case args.input is None and main() falls back to the
    # top-level INPUT_IMAGE variable, which the router has patched.
    parser.add_argument(
        "input",
        nargs="?",
        default=None,
        help=(
            "Input image path. Optional so the tool can run under the "
            "router in mcp.py, which supplies the path via the top-level "
            "INPUT_IMAGE variable instead of a CLI positional."
        ),
    )
    parser.add_argument(
        "--text",
        default=None,
        metavar="STR",
        help=(
            "Use this text instead of the filename (without extension) "
            "as the caption."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    # Input path resolution order: CLI positional, then the
    # router-patched top-level INPUT_IMAGE.
    in_path = args.input if args.input is not None else INPUT_IMAGE
    if in_path is None:
        print("Error: no input image given (pass a path on the command "
              "line or set INPUT_IMAGE).")
        sys.exit(1)
    in_path = os.path.expanduser(in_path)
    if not os.path.isfile(in_path):
        print(f"Error: Input image '{in_path}' does not exist.")
        sys.exit(1)

    stem, ext = os.path.splitext(in_path)
    out_path = f"{stem}{OUTPUT_SUFFIX}{ext}"

    if os.path.abspath(out_path) == os.path.abspath(in_path):
        print("Error: Output path would overwrite the input "
              "(check OUTPUT_SUFFIX).")
        sys.exit(1)

    # Caption resolution order: --text on the CLI wins, then the
    # router-patched top-level TEXT, then the input's filename stem.
    if args.text is not None:
        caption_text = args.text
        caption_source = "custom --text"
    elif TEXT is not None:
        caption_text = TEXT
        caption_source = "custom TEXT"
    else:
        caption_text = os.path.splitext(os.path.basename(in_path))[0]
        caption_source = "filename"
    if not SHOW_LABEL:
        caption_text = ""

    captionize(in_path, out_path, caption_text)

    with Image.open(in_path) as im:
        in_w, in_h = im.size

    # The geometry shown below is in the (possibly rotated) working frame.
    work_h = in_w if ROTATE_BEFORE_PROCESSING else in_h
    canvas_h, image_area_h, strip_h = _compute_geometry(work_h)

    scaling = ("fit" if not MAXIMIZE_PAGE_USAGE
               else ("maximize + crop" if not AVOID_CROP
                     else "maximize, no crop"))

    with Image.open(out_path) as im:
        out_w, out_h = im.size

    print("=" * 60)
    print("Caption embedded")
    print("=" * 60)
    print(f"  Input:   {in_path}  ({in_w} x {in_h})")
    print(f"  Output:  {out_path}  ({out_w} x {out_h})")
    if ROTATE_BEFORE_PROCESSING:
        print(f"  Rotated: 90 deg CW before, 90 deg CCW after "
              f"(label strip lands on the LEFT edge)")
    if SHOW_LABEL:
        print(f"  Caption: {caption_text!r}  ({caption_source})")
        print(f"  Mode:    {LABEL_MODE}")
        if LABEL_MODE == "strip":
            print(f"  Strip:   {strip_h} px "
                  f"({LABEL_HEIGHT_RATIO*100:.1f}% of working height)")
            print(f"  SHRINK_RATIO = {SHRINK_RATIO:.3f}  ->  "
                  f"image area: "
                  f"{in_w if not ROTATE_BEFORE_PROCESSING else in_h} x "
                  f"{image_area_h}, "
                  f"canvas growth: +{canvas_h - work_h} px")
            print(f"  Scaling: {scaling}")
            if TRIM_SIDE_MARGINS:
                print("  Trim:    side margins trimmed to scaled image width")
    else:
        print("  Caption: off (image copied unchanged)")
    print("=" * 60)


if __name__ == "__main__":
    main()
