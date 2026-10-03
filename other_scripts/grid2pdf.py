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
#   python grid2pdf.py [image_path]                    # optional: override INPUT_IMAGE
#   python grid2pdf.py [image_path] --dimensions       # print final image dimensions and exit
#   python grid2pdf.py [image_path] --grid-for-size 100  # override GRID_FOR_SIZE_CM and generate PDF
#   python grid2pdf.py [image_path] --dimensions --grid-for-size 100
#       ^ report the printed dimensions that would result from that target, no PDF
#
#   If no image is given, the INPUT_IMAGE variable below is used.
# ============================================

import os
import sys
import json
import math
import argparse
import shutil
import tempfile
import multiprocessing as mp
from PIL import Image, ImageDraw
from PIL import JpegImagePlugin  # noqa: F401 - registers JPEG encoder needed by PdfImagePlugin

try:
    import img2pdf
    HAVE_IMG2PDF = True
except ImportError:
    HAVE_IMG2PDF = False

# Explicit script name (derived from the filename on disk)
SCRIPT_NAME = os.path.basename(__file__)

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

# Overlap between adjacent tiles, in CENTIMETRES (converted to pixels based on DPI).
# Set >0 to show extra image in each tile. The tile size on the page remains
# unchanged; the image is scaled down slightly to fit the extra content, so
# adjacent tiles will have overlapping areas.
OVERLAP_DELTA_CM = 0.5

# Surround the actual image content inside each tile with a thin black outline.
# Applies to both normal mode and --grid-for-size mode.
OUTLINE_TILES = False
OUTLINE_WIDTH_PX = 2           # thickness of the outline in pixels (at print DPI)

# ---- Grid adaptation (normal mode only) ----
# Shrink the grid to the smallest C'×R' that still holds the contain-scaled
# image. Printed dimensions are unchanged; only blank pages are removed.
SHRINK_GRID_TO_FIT = True

# Additionally search alternative grids up to (max(C,R), max(C,R)) and pick
# the one whose canvas aspect best matches the image (least waste), among
# those whose printed long side is at least the current one.
AUTO_GRID_ASPECT = False

# Write a JSON file with meta information about the final placement of the
# image content segments on each page. Useful for automation / verification.
WRITE_META_JSON = False
META_JSON_PATH = "meta.json"   # where the JSON file is written (when enabled)

# Target printed long side (cm) for the reverse grid calculation.
#   - If --grid-for-size is passed on the CLI, it overrides GRID_FOR_SIZE_CM.
#   - If USE_GRID_FOR_SIZE_BY_DEFAULT is True, GRID_FOR_SIZE_CM is used even
#     when --grid-for-size is NOT passed (i.e. it becomes the script default).
#   - When neither applies, the grid stays as configured by C and R above.
GRID_FOR_SIZE_CM = 50.0                # e.g. 100.0
USE_GRID_FOR_SIZE_BY_DEFAULT = False   # True → behave as if --grid-for-size GRID_FOR_SIZE_CM was passed

# ---- Performance settings ----
# Resampling filter used for scaling the image down/up into tiles.
#   LANCZOS  – sharpest, slowest
#   BICUBIC  – middle ground
#   BILINEAR – fastest, plenty for large prints
RESAMPLE = Image.Resampling.BILINEAR

# Use multiple processes to produce tiles in parallel. Big win on many-core machines.
USE_MULTIPROCESSING = True
NUM_WORKERS = 0                 # 0 = auto (os.cpu_count())

# Prefer img2pdf for combining pages (wraps JPEGs without re-encoding).
# Requires `pip install img2pdf`. If False or not installed, PIL is used.
USE_IMG2PDF = True

# JPEG encoding settings for intermediate pages and (if PIL is used) the final PDF.
JPEG_QUALITY = 88
JPEG_SUBSAMPLING = 2            # 4:2:0 chroma; halves encoder work at little visual cost
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


def compute_printed_size_cm(img_w, img_h, C_req, R_req, DPI,
                            LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA):
    """
    Given a grid CxR and the already-rotated image size, return the printed
    image size in cm for fit/contain mode, plus the maximum cover size in cm:
        (fit_w_cm, fit_h_cm, cover_w_cm, cover_h_cm)
    """
    total_grid_w = LIVE_WIDTH * C_req
    total_grid_h = LIVE_HEIGHT * R_req

    scale_fit = min(total_grid_w / img_w, total_grid_h / img_h)
    new_w = img_w * scale_fit
    new_h = img_h * scale_fit

    assembled_w, assembled_h = compute_assembled_dimensions(
        LIVE_WIDTH, LIVE_HEIGHT, C_req, R_req, OVERLAP_DELTA
    )

    scale_x = assembled_w / total_grid_w
    scale_y = assembled_h / total_grid_h

    return (
        new_w * scale_x * 2.54 / DPI,
        new_h * scale_y * 2.54 / DPI,
        assembled_w * 2.54 / DPI,
        assembled_h * 2.54 / DPI,
    )


