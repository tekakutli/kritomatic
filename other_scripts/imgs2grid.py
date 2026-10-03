#!/usr/bin/env python3
"""
imgs2grid.py Grid Images to PDF Converter with optional filename/number labels
-----------------------------------------------------------------
Takes all images in a directory, groups them into bunches of 9 (3x3 grid),
and places each bunch on a single US Letter page (300 DPI).
Each image is scaled to fit its cell while preserving aspect ratio.
Optionally, the filename (without extension) or a sequential number is
printed below each image.
Supports per-edge margins, cell spacing, and optional forced rotation.
Only processes images in the top-level directory (no subdirectories).

Usage:
    python3 script.py [directory_path] [-n|--numbers]

Options:
    -n, --numbers   Print a sequential number below each image instead
                    of the filename.
    -h, --help      Show this help message.

If no directory is given, the INPUT_DIR variable in the script is used.
"""

import os
import sys
from math import ceil
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from PIL import JpegImagePlugin  # noqa: F401 - registers JPEG encoder needed by PdfImagePlugin

# ========== CONFIGURATION - EDIT THESE VALUES ==========
INPUT_DIR = "/tmp/"                    # Directory containing images (default)
OUTPUT_PDF = "output_grid.pdf"         # Output PDF filename

# Per-edge margins in pixels (150px = 0.5 inches at 300 DPI)
MARGIN_TOP = 200
MARGIN_BOTTOM = 300
MARGIN_LEFT = 200
MARGIN_RIGHT = 200

# Gap between cells in the grid (pixels)
CELL_GAP = 20

# Set to True to rotate any image with width > height to portrait (vertical),
# set to False to leave all images in their original orientation.
ROTATE_TO_VERTICAL = True

# ------------- LABEL OPTIONS -------------
SHOW_FILENAME = True           # Print filename below each image
NUMBER_IMAGES = False          # Print a sequential number below each image
                               # (takes precedence over SHOW_FILENAME)
FONT_SIZE = 30                 # Font size in pixels (at 300 DPI)
FONT_COLOR = "black"           # Any valid PIL color (e.g., "black", "#333333")
FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"  # .ttf/.otf path, or None
TEXT_MARGIN_BOTTOM = 10        # Extra space below the text (within the cell)
GAP_BETWEEN_IMAGE_AND_TEXT = 5  # Gap between the image and the text (pixels)
EXTRA_GAP_BELOW_TEXT = 10       # Additional gap below the text
# -----------------------------------------

# Set to True for RAW mode (exact pixels, no final scaling),
# False for normal mode (pages are already at US Letter size)
RAW_MODE = False   # not really used, kept for compatibility
# ===================================================

# Grid dimensions (fixed 3x3)
GRID_COLS = 3
GRID_ROWS = 3

# US Letter size at 300 DPI (fixed, do not change)
PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300

# Supported image extensions
IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp')


def get_images(directory):
    """Return sorted list of image paths in the top-level directory."""
    images = []
    for ext in IMAGE_EXTS:
        pattern = f"*{ext}"
        for path in Path(directory).glob(pattern):
            if path.is_file():
                images.append(str(path))
        for path in Path(directory).glob(f"*{ext.upper()}"):
            if path.is_file() and str(path) not in images:
                images.append(str(path))
    return sorted(images)


