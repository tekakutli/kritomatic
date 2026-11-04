#!/usr/bin/env python3
"""
imgs2size.py Images to Size PDF Converter with optional filename/number labels
-----------------------------------------------------------------
Takes all images in a directory, scales each one (up or down, aspect ratio
preserved) to fit within a maximum width/height box (specified in cm), and
lays them out in flow-layout rows on US Letter pages.

Every physical dimension (page size, margins, gaps, image box, font) is
declared in real units (inches / cm / points) and converted to pixels
using DPI.  Changing DPI therefore changes only the output resolution,
not the physical layout.

When ROTATE_TO_VERTICAL is False, images are sorted by their post-scaling
height (smallest first) before layout, so shorter images fill the top of
each page and taller images trail toward the bottom.  When rotation is
on, the incoming filename order is preserved.

Optionally, the filename (without extension) or a sequential number is
printed below each image.
Supports per-edge margins, image spacing, and optional forced rotation.
Only processes images in the top-level directory (no subdirectories).

Usage:
    python3 imgs2size.py [directory_path] [-n|--numbers]

Options:
    -n, --numbers   Print a sequential number below each image instead
                    of the filename.
    -h, --help      Show this help message.

If no directory is given, the INPUT_DIR variable in the script is used.
"""

import os
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from PIL import JpegImagePlugin  # noqa: F401 - registers JPEG encoder needed by PdfImagePlugin

# ========== CONFIGURATION - EDIT THESE VALUES ==========
INPUT_DIR = "/tmp/"                    # Directory containing images (default)
OUTPUT_PDF = "output_size.pdf"         # Output PDF filename

# Fallback filename used when OUTPUT_PDF is empty / None / whitespace-only,
# or when OUTPUT_PDF names a directory.  Kept as a separate constant so a
# router-side override of OUTPUT_PDF to an empty string (or to a bare
# directory) still leaves a working default in place.
DEFAULT_OUTPUT_PDF = "output_size.pdf"

# ---- Output resolution ----
# Change this freely; every physical size below stays the same.
DPI = 300

# ---- Page size (inches) ----
PAGE_WIDTH_IN = 8.5                    # US Letter width
PAGE_HEIGHT_IN = 11.0                  # US Letter height

# ---- Margins (cm) ----
# (Defaults ≈ 200/300 px at 300 DPI, matching the original script)
MARGIN_TOP_CM = 1.69
MARGIN_BOTTOM_CM = 2.54
MARGIN_LEFT_CM = 1.69
MARGIN_RIGHT_CM = 1.69

# ---- Layout (cm) ----
CELL_GAP_CM = 0.17                     # Gap between images (h and between rows)

# Maximum dimensions an image may occupy after scaling, in cm.
# Images are scaled up or down to fit inside this box, aspect preserved.
MAX_IMAGE_WIDTH_CM = 6.0
MAX_IMAGE_HEIGHT_CM = 7.5

# ---- Rotation ----
# When True: landscape images are rotated 90° CCW before scaling, and the
#            incoming filename order is preserved.
# When False: images are left as-is, and the list is sorted by post-
#             scaling height (smallest first) before layout.
ROTATE_TO_VERTICAL = False

# ---- Label options ----
SHOW_FILENAME = True
NUMBER_IMAGES = False                  # Takes precedence over SHOW_FILENAME
FONT_SIZE_PT = 7.2                     # Font size in POINTS (DPI-independent)
FONT_COLOR = "black"
FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
GAP_BETWEEN_IMAGE_AND_TEXT_CM = 0.04   # ≈ 5 px at 300 DPI
EXTRA_GAP_BELOW_TEXT_CM = 0.08         # ≈ 10 px at 300 DPI
TEXT_MARGIN_BOTTOM_CM = 0.08           # ≈ 10 px at 300 DPI
# -----------------------------------------

# ========== DERIVED PIXEL VALUES - DO NOT EDIT ==========
PX_PER_CM = DPI / 2.54
PX_PER_IN = DPI
PX_PER_PT = DPI / 72.0


def _cm(v):
    return int(round(v * PX_PER_CM))


def _in(v):
    return int(round(v * PX_PER_IN))


def _pt(v):
    return int(round(v * PX_PER_PT))


PAGE_WIDTH  = _in(PAGE_WIDTH_IN)
PAGE_HEIGHT = _in(PAGE_HEIGHT_IN)

MARGIN_TOP    = _cm(MARGIN_TOP_CM)
MARGIN_BOTTOM = _cm(MARGIN_BOTTOM_CM)
MARGIN_LEFT   = _cm(MARGIN_LEFT_CM)
MARGIN_RIGHT  = _cm(MARGIN_RIGHT_CM)