def compute_scale_for_target_longside(img_w, img_h, C_req, R_req, DPI,
                                      LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
                                      target_cm):
    """
    Compute the image scale (orig px -> canvas px) so the *printed* long side
    equals target_cm. Returns (scale, printed_w_cm, printed_h_cm) or None
    if it can't be achieved without overflowing the grid canvas.
    """
    total_grid_w = LIVE_WIDTH * C_req
    total_grid_h = LIVE_HEIGHT * R_req

    assembled_w, assembled_h = compute_assembled_dimensions(
        LIVE_WIDTH, LIVE_HEIGHT, C_req, R_req, OVERLAP_DELTA
    )
    sw = assembled_w / total_grid_w
    sh = assembled_h / total_grid_h

    w_factor = img_w * sw
    h_factor = img_h * sh

    if w_factor >= h_factor:
        s = target_cm * DPI / (2.54 * w_factor)
    else:
        s = target_cm * DPI / (2.54 * h_factor)

    contain_scale = min(total_grid_w / img_w, total_grid_h / img_h)
    if s > contain_scale + 1e-9:
        return None  # would overflow the canvas, i.e. get cropped

    printed_w_cm = img_w * s * sw * 2.54 / DPI
    printed_h_cm = img_h * s * sh * 2.54 / DPI
    return s, printed_w_cm, printed_h_cm


def find_grid_for_long_side(target_cm, img_w, img_h, DPI,
                            LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
                            max_c=200, max_r=200):
    """
    Find the smallest CxR grid whose contain-mode printed long side already
    meets or exceeds target_cm. The image will then be scaled *down* to land
    exactly on target_cm.
    """
    target_px = target_cm * DPI / 2.54

    inc_w = LIVE_WIDTH * LIVE_WIDTH / max(1.0, LIVE_WIDTH + 2 * OVERLAP_DELTA)
    inc_h = LIVE_HEIGHT * LIVE_HEIGHT / max(1.0, LIVE_HEIGHT + 2 * OVERLAP_DELTA)

    max_c = max(1, min(max_c, int(target_px / max(1.0, inc_w)) + 3))
    max_r = max(1, min(max_r, int(target_px / max(1.0, inc_h)) + 3))

    best = None
    for C_req in range(1, max_c + 1):
        for R_req in range(1, max_r + 1):
            w_cm, h_cm, _, _ = compute_printed_size_cm(
                img_w, img_h, C_req, R_req, DPI,
                LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
            )
            long_cm = max(w_cm, h_cm)
            if long_cm + 1e-9 < target_cm:
                continue

            pages = C_req * R_req
            overshoot = long_cm - target_cm
            score = (pages, round(overshoot, 3), abs(C_req - R_req), C_req, R_req)
            if best is None or score < best[0]:
                best = (score, C_req, R_req, w_cm, h_cm)
    return best


def shrink_grid_to_fit(img_w, img_h, C, R, LIVE_WIDTH, LIVE_HEIGHT):
    """
    Iteratively shrink C, R to the smallest grid that still holds the
    contain-scaled image. The contain scale is invariant because the binding
    dimension isn't touched. Returns (C', R').
    """
    EPS = 1e-9
    while True:
        TGW = LIVE_WIDTH * C
        TGH = LIVE_HEIGHT * R
        scale_fit = min(TGW / img_w, TGH / img_h)
        new_w = int(round(img_w * scale_fit))
        new_h = int(round(img_h * scale_fit))
        cols_needed = max(1, min(C, int(math.ceil((new_w - EPS) / LIVE_WIDTH))))
        rows_needed = max(1, min(R, int(math.ceil((new_h - EPS) / LIVE_HEIGHT))))
        if (cols_needed, rows_needed) == (C, R):
            return C, R
        C, R = cols_needed, rows_needed


