#!/usr/bin/env python3
"""
imgs2pdf.py - Directory Images to PDF Converter
-----------------------------------------------
Converts all images in a directory to a single PDF.

Layout is controlled by TWO_HORIZONTAL_PER_PAGE:

  * False: one image per portrait US Letter page.
  * True:  two horizontal images per portrait US Letter page, stacked
           vertically (top + bottom). Odd image counts leave the last
           bottom cell empty.

Scaling is controlled by MAXIMIZE_PAGE_USAGE + AVOID_CROP:

  * MAXIMIZE_PAGE_USAGE = False
      "fit" — scale down only, never upscales, whitespace on both axes.
  * MAXIMIZE_PAGE_USAGE = True, AVOID_CROP = False
      "maximize" — cover the cell, cropping the overflow.
  * MAXIMIZE_PAGE_USAGE = True, AVOID_CROP = True
      "maximize without crop" — fill the cell on one axis, leave
      symmetric whitespace on the other. Nothing is lost.

A new flag SHOW_FILENAME_LABEL reserves a strip at the top of each cell
whose height is LABEL_HEIGHT_RATIO of the cell height. Inside it, the
image's filename (without extension) is drawn, auto-wrapped across as
many lines as needed, with a font size chosen so the text fills the
strip vertically as fully as possible.

Only processes images in the top-level directory (no subdirectories).

Usage:
    python3 imgs2pdf.py [directory_path]
    python3 imgs2pdf.py --show-ratios
    python3 imgs2pdf.py --test-image /path/to/one/image.jpg
"""

import argparse
import os
import sys
from math import gcd
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps
from PIL import JpegImagePlugin  # noqa: F401 - registers JPEG encoder needed by PdfImagePlugin

# ========== CONFIGURATION - EDIT THESE VALUES ==========
INPUT_DIR = "/tmp/"                    # Default directory containing images
OUTPUT_PDF = "output.pdf"              # Output PDF filename

# >>> LAYOUT TOGGLE <<<
# True  -> two horizontal images per portrait page (stacked vertically).
# False -> one image per portrait page.
TWO_HORIZONTAL_PER_PAGE = True

# >>> SCALING TOGGLE <<<
# True -> scale each image to fill its allotted area.
MAXIMIZE_PAGE_USAGE = True

# Only meaningful when MAXIMIZE_PAGE_USAGE is True.
#   False -> "maximize": fill the cell by CROPPING the overflow.
#   True  -> "maximize without crop": scale DOWN just enough that nothing
#            is lost; the image fills the cell on one axis and leaves
#            symmetric whitespace on the other.
AVOID_CROP = True

# >>> LABEL TOGGLE <<<
# True  -> reserve a strip at the top of each cell and draw the image's
#          filename (without extension) inside it. The image is placed
#          in the remaining area below the strip.
SHOW_FILENAME_LABEL = True

# Proportion of the cell height reserved for the filename strip.
# 0.08 means 8% of the cell's height becomes the label area.
LABEL_HEIGHT_RATIO = 0.08 # Horizontally apt
# LABEL_HEIGHT_RATIO = 0.03 # Vertically apt

# Font used for the label. Must be a .ttf/.otf file readable by Pillow.
LABEL_FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
LABEL_COLOR = "black"
LABEL_PADDING_X = 20            # horizontal padding inside the strip (px)

# Per-edge margins in pixels (150px = 0.5 inches at 300 DPI)
MARGIN_TOP = 250
MARGIN_BOTTOM = 250
MARGIN_LEFT = 250
MARGIN_RIGHT = 250

# Gap between the two stacked images (only used in TWO_HORIZONTAL_PER_PAGE mode)
CELL_GAP = 30

# Rotate landscape images to portrait in default mode (and portrait -> landscape
# in two-per-page mode). When False, images keep their original orientation.
ROTATE_TO_VERTICAL = True

# Kept for compatibility with the original script.
RAW_MODE = False
# =======================================================

# US Letter at 300 DPI (fixed, do not change)
PAGE_WIDTH = 2550                       # portrait width  (8.5"  * 300)
PAGE_HEIGHT = 3300                      # portrait height (11"   * 300)

# Supported image extensions
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp')