CELL_GAP = _cm(CELL_GAP_CM)
MAX_IMAGE_WIDTH_PX  = _cm(MAX_IMAGE_WIDTH_CM)
MAX_IMAGE_HEIGHT_PX = _cm(MAX_IMAGE_HEIGHT_CM)

FONT_SIZE = _pt(FONT_SIZE_PT)
GAP_BETWEEN_IMAGE_AND_TEXT = _cm(GAP_BETWEEN_IMAGE_AND_TEXT_CM)
EXTRA_GAP_BELOW_TEXT       = _cm(EXTRA_GAP_BELOW_TEXT_CM)
TEXT_MARGIN_BOTTOM         = _cm(TEXT_MARGIN_BOTTOM_CM)
# =======================================================

# Supported image extensions
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp')

# --------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------

_cached_font = None


def _resolve_output_pdf():
    """Resolve OUTPUT_PDF into a concrete file path.

    Handled cases, in order:

      * Empty / None / non-string / whitespace-only
            -> DEFAULT_OUTPUT_PDF in the current directory.

      * Directory intent — any of: the value ends with a path
        separator; the value points at an existing directory; the
        basename is "" (as in "/tmp/" before normalization); or the
        basename is exactly ".pdf".  The last case exists because the
        router appends ".pdf" to any extensionless value, INCLUDING a
        bare directory like "/tmp/", producing "/tmp/.pdf".  In each
        of these cases the fake suffix (if any) is stripped and
        DEFAULT_OUTPUT_PDF is placed inside the intended directory.

      * A path with no extension  -> append ".pdf".
            ("album" -> "album.pdf"; also makes direct invocations
            like `python3 imgs2size.py` behave like the router.)

      * Anything else              -> use as-is.
    """
    val = OUTPUT_PDF

    # 1. Empty / None / non-string / whitespace → default in cwd.
    if not (isinstance(val, str) and val.strip()):
        print(f"  Note: OUTPUT_PDF was empty; using default: {DEFAULT_OUTPUT_PDF}")
        return DEFAULT_OUTPUT_PDF

    val = val.strip()
    p = Path(val)

    # 2. Directory intent.
    looks_like_dir = (
        val.endswith(("/", os.sep))
        or p.is_dir()
        or p.name == ""
        or p.name == ".pdf"
    )
    if looks_like_dir:
        # "/tmp/.pdf" is what the router produces from "/tmp/" by
        # appending ".pdf" to an extensionless value.  Strip the fake
        # suffix to recover the intended directory.  For every other
        # directory-looking form, use the path as-is.
        dir_path = p.parent if p.name == ".pdf" else p
        resolved = str(dir_path / DEFAULT_OUTPUT_PDF)
        print(f"  Note: OUTPUT_PDF looked like a directory; using: {resolved}")
        return resolved

    # 3. Missing extension → append .pdf.
    if not p.suffix:
        return str(p) + ".pdf"

    return str(p)


def _get_font():
    global _cached_font
    if _cached_font is not None:
        return _cached_font
    try:
        if FONT_PATH and os.path.isfile(FONT_PATH):
            _cached_font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
        else:
            _cached_font = ImageFont.load_default()
            print("  Warning: Using default PIL font; FONT_PATH not set or invalid.")
    except Exception as e:
        print(f"  Warning: Could not load font: {e}. Using default.")
        _cached_font = ImageFont.load_default()
    return _cached_font


def scale_to_fit(img, max_w, max_h):
    """Return img resized (up OR down) to fit inside (max_w, max_h),
       preserving aspect ratio."""
    if img.width <= 0 or img.height <= 0:
        return img
    scale = min(max_w / img.width, max_h / img.height)
    new_w = max(1, int(round(img.width * scale)))
    new_h = max(1, int(round(img.height * scale)))
    if (new_w, new_h) == (img.width, img.height):
        return img
    return img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _scaled_height(path):
    """Return the height (px) this image will have after scale_to_fit into
    the MAX_IMAGE_WIDTH_PX × MAX_IMAGE_HEIGHT_PX box, or None if the
    file's dimensions can't be read.

    Uses Image.open(path).size only — the header is parsed but the
    pixel data is not decoded, so this is cheap even for large files
    and large directories.
    """
    try:
        with Image.open(path) as im:
            w, h = im.size
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    scale = min(MAX_IMAGE_WIDTH_PX / w, MAX_IMAGE_HEIGHT_PX / h)
    return h * scale


