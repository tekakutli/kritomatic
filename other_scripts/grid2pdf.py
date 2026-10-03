#!/usr/bin/env python3
# ============================================
# grid2pdf.py
# DYNAMIC GRID TO PDF CONVERTER (PER-EDGE MARGINS)
# ============================================
# A script that scales and splits an image into parts for printing.
# Each page can have different margins per edge (top, bottom, left, right)
# specified in centimetres (converted to pixels automatically).
#
# USAGE:
#   python grid2pdf.py [image_path]    # optional: override INPUT_IMAGE
#   python grid2pdf.py --dimensions    # print final image dimensions and exit
#
#   If no image is given, the INPUT_IMAGE variable below is used.
# ============================================

import os
import sys
import argparse
from PIL import Image
from PIL import JpegImagePlugin  # noqa: F401 - registers JPEG encoder needed by PdfImagePlugin

# Explicit script name
SCRIPT_NAME = "grid2pdf.py"

# ========== EDIT THESE VALUES (defaults) ==========
INPUT_IMAGE = "image.jpg"      # Path to your image (can be overridden via CLI)
OUTPUT_PDF = "output.pdf"      # Output PDF filename
RAW_MODE = False               # True → exact pixel dimensions, False → scale to US Letter

# Grid layout
C = 2                          # Columns
R = 3                          # Rows

# Per‑edge margins in CENTIMETRES (converted to pixels based on DPI)
MARGIN_TOP_CM = 1
MARGIN_BOTTOM_CM = 2
MARGIN_LEFT_CM = 1.5
MARGIN_RIGHT_CM = 1.5

# Overlap between adjacent tiles (pixels) – set >0 to show extra image in each tile
# The tile size on the page remains unchanged; the image is scaled down slightly
# to fit the extra content, so adjacent tiles will have overlapping areas.
OVERLAP_DELTA = 50
# ===================================================

# US Letter size at 300 DPI (fixed)
PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300


def compute_assembled_dimensions(LIVE_WIDTH, LIVE_HEIGHT, C, R, OVERLAP_DELTA):
    """
    Calculate the total width and height (in pixels at print resolution) of the
    assembled grid after accounting for the overlap scaling.
    Returns (width_px, height_px).
    """
    # ---- Columns ----
    offsets_w = [0.0] * C
    for i in range(1, C):
        left_prev = max(0, (i - 1) * LIVE_WIDTH - OVERLAP_DELTA)
        right_prev = min(C * LIVE_WIDTH, i * LIVE_WIDTH + OVERLAP_DELTA)
        crop_prev = right_prev - left_prev
        left_cur = max(0, i * LIVE_WIDTH - OVERLAP_DELTA)
        right_cur = min(C * LIVE_WIDTH, (i + 1) * LIVE_WIDTH + OVERLAP_DELTA)
        crop_cur = right_cur - left_cur
        overlap_left = max(0, i * LIVE_WIDTH - OVERLAP_DELTA)
        overlap_right = min(C * LIVE_WIDTH, i * LIVE_WIDTH + OVERLAP_DELTA)
        x_prev = (overlap_left - left_prev) * LIVE_WIDTH / crop_prev
        x_cur = (overlap_left - left_cur) * LIVE_WIDTH / crop_cur
        offsets_w[i] = offsets_w[i - 1] + x_prev - x_cur
    total_width_px = offsets_w[-1] + LIVE_WIDTH

    # ---- Rows ----
    offsets_h = [0.0] * R
    for i in range(1, R):
        top_prev = max(0, (i - 1) * LIVE_HEIGHT - OVERLAP_DELTA)
        bottom_prev = min(R * LIVE_HEIGHT, i * LIVE_HEIGHT + OVERLAP_DELTA)
        crop_prev = bottom_prev - top_prev
        top_cur = max(0, i * LIVE_HEIGHT - OVERLAP_DELTA)
        bottom_cur = min(R * LIVE_HEIGHT, (i + 1) * LIVE_HEIGHT + OVERLAP_DELTA)
        crop_cur = bottom_cur - top_cur
        overlap_top = max(0, i * LIVE_HEIGHT - OVERLAP_DELTA)
        overlap_bottom = min(R * LIVE_HEIGHT, i * LIVE_HEIGHT + OVERLAP_DELTA)
        y_prev = (overlap_top - top_prev) * LIVE_HEIGHT / crop_prev
        y_cur = (overlap_top - top_cur) * LIVE_HEIGHT / crop_cur
        offsets_h[i] = offsets_h[i - 1] + y_prev - y_cur
    total_height_px = offsets_h[-1] + LIVE_HEIGHT

    return total_width_px, total_height_px