# Width of the report banners (characters)
_BANNER_W = 64

# Common photo / screen aspect ratios to hint at (name -> value as w/h)
_COMMON_RATIOS = {
    "21:9 (cinema ultrawide)": 21 / 9,
    "16:9 (widescreen / 1080p)": 16 / 9,
    "3:2  (35mm / APS-C photo)": 3 / 2,
    "4:3  (Micro 4/3 / classic)": 4 / 3,
    "5:4  (large format)": 5 / 4,
    "1:1  (square)": 1.0,
    "4:5  (portrait Instagram)": 4 / 5,
    "3:4  (portrait 4:3)": 3 / 4,
    "2:3  (portrait 35mm)": 2 / 3,
    "9:16 (portrait 16:9)": 9 / 16,
}


# --------------------------------------------------------------------------
#  Image discovery / loading / scaling helpers
# --------------------------------------------------------------------------

def get_images(directory):
    """Return a sorted list of image paths in the top-level directory only."""
    images = []
    for ext in IMAGE_EXTS:
        for path in Path(directory).glob(f"*{ext}"):
            if path.is_file():
                images.append(str(path))
        for path in Path(directory).glob(f"*{ext.upper()}"):
            if path.is_file() and str(path) not in images:
                images.append(str(path))
    return sorted(images)


def _load_image(img_path, target_horizontal):
    """Open, flatten to RGB, and rotate to the requested orientation."""
    img = Image.open(img_path)
    if img.mode in ('RGBA', 'LA', 'P'):
        img = img.convert('RGB')

    is_landscape = img.width > img.height
    if target_horizontal and not is_landscape:
        img = img.rotate(-90, expand=True)
    elif not target_horizontal and is_landscape:
        img = img.rotate(-90, expand=True)
    return img


def _scale_image(img, box_w, box_h):
    """Scale `img` into (box_w, box_h) honoring the mode flags."""
    if MAXIMIZE_PAGE_USAGE:
        if AVOID_CROP:
            return ImageOps.contain(
                img, (box_w, box_h), method=Image.Resampling.LANCZOS
            )
        else:
            return ImageOps.fit(
                img, (box_w, box_h), method=Image.Resampling.LANCZOS
            )
    else:
        img.thumbnail((box_w, box_h), Image.Resampling.LANCZOS)
        return img


# --------------------------------------------------------------------------
#  Label rendering helpers
# --------------------------------------------------------------------------

def _text_width(font, text):
    """Return the pixel width of `text` rendered with `font` (Pillow-compatible)."""
    try:
        return font.getlength(text)
    except AttributeError:
        return font.getsize(text)[0]


def _wrap_text(text, font, max_w):
    """Wrap `text` into lines that each fit within `max_w` pixels."""
    if not text:
        return [""]

    # Single line fits? Done.
    if _text_width(font, text) <= max_w:
        return [text]

    # Try word-wrap on spaces if there are any.
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

    # Character wrap fallback.
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


def _fs_for_line_height(font_path, target_line_h):
    """
    Largest integer font size whose (ascent + descent) is <= target_line_h.
    Binary search so it's exact for the given font.
    """
    if target_line_h <= 0:
        return 0
    lo, hi = 1, max(2, int(target_line_h * 2))
    best = 1
    while lo <= hi:
        mid = (lo + hi) // 2
        try:
            font = ImageFont.truetype(font_path, mid)
            ascent, descent = font.getmetrics()
            if ascent + descent <= target_line_h:
                best = mid
                lo = mid + 1
            else:
                hi = mid - 1
        except Exception:
            hi = mid - 1
    return best


