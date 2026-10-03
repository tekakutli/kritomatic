#!/usr/bin/env python3
# ============================================
# solo2pdf.py
# SINGLE IMAGE -> US LETTER PDF
# ============================================
# Places ONE image at the TOP-LEFT corner of a US Letter page,
# inside user-defined margins.
#
# - Page size is fixed to US Letter (8.5 x 11 in).
# - Margins are set per edge, in centimetres.
# - The image WIDTH is set in centimetres; the height is derived
#   automatically so the aspect ratio is preserved.
# - If the requested image does not fit inside the live area,
#   it is scaled down to fit (a warning is printed).
#
# USAGE:
#   python solo2pdf.py [image_path]          # optional: override INPUT_IMAGE
#   python solo2pdf.py --width 12.5          # override image width (cm)
#   python solo2pdf.py --height 15.0         # set target height (cm);
#                                            # width is derived from aspect ratio
#   python solo2pdf.py --output poster.pdf   # override output filename
#   python solo2pdf.py --dimensions          # print computed sizes and exit
#
# NOTE: Long options must be spelled out in full; abbreviations such as
#       --w or --h are NOT accepted.
# ============================================

import argparse
import os
import sys

from PIL import Image

# Explicit script name
SCRIPT_NAME = "solo2pdf.py"

# ========== EDIT THESE VALUES (defaults) ==========
INPUT_IMAGE = "image.jpg"      # Path to your image (can be overridden via CLI)
OUTPUT_PDF = "output.pdf"      # Output PDF filename

DPI = 300                      # Print resolution (300 DPI is standard for print)

# --- Page size: US Letter (fixed) ---
PAGE_WIDTH_IN = 8.5
PAGE_HEIGHT_IN = 11.0

# --- Per-edge margins, in CENTIMETRES ---
MARGIN_TOP_CM = 2.54
MARGIN_BOTTOM_CM = 2.54
MARGIN_LEFT_CM = 2.54
MARGIN_RIGHT_CM = 2.54

# --- Image size, in CENTIMETRES. ---
# When FLIP_WIDTH_HEIGHT is False (default): IMAGE_WIDTH_CM is the image
# width, and the height is derived from the aspect ratio.
# When FLIP_WIDTH_HEIGHT is True: IMAGE_WIDTH_CM is instead interpreted as
# the image HEIGHT, and the width is derived from the aspect ratio.
# This flip affects ONLY this default value; the --width and --height flags
# always mean exactly what they say.
IMAGE_WIDTH_CM = 9.0

# --- If True, reinterpret IMAGE_WIDTH_CM as a height instead of a width. ---
FLIP_WIDTH_HEIGHT = False

# --- Rotate landscape images 90° clockwise before placing? ---
ROTATE_LANDSCAPE = False
# ===================================================


def cm_to_px(cm, dpi):
    """Convert centimetres to pixels at the given DPI."""
    return int(round(cm * dpi / 2.54))


def px_to_cm(px, dpi):
    """Convert pixels to centimetres at the given DPI."""
    return px / dpi * 2.54


def load_image(path):
    """Open the image, optionally rotate it, and flatten transparency to white."""
    try:
        img = Image.open(path)
    except Exception as e:
        print(f"Error opening image: {e}", file=sys.stderr)
        sys.exit(1)

    if ROTATE_LANDSCAPE and img.width > img.height:
        img = img.transpose(Image.Transpose.ROTATE_90)

    # Flatten any alpha channel onto a white background so the PDF has no
    # black/transparent surprises.
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.split()[-1])
        return bg

    return img.convert("RGB")


def size_from_primary(img, primary_cm, primary_is_height):
    """
    Given a primary centimetre value and whether it represents the height
    (True) or the width (False), return the (width_cm, height_cm) pair that
    preserves the image's aspect ratio.
    """
    aspect = img.width / img.height       # w / h
    if primary_is_height:
        height_cm = primary_cm
        width_cm = height_cm * aspect
    else:
        width_cm = primary_cm
        height_cm = width_cm / aspect
    return width_cm, height_cm