def _sort_by_scaled_height(paths):
    """Return paths ordered from smallest to largest eventual on-page
    height.

    Files that cannot be probed go last, keeping their incoming order
    among themselves (stable sort with a large sentinel key).
    """
    sentinel = float('inf')
    keyed = []
    for p in paths:
        h = _scaled_height(p)
        keyed.append((sentinel if h is None else h, p))
    keyed.sort(key=lambda t: t[0])
    return [p for _, p in keyed]


def get_images(directory):
    """Return a sorted list of image paths in the top-level directory.

    Ordering:
      * If ROTATE_TO_VERTICAL is True  -> by filename (unchanged).
      * If ROTATE_TO_VERTICAL is False -> by post-scaling height,
                                          smallest first.
    """
    images = []
    for ext in IMAGE_EXTS:
        for path in Path(directory).glob(f"*{ext}"):
            if path.is_file():
                images.append(str(path))
        for path in Path(directory).glob(f"*{ext.upper()}"):
            if path.is_file() and str(path) not in images:
                images.append(str(path))
    images = sorted(images)
    if not ROTATE_TO_VERTICAL:
        images = _sort_by_scaled_height(images)
    return images


def create_page(images, start_idx):
    labels_enabled = SHOW_FILENAME or NUMBER_IMAGES
    font = _get_font() if labels_enabled else None

    page = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')
    draw = ImageDraw.Draw(page) if labels_enabled else None

    live_width  = PAGE_WIDTH  - MARGIN_LEFT - MARGIN_RIGHT
    live_height = PAGE_HEIGHT - MARGIN_TOP  - MARGIN_BOTTOM
    live_right  = PAGE_WIDTH  - MARGIN_RIGHT
    live_bottom = PAGE_HEIGHT - MARGIN_BOTTOM

    label_reserve = 0
    if labels_enabled:
        label_reserve = (GAP_BETWEEN_IMAGE_AND_TEXT + FONT_SIZE
                         + EXTRA_GAP_BELOW_TEXT + TEXT_MARGIN_BOTTOM)

    eff_max_w = max(1, min(MAX_IMAGE_WIDTH_PX,  live_width))
    eff_max_h = max(1, min(MAX_IMAGE_HEIGHT_PX, live_height - label_reserve))

    cur_x = MARGIN_LEFT
    cur_y = MARGIN_TOP
    row_height = 0
    row_items = []

    def commit_row():
        # Draw the buffered row at its top-left corner (cur_y), then
        # advance cur_y past the row's tallest item and reset BOTH
        # cursors so the next row starts flush at the left margin.
        # Resetting cur_x here is what makes horizontal wrapping
        # actually return to the left edge — without it, every image
        # after the first wrap is pasted at whatever x-coordinate the
        # previous row had grown to, off the right side of the page.
        nonlocal cur_x, cur_y, row_height, row_items
        for (x, img, label, tw, _th) in row_items:
            page.paste(img, (x, cur_y))
            if labels_enabled:
                label_y = cur_y + img.height + GAP_BETWEEN_IMAGE_AND_TEXT
                label_x = x + (img.width - tw) // 2
                draw.text((label_x, label_y), label, fill=FONT_COLOR, font=font)
        if row_items:
            cur_y += row_height + CELL_GAP
        row_height = 0
        row_items = []
        cur_x = MARGIN_LEFT

    idx = start_idx
    while idx < len(images):
        img_path = images[idx]
        with Image.open(img_path) as im:
            if im.mode in ('RGBA', 'LA', 'P'):
                im = im.convert('RGB')
            if ROTATE_TO_VERTICAL and im.width > im.height:
                im = im.rotate(90, expand=True)
            im = scale_to_fit(im, eff_max_w, eff_max_h)

            label = ""
            tw = th = 0
            if labels_enabled:
                label = str(idx + 1) if NUMBER_IMAGES \
                    else os.path.splitext(os.path.basename(img_path))[0]
                bbox = draw.textbbox((0, 0), label, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]

            label_extra = 0
            if labels_enabled:
                label_extra = (GAP_BETWEEN_IMAGE_AND_TEXT + th
                               + EXTRA_GAP_BELOW_TEXT + TEXT_MARGIN_BOTTOM)
            item_h = im.height + label_extra

            # 1) Wrap horizontally if this item overflows the current row.
            #    commit_row() also resets cur_x to MARGIN_LEFT, so the new
            #    item lands flush left on the next row rather than at the
            #    old row's rightmost x-coordinate.
            if row_items and (cur_x + im.width > live_right):
                commit_row()

            # 2) If it doesn't fit vertically, either start a new row or
            #    end the page.  With row_items empty (fresh row), an
            #    overflow means nothing more fits at all on this page.
            if cur_y + item_h > live_bottom:
                if row_items:
                    commit_row()
                    if cur_y + item_h > live_bottom:
                        break
                else:
                    break

            row_items.append((cur_x, im, label, tw, th))
            row_height = max(row_height, item_h)
            cur_x += im.width + CELL_GAP
            idx += 1

    commit_row()
    return page, idx