def _fit_label_to_box(text, max_w, max_h, font_path, max_lines=8):
    """
    Find the largest font size at which `text`, wrapped to fit `max_w`,
    still renders within `max_h` pixels of total height (lines * line_height).
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

    # Total rendered height (n_lines * line_h) grows monotonically with
    # font size, so a plain binary search finds the largest font that fits.
    lo, hi = 1, max(2, int(max_h * 2))
    best = (None, [], 0)
    while lo <= hi:
        mid = (lo + hi) // 2
        result = try_font(mid)
        if result is not None:
            best = result
            lo = mid + 1          # try a bigger font
        else:
            hi = mid - 1          # too big, back off

    if best[0] is None:
        # Absolute fallback if even a tiny font can't be found.
        result = try_font(8)
        if result is not None:
            return result
        return None, [], 0
    return best


def _label_strip_height(cell_h):
    """Return the strip height (in px) for a cell of the given height."""
    if not SHOW_FILENAME_LABEL:
        return 0
    return max(0, int(cell_h * LABEL_HEIGHT_RATIO))


def _draw_label(page, img_path, cell_x, cell_y, cell_w, strip_h):
    """Draw the filename label centered within the strip at top of the cell."""
    if strip_h <= 0:
        return
    label = os.path.splitext(os.path.basename(img_path))[0]
    max_w = cell_w - 2 * LABEL_PADDING_X
    if max_w <= 0:
        return

    font, lines, line_h = _fit_label_to_box(
        label, max_w, strip_h, LABEL_FONT_PATH
    )
    if font is None or not lines:
        return

    draw = ImageDraw.Draw(page)
    total_h = line_h * len(lines)
    y = cell_y + max(0, (strip_h - total_h) // 2)
    for line in lines:
        w = _text_width(font, line)
        x = cell_x + (cell_w - w) // 2
        draw.text((x, y), line, fill=LABEL_COLOR, font=font)
        y += line_h


# --------------------------------------------------------------------------
#  Page builders
# --------------------------------------------------------------------------

def create_portrait_page(img_path):
    """One image per portrait page."""
    page = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')

    live_w = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    live_h = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    cell_x, cell_y = MARGIN_LEFT, MARGIN_TOP
    strip_h = _label_strip_height(live_h)
    _draw_label(page, img_path, cell_x, cell_y, live_w, strip_h)

    img_x = cell_x
    img_y = cell_y + strip_h
    img_w = live_w
    img_h = live_h - strip_h

    img = _load_image(img_path, target_horizontal=not ROTATE_TO_VERTICAL)
    img = _scale_image(img, img_w, img_h)

    x_offset = img_x + (img_w - img.width) // 2
    y_offset = img_y + (img_h - img.height) // 2
    page.paste(img, (x_offset, y_offset))
    return page


def create_two_per_page(img_paths):
    """Two landscape images stacked on a portrait page."""
    page = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')

    live_w = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    live_h = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    cell_w = live_w
    cell_h = (live_h - CELL_GAP) // 2

    for i, img_path in enumerate(img_paths):
        cell_x = MARGIN_LEFT
        cell_y = MARGIN_TOP + i * (cell_h + CELL_GAP)

        strip_h = _label_strip_height(cell_h)
        _draw_label(page, img_path, cell_x, cell_y, cell_w, strip_h)

        img_x = cell_x
        img_y = cell_y + strip_h
        img_w = cell_w
        img_h = cell_h - strip_h

        img = _load_image(img_path, target_horizontal=True)
        img = _scale_image(img, img_w, img_h)

        x_offset = img_x + (img_w - img.width) // 2
        y_offset = img_y + (img_h - img.height) // 2
        page.paste(img, (x_offset, y_offset))

    return page


# --------------------------------------------------------------------------
#  Aspect-ratio reporting (--show-ratios)
# --------------------------------------------------------------------------

def _fmt_ratio(w, h):
    g = gcd(int(w), int(h))
    return f"{int(w)//g}:{int(h)//g}"


def _closest_common(value, tolerance=0.02):
    name, val = min(_COMMON_RATIOS.items(), key=lambda kv: abs(kv[1] - value))
    if abs(val - value) <= tolerance:
        return name
    return None


def _describe_cell(label, cell_w, cell_h, orientation_note, active):
    ratio = cell_w / cell_h
    marker = "  <-- ACTIVE" if active else ""
    print(f"[{label}]{marker}")
    print(f"  Cell size (image area): {cell_w} x {cell_h} px "
          f"({cell_w/300:.3f}\" x {cell_h/300:.3f}\" @ 300 DPI)")
    print(f"  Required image aspect ratio (w:h): "
          f"{_fmt_ratio(cell_w, cell_h)}  =  {ratio:.4f}")
    hint = _closest_common(ratio)
    if hint:
        print(f"  Closest common ratio: {hint}")
    else:
        print(f"  No common photo ratio matches within ±2%.")
    print(f"  Image orientation: {orientation_note}")
    print()


def print_ratios():
    """Print the aspect ratios required to fill each cell without cropping."""
    live_w = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    live_h = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    strip_one = _label_strip_height(live_h)
    cell_h_two_full = (live_h - CELL_GAP) // 2
    strip_two = _label_strip_height(cell_h_two_full)

    print("=" * _BANNER_W)
    print("Required image aspect ratios (no cropping)")
    print("=" * _BANNER_W)
    print(f"Page: {PAGE_WIDTH} x {PAGE_HEIGHT} px  "
          f"({PAGE_WIDTH/300:.2f}\" x {PAGE_HEIGHT/300:.2f}\" @ 300 DPI, portrait)")
    print(f"Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} "
          f"Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print(f"Live area: {live_w} x {live_h} px")
    if SHOW_FILENAME_LABEL:
        print(f"Label strip: {LABEL_HEIGHT_RATIO*100:.1f}% of each cell height "
              f"(top of cell)")
    print()

    # One-per-page cell (image area only)
    orientation_one = ("portrait (forced)" if ROTATE_TO_VERTICAL
                       else "any orientation (no forced rotation)")
    _describe_cell(
        "ONE IMAGE PER PAGE",
        live_w, live_h - strip_one,
        orientation_one,
        active=(not TWO_HORIZONTAL_PER_PAGE),
    )

    # Two-per-page cell (image area only)
    print(f"[TWO IMAGES PER PAGE]  (cell gap: {CELL_GAP} px)")
    print(f"  Full cell:  {live_w} x {cell_h_two_full} px")
    if strip_two:
        print(f"  Label strip: {strip_two} px  ->  "
              f"image area: {live_w} x {cell_h_two_full - strip_two} px")
    img_h_two = cell_h_two_full - strip_two
    ratio = live_w / img_h_two
    print(f"  Required image aspect ratio (w:h): "
          f"{_fmt_ratio(live_w, img_h_two)}  =  {ratio:.4f}")
    hint = _closest_common(ratio)
    if hint:
        print(f"  Closest common ratio: {hint}")
    else:
        print(f"  No common photo ratio matches within ±2%.")
    print(f"  Image orientation: landscape (forced)")
    if TWO_HORIZONTAL_PER_PAGE:
        print("  <-- ACTIVE")
    print()

    print("Note: these are the ratios an image must already have for the")
    print("      image to fill its cell completely in MAXIMIZE mode")
    print("      without any part of it being cropped.")
    print("=" * _BANNER_W)


# --------------------------------------------------------------------------
#  Single-image crop analysis (--test-image)
# --------------------------------------------------------------------------

def test_image(img_path):
    """Analyze a single image against the current configuration."""
    if not os.path.isfile(img_path):
        print(f"Error: Image file '{img_path}' does not exist.")
        sys.exit(1)

    live_w = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    live_h = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    if TWO_HORIZONTAL_PER_PAGE:
        full_cell_h = (live_h - CELL_GAP) // 2
        target_horizontal = True
        layout_label = "TWO IMAGES PER PAGE  (top or bottom cell)"
        orientation_label = "landscape (forced)"
    else:
        full_cell_h = live_h
        target_horizontal = not ROTATE_TO_VERTICAL
        layout_label = "ONE IMAGE PER PAGE"
        orientation_label = ("portrait (forced)" if ROTATE_TO_VERTICAL
                             else "any orientation (no forced rotation)")

    strip_h = _label_strip_height(full_cell_h)
    cell_w = live_w
    cell_h = full_cell_h - strip_h

    try:
        with Image.open(img_path) as img:
            orig_w, orig_h = img.size
    except Exception as e:
        print(f"Error: Could not open '{img_path}': {e}")
        sys.exit(1)

    orig_landscape = orig_w > orig_h
    would_rotate = (target_horizontal and not orig_landscape) or \
                   (not target_horizontal and orig_landscape)

    final_w, final_h = (orig_h, orig_w) if would_rotate else (orig_w, orig_h)

    img_ratio = final_w / final_h
    cell_ratio = cell_w / cell_h

    if MAXIMIZE_PAGE_USAGE and AVOID_CROP:
        scaling_label = "MAXIMIZE WITHOUT CROP (contain)"
    elif MAXIMIZE_PAGE_USAGE:
        scaling_label = "MAXIMIZE (fill + crop)"
    else:
        scaling_label = "FIT (whole image, no upscaling)"

    # ======================================================================
    #  HEADER
    # ======================================================================
    print("=" * _BANNER_W)
    print("  IMAGE TEST")
    print("=" * _BANNER_W)
    print(f"  File:    {img_path}")
    print(f"  Layout:  {layout_label}")
    print(f"  Scaling: {scaling_label}")
    if SHOW_FILENAME_LABEL:
        print(f"  Label:   ON  ({LABEL_HEIGHT_RATIO*100:.1f}% of cell height)")
    else:
        print(f"  Label:   off")
    print()

    # ======================================================================
    #  SUPPORTING DETAILS
    # ======================================================================
    print("-" * _BANNER_W)
    print("  Details")
    print("-" * _BANNER_W)

    print(f"  Original size:         {orig_w} x {orig_h} px   "
          f"({orig_w/300:.2f}\" x {orig_h/300:.2f}\" @ 300 DPI)")
    print(f"  Original orientation:  "
          f"{'landscape' if orig_landscape else 'portrait'}")

    if would_rotate:
        print(f"  Rotation:              90° clockwise "
              f"->  {final_w} x {final_h} px")
    else:
        print(f"  Rotation:              none "
              f"->  {final_w} x {final_h} px")

    if SHOW_FILENAME_LABEL:
        print(f"  Full cell size:        {cell_w} x {full_cell_h} px")
        print(f"  Label strip:           {strip_h} px  (at top of cell)")
        print(f"  Image area:            {cell_w} x {cell_h} px   "
              f"({cell_w/300:.3f}\" x {cell_h/300:.3f}\" @ 300 DPI)")
    else:
        print(f"  Cell size:             {cell_w} x {cell_h} px   "
              f"({cell_w/300:.3f}\" x {cell_h/300:.3f}\" @ 300 DPI)")
    print(f"  Required orientation:  {orientation_label}")
    print()
    print(f"  Aspect ratio — image:  {_fmt_ratio(final_w, final_h)}  "
          f"=  {img_ratio:.4f}")
    print(f"  Aspect ratio — cell:   {_fmt_ratio(cell_w, cell_h)}  "
          f"=  {cell_ratio:.4f}")

    if MAXIMIZE_PAGE_USAGE:
        if AVOID_CROP:
            s = min(cell_w / final_w, cell_h / final_h)
            print(f"  Contain scale factor:  {s:.4f}")
            print(f"  Scaled to:             "
                  f"{round(final_w * s)} x {round(final_h * s)} px")
        else:
            s = max(cell_w / final_w, cell_h / final_h)
            print(f"  Cover scale factor:    {s:.4f}")
            print(f"  Scaled before crop:    "
                  f"{round(final_w * s)} x {round(final_h * s)} px")
    else:
        s = min(cell_w / final_w, cell_h / final_h)
        if s >= 1.0:
            print(f"  Fit scale factor:      (none — fits at native size)")
        else:
            print(f"  Fit scale factor:      {s:.4f}")
            print(f"  Scaled to:             "
                  f"{int(final_w * s)} x {int(final_h * s)} px")
    print()

    # ======================================================================
    #  VERDICT — printed last, made prominent
    # ======================================================================
    print("!" * _BANNER_W)
    print("  >>> VERDICT <<<")
    print("!" * _BANNER_W)

    if MAXIMIZE_PAGE_USAGE and not AVOID_CROP:
        s = max(cell_w / final_w, cell_h / final_h)
        scaled_w = final_w * s
        scaled_h = final_h * s
        crop_w = scaled_w - cell_w
        crop_h = scaled_h - cell_h

        if crop_w < 0.5 and crop_h < 0.5:
            print("  Image aspect ratio MATCHES the cell exactly.")
            print("  ==> NO CROPPING will occur. Perfect fill.")
        elif crop_w >= 0.5 and crop_h < 0.5:
            crop_w_i = round(crop_w)
            left = crop_w_i // 2
            right = crop_w_i - left
            pct = 100.0 * crop_w / scaled_w
            print(f"  ==> Image is relatively WIDER than the cell.")
            print(f"  ==> Will be cropped on the LEFT and RIGHT sides.")
            print()
            print(f"  ==> {pct:.2f}% of the scaled width is lost.")
            print(f"      ({crop_w_i} px total: {left} px left + {right} px right)")
        elif crop_h >= 0.5 and crop_w < 0.5:
            crop_h_i = round(crop_h)
            top = crop_h_i // 2
            bottom = crop_h_i - top
            pct = 100.0 * crop_h / scaled_h
            print(f"  ==> Image is relatively TALLER than the cell.")
            print(f"  ==> Will be cropped on the TOP and BOTTOM sides.")
            print()
            print(f"  ==> {pct:.2f}% of the scaled height is lost.")
            print(f"      ({crop_h_i} px total: {top} px top + {bottom} px bottom)")
        else:
            print(f"  Negligible sub-pixel crop "
                  f"({crop_w:.2f} px H, {crop_h:.2f} px V).")
            print(f"  ==> Effectively NO CROPPING.")

    elif MAXIMIZE_PAGE_USAGE and AVOID_CROP:
        s = min(cell_w / final_w, cell_h / final_h)
        placed_w = round(final_w * s)
        placed_h = round(final_h * s)
        free_w = cell_w - placed_w
        free_h = cell_h - placed_h

        if free_w < 0.5 and free_h < 0.5:
            print("  Image aspect ratio MATCHES the cell exactly.")
            print("  ==> NO CROPPING, NO WHITESPACE. Perfect fill.")
        elif img_ratio > cell_ratio:
            pct = 100.0 * free_h / cell_h
            top = round(free_h / 2)
            bottom = round(free_h / 2)
            print(f"  ==> Image is relatively WIDER than the cell.")
            print(f"  ==> Scaled to fit the cell WIDTH; no cropping occurs.")
            print()
            print(f"  ==> {pct:.2f}% of the cell HEIGHT is whitespace.")
            print(f"      ({round(free_h)} px: {top} px top + {bottom} px bottom)")
        else:
            pct = 100.0 * free_w / cell_w
            left = round(free_w / 2)
            right = round(free_w / 2)
            print(f"  ==> Image is relatively TALLER than the cell.")
            print(f"  ==> Scaled to fit the cell HEIGHT; no cropping occurs.")
            print()
            print(f"  ==> {pct:.2f}% of the cell WIDTH is whitespace.")
            print(f"      ({round(free_w)} px: {left} px left + {right} px right)")

    else:
        s = min(cell_w / final_w, cell_h / final_h)
        if s >= 1.0:
            placed_w, placed_h = final_w, final_h
        else:
            placed_w = int(final_w * s)
            placed_h = int(final_h * s)
        free_w = cell_w - placed_w
        free_h = cell_h - placed_h

        if free_w <= 0 and free_h <= 0:
            print("  ==> Image FILLS the cell exactly. No cropping, no waste.")
        else:
            print("  ==> FIT mode: NO CROPPING occurs.")
            print("  ==> Image will be smaller than the cell.")
            if free_h >= free_w:
                pct = 100.0 * free_h / cell_h
                print()
                print(f"  ==> {pct:.2f}% of the cell HEIGHT is whitespace.")
                print(f"      ({free_h} px, split top/bottom)")
            else:
                pct = 100.0 * free_w / cell_w
                print()
                print(f"  ==> {pct:.2f}% of the cell WIDTH is whitespace.")
                print(f"      ({free_w} px, split left/right)")

    print("!" * _BANNER_W)
    print()
    print("  Hint: run --show-ratios to see the exact aspect ratio")
    print("        needed for a crop-free fill of the cell.")
    print("=" * _BANNER_W)


# --------------------------------------------------------------------------
#  CLI
# --------------------------------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        prog="imgs2pdf.py",
        description=(
            "Convert all images in a directory (top-level only) to a single PDF. "
            "If no directory is given, the INPUT_DIR variable in the script is used."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python3 imgs2pdf.py\n"
            "  python3 imgs2pdf.py /path/to/images\n"
            "  python3 imgs2pdf.py ~/Pictures\n"
            "  python3 imgs2pdf.py --show-ratios\n"
            "  python3 imgs2pdf.py --test-image ~/Pictures/one.jpg\n"
        ),
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=INPUT_DIR,
        help=f"Directory containing images (default: {INPUT_DIR!r})",
    )
    parser.add_argument(
        "--show-ratios", "--ratios",
        action="store_true",
        help=(
            "Print the exact image aspect ratio needed to fill each cell "
            "with no cropping (in maximize mode) for the current page "
            "configuration, then exit without generating a PDF."
        ),
    )
    parser.add_argument(
        "--test-image",
        metavar="PATH",
        default=None,
        help=(
            "Analyze a single image against the current configuration and "
            "report what would happen to it (rotation, scaling, and what "
            "happens inside its cell), then exit without generating a PDF."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.show_ratios:
        print_ratios()
        sys.exit(0)

    if args.test_image:
        test_image(os.path.expanduser(args.test_image))
        sys.exit(0)

    input_dir = os.path.expanduser(args.directory)

    if not os.path.isdir(input_dir):
        print(f"Error: Directory '{input_dir}' does not exist.")
        sys.exit(1)

    images = get_images(input_dir)
    total_images = len(images)
    if total_images == 0:
        print(f"Error: No supported images found in '{input_dir}'")
        print(f"Supported formats: {', '.join(IMAGE_EXTS)}")
        print("Note: Subdirectories are NOT searched.")
        sys.exit(1)

    if MAXIMIZE_PAGE_USAGE and AVOID_CROP:
        scaling_str = "MAXIMIZE WITHOUT CROP (fills cell on one axis, no loss)"
    elif MAXIMIZE_PAGE_USAGE:
        scaling_str = "MAXIMIZE (fill + crop)"
    else:
        scaling_str = "FIT (no upscale, may leave whitespace)"

    print("=" * _BANNER_W)
    print("Directory Images to PDF Converter")
    print("=" * _BANNER_W)
    print(f"Found {total_images} image(s) in: {input_dir}")
    if TWO_HORIZONTAL_PER_PAGE:
        print("Mode: TWO HORIZONTAL IMAGES PER PAGE (portrait pages)")
    else:
        print("Mode: ONE IMAGE PER PAGE (portrait pages)")
    print(f"Scaling: {scaling_str}")
    if SHOW_FILENAME_LABEL:
        print(f"Labels: ON  ({LABEL_HEIGHT_RATIO*100:.1f}% of cell height, "
              f"font: {os.path.basename(LABEL_FONT_PATH)})")
    else:
        print(f"Labels: off")
    print(f"Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} "
          f"Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    if TWO_HORIZONTAL_PER_PAGE:
        print(f"Cell gap between stacked images: {CELL_GAP} px")
    print("=" * _BANNER_W)
    print()

    pages = []

    if TWO_HORIZONTAL_PER_PAGE:
        chunk_size = 2
        total_pages = (total_images + chunk_size - 1) // chunk_size
        for page_num in range(total_pages):
            start = page_num * chunk_size
            chunk = images[start:start + chunk_size]
            for offset, img_path in enumerate(chunk):
                print(f"[{start + offset + 1}/{total_images}] "
                      f"Processing: {os.path.basename(img_path)}")
            page = create_two_per_page(chunk)
            pages.append(page)
            print(f"  ✓ Page {page_num + 1}/{total_pages} ready")
    else:
        total_pages = total_images
        for i, img_path in enumerate(images):
            print(f"[{i + 1}/{total_images}] "
                  f"Processing: {os.path.basename(img_path)}")
            page = create_portrait_page(img_path)
            pages.append(page)
            print(f"  ✓ Page {i + 1} ready")

    print()
    print("Saving PDF...")

    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=300.0,
        title="Directory Images PDF"
    )

    print()
    print("=" * _BANNER_W)
    print(f"✓ Done! PDF created: {OUTPUT_PDF}")
    print(f"  Total pages: {total_pages}")
    print(f"  Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} "
          f"Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print("=" * _BANNER_W)


if __name__ == "__main__":
    main()