def compute_layout(img, width_cm, height_cm):
    """
    Compute all pixel dimensions for the page and the placed image.

    Returns a dict with page size, margins, live area, and the final
    image placement rectangle (in pixels).
    """
    page_w = int(round(PAGE_WIDTH_IN * DPI))
    page_h = int(round(PAGE_HEIGHT_IN * DPI))

    m_top = cm_to_px(MARGIN_TOP_CM, DPI)
    m_bottom = cm_to_px(MARGIN_BOTTOM_CM, DPI)
    m_left = cm_to_px(MARGIN_LEFT_CM, DPI)
    m_right = cm_to_px(MARGIN_RIGHT_CM, DPI)

    live_w = page_w - m_left - m_right
    live_h = page_h - m_top - m_bottom

    if live_w <= 0 or live_h <= 0:
        print(
            "Error: margins are larger than the page; nothing left to print on.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Requested image size, in pixels, from the resolved cm values.
    req_w = cm_to_px(width_cm, DPI)
    req_h = cm_to_px(height_cm, DPI)

    # Scale down if it does not fit inside the live area
    clamped = False
    scale = min(1.0, live_w / req_w, live_h / req_h)
    if scale < 1.0:
        clamped = True
        req_w = max(1, int(round(req_w * scale)))
        req_h = max(1, int(round(req_h * scale)))

    return {
        "page_w": page_w,
        "page_h": page_h,
        "m_top": m_top,
        "m_bottom": m_bottom,
        "m_left": m_left,
        "m_right": m_right,
        "live_w": live_w,
        "live_h": live_h,
        "img_w": req_w,
        "img_h": req_h,
        "clamped": clamped,
    }


def main():
    parser = argparse.ArgumentParser(
        prog=SCRIPT_NAME,
        description=(
            "Place a single image at the top-left of a US Letter PDF page, "
            "respecting per-edge margins."
        ),
        epilog=(
            "Defaults (margins, DPI, image width, rotation) live at the top of "
            f"{SCRIPT_NAME}."
        ),
        allow_abbrev=False,
    )
    parser.add_argument(
        "input_image",
        nargs="?",
        help="Path to input image (overrides INPUT_IMAGE variable if provided)",
    )
    parser.add_argument(
        "--width",
        type=float,
        metavar="CM",
        help="Image width in centimetres (overrides IMAGE_WIDTH_CM). "
             "Always means width, regardless of FLIP_WIDTH_HEIGHT.",
    )
    parser.add_argument(
        "--height",
        type=float,
        metavar="CM",
        help="Image height in centimetres. The width is derived from the "
             "image's aspect ratio. Always means height, regardless of "
             "FLIP_WIDTH_HEIGHT.",
    )
    parser.add_argument(
        "--output",
        metavar="PDF",
        help="Output PDF filename (overrides OUTPUT_PDF)",
    )
    parser.add_argument(
        "--dimensions",
        action="store_true",
        help="Print computed dimensions and exit (no PDF created)",
    )
    args = parser.parse_args()

    if args.height is not None and args.height <= 0:
        print("Error: --height must be a positive number of centimetres.",
              file=sys.stderr)
        sys.exit(1)
    if args.width is not None and args.width <= 0:
        print("Error: --width must be a positive number of centimetres.",
              file=sys.stderr)
        sys.exit(1)

    global OUTPUT_PDF
    if args.output:
        OUTPUT_PDF = args.output

    input_path = args.input_image or INPUT_IMAGE
    if not input_path:
        print(
            f"Error: no input image specified. Set INPUT_IMAGE in {SCRIPT_NAME} "
            "or pass it as an argument.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not os.path.isfile(input_path):
        print(f"Error: input file '{input_path}' not found.", file=sys.stderr)
        sys.exit(1)

    img = load_image(input_path)
    aspect = img.width / img.height           # w / h

    # --- Resolve the primary centimetre value and its meaning. ---
    # Priority: --width > --height > default IMAGE_WIDTH_CM.
    # Flags always mean exactly what they say; only the default value is
    # subject to FLIP_WIDTH_HEIGHT.
    if args.width is not None:
        primary_cm = args.width
        primary_is_height = False
        print(f"--width {args.width:.2f} cm supplied; using it as the image width.")
    elif args.height is not None:
        primary_cm = args.height
        primary_is_height = True
        print(f"--height {args.height:.2f} cm supplied; using it as the image height.")
    else:
        primary_cm = IMAGE_WIDTH_CM
        primary_is_height = FLIP_WIDTH_HEIGHT
        if FLIP_WIDTH_HEIGHT:
            print(
                f"FLIP_WIDTH_HEIGHT is enabled: IMAGE_WIDTH_CM {IMAGE_WIDTH_CM:.2f} cm "
                "is being interpreted as a HEIGHT."
            )
        else:
            print(
                f"Using default IMAGE_WIDTH_CM {IMAGE_WIDTH_CM:.2f} cm as the image width."
            )

    width_cm, height_cm = size_from_primary(img, primary_cm, primary_is_height)
    print(
        f"Resolved image size: width {width_cm:.2f} cm, "
        f"height {height_cm:.2f} cm (aspect ratio {aspect:.4f})."
    )

    layout = compute_layout(img, width_cm, height_cm)

    # ---- Dimensions-only mode ----
    if args.dimensions:
        print(f"Page: US Letter {PAGE_WIDTH_IN} in wide x {PAGE_HEIGHT_IN} in tall "
              f"({layout['page_w']} px wide x {layout['page_h']} px tall at {DPI} DPI)")
        print(
            f"Margins (cm) - Top:{MARGIN_TOP_CM:.2f} Bottom:{MARGIN_BOTTOM_CM:.2f} "
            f"Left:{MARGIN_LEFT_CM:.2f} Right:{MARGIN_RIGHT_CM:.2f}"
        )
        print(
            f"Margins (px) - Top:{layout['m_top']} Bottom:{layout['m_bottom']} "
            f"Left:{layout['m_left']} Right:{layout['m_right']}"
        )
        print(
            f"Live area: width {layout['live_w']} px ({px_to_cm(layout['live_w'], DPI):.2f} cm), "
            f"height {layout['live_h']} px ({px_to_cm(layout['live_h'], DPI):.2f} cm)"
        )
        print(
            f"Placed image: width {layout['img_w']} px ({px_to_cm(layout['img_w'], DPI):.2f} cm), "
            f"height {layout['img_h']} px ({px_to_cm(layout['img_h'], DPI):.2f} cm)"
        )
        if layout["clamped"]:
            print(
                "NOTE: requested size did not fit the live area; "
                "the image was scaled down."
            )
        print("Position: top-left corner of the live area")
        return

    # ---- Normal PDF generation ----
    print("==============================================")
    print(f"{SCRIPT_NAME} - Single Image to US Letter PDF")
    print("==============================================")
    print(f"Input image: {input_path} "
          f"(width {img.width} px, height {img.height} px)")
    print(f"Page: US Letter {PAGE_WIDTH_IN} in wide x {PAGE_HEIGHT_IN} in tall "
          f"({layout['page_w']} px wide x {layout['page_h']} px tall at {DPI} DPI)")
    print(
        f"Margins (cm) - Top:{MARGIN_TOP_CM:.2f} Bottom:{MARGIN_BOTTOM_CM:.2f} "
        f"Left:{MARGIN_LEFT_CM:.2f} Right:{MARGIN_RIGHT_CM:.2f}"
    )
    print(
        f"Margins (px) - Top:{layout['m_top']} Bottom:{layout['m_bottom']} "
        f"Left:{layout['m_left']} Right:{layout['m_right']}"
    )
    print(
        f"Live area: width {layout['live_w']} px, "
        f"height {layout['live_h']} px"
    )
    print(
        f"Placed image: width {layout['img_w']} px ({px_to_cm(layout['img_w'], DPI):.2f} cm), "
        f"height {layout['img_h']} px ({px_to_cm(layout['img_h'], DPI):.2f} cm)"
    )
    if layout["clamped"]:
        print(
            "WARNING: requested size exceeds the live area; "
            "the image was scaled down to fit."
        )
    print("==============================================")

    # 1. Blank white page
    page = Image.new("RGB", (layout["page_w"], layout["page_h"]), "white")

    # 2. Resize the image to the computed placement size
    resized = img.resize(
        (layout["img_w"], layout["img_h"]),
        Image.Resampling.LANCZOS,
    )

    # 3. Paste at the TOP-LEFT of the live area
    page.paste(resized, (layout["m_left"], layout["m_top"]))

    # 4. Save as PDF
    print(f"\nSaving PDF at {DPI} DPI -> {OUTPUT_PDF}")
    page.save(OUTPUT_PDF, "PDF", resolution=DPI)

    print("\n==============================================")
    print(f"Done! PDF created: {OUTPUT_PDF}")
    print("PRINTING INSTRUCTIONS:")
    print("  - Print at 100% / 'Actual size' (no scaling, no 'fit to page')")
    print("==============================================")


if __name__ == "__main__":
    main()