def create_grid_page(images_chunk, page_num, total_pages, start_index=0):
    """
    Create a single page (PIL Image) with up to 9 images arranged in a 3x3 grid.

    Labels (if enabled):
      - NUMBER_IMAGES=True  -> print a sequential number (1-based, global)
      - else SHOW_FILENAME  -> print the filename base (no extension)

    `start_index` is the global index of the first image in `images_chunk`
    (used so the numbering continues across pages).
    """
    page = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')

    live_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    live_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    cell_width = (live_width - (GRID_COLS - 1) * CELL_GAP) // GRID_COLS
    cell_height = (live_height - (GRID_ROWS - 1) * CELL_GAP) // GRID_ROWS

    labels_enabled = SHOW_FILENAME or NUMBER_IMAGES

    font = None
    if labels_enabled:
        try:
            if FONT_PATH and os.path.isfile(FONT_PATH):
                font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
            else:
                font = ImageFont.load_default()
                print("  Warning: Using default PIL font; FONT_PATH not set or invalid.")
        except Exception as e:
            print(f"  Warning: Could not load font: {e}. Using default.")
            font = ImageFont.load_default()

    for idx, img_path in enumerate(images_chunk):
        row = idx // GRID_COLS
        col = idx % GRID_COLS

        cell_x = MARGIN_LEFT + col * (cell_width + CELL_GAP)
        cell_y = MARGIN_TOP + row * (cell_height + CELL_GAP)

        with Image.open(img_path) as img:
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')

            if ROTATE_TO_VERTICAL and img.width > img.height:
                img = img.rotate(90, expand=True)

            text_height = 0
            text_width = 0
            label = ""
            if labels_enabled:
                if NUMBER_IMAGES:
                    label = str(start_index + idx + 1)
                else:
                    label = os.path.splitext(os.path.basename(img_path))[0]

                draw = ImageDraw.Draw(page)
                try:
                    bbox = draw.textbbox((0, 0), label, font=font)
                    text_width = bbox[2] - bbox[0]
                    text_height = bbox[3] - bbox[1]
                except AttributeError:
                    try:
                        text_width, text_height = draw.textsize(label, font=font)
                    except AttributeError:
                        text_width = len(label) * FONT_SIZE // 2
                        text_height = FONT_SIZE

            if labels_enabled:
                text_y = cell_y + cell_height - text_height - TEXT_MARGIN_BOTTOM - EXTRA_GAP_BELOW_TEXT
                max_img_height = text_y - GAP_BETWEEN_IMAGE_AND_TEXT - cell_y
                if max_img_height < 0:
                    max_img_height = 0
            else:
                max_img_height = cell_height

            img.thumbnail((cell_width, max_img_height), Image.Resampling.LANCZOS)

            x_offset = cell_x + (cell_width - img.width) // 2
            y_offset = cell_y + (max_img_height - img.height) // 2

            page.paste(img, (x_offset, y_offset))

            if labels_enabled:
                text_x = cell_x + (cell_width - text_width) // 2
                draw.text((text_x, text_y), label, fill=FONT_COLOR, font=font)

    return page


def print_usage():
    print("Usage: python3 script.py [directory_path] [-n|--numbers]")
    print("If no directory is given, the INPUT_DIR variable in the script is used.")
    print("Options:")
    print("  -n, --numbers   Number each image sequentially instead of showing filenames")
    print("  -h, --help      Show this help message")


def main():
    global NUMBER_IMAGES, SHOW_FILENAME

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

    images = get_images(input_dir)
    total_images = len(images)
    if total_images == 0:
        print(f"Error: No supported images found in '{input_dir}'")
        print(f"Supported formats: {', '.join(IMAGE_EXTS)}")
        sys.exit(1)

    images_per_page = GRID_COLS * GRID_ROWS
    total_pages = ceil(total_images / images_per_page)

    labels_enabled = SHOW_FILENAME or NUMBER_IMAGES

    print("==============================================")
    print("Grid Images to PDF Converter (3x3 per page)")
    print("==============================================")
    print(f"Found {total_images} image(s) in: {input_dir}")
    print(f"Will generate {total_pages} page(s) (9 images per page)")
    print(f"Margins - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print(f"Cell gap: {CELL_GAP} px")
    print(f"Rotate to vertical: {'YES' if ROTATE_TO_VERTICAL else 'NO'}")

    if NUMBER_IMAGES:
        print("Label mode: NUMBERS (sequential, across pages)")
    elif SHOW_FILENAME:
        print("Label mode: FILENAMES")
    else:
        print("Label mode: NONE")

    if labels_enabled:
        print(f"  Font size: {FONT_SIZE} px, Color: {FONT_COLOR}")
        print(f"  Font path: {FONT_PATH if FONT_PATH else 'default'}")
        print(f"  Gap between image and text: {GAP_BETWEEN_IMAGE_AND_TEXT} px")
        print(f"  Extra gap below text: {EXTRA_GAP_BELOW_TEXT} px")
        print(f"  Text bottom margin: {TEXT_MARGIN_BOTTOM} px")
    print("==============================================")
    print()

    pages = []
    for page_num in range(total_pages):
        start = page_num * images_per_page
        end = min(start + images_per_page, total_images)
        chunk = images[start:end]
        print(f"--- Page {page_num+1}/{total_pages} ---")
        for img_path in chunk:
            print(f"  Processing: {os.path.basename(img_path)}")
        page_img = create_grid_page(chunk, page_num + 1, total_pages, start_index=start)
        pages.append(page_img)
        print(f"  Page {page_num+1} ready")

    print()
    print("Saving PDF...")
    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=300.0,
        title="Grid Images PDF"
    )
    print(f"PDF created: {OUTPUT_PDF}")
    print(f"  Total pages: {total_pages}")
    print("==============================================")


if __name__ == "__main__":
    main()
