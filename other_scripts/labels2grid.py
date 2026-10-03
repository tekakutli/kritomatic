#!/usr/bin/env python3
"""
labels2grid.py
------------
Takes a list of text strings, arranges them in a grid of user-defined rows and columns,
and places each grid on a US Letter page (300 DPI). The grid occupies a user-defined
area (e.g., 14.5x21.0 cm) positioned at the top-left of the page, with a boundary (margin)
around the grid and gaps between cells. Each label is drawn in the center of its cell.
Supports multi-line labels using \n. If there are more labels than cells, multiple pages
are generated.

Usage:
    python3 labels2grid.py
"""

import os
import sys
import tempfile
from math import ceil
from PIL import Image, ImageDraw, ImageFont

# ========== CONFIGURATION - EDIT THESE VALUES ==========
# List of labels to place in the grid
LABELS = [
    "Desarmadores",
    "Puntas y Manerales",
    "Cúter y Navajas\nCáñamo y Cinturones",
    "Pinzas\nNavaja Suiza\nDestapabotellas",
    "Tornillos",
    "Llaves Mixtas\nEspañolas y Allen\nPerico",
    "Metros y Reglas\nNiveles, Contador, Compás\nProbador de Pilas",
    "Martillos\nPop\nCadena",
    "Cosas Taladro\nBrocas\nCortar Círculos - Madera",
    "Dados",
    "Prensas / Calentador de Agua",
    "LED y Timer",
    "Bisagras y Correderas",
    "Cintas",
    "Grinder (Grabadora)",
    "Lijas",
    "Multímetro, Electrónico\nHerramientas Cables",
    "Cautines",
    "Fresadora y Caladora",
    "Vaporera y Peines"
]
# LABELS = [
#     "Label 1", "Label 2", "Label 3", "Label 4",
#     "Label 5", "Label 6", "Label 7", "Label 8",
#     "Label 9", "Label 10", "Label 11", "Label 12",
#     # Add more as needed
# ]

# Grid dimensions (rows x columns)
GRID_ROWS = 6
GRID_COLS = 2

# Overall area size (in centimeters) that the grid occupies (including boundary)
AREA_WIDTH_CM = 14.5
AREA_HEIGHT_CM = 21.0

# Boundary (margin) around the grid within the area (cm)
BOUNDARY_CM = 0.5

# Gap between adjacent cells (cm)
GAP_CM = 0.56
# GAP_CM = 0.3

# Page margins (in centimeters) - the area will be placed right after these margins
PAGE_MARGIN_LEFT_CM = 0
PAGE_MARGIN_TOP_CM = 0

# PRINTER OFFSET COMPENSATION (in centimeters)
# If your printer starts printing 0.6cm from the top of the page,
# set this to -0.6 to shift everything up and compensate
# (negative values shift content up, positive values shift it down)
PRINTER_OFFSET_TOP_CM = -0.6  # <-- ADDED: Compensate for printer offset
# PRINTER_OFFSET_TOP_CM = 0  # <-- ADDED: Compensate for printer offset

# Cell boundary visualization
SHOW_CELL_BOUNDARIES = False      # Set to True to draw cell borders
BOUNDARY_COLOR = "lightgray"     # Color of the cell boundaries
BOUNDARY_WIDTH = 1               # Width of the boundary lines in pixels

# Output PDF filename
OUTPUT_PDF = "labels2grid.pdf"

# Font settings
FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"   # Path to a .ttf/.otf file, or None for default PIL font
FONT_COLOR = "black"                                      # Any valid PIL color

# If AUTO_FIT is True, the font size is automatically reduced to fit each label
# within its cell (with some padding). If False, the fixed FIXED_FONT_SIZE is used.
AUTO_FIT = True
FIXED_FONT_SIZE = 40        # Used only if AUTO_FIT is False
MAX_FONT_SIZE = 80          # Maximum font size when auto‑fitting
MIN_FONT_SIZE = 6           # Minimum font size when auto‑fitting
TEXT_PADDING_FACTOR = 0.9   # Fraction of cell width/height used for text bounding box

# Multi-line text spacing (in pixels)
LINE_SPACING = 40            # Additional pixels between lines of multi-line text

# ===================================================

# US Letter size at 300 DPI
PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300
DPI = 300

# Conversion factor: cm to pixels
PX_PER_CM = DPI / 2.54


def cm_to_px(cm):
    """Convert centimeters to pixels at the current DPI."""
    return cm * PX_PER_CM


def get_text_dimensions(draw, text, font):
    """Get the width and height of multi-line text."""
    lines = text.split('\n')
    max_width = 0
    total_height = 0

    for i, line in enumerate(lines):
        # Use anchor='ls' to get accurate dimensions from the baseline
        bbox = draw.textbbox((0, 0), line, font=font, anchor='ls')
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        max_width = max(max_width, line_width)
        total_height += line_height

        # Add spacing between lines (except after the last line)
        if i < len(lines) - 1:
            total_height += LINE_SPACING

    return max_width, total_height