def find_best_grid_by_aspect(img_w, img_h, C, R, DPI,
                             LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
                             min_long_cm):
    """
    Search grids up to (max(C,R), max(C,R)) and pick the one whose assembled
    canvas minimizes waste, among those whose printed long side is at least
    min_long_cm. Rank: (waste%, -long_side_cm, pages, c, r).
    """
    C_max = max(C, R)
    R_max = max(C, R)

    best = None
    for c in range(1, C_max + 1):
        for r in range(1, R_max + 1):
            canvas_w = c * LIVE_WIDTH
            canvas_h = r * LIVE_HEIGHT
            scale = min(canvas_w / img_w, canvas_h / img_h)
            new_w = img_w * scale
            new_h = img_h * scale
            waste = 1.0 - (new_w * new_h) / (canvas_w * canvas_h)

            assembled_w, assembled_h = compute_assembled_dimensions(
                LIVE_WIDTH, LIVE_HEIGHT, c, r, OVERLAP_DELTA
            )
            sw = assembled_w / canvas_w
            sh = assembled_h / canvas_h
            print_w_cm = new_w * sw * 2.54 / DPI
            print_h_cm = new_h * sh * 2.54 / DPI
            long_cm = max(print_w_cm, print_h_cm)

            if long_cm < min_long_cm - 1e-6:
                continue

            pages = c * r
            score = (round(waste, 2), -round(long_cm, 2), pages, c, r)
            if best is None or score < best[0]:
                best = (score, c, r, waste, print_w_cm, print_h_cm, pages)
    return best


# ============================================================
# Tile production
# ============================================================

_TILE_CTX = None  # worker-process global


def _tile_init(*args):
    global _TILE_CTX
    _TILE_CTX = args


def _tile_worker(colrow):
    col, row = colrow
    return build_tile(col, row, _TILE_CTX)


def build_tile(col, row, ctx):
    """
    Produce the (tile, content_bbox) pair for grid cell (col, row) directly
    from the source image. No giant canvas is ever materialized.

    ctx = (source, img_w, img_h, new_w, new_h, x_offset, y_offset,
           LIVE_WIDTH, LIVE_HEIGHT, TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT,
           OVERLAP_DELTA, resample)
    """
    (source, img_w, img_h, new_w, new_h, x_offset, y_offset,
     LIVE_WIDTH, LIVE_HEIGHT, TGW, TGH, OVERLAP_DELTA, resample) = ctx

    # Scale factors: source px -> canvas px
    scale_x = new_w / img_w
    scale_y = new_h / img_h

    # Canvas region of this tile (including overlap spill)
    left = col * LIVE_WIDTH
    top = row * LIVE_HEIGHT
    x1 = max(0, left - OVERLAP_DELTA)
    y1 = max(0, top - OVERLAP_DELTA)
    x2 = min(TGW, left + LIVE_WIDTH + OVERLAP_DELTA)
    y2 = min(TGH, top + LIVE_HEIGHT + OVERLAP_DELTA)

    crop_w = x2 - x1
    crop_h = y2 - y1

    # Portion of the canvas region that actually contains image content
    c_left = max(x1, x_offset)
    c_top = max(y1, y_offset)
    c_right = min(x2, x_offset + new_w)
    c_bottom = min(y2, y_offset + new_h)

    tile = Image.new("RGB", (LIVE_WIDTH, LIVE_HEIGHT), "white")

    if c_right <= c_left or c_bottom <= c_top:
        return tile, None  # pure whitespace tile

    # Destination sub-rect inside the tile (tile-local pixels)
    sx = LIVE_WIDTH / crop_w
    sy = LIVE_HEIGHT / crop_h
    dx0 = int(round((c_left - x1) * sx))
    dy0 = int(round((c_top - y1) * sy))
    dx1 = int(round((c_right - x1) * sx))
    dy1 = int(round((c_bottom - y1) * sy))
    if dx1 <= dx0:
        dx1 = dx0 + 1
    if dy1 <= dy0:
        dy1 = dy0 + 1

    # Corresponding source sub-rect
    src_x0 = max(0, int(round((c_left - x_offset) / scale_x)))
    src_y0 = max(0, int(round((c_top - y_offset) / scale_y)))
    src_x1 = min(img_w, int(round((c_right - x_offset) / scale_x)))
    src_y1 = min(img_h, int(round((c_bottom - y_offset) / scale_y)))

    if src_x1 <= src_x0 or src_y1 <= src_y0:
        return tile, None

    region = source.crop((src_x0, src_y0, src_x1, src_y1))
    region = region.resize((dx1 - dx0, dy1 - dy0), resample)
    tile.paste(region, (dx0, dy0))

    return tile, (dx0, dy0, dx1, dy1)