def load_and_prepare_image(path):
    """Load image, rotate if needed, return image object."""
    try:
        img = Image.open(path)
    except Exception as e:
        print(f"Error opening image: {e}", file=sys.stderr)
        sys.exit(1)
    if img.width > img.height:
        img = img.transpose(Image.ROTATE_90)
    return img


def get_image_path(args):
    """Determine input image path from CLI or variable."""
    if args.input_image:
        return args.input_image
    if INPUT_IMAGE:
        return INPUT_IMAGE
    print(
        f"Error: No input image specified. Set INPUT_IMAGE in {SCRIPT_NAME} "
        "or provide it as an argument.",
        file=sys.stderr,
    )
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Convert an image to a grid PDF with per‑edge margins (specified in cm).",
        epilog=(
            "All other settings (grid, margins, raw mode) are configured at the top "
            f"of {SCRIPT_NAME}."
        ),
    )
    parser.add_argument(
        "input_image",
        nargs="?",
        help="Path to input image (overrides INPUT_IMAGE variable if provided)"
    )
    parser.add_argument(
        "--dimensions", "-d",
        action="store_true",
        help="Print the final printed image dimensions (content only) and exit (no PDF created)."
    )
    args = parser.parse_args()

    input_path = get_image_path(args)
    if not os.path.isfile(input_path):
        print(f"Error: Input file '{input_path}' not found.", file=sys.stderr)
        sys.exit(1)

    # Determine DPI based on RAW_MODE
    DPI = 72.0 if RAW_MODE else 300.0

    # Convert margins from cm to pixels
    MARGIN_TOP = int(round(MARGIN_TOP_CM * DPI / 2.54))
    MARGIN_BOTTOM = int(round(MARGIN_BOTTOM_CM * DPI / 2.54))
    MARGIN_LEFT = int(round(MARGIN_LEFT_CM * DPI / 2.54))
    MARGIN_RIGHT = int(round(MARGIN_RIGHT_CM * DPI / 2.54))

    # Recalculate derived values with the new margins
    LIVE_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    LIVE_HEIGHT = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    TOTAL_GRID_WIDTH = LIVE_WIDTH * C
    TOTAL_GRID_HEIGHT = LIVE_HEIGHT * R
    TOTAL_PAGES = C * R

    # Load image (needed for dimensions, and for normal PDF creation)
    img = load_and_prepare_image(input_path)
    orig_w, orig_h = img.size

    # ---- If only dimensions are requested, compute and exit ----
    if args.dimensions:
        # 1. How the image is scaled to fit the grid (contain mode)
        target_w, target_h = TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT
        scale_fit = min(target_w / img.width, target_h / img.height)
        new_w_fit = int(round(img.width * scale_fit))
        new_h_fit = int(round(img.height * scale_fit))

        # 2. Assembled grid size (accounts for overlap scaling)
        assembled_w, assembled_h = compute_assembled_dimensions(
            LIVE_WIDTH, LIVE_HEIGHT, C, R, OVERLAP_DELTA
        )

        # 3. Scaling factors from canvas to assembled print
        scale_x = assembled_w / TOTAL_GRID_WIDTH
        scale_y = assembled_h / TOTAL_GRID_HEIGHT

        # 4. Printed image content size for "fit" (contain) mode
        image_print_w_fit = new_w_fit * scale_x
        image_print_h_fit = new_h_fit * scale_y

        w_cm_fit = image_print_w_fit / DPI * 2.54
        h_cm_fit = image_print_h_fit / DPI * 2.54

        # 5. Maximum possible size if the image fills the entire grid (cover mode)
        #    This is simply the assembled grid dimensions.
        w_cm_max = assembled_w / DPI * 2.54
        h_cm_max = assembled_h / DPI * 2.54

        print(
            f"Final printed image dimensions (content only, fit preserving aspect): "
            f"{w_cm_fit:.2f} cm × {h_cm_fit:.2f} cm (at {int(DPI)} DPI)"
        )
        print(f"Maximum if image fills entire grid (cover): {w_cm_max:.2f} cm × {h_cm_max:.2f} cm")
        print(
            f"Based on grid: {C}×{R}, margins (cm) "
            f"T:{MARGIN_TOP_CM:.2f} B:{MARGIN_BOTTOM_CM:.2f} "
            f"L:{MARGIN_LEFT_CM:.2f} R:{MARGIN_RIGHT_CM:.2f}  "
            f"(→ {MARGIN_TOP},{MARGIN_BOTTOM},{MARGIN_LEFT},{MARGIN_RIGHT} px), "
            f"overlap: {OVERLAP_DELTA} px"
        )
        return

    # ---- Normal PDF generation ----
    print("==============================================")
    print(f"{SCRIPT_NAME} - Dynamic Grid to PDF Converter (Per-Edge Margins)")
    print("==============================================")
    print(f"Original dimensions: {orig_w}×{orig_h}")
    if orig_w > orig_h:
        print("Width > Height: Rotating image 90 degrees clockwise...")
        print(f"Image rotated. New dimensions: {img.width}×{img.height}")
    else:
        print("No rotation needed (Height >= Width)")

    print(f"Mode: {'RAW (exact pixels)' if RAW_MODE else 'NORMAL (scaled to fit US Letter)'} (DPI={int(DPI)})")
    print(f"Grid: {C}×{R} ({TOTAL_PAGES} pages)")
    print(
        f"Margins (cm) - Top:{MARGIN_TOP_CM:.2f} Bottom:{MARGIN_BOTTOM_CM:.2f} "
        f"Left:{MARGIN_LEFT_CM:.2f} Right:{MARGIN_RIGHT_CM:.2f}"
    )
    print(f"Margins (px) - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print(f"Live area: {LIVE_WIDTH}×{LIVE_HEIGHT} px")
    print(f"Total grid: {TOTAL_GRID_WIDTH}×{TOTAL_GRID_HEIGHT} px")
    print(f"Overlap delta: {OVERLAP_DELTA} px")
    print("==============================================")

    # Step 1: Resize image to fill total grid (contain)
    print(f"\nStep 1/3: Resizing image to {TOTAL_GRID_WIDTH}×{TOTAL_GRID_HEIGHT}...")
    target_w, target_h = TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT
    scale_fit = min(target_w / img.width, target_h / img.height)
    new_w = int(round(img.width * scale_fit))
    new_h = int(round(img.height * scale_fit))
    img_resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), "white")
    x_offset = (target_w - new_w) // 2
    y_offset = (target_h - new_h) // 2
    canvas.paste(img_resized, (x_offset, y_offset))

    # Step 2: Split into tiles with overlap
    print(f"Step 2/3: Splitting into {C}×{R} parts with overlap delta={OVERLAP_DELTA}...")
    tiles = []
    for row in range(R):
        for col in range(C):
            left = col * LIVE_WIDTH
            top = row * LIVE_HEIGHT
            right = left + LIVE_WIDTH
            bottom = top + LIVE_HEIGHT

            x1 = max(0, left - OVERLAP_DELTA)
            y1 = max(0, top - OVERLAP_DELTA)
            x2 = min(TOTAL_GRID_WIDTH, right + OVERLAP_DELTA)
            y2 = min(TOTAL_GRID_HEIGHT, bottom + OVERLAP_DELTA)

            large_crop = canvas.crop((x1, y1, x2, y2))
            tile = large_crop.resize((LIVE_WIDTH, LIVE_HEIGHT), Image.Resampling.LANCZOS)
            tiles.append(tile)

    # Step 3: Place tiles on pages
    print("Step 3/3: Placing each tile onto US Letter pages with per‑edge margins...")
    pages = []
    for i, tile in enumerate(tiles, start=1):
        page = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
        page.paste(tile, (MARGIN_LEFT, MARGIN_TOP))
        pages.append(page)
        print(f"  Processed page {i} of {TOTAL_PAGES}")

    # Save PDF
    resolution = DPI
    print(f"\nSaving PDF with resolution={int(resolution)} DPI...")
    pages[0].save(
        OUTPUT_PDF,
        save_all=True,
        append_images=pages[1:],
        resolution=resolution
    )

    print("\n==============================================")
    print(f"Done! PDF created: {OUTPUT_PDF}")
    print(f"Pages: {TOTAL_PAGES}")
    if RAW_MODE:
        print("This PDF uses EXACT pixel dimensions (no scaling).")
    else:
        print("This PDF is scaled to US Letter paper (8.5×11 inches).")
    print(
        f"Margins (cm) - Top:{MARGIN_TOP_CM:.2f} Bottom:{MARGIN_BOTTOM_CM:.2f} "
        f"Left:{MARGIN_LEFT_CM:.2f} Right:{MARGIN_RIGHT_CM:.2f}"
    )
    print(f"Margins (px) - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    if OVERLAP_DELTA > 0:
        print(f"Overlap delta: {OVERLAP_DELTA} px (tiles show extra content; overlap when assembling and trim excess)")
    print("\nPRINTING INSTRUCTIONS:")
    print("  - Print all pages (actual size, no scaling)")
    print("  - Cut off the white margins")
    if OVERLAP_DELTA > 0:
        print("  - When assembling, overlap the edges to align the image, then trim the overlapping parts")
    print(f"  - Tape/glue pages together in a {C}×{R} grid")
    print("==============================================")


if __name__ == "__main__":
    main()