def get_fitting_font_multiline(draw, text, max_width, max_height, font_path, max_size, min_size):
    """
    Find the largest font size (between min_size and max_size) such that the multi-line
    text fits within max_width and max_height. Returns an ImageFont object.
    """
    size = max_size
    while size >= min_size:
        try:
            font = ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
            return font

        # Measure the multi-line text
        text_w, text_h = get_text_dimensions(draw, text, font)

        if text_w <= max_width and text_h <= max_height:
            return font
        size -= 1

    # If we reach min_size and still doesn't fit, return min size
    try:
        return ImageFont.truetype(font_path, min_size) if font_path else ImageFont.load_default()
    except Exception:
        return ImageFont.load_default()


def draw_multiline_text_centered(draw, text, x, y, font, fill):
    """
    Draw multi-line text perfectly centered at (x, y).
    Uses anchor='mm' for perfect centering.
    """
    lines = text.split('\n')

    if not lines:
        return

    # Calculate the bounding box of the entire text block
    # We need to find the max width and total height
    line_heights = []
    line_widths = []
    total_height = 0

    for i, line in enumerate(lines):
        # Get the bounding box with anchor='ls' (left baseline)
        bbox = draw.textbbox((0, 0), line, font=font, anchor='ls')
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        line_widths.append(line_width)
        line_heights.append(line_height)
        total_height += line_height
        if i < len(lines) - 1:
            total_height += LINE_SPACING

    # Start from the top of the centered block
    # The top is at y - total_height/2
    current_y = y - total_height / 2

    for i, line in enumerate(lines):
        # Center this line horizontally
        line_x = x - line_widths[i] / 2

        # Draw the line at the current position using anchor='ls' (left baseline)
        draw.text((line_x, current_y + line_heights[i]), line, fill=fill, font=font, anchor='ls')

        # Move to next line position
        current_y += line_heights[i] + LINE_SPACING


def create_label_page(labels_chunk, page_num, total_pages):
    """
    Create a single page with the given labels arranged in the grid.
    Returns a PIL Image.
    """
    # Create white canvas
    page = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')
    draw = ImageDraw.Draw(page)

    # Convert dimensions to pixels
    area_w = cm_to_px(AREA_WIDTH_CM)
    area_h = cm_to_px(AREA_HEIGHT_CM)
    boundary = cm_to_px(BOUNDARY_CM)
    gap = cm_to_px(GAP_CM)
    margin_left = cm_to_px(PAGE_MARGIN_LEFT_CM)
    margin_top = cm_to_px(PAGE_MARGIN_TOP_CM)

    # Apply printer offset compensation (negative offset moves content up)
    offset_compensation = cm_to_px(PRINTER_OFFSET_TOP_CM)

    # Position the area at top-left, right after the margins, with offset compensation
    offset_x = margin_left
    offset_y = margin_top + offset_compensation  # <-- ADDED: Apply offset compensation

    # Inner area (available for cells)
    inner_w = area_w - 2 * boundary
    inner_h = area_h - 2 * boundary

    # Cell dimensions
    cell_w = (inner_w - (GRID_COLS - 1) * gap) / GRID_COLS
    cell_h = (inner_h - (GRID_ROWS - 1) * gap) / GRID_ROWS

    # Draw cell boundaries if enabled
    if SHOW_CELL_BOUNDARIES:
        draw_cell_boundaries(draw, offset_x, offset_y, boundary, cell_w, cell_h, gap)

    # Pre-load font (if fixed size) or we'll load per cell for auto-fit
    if not AUTO_FIT:
        try:
            font = ImageFont.truetype(FONT_PATH, FIXED_FONT_SIZE) if FONT_PATH else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
    else:
        font = None  # will be determined per cell

    # Process each label in the chunk
    for idx, label in enumerate(labels_chunk):
        row = idx // GRID_COLS
        col = idx % GRID_COLS

        # Cell top-left corner (within the area)
        cell_x = offset_x + boundary + col * (cell_w + gap)
        cell_y = offset_y + boundary + row * (cell_h + gap)

        # Cell center
        center_x = cell_x + cell_w / 2
        center_y = cell_y + cell_h / 2

        # Determine font for this label
        if AUTO_FIT:
            # Compute maximum allowed text size (with padding)
            max_text_w = cell_w * TEXT_PADDING_FACTOR
            max_text_h = cell_h * TEXT_PADDING_FACTOR
            label_font = get_fitting_font_multiline(
                draw, label, max_text_w, max_text_h,
                FONT_PATH, MAX_FONT_SIZE, MIN_FONT_SIZE
            )
        else:
            label_font = font

        # Draw the multi-line label perfectly centered at (center_x, center_y)
        draw_multiline_text_centered(draw, label, center_x, center_y, label_font, FONT_COLOR)

    return page