def print_usage():
    print("Usage: python3 imgs2size.py [directory_path] [-n|--numbers]")
    print("If no directory is given, the INPUT_DIR variable in the script is used.")
    print("Options:")
    print("  -n, --numbers   Number each image sequentially instead of showing filenames")
    print("  -h, --help      Show this help message")


def main():
    global NUMBER_IMAGES

    input_dir = None
    for arg in sys.argv[1:]:
        if arg in ('-h', '--help'):
            print_usage()
            sys.exit(0)
        elif arg in ('-n', '--numbers'):
            NUMBER_IMAGES = True
        else:
            input_dir = arg

    if input_dir is None:
        input_dir = INPUT_DIR

    if not os.path.isdir(input_dir):
        print(f"Error: Directory '{input_dir}' does not exist.")
        sys.exit(1)

    output_pdf = _resolve_output_pdf()

    images = get_images(input_dir)
    total_images = len(images)
    if total_images == 0:
        print(f"Error: No supported images found in '{input_dir}'")
        print(f"Supported formats: {', '.join(IMAGE_EXTS)}")
        sys.exit(1)

    labels_enabled = SHOW_FILENAME or NUMBER_IMAGES

    print("==============================================")
    print("Images to Size PDF Converter")
    print("==============================================")
    print(f"DPI: {DPI}")
    print(f"Page: {PAGE_WIDTH_IN} in x {PAGE_HEIGHT_IN} in "
          f"({PAGE_WIDTH} x {PAGE_HEIGHT} px)")
    print(f"Margins (cm): T:{MARGIN_TOP_CM} B:{MARGIN_BOTTOM_CM} "
          f"L:{MARGIN_LEFT_CM} R:{MARGIN_RIGHT_CM}")
    print(f"Cell gap: {CELL_GAP_CM} cm ({CELL_GAP} px)")
    print(f"Max image size: {MAX_IMAGE_WIDTH_CM:.2f} cm x {MAX_IMAGE_HEIGHT_CM:.2f} cm "
          f"({MAX_IMAGE_WIDTH_PX} x {MAX_IMAGE_HEIGHT_PX} px)")
    print(f"Rotate to vertical: {'YES' if ROTATE_TO_VERTICAL else 'NO'}")
    if ROTATE_TO_VERTICAL:
        print("Sort order: by filename")
    else:
        print("Sort order: by post-scaling height (smallest first)")
    print(f"Output PDF: {output_pdf}")

    if NUMBER_IMAGES:
        print("Label mode: NUMBERS (sequential)")
    elif SHOW_FILENAME:
        print("Label mode: FILENAMES")
    else:
        print("Label mode: NONE")

    if labels_enabled:
        print(f"  Font size: {FONT_SIZE_PT} pt ({FONT_SIZE} px), Color: {FONT_COLOR}")
        print(f"  Font path: {FONT_PATH if FONT_PATH else 'default'}")
        print(f"  Gap between image and text: {GAP_BETWEEN_IMAGE_AND_TEXT_CM} cm "
              f"({GAP_BETWEEN_IMAGE_AND_TEXT} px)")
        print(f"  Extra gap below text: {EXTRA_GAP_BELOW_TEXT_CM} cm "
              f"({EXTRA_GAP_BELOW_TEXT} px)")
        print(f"  Text bottom margin: {TEXT_MARGIN_BOTTOM_CM} cm "
              f"({TEXT_MARGIN_BOTTOM} px)")
    print(f"Found {total_images} image(s) in: {input_dir}")
    print("==============================================")
    print()

    pages = []
    idx = 0
    page_num = 1
    while idx < total_images:
        page, next_idx = create_page(images, idx)
        if next_idx == idx:
            print(f"Warning: could not place '{os.path.basename(images[idx])}', skipping.")
            next_idx = idx + 1
        print(f"--- Page {page_num}: placed image(s) {idx + 1}\u2013{next_idx} "
              f"({next_idx - idx} image(s)) ---")
        pages.append(page)
        idx = next_idx
        page_num += 1

    total_pages = len(pages)
    print()
    print(f"Saving PDF ({total_pages} page(s))...")
    pages[0].save(
        output_pdf,
        save_all=True,
        append_images=pages[1:],
        resolution=float(DPI),
        title="Images to Size PDF"
    )
    print(f"PDF created: {output_pdf}")
    print(f"  Total pages: {total_pages}")
    print(f"  Images placed: {total_images}")
    print("==============================================")


if __name__ == "__main__":
    main()