def assemble_page(tile, bbox, MARGIN_LEFT, MARGIN_TOP):
    """Place a tile on a fresh page, optionally outlining the content."""
    page = Image.new("RGB", (PAGE_WIDTH, PAGE_HEIGHT), "white")
    page.paste(tile, (MARGIN_LEFT, MARGIN_TOP))

    if OUTLINE_TILES and bbox is not None:
        draw = ImageDraw.Draw(page)
        bx0, by0, bx1, by1 = bbox
        x0 = MARGIN_LEFT + bx0
        y0 = MARGIN_TOP + by0
        x1 = MARGIN_LEFT + bx1
        y1 = MARGIN_TOP + by1
        for k in range(OUTLINE_WIDTH_PX):
            draw.rectangle([x0 - k, y0 - k, x1 + k, y1 + k], outline="black")

    return page


# ============================================================
# Image loading
# ============================================================

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


# ============================================================
# Main
# ============================================================

def main():
    global C, R

    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description="Convert an image to a grid PDF with per‑edge margins (specified in cm).",
        epilog=(
            "All other settings (grid, margins, raw mode) are configured at the top "
            f"of {SCRIPT_NAME}. --grid-for-size overrides GRID_FOR_SIZE_CM; "
            "USE_GRID_FOR_SIZE_BY_DEFAULT applies GRID_FOR_SIZE_CM even without the flag. "
            "Combine --dimensions with --grid-for-size to preview the resulting printed "
            "size without generating a PDF."
        ),
    )
    parser.add_argument(
        "input_image",
        nargs="?",
        help="Path to input image (overrides INPUT_IMAGE variable if provided)"
    )
    parser.add_argument(
        "--dimensions",
        action="store_true",
        help="Print the final printed image dimensions (content only) and exit (no PDF created)."
    )
    parser.add_argument(
        "--grid-for-size",
        type=float,
        metavar="CM",
        help=("Target printed long side in cm. Overrides the GRID_FOR_SIZE_CM variable. "
              "Finds the smallest grid that can reach it, scales the image so the printed "
              "long side equals CM, and generates the PDF.")
    )
    args = parser.parse_args()

    # ---- Resolve effective grid-for-size target ----
    if args.grid_for_size is not None:
        grid_target_cm = args.grid_for_size
    elif USE_GRID_FOR_SIZE_BY_DEFAULT:
        grid_target_cm = GRID_FOR_SIZE_CM
    else:
        grid_target_cm = None

    if args.grid_for_size is not None and args.grid_for_size <= 0:
        parser.error("--grid-for-size must be positive")

    if USE_GRID_FOR_SIZE_BY_DEFAULT and grid_target_cm is not None and grid_target_cm <= 0:
        parser.error("GRID_FOR_SIZE_CM must be positive when USE_GRID_FOR_SIZE_BY_DEFAULT is True")

    if USE_GRID_FOR_SIZE_BY_DEFAULT and GRID_FOR_SIZE_CM is None:
        parser.error("USE_GRID_FOR_SIZE_BY_DEFAULT is True but GRID_FOR_SIZE_CM is None")

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

    # Convert overlap from cm to pixels (this is the value used by all calculations)
    OVERLAP_DELTA = int(round(OVERLAP_DELTA_CM * DPI / 2.54))

    # Live area per page
    LIVE_WIDTH = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    LIVE_HEIGHT = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    # Load image now – needed both for dimension reporting and for grid-for-size
    img = load_and_prepare_image(input_path)
    orig_w, orig_h = img.size

    # ---- If a target long side is active, resolve grid + custom scale ----
    scale_override = None
    if grid_target_cm is not None:
        result = find_grid_for_long_side(
            grid_target_cm, img.width, img.height,
            DPI, LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
        )
        if result is None:
            print(f"Error: no grid up to 200x200 reaches "
                  f"{grid_target_cm:.2f} cm.", file=sys.stderr)
            sys.exit(1)

        _, C_req, R_req, fit_w_cm, fit_h_cm = result
        scale_result = compute_scale_for_target_longside(
            img.width, img.height, C_req, R_req,
            DPI, LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
            grid_target_cm,
        )
        if scale_result is None:
            print(f"Error: grid {C_req}x{R_req} cannot reach "
                  f"{grid_target_cm:.2f} cm without overflow.", file=sys.stderr)
            sys.exit(1)

        scale_override, predicted_w_cm, predicted_h_cm = scale_result

        print("==============================================")
        source = ("--grid-for-size" if args.grid_for_size is not None
                  else "USE_GRID_FOR_SIZE_BY_DEFAULT / GRID_FOR_SIZE_CM")
        print(f"{SCRIPT_NAME} - grid-for-size target {grid_target_cm:.2f} cm (from {source})")
        print("==============================================")
        print(f"  Chosen grid: C={C_req}, R={R_req} ({C_req * R_req} pages)")
        print(f"  Contain-fit printed size for this grid: "
              f"{fit_w_cm:.2f} cm × {fit_h_cm:.2f} cm")
        print(f"  Image scale (orig px -> canvas px): {scale_override:.6f}")
        print(f"  Predicted printed image: "
              f"{predicted_w_cm:.2f} cm × {predicted_h_cm:.2f} cm")
        print("==============================================")

        C = C_req
        R = R_req

    # ---- Normal mode: shrink + optional aspect search ----
    if scale_override is None:
        if SHRINK_GRID_TO_FIT:
            C0, R0 = C, R
            C, R = shrink_grid_to_fit(
                img.width, img.height, C0, R0, LIVE_WIDTH, LIVE_HEIGHT
            )
            if (C, R) != (C0, R0):
                saved = C0 * R0 - C * R
                print("==============================================")
                print(
                    f"NOTICE: With the stated grid {C0}×{R0} and contain scaling, the "
                    f"image only needs a {C}×{R} cell region."
                )
                print(
                    f"        Shrinking to avoid printing {saved} blank page(s)."
                )
                print(
                    "        Printed image dimensions are unaffected: the contain "
                    "scale does not change."
                )
                print(
                    "        (Disable with SHRINK_GRID_TO_FIT = False in the script.)"
                )
                print("==============================================")

        if AUTO_GRID_ASPECT:
            # Baseline: contain-print long side at the current (post-shrink) grid
            TGW_c = LIVE_WIDTH * C
            TGH_c = LIVE_HEIGHT * R
            scale_c = min(TGW_c / img.width, TGH_c / img.height)
            new_w_c = img.width * scale_c
            new_h_c = img.height * scale_c
            assembled_w_c, assembled_h_c = compute_assembled_dimensions(
                LIVE_WIDTH, LIVE_HEIGHT, C, R, OVERLAP_DELTA
            )
            sw_c = assembled_w_c / TGW_c
            sh_c = assembled_h_c / TGH_c
            cur_long_cm = max(
                new_w_c * sw_c * 2.54 / DPI,
                new_h_c * sh_c * 2.54 / DPI,
            )

            best = find_best_grid_by_aspect(
                img.width, img.height, C, R, DPI,
                LIVE_WIDTH, LIVE_HEIGHT, OVERLAP_DELTA,
                min_long_cm=cur_long_cm,
            )
            if best is not None:
                _, C_new, R_new, waste, w_cm, h_cm, pages = best
                if (C_new, R_new) != (C, R):
                    print("==============================================")
                    print(
                        f"NOTICE: AUTO_GRID_ASPECT adjusted grid to "
                        f"{C_new}×{R_new} (was {C}×{R}) for less waste."
                    )
                    print(
                        f"        Waste: {waste * 100:.1f}%   "
                        f"Pages: {pages}   "
                        f"Printed: {w_cm:.2f} × {h_cm:.2f} cm"
                    )
                    print("==============================================")
                    C, R = C_new, R_new

    # Recompute derived values now that C/R may have been overridden
    TOTAL_GRID_WIDTH = LIVE_WIDTH * C
    TOTAL_GRID_HEIGHT = LIVE_HEIGHT * R
    TOTAL_PAGES = C * R

    # ---- If only dimensions are requested, compute and exit ----
    if args.dimensions:
        if scale_override is not None:
            scale_fit = scale_override
            mode_label = f"at target long side {grid_target_cm:.2f} cm"
        else:
            target_w, target_h = TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT
            scale_fit = min(target_w / img.width, target_h / img.height)
            mode_label = "fit preserving aspect"

        new_w_fit = int(round(img.width * scale_fit))
        new_h_fit = int(round(img.height * scale_fit))

        assembled_w, assembled_h = compute_assembled_dimensions(
            LIVE_WIDTH, LIVE_HEIGHT, C, R, OVERLAP_DELTA
        )

        scale_x = assembled_w / TOTAL_GRID_WIDTH
        scale_y = assembled_h / TOTAL_GRID_HEIGHT

        image_print_w = new_w_fit * scale_x
        image_print_h = new_h_fit * scale_y

        w_cm = image_print_w / DPI * 2.54
        h_cm = image_print_h / DPI * 2.54

        w_cm_max = assembled_w / DPI * 2.54
        h_cm_max = assembled_h / DPI * 2.54

        # Report waste of the (post-adaptation) grid for reference
        waste_pct = 100.0 * (1.0 - (new_w_fit * new_h_fit) /
                             float(TOTAL_GRID_WIDTH * TOTAL_GRID_HEIGHT))

        print(
            f"Final printed image dimensions (content only, {mode_label}): "
            f"{w_cm:.2f} cm × {h_cm:.2f} cm (at {int(DPI)} DPI)"
        )
        print(f"Maximum if image fills entire grid (cover): "
              f"{w_cm_max:.2f} cm × {h_cm_max:.2f} cm")
        print(f"Grid waste: {waste_pct:.1f}%  (grid {C}×{R}, {TOTAL_PAGES} pages)")
        print(
            f"Based on grid: {C}×{R}, margins (cm) "
            f"T:{MARGIN_TOP_CM:.2f} B:{MARGIN_BOTTOM_CM:.2f} "
            f"L:{MARGIN_LEFT_CM:.2f} R:{MARGIN_RIGHT_CM:.2f}  "
            f"(→ {MARGIN_TOP},{MARGIN_BOTTOM},{MARGIN_LEFT},{MARGIN_RIGHT} px), "
            f"overlap: {OVERLAP_DELTA_CM:.2f} cm (→ {OVERLAP_DELTA} px)"
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
    print(f"Margins (px) - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} "
          f"Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    print(f"Live area: {LIVE_WIDTH}×{LIVE_HEIGHT} px")
    print(f"Total grid: {TOTAL_GRID_WIDTH}×{TOTAL_GRID_HEIGHT} px")
    print(f"Overlap delta: {OVERLAP_DELTA_CM:.2f} cm (→ {OVERLAP_DELTA} px)")
    print(f"Outline tiles: {'ON' if OUTLINE_TILES else 'OFF'}"
          + (f" (width={OUTLINE_WIDTH_PX} px)" if OUTLINE_TILES else ""))
    print(f"Resample filter: {RESAMPLE.name}")
    print(f"Multiprocessing: {'ON' if USE_MULTIPROCESSING else 'OFF'}"
          + (f" (workers={NUM_WORKERS or os.cpu_count()})" if USE_MULTIPROCESSING else ""))
    print(f"PDF backend: {'img2pdf' if (USE_IMG2PDF and HAVE_IMG2PDF) else 'PIL'}"
          + (" (img2pdf not installed; falling back to PIL)"
             if (USE_IMG2PDF and not HAVE_IMG2PDF) else ""))
    print("==============================================")

    # Prominent, user-visible recommendation to install img2pdf when it's missing.
    if USE_IMG2PDF and not HAVE_IMG2PDF:
        print("==============================================")
        print("WARNING: img2pdf is NOT installed.")
        print("         Falling back to PIL for PDF assembly, which is much")
        print("         slower for large grids and uses more memory.")
        print("         Install it with:  pip install img2pdf")
        print("         Then re-run this script to get the fast path.")
        print("==============================================")

    # ---- Compute layout geometry (no canvas needed) ----
    target_w, target_h = TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT
    if scale_override is not None:
        scale_fit = scale_override
    else:
        scale_fit = min(target_w / img.width, target_h / img.height)

    new_w = int(round(img.width * scale_fit))
    new_h = int(round(img.height * scale_fit))
    x_offset = (target_w - new_w) // 2
    y_offset = (target_h - new_h) // 2

    if scale_override is not None:
        print(f"\nStep 1/3: Scaling image by factor {scale_fit:.6f} "
              f"(target long side) ...")
    else:
        print(f"\nStep 1/3: Scaling image (contain) ...")

    print(f"Step 2/3: Producing {C}×{R} tiles directly from source "
          f"(overlap delta={OVERLAP_DELTA} px) ...")
    print(f"Step 3/3: Assembling pages and streaming them to disk ...")

    tiles_coords = [(c, r) for r in range(R) for c in range(C)]

    tile_ctx = (
        img, img.width, img.height, new_w, new_h, x_offset, y_offset,
        LIVE_WIDTH, LIVE_HEIGHT, TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT,
        OVERLAP_DELTA, RESAMPLE,
    )

    # ---- Per-page meta collector (only populated when WRITE_META_JSON) ----
    page_meta = []
    CM_PER_PX = 2.54 / DPI
    TILE_AREA_PX = float(LIVE_WIDTH * LIVE_HEIGHT)

    def record_page_meta(idx, col, row, bbox_tile):
        left = col * LIVE_WIDTH
        top = row * LIVE_HEIGHT
        cx1 = max(0, left - OVERLAP_DELTA)
        cy1 = max(0, top - OVERLAP_DELTA)
        cx2 = min(TOTAL_GRID_WIDTH, left + LIVE_WIDTH + OVERLAP_DELTA)
        cy2 = min(TOTAL_GRID_HEIGHT, top + LIVE_HEIGHT + OVERLAP_DELTA)

        entry = {
            "index": idx,
            "col": col,
            "row": row,
            "canvas_crop_px": [cx1, cy1, cx2, cy2],
            "content_in_tile_px": list(bbox_tile) if bbox_tile else None,
        }
        if bbox_tile is not None:
            bx0, by0, bx1, by1 = bbox_tile
            px0 = MARGIN_LEFT + bx0
            py0 = MARGIN_TOP + by0
            px1 = MARGIN_LEFT + bx1
            py1 = MARGIN_TOP + by1
            entry["content_on_page_px"] = [px0, py0, px1, py1]
            entry["content_on_page_cm"] = [
                round(px0 * CM_PER_PX, 4),
                round(py0 * CM_PER_PX, 4),
                round((px1 - px0) * CM_PER_PX, 4),
                round((py1 - py0) * CM_PER_PX, 4),
            ]
            content_area = float((bx1 - bx0) * (by1 - by0))
            entry["waste_pct"] = round(100.0 * (1.0 - content_area / TILE_AREA_PX), 2)
        else:
            entry["content_on_page_px"] = None
            entry["content_on_page_cm"] = None
            entry["waste_pct"] = 100.0
        page_meta.append(entry)

    # Choose PDF backend / page sink
    use_img2pdf = USE_IMG2PDF and HAVE_IMG2PDF
    temp_dir = tempfile.mkdtemp(prefix="grid2pdf_") if use_img2pdf else None
    page_paths = []           # used when img2pdf is the backend
    pages_in_mem = []         # used when PIL is the backend

    def emit_page(page):
        if use_img2pdf:
            path = os.path.join(temp_dir, f"page_{len(page_paths):05d}.jpg")
            page.save(path, "JPEG",
                      quality=JPEG_QUALITY,
                      subsampling=JPEG_SUBSAMPLING,
                      dpi=(DPI, DPI),
                      optimize=False)
            page_paths.append(path)
        else:
            pages_in_mem.append(page)

    # Produce tiles (parallel or sequential), assemble pages, stream to sink
    if USE_MULTIPROCESSING and len(tiles_coords) > 1:
        workers = NUM_WORKERS if NUM_WORKERS > 0 else None
        with mp.Pool(workers, initializer=_tile_init, initargs=tile_ctx) as pool:
            for i, ((col, row), (tile, bbox)) in enumerate(
                zip(tiles_coords, pool.imap(_tile_worker, tiles_coords)), 1
            ):
                page = assemble_page(tile, bbox, MARGIN_LEFT, MARGIN_TOP)
                emit_page(page)
                if WRITE_META_JSON:
                    record_page_meta(i, col, row, bbox)
                print(f"  Processed page {i} of {TOTAL_PAGES}")
    else:
        for i, (col, row) in enumerate(tiles_coords, 1):
            tile, bbox = build_tile(col, row, tile_ctx)
            page = assemble_page(tile, bbox, MARGIN_LEFT, MARGIN_TOP)
            emit_page(page)
            if WRITE_META_JSON:
                record_page_meta(i, col, row, bbox)
            print(f"  Processed page {i} of {TOTAL_PAGES}")

    # ---- Combine pages into the final PDF ----
    if use_img2pdf:
        print(f"\nCombining {len(page_paths)} JPEG pages into PDF with img2pdf ...")
        with open(OUTPUT_PDF, "wb") as f:
            f.write(img2pdf.convert(page_paths))
        shutil.rmtree(temp_dir, ignore_errors=True)
    else:
        print(f"\nCombining {len(pages_in_mem)} pages into PDF with PIL ...")
        pages_in_mem[0].save(
            OUTPUT_PDF,
            save_all=True,
            append_images=pages_in_mem[1:],
            resolution=DPI,
            quality=JPEG_QUALITY,
            subsampling=JPEG_SUBSAMPLING,
            optimize=False,
        )
        pages_in_mem = []

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
    print(f"Margins (px) - Top:{MARGIN_TOP} Bottom:{MARGIN_BOTTOM} "
          f"Left:{MARGIN_LEFT} Right:{MARGIN_RIGHT}")
    if OVERLAP_DELTA > 0:
        print(f"Overlap delta: {OVERLAP_DELTA_CM:.2f} cm (→ {OVERLAP_DELTA} px) "
              f"(tiles show extra content; overlap when assembling and trim excess)")
    if OUTLINE_TILES:
        print(f"Outline tiles: ON (width={OUTLINE_WIDTH_PX} px)")

    # ---- Confirmation of achieved printed size when grid-for-size was active ----
    if scale_override is not None:
        assembled_w, assembled_h = compute_assembled_dimensions(
            LIVE_WIDTH, LIVE_HEIGHT, C, R, OVERLAP_DELTA
        )
        sw = assembled_w / TOTAL_GRID_WIDTH
        sh = assembled_h / TOTAL_GRID_HEIGHT
        printed_w_cm = new_w * sw * 2.54 / DPI
        printed_h_cm = new_h * sh * 2.54 / DPI

        print("----------------------------------------------")
        print("CONFIRMED PRINTED IMAGE DIMENSIONS (content only):")
        print(f"  {printed_w_cm:.2f} cm × {printed_h_cm:.2f} cm")
        print(f"  (target long side: {grid_target_cm:.2f} cm)")
        print(f"  Grid used: C={C}, R={R} ({C * R} pages)")
        print("----------------------------------------------")

    # ---- Optional JSON meta file ----
    if WRITE_META_JSON:
        assembled_w_m, assembled_h_m = compute_assembled_dimensions(
            LIVE_WIDTH, LIVE_HEIGHT, C, R, OVERLAP_DELTA
        )
        sw_m = assembled_w_m / TOTAL_GRID_WIDTH
        sh_m = assembled_h_m / TOTAL_GRID_HEIGHT
        printed_w_cm_m = new_w * sw_m * 2.54 / DPI
        printed_h_cm_m = new_h * sh_m * 2.54 / DPI

        overall_waste_pct = 100.0 * (
            1.0 - float(new_w * new_h) / float(TOTAL_GRID_WIDTH * TOTAL_GRID_HEIGHT)
        )

        meta = {
            "input": {
                "path": os.path.abspath(input_path),
                "original_px": [orig_w, orig_h],
                "rotated_px": [img.width, img.height],
            },
            "mode": ("grid_for_size" if scale_override is not None else "normal"),
            "target_long_side_cm": (grid_target_cm if scale_override is not None else None),
            "raw_mode": RAW_MODE,
            "dpi": DPI,
            "page": {
                "size_px": [PAGE_WIDTH, PAGE_HEIGHT],
                "size_cm": [
                    round(PAGE_WIDTH * CM_PER_PX, 4),
                    round(PAGE_HEIGHT * CM_PER_PX, 4),
                ],
            },
            "margins": {
                "top":    {"cm": MARGIN_TOP_CM,    "px": MARGIN_TOP},
                "bottom": {"cm": MARGIN_BOTTOM_CM, "px": MARGIN_BOTTOM},
                "left":   {"cm": MARGIN_LEFT_CM,   "px": MARGIN_LEFT},
                "right":  {"cm": MARGIN_RIGHT_CM,  "px": MARGIN_RIGHT},
            },
            "live_area_px": [LIVE_WIDTH, LIVE_HEIGHT],
            "overlap": {
                "cm": OVERLAP_DELTA_CM,
                "px": OVERLAP_DELTA,
            },
            "grid": {"cols": C, "rows": R, "total_pages": TOTAL_PAGES},
            "canvas_px": [TOTAL_GRID_WIDTH, TOTAL_GRID_HEIGHT],
            "scale_source_to_canvas": round(scale_fit, 8),
            "image_on_canvas_px": {
                "size": [new_w, new_h],
                "offset": [x_offset, y_offset],
            },
            "printed_size_cm": [
                round(printed_w_cm_m, 4),
                round(printed_h_cm_m, 4),
            ],
            "overall_waste_pct": round(overall_waste_pct, 2),
            "outline_tiles": OUTLINE_TILES,
            "outline_width_px": OUTLINE_WIDTH_PX,
            "pages": page_meta,
        }

        try:
            with open(META_JSON_PATH, "w", encoding="utf-8") as f:
                json.dump(meta, f, indent=2)
            print(f"Meta JSON written to: {os.path.abspath(META_JSON_PATH)}")
        except Exception as e:
            print(f"Warning: could not write meta JSON to '{META_JSON_PATH}': {e}",
                  file=sys.stderr)

    print("\nPRINTING INSTRUCTIONS:")
    print("  - Print all pages (actual size, no scaling)")
    print("  - Cut off the white margins")
    if OVERLAP_DELTA > 0:
        print("  - When assembling, overlap the edges to align the image, "
              "then trim the overlapping parts")
    print(f"  - Tape/glue pages together in a {C}×{R} grid")
    print("==============================================")


if __name__ == "__main__":
    main()