def draw_cell_boundaries(draw, offset_x, offset_y, boundary, cell_w, cell_h, gap):
    """
    Draw rectangle borders around each cell in the grid.
    """
    for row in range(GRID_ROWS):
        for col in range(GRID_COLS):
            # Cell top-left corner
            cell_x = offset_x + boundary + col * (cell_w + gap)
            cell_y = offset_y + boundary + row * (cell_h + gap)

            # Draw rectangle around the cell
            draw.rectangle(
                [cell_x, cell_y, cell_x + cell_w, cell_y + cell_h],
                outline=BOUNDARY_COLOR,
                width=BOUNDARY_WIDTH
            )


def save_pdf_with_png_fallback(pages, output_pdf, dpi):
    """
    Save pages as PDF. If the standard method fails, save each page as PNG
    and then combine them into a PDF using an alternative method.
    """
    try:
        # First attempt: try to save directly as PDF
        pages[0].save(
            output_pdf,
            save_all=True,
            append_images=pages[1:],
            resolution=dpi,
            title="Label Grid PDF"
        )
        return True
    except KeyError as e:
        print(f"PDF save failed with KeyError: {e}. Using fallback method...")

        # Fallback: Save each page as PNG, then combine into PDF
        from PIL import Image

        # Create temporary PNG files
        temp_files = []
        try:
            for i, page in enumerate(pages):
                temp_png = tempfile.NamedTemporaryFile(suffix=f"_page_{i+1}.png", delete=False)
                temp_png.close()
                temp_files.append(temp_png.name)

                # Save as PNG first
                page.save(temp_png.name, "PNG", dpi=(dpi, dpi))
                print(f"  Saved page {i+1} as PNG: {temp_png.name}")

            # Now load all PNGs and save as PDF
            png_pages = []
            for png_file in temp_files:
                img = Image.open(png_file)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                png_pages.append(img)

            # Save as PDF
            png_pages[0].save(
                output_pdf,
                save_all=True,
                append_images=png_pages[1:],
                title="Label Grid PDF"
            )
            print("✓ PDF created successfully using fallback method")

            # Clean up temp files
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except:
                    pass

            return True

        except Exception as e2:
            print(f"Fallback method also failed: {e2}")
            return False


def main():
    if not LABELS:
        print("Error: No labels defined. Please populate the LABELS list.")
        sys.exit(1)

    total_labels = len(LABELS)
    cells_per_page = GRID_ROWS * GRID_COLS
    total_pages = ceil(total_labels / cells_per_page)

    print("==============================================")
    print("Label Grid to PDF Converter")
    print("==============================================")
    print(f"Total labels: {total_labels}")
    print(f"Grid: {GRID_ROWS} rows × {GRID_COLS} cols = {cells_per_page} cells per page")
    print(f"Area: {AREA_WIDTH_CM} × {AREA_HEIGHT_CM} cm  (boundary: {BOUNDARY_CM} cm, gap: {GAP_CM} cm)")
    print(f"Page margins: Left={PAGE_MARGIN_LEFT_CM} cm, Top={PAGE_MARGIN_TOP_CM} cm")
    print(f"Printer offset compensation: {PRINTER_OFFSET_TOP_CM} cm")
    print(f"Show cell boundaries: {'YES' if SHOW_CELL_BOUNDARIES else 'NO'}")
    if SHOW_CELL_BOUNDARIES:
        print(f"  Boundary color: {BOUNDARY_COLOR}, Width: {BOUNDARY_WIDTH} px")
    print(f"Auto‑fit font: {'YES' if AUTO_FIT else 'NO'}")
    if not AUTO_FIT:
        print(f"Fixed font size: {FIXED_FONT_SIZE} px")
    print(f"Font color: {FONT_COLOR}")
    print(f"Line spacing: {LINE_SPACING} px")
    print(f"Will generate {total_pages} page(s)")
    print("==============================================")
    print()

    pages = []
    for page_num in range(total_pages):
        start = page_num * cells_per_page
        end = min(start + cells_per_page, total_labels)
        chunk = LABELS[start:end]
        print(f"--- Page {page_num+1}/{total_pages} ---")
        for label in chunk:
            # Show multi-line labels properly in console
            display_label = label.replace('\n', '\\n')
            print(f"  {display_label}")
        page_img = create_label_page(chunk, page_num+1, total_pages)
        pages.append(page_img)
        print(f"  ✓ Page {page_num+1} ready")

    print()
    print("Saving PDF...")

    success = save_pdf_with_png_fallback(pages, OUTPUT_PDF, DPI)

    if success:
        print(f"✓ PDF created: {OUTPUT_PDF}")
        print(f"  Total pages: {total_pages}")
    else:
        print("❌ Failed to create PDF")

    print("==============================================")


if __name__ == "__main__":
    main()
