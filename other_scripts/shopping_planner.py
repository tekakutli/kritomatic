#!/usr/bin/env python3
"""
shopping_planner.py
-------------------
Generates a PDF with a shopping table organized by sections.
Data is loaded from a JSON file with multi-sheet support (tksheet
format).  The main interaction is through a GUI built on tksheet.

Usage:
  python shopping_planner.py                       # GUI editor only
  python shopping_planner.py --json other.json     # use another JSON
  python shopping_planner.py --export-calendar     # headless: calendar PDF
  python shopping_planner.py --export-costs        # headless: costs PDF
                                                   # (from latest saved sheet)

Language
--------
Every user-visible string — console output, GUI messages, PDF
headers, the PDF page footer — routes through T() from
shopping_planner_i18n.py.  To switch language, edit the LANG
constant in that file:

    LANG = "en"    ->  English
    LANG = "es"    ->  Spanish
    LANG = "fr"    ->  whatever you add to TRANSLATIONS

That is the only switch.  This file contains no user-visible
literals.

JSON layout
-----------
The JSON has two reserved top-level keys and any number of user
sheets:

    "items"   the catalog: [["name","description","price",
              "section"], ...]
    "_meta"   reserved for app bookkeeping.  Currently holds
              "sheets_saved_at": {sheet_name: iso_timestamp} so
              the app knows which sheet to autoload at startup.
    <name>    one user sheet per saved state:
              [["name","quantity"], ...]

Any top-level key that is neither "items" nor "_meta" is a user
sheet.  A JSON written before "_meta" existed still loads; it just
won't autoload until the first save creates the metadata block.

PDF kinds
---------
Two shapes of PDF can be produced:

    calendar  the plain catalog grouped by section, no quantities.
              Seven columns with month headers.  Written by the
              --export-calendar CLI flag.

    costs     one row per kept item with price × quantity and
              subtotal, a subtotal row per section, and a grand
              total at the end.  Three columns.  Written by the
              GUI's "Export PDF" / "Export PDF detailed" buttons,
              and by the --export-costs CLI flag (which reads the
              quantities from the latest saved sheet).
"""
# https://chat.deepseek.com/a/chat/s/f5c3322e-c02c-486a-a517-e519aad1d661

import os
import sys
import signal
import argparse
import json
import re
import tempfile
from datetime import datetime
from math import ceil
from PIL import Image, ImageDraw, ImageFont

from shopping_planner_i18n import T

# Try to import tksheet and tkinter.  Both are only needed for the
# interactive editor; PDF-only generation works without them.
try:
    import tkinter as tk
    from tkinter import ttk, messagebox, simpledialog
    import tksheet
    TKINTER_AVAILABLE = True
except ImportError:
    TKINTER_AVAILABLE = False
    print(T("warnNoTkinter"))
    print(T("warnNoTkinterHint1"))
    print(T("warnNoTkinterHint2"))
    print(T("warnNoTkinterHint3"))

# ==================== CONFIGURATION ====================
DEFAULT_JSON = "shopping_planner.json"

# PDF table headers are pulled from the i18n table at use time via
# T("pdfHeaders") / T("pdfHeadersDetailed"), so switching LANG in
# shopping_planner_i18n.py switches them too.

# Layout mode: two virtual pages per physical sheet (landscape) or
# one (portrait).
TWO_PAGES_PER_SHEET = True   # set to False for portrait, one per sheet

# Page sizes at 300 DPI.
if TWO_PAGES_PER_SHEET:
    PAGE_WIDTH = 3300   # 11 inches
    PAGE_HEIGHT = 2550  # 8.5 inches
else:
    PAGE_WIDTH = 2550
    PAGE_HEIGHT = 3300

DPI = 300

# Margins in centimeters.
MARGIN_LEFT_CM = 2.0
MARGIN_RIGHT_CM = 2.0
MARGIN_TOP_CM = 2.0
MARGIN_BOTTOM_CM = 2.0

MARGIN_LEFT = int(MARGIN_LEFT_CM * DPI / 2.54)
MARGIN_RIGHT = int(MARGIN_RIGHT_CM * DPI / 2.54)
MARGIN_TOP = int(MARGIN_TOP_CM * DPI / 2.54)
MARGIN_BOTTOM = int(MARGIN_BOTTOM_CM * DPI / 2.54)

GAP_BETWEEN_PAGES = 20  # pixels between the two virtual pages
PADDING_TOP = 8
PADDING_BOTTOM = 20
FOOTER_HEIGHT = 60

FONT_SIZE = 50
FONT_PATH = "/usr/share/fonts/noto/NotoSans-Regular.ttf"
# On Windows: "C:/Windows/Fonts/Arial.ttf"

TABLE_LINE_COLOR = "black"
LINE_WIDTH = 2

# Output file for the calendar PDF (the plain table with month
# columns).  The costs PDF is named after the sheet it was built
# from, so it has no fixed output constant here.
OUTPUT_CALENDAR_PDF = "shopping_planner_calendar.pdf"

# Reserved top-level keys in the JSON.  Anything else is a
# user-defined sheet.
RESERVED_JSON_KEYS = ("items", "_meta")

# Header row for a saved user sheet.  Written first whenever a sheet
# is saved, and expected as the first row whenever one is loaded.
SHEET_HEADER = ["name", "quantity"]

# ===================================================

# Compute the virtual canvas size for the chosen mode.  In two-page
# mode the virtual page is sized so that two of them plus the gap
# fit side by side inside the sheet's margins.
if TWO_PAGES_PER_SHEET:
    avail_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    avail_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM
    page_width = (avail_width - GAP_BETWEEN_PAGES) // 2
    VIRTUAL_WIDTH = 2550
    VIRTUAL_HEIGHT = int(VIRTUAL_WIDTH * (avail_height / page_width))
else:
    VIRTUAL_WIDTH = 2550
    VIRTUAL_HEIGHT = 3300

# ==================== DATA LOADING FROM JSON ====================

def load_sections_from_json(json_file):
    """
    Load data from the JSON file and return it as the SECTIONS list.

    The JSON must exist and must contain an 'items' sheet with
    columns: name, description, price, section.  The first row of
    'items' is the header row; the remaining rows are data.  Items
    are grouped by section, preserving first-seen order.
    """
    if not os.path.exists(json_file):
        print(T("errJsonMissing")(json_file))
        print(T("errJsonMissingHint"))
        sys.exit(1)

    with open(json_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    if "items" not in data:
        print(T("errNoItems")(json_file))
        print(T("errNoItemsHint"))
        sys.exit(1)

    rows = data["items"]
    if len(rows) < 2:
        print(T("warnItemsEmpty"))
        return []

    # First row is the header, the rest are data.
    # Columns: 0=name, 1=description, 2=price, 3=section
    sections_by_name = {}
    for row in rows[1:]:
        if len(row) < 4:
            continue  # skip incomplete rows
        name = str(row[0]).strip()
        description = str(row[1]).strip()
        try:
            price = float(row[2])
        except (ValueError, TypeError):
            price = 0
        section = str(row[3]).strip() if len(row) > 3 else "General"

        if not name:
            continue

        if section not in sections_by_name:
            sections_by_name[section] = []
        sections_by_name[section].append((name, description, price))

    # Convert to an ordered list of sections.
    sections = []
    for section_name, items in sections_by_name.items():
        sections.append({"name": section_name, "items": items})

    return sections


def load_json_data(fname):
    """Read the JSON file, returning {} if it does not exist."""
    try:
        with open(fname, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def get_user_sheet_names(json_data):
    """Return the names of every user-defined sheet: everything in
    the JSON except the reserved keys."""
    return [k for k in json_data.keys() if k not in RESERVED_JSON_KEYS]


def find_latest_saved_sheet(json_data):
    """Return the name of the most-recently-saved sheet, or None.

    Timestamps are stored in json_data["_meta"]["sheets_saved_at"]
    as ISO 8601 strings, which sort lexicographically, so `max` over
    the values gives the newest.  Sheets present in _meta but no
    longer in the data (or reserved) are ignored."""
    meta = json_data.get("_meta", {})
    saved_at = meta.get("sheets_saved_at", {})
    valid = {k: v for k, v in saved_at.items()
             if k in json_data and k not in RESERVED_JSON_KEYS}
    if not valid:
        return None
    return max(valid.items(), key=lambda kv: kv[1])[0]


def read_quantities_from_sheet(sheet_data):
    """Turn one stored user sheet into a {name: quantity} dict.

    A stored sheet looks like [["name","quantity"], ["item", 2], ...].
    The header row is skipped; rows with fewer than two entries are
    ignored; the quantity is coerced to a float, defaulting to 0.0."""
    quantities = {}
    if not sheet_data or len(sheet_data) < 2:
        return quantities
    for row in sheet_data[1:]:  # skip header
        if len(row) >= 2:
            name = str(row[0]).strip()
            try:
                qty = float(row[1]) if row[1] is not None else 0.0
            except (ValueError, TypeError):
                qty = 0.0
            if name:
                quantities[name] = qty
    return quantities


# Sections are loaded at import time; --json overrides them in main().
SECTIONS = load_sections_from_json(DEFAULT_JSON)

# ==================== SCRIPT FUNCTIONS ====================

def get_text_bbox(draw, text, font):
    """Return the (left, top, right, bottom) bounding box of a text
    string rendered with `font`."""
    return draw.textbbox((0, 0), text, font=font)


def draw_table_page(elements_chunk, page_num, total_pages, font_size,
                    virtual_width, virtual_height, use_margins=True,
                    footer_height=0, headers=None, col_widths=None):
    """Draw one virtual page of the table and return it as a PIL
    Image.  `elements_chunk` is the slice of elements assigned to
    this page; they are drawn top to bottom."""
    img = Image.new('RGB', (virtual_width, virtual_height), 'white')
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype(FONT_PATH, font_size) if FONT_PATH else ImageFont.load_default()
        try:
            font_bold = ImageFont.truetype(FONT_PATH, font_size, encoding="unic", index=1)
        except:
            font_bold = font
    except:
        font = ImageFont.load_default()
        font_bold = font

    # In two-page mode the virtual pages have no page margins of
    # their own; the physical sheet provides them.  The footer strip
    # at the bottom of each virtual page holds the page number.
    if use_margins:
        v_margin_left = int(MARGIN_LEFT_CM * DPI / 2.54)
        v_margin_right = int(MARGIN_RIGHT_CM * DPI / 2.54)
        v_margin_top = int(MARGIN_TOP_CM * DPI / 2.54)
        v_margin_bottom = int(MARGIN_BOTTOM_CM * DPI / 2.54)
    else:
        v_margin_left = 0
        v_margin_right = 0
        v_margin_top = 0
        v_margin_bottom = footer_height

    total_width = virtual_width - v_margin_left - v_margin_right

    # Default column widths for the seven-column table.
    if col_widths is None:
        col_widths = [
            int(total_width * 0.40),
            int(total_width * 0.12),
            int(total_width * 0.12),
            int(total_width * 0.09),
            int(total_width * 0.09),
            int(total_width * 0.09),
            int(total_width * 0.09),
        ]
    # Absorb rounding drift into the last column so widths sum to
    # total_width exactly.
    diff = total_width - sum(col_widths)
    if diff != 0:
        col_widths[-1] += diff

    x_positions = [v_margin_left]
    for w in col_widths[:-1]:
        x_positions.append(x_positions[-1] + w)

    # Row height is derived from the font's rendered height.
    bbox = get_text_bbox(draw, "Ay", font)
    h = bbox[3] - bbox[1]
    row_height = h + PADDING_TOP + PADDING_BOTTOM
    header_height = row_height + 5

    table_available_height = (virtual_height - v_margin_top
                              - v_margin_bottom - header_height)
    max_rows = table_available_height // row_height
    if max_rows <= 0:
        max_rows = 1

    # Truncate the chunk if it overflows.  The caller is responsible
    # for having split elements across pages beforehand.
    if len(elements_chunk) > max_rows:
        elements_chunk = elements_chunk[:max_rows]

    total_table_height = header_height + len(elements_chunk) * row_height

    # Vertical grid lines.
    for x in x_positions[1:]:
        draw.line([x, v_margin_top, x, v_margin_top + total_table_height],
                  fill=TABLE_LINE_COLOR, width=LINE_WIDTH)

    # Header band.
    y = v_margin_top
    draw.rectangle(
        [v_margin_left, y, v_margin_left + total_width, y + header_height],
        fill="lightgray",
        outline=TABLE_LINE_COLOR,
        width=LINE_WIDTH
    )
    if headers is None:
        headers = T("pdfHeaders")
    for i, header in enumerate(headers):
        x = x_positions[i]
        bbox = get_text_bbox(draw, header, font_bold)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        top_offset = bbox[1]
        text_x = x + (col_widths[i] - tw) / 2
        text_y = y + (header_height - th) / 2 - top_offset
        draw.text((text_x, text_y), header, font=font_bold, fill="black")

    draw.line([v_margin_left, y + header_height,
               v_margin_left + total_width, y + header_height],
              fill=TABLE_LINE_COLOR, width=LINE_WIDTH)

    # Body rows.
    y = v_margin_top + header_height
    for idx, elem in enumerate(elements_chunk):
        row_y = y + idx * row_height

        if elem["type"] == "section":
            # Section header: gray band across the full width, name
            # centered in white bold.
            draw.rectangle(
                [v_margin_left, row_y, v_margin_left + total_width,
                 row_y + row_height],
                fill="dimgray",
                outline=None
            )
            section_name = elem["name"]
            bbox = get_text_bbox(draw, section_name, font_bold)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = v_margin_left + (total_width - tw) / 2
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), section_name,
                      font=font_bold, fill="white")

        elif elem["type"] in ("subtotal", "total"):
            # Subtotal or grand-total row: colored band, label on
            # the left, dollar amount centered under the "Cost"
            # column.
            if elem["type"] == "subtotal":
                fill_color = "lightcyan"
            else:
                fill_color = "lightgray"
            draw.rectangle(
                [v_margin_left, row_y, v_margin_left + total_width,
                 row_y + row_height],
                fill=fill_color,
                outline=TABLE_LINE_COLOR,
                width=1
            )
            label = elem["name"]
            subtotal_val = elem["data"][2]
            bbox = get_text_bbox(draw, label, font_bold)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[0] + 5
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), label, font=font_bold, fill="black")
            total_str = f"${subtotal_val:.2f}"
            bbox = get_text_bbox(draw, total_str, font_bold)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[2] + (col_widths[2] - tw) / 2
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), total_str,
                      font=font_bold, fill="black")

        else:
            # Regular item row: product, description, cost.
            product, description, cost = elem["data"]
            bbox = get_text_bbox(draw, product, font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[0] + 5
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), product, font=font, fill="black")

            bbox = get_text_bbox(draw, description, font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[1] + 5
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), description, font=font, fill="black")

            cost_str = f"${cost:.2f}"
            bbox = get_text_bbox(draw, cost_str, font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            top_offset = bbox[1]
            text_x = x_positions[2] + (col_widths[2] - tw) / 2
            text_y = row_y + (row_height - th) / 2 - top_offset
            draw.text((text_x, text_y), cost_str, font=font, fill="black")

            draw.line([v_margin_left, row_y + row_height,
                       v_margin_left + total_width, row_y + row_height],
                      fill=TABLE_LINE_COLOR, width=1)

    # Left and right table borders.
    draw.line([v_margin_left, v_margin_top,
               v_margin_left, v_margin_top + total_table_height],
              fill=TABLE_LINE_COLOR, width=LINE_WIDTH)
    draw.line([v_margin_left + total_width, v_margin_top,
               v_margin_left + total_width,
               v_margin_top + total_table_height],
              fill=TABLE_LINE_COLOR, width=LINE_WIDTH)

    # Footer: "Page N of M".
    footer_text = T("pageFooter")(page_num, total_pages)
    bbox = get_text_bbox(draw, footer_text, font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    top_offset = bbox[1]

    if footer_height > 0:
        footer_y = virtual_height - footer_height
        text_x = (virtual_width - tw) // 2
        text_y = footer_y + (footer_height - th) // 2 - top_offset
        draw.text((text_x, text_y), footer_text, font=font, fill="black")
    else:
        text_x = virtual_width - v_margin_right - tw
        text_y = virtual_height - v_margin_bottom - th - top_offset
        draw.text((text_x, text_y), footer_text, font=font, fill="black")

    return img


def create_physical_page(virtual_pages):
    """Compose one physical sheet from one or two virtual pages."""
    if not TWO_PAGES_PER_SHEET:
        return virtual_pages[0]

    physical = Image.new('RGB', (PAGE_WIDTH, PAGE_HEIGHT), 'white')

    avail_width = PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    avail_height = PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM

    virtual_w, virtual_h = virtual_pages[0].size

    if len(virtual_pages) == 2:
        # Two virtual pages side by side inside the sheet margins.
        page_width = (avail_width - GAP_BETWEEN_PAGES) // 2
        gap = GAP_BETWEEN_PAGES
        scale = page_width / virtual_w
        if virtual_h * scale > avail_height:
            scale = avail_height / virtual_h
        new_w = int(virtual_w * scale)
        new_h = int(virtual_h * scale)
        x_positions = [MARGIN_LEFT, MARGIN_LEFT + page_width + gap]
        y_offset = MARGIN_TOP + (avail_height - new_h) // 2
    else:
        # A single virtual page, centered.
        scale = min(avail_width / virtual_w, avail_height / virtual_h)
        new_w = int(virtual_w * scale)
        new_h = int(virtual_h * scale)
        x_positions = [MARGIN_LEFT]
        y_offset = MARGIN_TOP + (avail_height - new_h) // 2

    for idx, vimg in enumerate(virtual_pages):
        vimg_resized = vimg.resize((new_w, new_h), Image.Resampling.LANCZOS)
        physical.paste(vimg_resized, (x_positions[idx], y_offset))

    return physical


def save_pdf_with_png_fallback(pages, output_pdf, dpi):
    """Save `pages` as a multi-page PDF.  Some Pillow builds raise
    KeyError on multi-page save, so fall back to writing PNGs first
    and re-opening them.

    Returns True on success, False if there was nothing to save or
    both methods failed."""
    # Guard: an empty page list means there is nothing to save.  This
    # is the crash path that used to raise IndexError on pages[0].
    if not pages:
        print(T("pdfNoPages"))
        return False
    try:
        pages[0].save(
            output_pdf,
            save_all=True,
            append_images=pages[1:],
            resolution=dpi,
            title="Shopping planner"
        )
        return True
    except KeyError as e:
        print(T("pdfSaveError")(e))
        temp_files = []
        try:
            for i, page in enumerate(pages):
                temp_png = tempfile.NamedTemporaryFile(
                    suffix=f"_page_{i+1}.png", delete=False)
                temp_png.close()
                temp_files.append(temp_png.name)
                page.save(temp_png.name, "PNG", dpi=(dpi, dpi))
            png_pages = []
            for png_file in temp_files:
                img = Image.open(png_file)
                if img.mode != 'RGB':
                    img = img.convert('RGB')
                png_pages.append(img)
            png_pages[0].save(
                output_pdf,
                save_all=True,
                append_images=png_pages[1:],
                title="Shopping planner"
            )
            for temp_file in temp_files:
                try:
                    os.unlink(temp_file)
                except:
                    pass
            return True
        except Exception as e2:
            print(T("pdfAltFailed")(e2))
            return False


def generate_pdf_from_elements(elements, headers, col_widths,
                               output_pdf, virtual_width, virtual_height):
    """Generate a PDF from a flat list of elements.

    The list is chunked into virtual pages of `rows_per_page` rows
    each, then two virtual pages (or one, in portrait mode) are
    composed onto each physical sheet."""
    # Guard: with zero elements there is nothing to lay out, so
    # there are no virtual pages, hence no physical pages, hence
    # nothing to save.  Bail out cleanly instead of crashing.
    if not elements:
        print(T("pdfNoPages"))
        return

    total_elements = len(elements)
    print(T("hrLine"))
    print(T("genPdf")(output_pdf))
    print(T("hrLine"))
    print(T("totalElements")(total_elements))
    print(T("fontSize")(FONT_SIZE))
    print(T("padding")(PADDING_TOP, PADDING_BOTTOM))
    print(T("margins")(MARGIN_LEFT_CM, MARGIN_RIGHT_CM,
                       MARGIN_TOP_CM, MARGIN_BOTTOM_CM))
    print(T("twoPagesMode")(TWO_PAGES_PER_SHEET))
    if TWO_PAGES_PER_SHEET:
        print(T("footerHeight")(FOOTER_HEIGHT))
    print(T("virtualSize")(virtual_width, virtual_height))

    # Measure one row so we can compute how many fit per page.
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)
    try:
        font = ImageFont.truetype(FONT_PATH, FONT_SIZE) if FONT_PATH else ImageFont.load_default()
    except:
        font = ImageFont.load_default()
    bbox = get_text_bbox(temp_draw, "Ay", font)
    h = bbox[3] - bbox[1]
    row_height = h + PADDING_TOP + PADDING_BOTTOM
    header_height = row_height + 5

    if TWO_PAGES_PER_SHEET:
        v_margin_top = 0
        v_margin_bottom = FOOTER_HEIGHT
    else:
        v_margin_top = int(MARGIN_TOP_CM * DPI / 2.54)
        v_margin_bottom = int(MARGIN_BOTTOM_CM * DPI / 2.54)

    available_height = (virtual_height - v_margin_top
                        - v_margin_bottom - header_height)
    rows_per_page = available_height // row_height
    if rows_per_page <= 0:
        rows_per_page = 1

    print(T("rowsPerVirtual")(rows_per_page))

    # Chunk the element list into per-virtual-page slices.
    virtual_pages_content = []
    current_page = []
    for elem in elements:
        if len(current_page) < rows_per_page:
            current_page.append(elem)
        else:
            virtual_pages_content.append(current_page)
            current_page = [elem]
    if current_page:
        virtual_pages_content.append(current_page)

    total_virtual_pages = len(virtual_pages_content)
    print(T("virtualPagesNeeded")(total_virtual_pages))

    virtual_images = []
    for page_num, chunk in enumerate(virtual_pages_content, start=1):
        print(T("genVirtualPage")(page_num, total_virtual_pages,
                                  len(chunk)))
        use_margins = not TWO_PAGES_PER_SHEET
        footer_h = FOOTER_HEIGHT if TWO_PAGES_PER_SHEET else 0
        img = draw_table_page(chunk, page_num, total_virtual_pages,
                              FONT_SIZE, virtual_width, virtual_height,
                              use_margins, footer_h,
                              headers=headers, col_widths=col_widths)
        virtual_images.append(img)

    # Pair virtual pages onto physical sheets.
    if TWO_PAGES_PER_SHEET:
        physical_pages = []
        for i in range(0, len(virtual_images), 2):
            pair = virtual_images[i:i+2]
            physical = create_physical_page(pair)
            physical_pages.append(physical)
        total_physical_pages = len(physical_pages)
        print(T("physicalPagesGenerated")(total_physical_pages))
    else:
        physical_pages = virtual_images
        total_physical_pages = len(physical_pages)

    print(T("savingPdf"))
    success = save_pdf_with_png_fallback(physical_pages, output_pdf, DPI)
    if success:
        print(T("pdfGenerated")(output_pdf))
        print(T("totalSheets")(total_physical_pages))
    else:
        print(T("pdfFailed"))
    print(T("hrLine"))


def build_costs_elements(quantity_by_name, sections, short=False):
    """Build the element list for a costs PDF.

    A costs PDF has, per section with at least one kept item: a
    section header band, one row per kept item ("name",
    "price × qty", subtotal), and a section subtotal row.  At the
    end, one grand-total row.  If nothing is kept, the list is
    empty.

    quantity_by_name    dict mapping item name -> quantity
    sections            list of {"name", "items"} as in SECTIONS
    short               True  -> keep only items with qty > 0
                        False -> keep every item
    """
    elements = []
    grand_total = 0.0
    for sec in sections:
        filtered_items = []
        section_subtotal = 0.0
        for name, description, price in sec["items"]:
            qty = quantity_by_name.get(name, 0.0)
            if short and qty == 0:
                continue
            subtotal = price * qty
            section_subtotal += subtotal
            filtered_items.append((name, description, price, qty, subtotal))
        if not filtered_items:
            continue
        grand_total += section_subtotal
        elements.append({"type": "section", "name": sec["name"]})
        for name, description, price, qty, subtotal in filtered_items:
            elements.append({
                "type": "item",
                "data": (name, f"{price:.2f} × {qty:.2f}", subtotal)
            })
        elements.append({"type": "subtotal",
                         "name": T("subtotalOfSection")(sec['name']),
                         "data": ("", "", section_subtotal)})
    if elements:
        elements.append({"type": "total",
                         "name": T("totalLabel"),
                         "data": ("", "", grand_total)})
    return elements


def generate_calendar_pdf():
    """Generate the calendar PDF (the plain catalog grouped by
    section, no quantity columns) using the module-level SECTIONS.
    This is what --export-calendar writes."""
    elements = []
    for sec in SECTIONS:
        elements.append({"type": "section", "name": sec["name"]})
        for item in sec["items"]:
            elements.append({"type": "item", "data": item})

    default_col_widths = [
        int(VIRTUAL_WIDTH * 0.40),
        int(VIRTUAL_WIDTH * 0.12),
        int(VIRTUAL_WIDTH * 0.12),
        int(VIRTUAL_WIDTH * 0.09),
        int(VIRTUAL_WIDTH * 0.09),
        int(VIRTUAL_WIDTH * 0.09),
        int(VIRTUAL_WIDTH * 0.09),
    ]
    generate_pdf_from_elements(
        elements, T("pdfHeaders"), default_col_widths,
        OUTPUT_CALENDAR_PDF, VIRTUAL_WIDTH, VIRTUAL_HEIGHT)


def generate_costs_pdf(json_file, sheet_name=None):
    """Generate a costs PDF from a saved sheet's quantities.

    Reads the JSON, picks `sheet_name` (or the most-recently-saved
    sheet when sheet_name is None), and writes a costs PDF — the
    same shape the GUI's "Export PDF" button produces.

    Output filename: "<sheet_name>.pdf", with characters unsafe for
    filenames replaced by underscores.

    If no suitable sheet is found, prints a short message and
    returns without writing anything."""
    json_data = load_json_data(json_file)

    if sheet_name is None:
        sheet_name = find_latest_saved_sheet(json_data)

    if not sheet_name or sheet_name not in json_data:
        print(T("costsNoSheet"))
        return

    quantities = read_quantities_from_sheet(json_data[sheet_name])
    elements = build_costs_elements(quantities, SECTIONS, short=True)

    # Guard: nothing to export (all quantities zero, or catalog empty).
    if not elements:
        print(T("pdfNoPages"))
        return

    detailed_headers = T("pdfHeadersDetailed")
    col_widths = [
        int(VIRTUAL_WIDTH * 0.50),
        int(VIRTUAL_WIDTH * 0.25),
        int(VIRTUAL_WIDTH * 0.25),
    ]
    diff = VIRTUAL_WIDTH - sum(col_widths)
    if diff != 0:
        col_widths[-1] += diff

    base = re.sub(r'[^\w\-]', '_', sheet_name)
    output_pdf = f"{base}.pdf"

    generate_pdf_from_elements(elements, detailed_headers, col_widths,
                               output_pdf, VIRTUAL_WIDTH, VIRTUAL_HEIGHT)

# ==================== FULL EDITOR WITH TKSHEET ====================

def open_editor_window(sections, json_file):
    """Open a tksheet window to edit quantities, load/save sheets,
    compute totals, and export PDFs.

    The main tab shows one row per catalog item with an editable
    "Quantity" column.  The Results tab shows a per-section breakdown
    with subtotals and a grand total.

    On startup, if the JSON contains at least one user sheet whose
    save time is recorded in "_meta", the most recent one is loaded
    automatically."""
    if not TKINTER_AVAILABLE:
        print(T("errNoEditor"))
        print(T("errNoEditorHint"))
        return

    # Build the sheet data: one row per item, with Quantity = 0.
    # Columns: Name, Description, Price, Quantity (editable, int).
    data = []
    for sec in sections:
        for name, description, price in sec["items"]:
            data.append([name, description, price, 0])

    # Main window.
    root = tk.Tk()
    root.title(T("windowTitle"))
    root.geometry("900x650")

    # --- Ctrl+C from the terminal ----------------------------------
    # By default tkinter's mainloop does not return to the Python
    # interpreter often enough to notice a pending SIGINT, so Ctrl+C
    # from the terminal is ignored until the window happens to
    # regain focus.  Two things fix that:
    #
    #   1. Install a SIGINT handler that destroys the window.
    #   2. Schedule a periodic no-op via root.after().  Each tick
    #      yields to Python just long enough for the handler to run.
    #
    # The combination makes Ctrl+C work regardless of which window
    # currently has focus.
    def _on_sigint(signum, frame):
        try:
            root.destroy()
        except Exception:
            pass

    try:
        signal.signal(signal.SIGINT, _on_sigint)
    except ValueError:
        # signal.signal() only works in the main thread; if for some
        # reason we are not in it, skip the handler silently.
        pass

    def _pump_signals():
        root.after(200, _pump_signals)

    _pump_signals()
    # ---------------------------------------------------------------

    # Unsaved-changes flag and the name of the sheet currently loaded
    # (used to pre-fill the save dialog).
    dirty = False
    current_sheet_name = None

    # ========== TOP FRAME: sheet selector and buttons ==========
    top_frame = ttk.Frame(root)
    top_frame.pack(fill=tk.X, padx=10, pady=5)

    status_label = ttk.Label(top_frame, text=T("statusSaved"),
                             foreground="green")
    status_label.pack(side=tk.RIGHT, padx=10)

    def set_dirty(flag):
        """Update the dirty flag and the visual status indicator."""
        nonlocal dirty
        dirty = flag
        if dirty:
            status_label.config(text=T("statusModified"),
                                foreground="red")
        else:
            status_label.config(text=T("statusSaved"),
                                foreground="green")

    def load_sheet_into_table(sheet_name, silent=False):
        """Load sheet `sheet_name` from the JSON into the table.

        When `silent` is True, suppress the "loaded" confirmation
        dialog — used by the startup autoload."""
        nonlocal data, current_sheet_name
        json_data = load_json_data(json_file)
        if sheet_name not in json_data:
            messagebox.showerror(T("errTitle"),
                                 T("errSheetMissing")(sheet_name))
            return
        sheet_data = json_data[sheet_name]
        if not sheet_data or len(sheet_data) < 2:
            messagebox.showinfo(T("infoEmptyTitle"),
                                T("infoSheetEmpty")(sheet_name))
            return
        # Sheet format: [["name","quantity"], ["item", 2], ...]
        quantity_by_name = {}
        for row in sheet_data[1:]:  # skip header
            if len(row) >= 2:
                value = row[1]
                try:
                    if isinstance(value, float):
                        value = int(value)
                    elif isinstance(value, str):
                        if value.isdigit():
                            value = int(value)
                        else:
                            value = 0
                    else:
                        value = int(value)
                except (ValueError, TypeError):
                    value = 0
                quantity_by_name[row[0].strip()] = value
        # Rebuild the table data with the loaded quantities.
        new_data = []
        for row in data:
            name = row[0]
            qty = quantity_by_name.get(name, 0)
            new_data.append([name, row[1], row[2], qty])
        sheet.set_sheet_data(new_data)
        sheet.refresh()
        set_dirty(False)
        current_sheet_name = sheet_name
        if not silent:
            messagebox.showinfo(T("infoLoadedTitle"),
                                T("infoSheetLoaded")(sheet_name))

    ttk.Label(top_frame, text=T("labelSheet")).pack(side=tk.LEFT, padx=5)

    # Combobox lists every user sheet.
    sheet_names = get_user_sheet_names(load_json_data(json_file))
    sheet_var = tk.StringVar()
    combobox = ttk.Combobox(top_frame, textvariable=sheet_var,
                            values=sheet_names, state="readonly",
                            width=20)
    combobox.pack(side=tk.LEFT, padx=5)
    combobox.set('')

    def on_load_clicked():
        """Load the selected sheet, offering to save unsaved changes
        first."""
        selected = sheet_var.get()
        if not selected:
            messagebox.showwarning(T("warnSelectTitle"),
                                   T("warnNoSheetSelected"))
            return
        if dirty:
            answer = messagebox.askyesnocancel(
                T("warnUnsavedTitle"),
                T("warnUnsavedLoadMsg")
            )
            if answer is None:  # Cancel
                return
            if answer:          # Yes
                on_save_clicked()
            # No: discard changes and continue.
        load_sheet_into_table(selected)

    ttk.Button(top_frame, text=T("btnLoad"),
               command=on_load_clicked).pack(side=tk.LEFT, padx=5)

    def on_save_clicked():
        """Save the current table to a sheet in the JSON, prompting
        for the sheet name.  Records the save time in "_meta" so the
        startup autoload can pick the most recent sheet.

        When no user sheet has been saved yet, the name dialog
        pre-fills with the current time in the same ISO 8601 format
        used for the "_meta" timestamps, so a first-time save only
        needs the user to press Enter."""
        nonlocal current_sheet_name

        # Load the JSON up front so we can decide what to pre-fill
        # the name dialog with.
        json_data = load_json_data(json_file)
        existing_sheets = get_user_sheet_names(json_data)

        # Pre-fill rules:
        #   - a loaded sheet name wins (Save-As from an open sheet)
        #   - otherwise, when no user sheet exists yet, use now()
        #   - otherwise leave blank
        if current_sheet_name:
            initial_name = current_sheet_name
        elif not existing_sheets:
            initial_name = datetime.now().isoformat()
        else:
            initial_name = ""

        sheet_name = simpledialog.askstring(
            T("dlgSaveSheetTitle"),
            T("dlgSaveSheetPrompt"),
            parent=root,
            initialvalue=initial_name
        )
        if sheet_name is None:  # Cancel
            return
        sheet_name = sheet_name.strip()
        if not sheet_name:
            messagebox.showwarning(T("warnNameEmptyTitle"),
                                   T("warnNameEmptyMsg"))
            return

        # Confirm overwrite if the sheet already exists.
        if sheet_name in json_data and \
           sheet_name not in RESERVED_JSON_KEYS:
            if not messagebox.askyesno(T("dlgOverwriteTitle"),
                                       T("dlgOverwriteMsg")(sheet_name)):
                return

        # Serialize the table as [["name","quantity"], ...].
        sheet_rows = sheet.get_sheet_data()
        new_sheet = [list(SHEET_HEADER)]
        for row in sheet_rows:
            if len(row) >= 4:
                name = str(row[0]).strip()
                try:
                    value = row[3]
                    if isinstance(value, float):
                        value = int(value)
                    elif isinstance(value, str):
                        try:
                            value = int(float(value))
                        except ValueError:
                            value = 0
                    else:
                        value = int(value)
                except (ValueError, TypeError):
                    value = 0
                if name:
                    new_sheet.append([name, value])

        # Write back to the JSON, updating the combobox and the
        # remembered sheet name.  Record the save time so the
        # startup autoload knows which sheet is the most recent.
        json_data[sheet_name] = new_sheet
        if "_meta" not in json_data:
            json_data["_meta"] = {"sheets_saved_at": {}}
        if "sheets_saved_at" not in json_data["_meta"]:
            json_data["_meta"]["sheets_saved_at"] = {}
        json_data["_meta"]["sheets_saved_at"][sheet_name] = \
            datetime.now().isoformat()

        with open(json_file, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False)

        new_sheet_names = get_user_sheet_names(json_data)
        combobox['values'] = new_sheet_names
        if sheet_name in new_sheet_names:
            combobox.set(sheet_name)
        current_sheet_name = sheet_name
        set_dirty(False)
        messagebox.showinfo(T("infoSavedTitle"),
                            T("infoSheetSaved")(sheet_name, json_file))

    ttk.Button(top_frame, text=T("btnSave"),
               command=on_save_clicked).pack(side=tk.LEFT, padx=5)

    def on_delete_clicked():
        """Delete the currently selected sheet from the JSON, along
        with its save timestamp."""
        nonlocal current_sheet_name
        selected = sheet_var.get()
        if not selected:
            messagebox.showwarning(T("warnSelectTitle"),
                                   T("warnNoSheetToDelete"))
            return
        if not messagebox.askyesno(T("dlgConfirmDeleteTitle"),
                                   T("dlgConfirmDeleteMsg")(selected)):
            return
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                json_data = json.load(f)
        except FileNotFoundError:
            json_data = {"items": []}
        if selected in json_data:
            del json_data[selected]
            # Drop the sheet's timestamp too.
            if "_meta" in json_data:
                json_data["_meta"].get("sheets_saved_at", {}) \
                    .pop(selected, None)
            with open(json_file, 'w', encoding='utf-8') as f:
                json.dump(json_data, f, indent=2, ensure_ascii=False)
            # Refresh the combobox.
            new_sheet_names = get_user_sheet_names(json_data)
            combobox['values'] = new_sheet_names
            if new_sheet_names:
                combobox.set(new_sheet_names[0])
            else:
                combobox.set('')
            if selected == current_sheet_name:
                current_sheet_name = None
            # Reset the table quantities to zero.
            new_data = []
            for row in data:
                new_data.append([row[0], row[1], row[2], 0])
            sheet.set_sheet_data(new_data)
            sheet.refresh()
            set_dirty(False)
            messagebox.showinfo(T("infoDeletedTitle"),
                                T("infoSheetDeleted")(selected))
        else:
            messagebox.showerror(T("errTitle"),
                                 T("errSheetMissing")(selected))

    ttk.Button(top_frame, text=T("btnDelete"),
               command=on_delete_clicked).pack(side=tk.LEFT, padx=5)
    ttk.Button(top_frame, text=T("btnCancel"),
               command=root.destroy).pack(side=tk.LEFT, padx=5)

    # ========== NOTEBOOK: Quantities and Results ==========
    notebook = ttk.Notebook(root)
    notebook.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

    # Tab 1: quantity table.
    tab1 = ttk.Frame(notebook)
    notebook.add(tab1, text=T("tabQuantities"))

    sheet = tksheet.Sheet(tab1,
                          data=data,
                          headers=[T("colName"), T("colDescription"),
                                   T("colPrice"), T("colQuantity")],
                          column_widths=[200, 150, 80, 100],
                          width=860, height=400)
    sheet.pack(fill=tk.BOTH, expand=True)

    # Enable every interaction (selection, edit, keyboard, etc.).
    sheet.enable_bindings("all")

    # Columns A, B, C (Name, Description, Price) are read-only.
    sheet.readonly("A:C")

    # Any modification marks the sheet dirty.  <<SheetModified>> is
    # tksheet's official catch-all event; end_edit_cell is a fallback
    # for older versions.
    def on_sheet_modified(event):
        set_dirty(True)

    sheet.bind("<<SheetModified>>", on_sheet_modified)

    try:
        sheet.extra_bindings("end_edit_cell",
                             lambda event: set_dirty(True))
    except AttributeError:
        pass  # older tksheet, ignore

    # Ctrl+F / Cmd+F search: find a product name in the table.
    def on_search():
        query = simpledialog.askstring(
            T("dlgSearchTitle"), T("dlgSearchPrompt"), parent=root)
        if query is None or not query.strip():
            return
        search_text = query.strip()
        sheet_rows = sheet.get_sheet_data()
        found = False
        for idx, row in enumerate(sheet_rows):
            if len(row) > 0 and search_text.lower() in str(row[0]).lower():
                sheet.select_cell(idx, 0)
                sheet.see(idx, 0)  # scroll into view
                found = True
                break
        if not found:
            messagebox.showinfo(T("infoSearchTitle"),
                                T("infoSearchNotFound")(search_text))

    root.bind_all("<Control-f>", lambda event: on_search())
    root.bind_all("<Command-f>", lambda event: on_search())  # macOS

    sheet.focus_set()

    # Tab 2: results (read-only, but selectable).
    tab2 = ttk.Frame(notebook)
    notebook.add(tab2, text=T("tabResults"))

    result_sheet = tksheet.Sheet(tab2,
                                 data=[],
                                 headers=[T("colArticle"),
                                          T("colPriceXQty"),
                                          T("colSubtotal")],
                                 column_widths=[300, 150, 100],
                                 width=860, height=400)
    result_sheet.pack(fill=tk.BOTH, expand=True)

    # All interactions except editing.
    result_sheet.enable_bindings("all")
    result_sheet.disable_bindings("edit_cell")

    # ========== BOTTOM ACTION BUTTONS ==========
    bottom_frame = ttk.Frame(root)
    bottom_frame.pack(fill=tk.X, padx=10, pady=5)

    def compute_results(short=False):
        """Compute totals and show the results in the Results tab,
        grouped by section.

        short=True  -> only items with quantity > 0
        short=False -> every item in the catalog"""
        rows = sheet.get_sheet_data()
        quantity_by_name = {}
        for row in rows:
            if len(row) >= 4:
                name = str(row[0]).strip()
                try:
                    qty = float(row[3]) if row[3] is not None else 0.0
                except (ValueError, TypeError):
                    qty = 0.0
                if name:
                    quantity_by_name[name] = qty

        result_rows = []
        grand_total = 0.0

        for sec in SECTIONS:
            section_rows = []
            section_total = 0.0
            for name, _, price in sec["items"]:
                qty = quantity_by_name.get(name, 0.0)
                if qty > 0 or not short:
                    subtotal = price * qty
                    if qty > 0:
                        section_total += subtotal
                        grand_total += subtotal
                    if short and qty == 0:
                        continue
                    section_rows.append(
                        [name, f"{price:.2f} × {qty:.2f}", subtotal])
            if section_rows:
                result_rows.append([T("sectionLine")(sec['name']), "", ""])
                result_rows.extend(section_rows)
                result_rows.append(
                    [T("subtotalLine")(sec['name']), "", section_total])

        result_rows.append([T("totalLabel"), "", grand_total])

        result_sheet.set_sheet_data(result_rows)
        result_sheet.refresh()
        notebook.select(tab2)

    def export_pdf(short=False):
        """Generate a costs PDF from the current quantities.

        short=True  -> only items with quantity > 0  ("Export PDF")
        short=False -> every item                    ("Export PDF detailed")
        """
        rows = sheet.get_sheet_data()
        quantity_by_name = {}
        for row in rows:
            if len(row) >= 4:
                name = str(row[0]).strip()
                try:
                    qty = float(row[3]) if row[3] is not None else 0.0
                except (ValueError, TypeError):
                    qty = 0.0
                if name:
                    quantity_by_name[name] = qty

        # Same element builder the CLI uses; see build_costs_elements.
        elements = build_costs_elements(quantity_by_name, SECTIONS,
                                        short=short)

        # Guard: nothing to export (all quantities were zero in
        # short mode, or the catalog is empty).
        if not elements:
            messagebox.showinfo(T("infoNoDataTitle"),
                                T("infoNoDataShort"))
            return

        # Costs mode uses three columns.
        detailed_headers = T("pdfHeadersDetailed")
        col_widths = [
            int(VIRTUAL_WIDTH * 0.50),
            int(VIRTUAL_WIDTH * 0.25),
            int(VIRTUAL_WIDTH * 0.25),
        ]
        diff = VIRTUAL_WIDTH - sum(col_widths)
        if diff != 0:
            col_widths[-1] += diff

        # Filename: sheet name if one is selected, otherwise today's
        # date.  Strip characters that are unsafe for filenames.
        selected = sheet_var.get()
        if selected:
            base = selected
        else:
            base = datetime.now().strftime("%Y-%m-%d")
        base = re.sub(r'[^\w\-]', '_', base)
        if short:
            output_pdf = f"{base}.pdf"
        else:
            output_pdf = f"{base}_detailed.pdf"

        generate_pdf_from_elements(elements, detailed_headers, col_widths,
                                   output_pdf, VIRTUAL_WIDTH,
                                   VIRTUAL_HEIGHT)

    # Button order: Compute, Compute detailed, Export PDF,
    # Export PDF detailed.
    ttk.Button(bottom_frame, text=T("btnCalc"),
               command=lambda: compute_results(short=True)
               ).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text=T("btnCalcDetailed"),
               command=lambda: compute_results(short=False)
               ).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text=T("btnExportPdf"),
               command=lambda: export_pdf(short=True)
               ).pack(side=tk.LEFT, padx=5)
    ttk.Button(bottom_frame, text=T("btnExportPdfDetailed"),
               command=lambda: export_pdf(short=False)
               ).pack(side=tk.LEFT, padx=5)

    # Warn about unsaved changes when closing the window.
    def on_closing():
        if dirty:
            answer = messagebox.askyesnocancel(
                T("warnUnsavedTitle"),
                T("warnUnsavedExitMsg")
            )
            if answer is None:  # Cancel
                return
            if answer:          # Yes
                on_save_clicked()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)

    # --- Autoload the most-recently-saved sheet --------------------
    # If the JSON has at least one user sheet with a saved-at
    # timestamp, load the newest one so the user resumes where they
    # left off.  If user sheets exist but none has a timestamp
    # (e.g. an older JSON written before _meta existed), do nothing.
    # Any failure here must not stop the editor from opening.
    try:
        startup_json = load_json_data(json_file)
        latest = find_latest_saved_sheet(startup_json)
        if latest:
            combobox.set(latest)
            load_sheet_into_table(latest, silent=True)
            print(T("autoLoaded")(latest))
        elif not get_user_sheet_names(startup_json):
            print(T("autoLoadedNone"))
    except Exception as e:
        print(f"Autoload skipped: {e}")

    root.mainloop()

# ==================== MAIN ====================

def main():
    parser = argparse.ArgumentParser(description=T("argDescription"))
    parser.add_argument("--json", default=DEFAULT_JSON,
                        help=T("argJsonHelp")(DEFAULT_JSON))
    parser.add_argument("--export-calendar", action="store_true",
                        help=T("argExportCalendarHelp"))
    parser.add_argument("--export-costs", action="store_true",
                        help=T("argExportCostsHelp"))
    args = parser.parse_args()

    # Reload sections from the specified JSON.
    global SECTIONS
    SECTIONS = load_sections_from_json(args.json)

    # Headless modes.  Both flags may be combined; if neither is
    # given, fall through to the interactive editor.
    headless = False

    if args.export_calendar:
        headless = True
        generate_calendar_pdf()

    if args.export_costs:
        headless = True
        generate_costs_pdf(args.json)

    if not headless:
        # Interactive: open the editor only.  Any PDF is generated
        # by the user clicking an Export button, never at startup.
        open_editor_window(SECTIONS, args.json)

if __name__ == "__main__":
    main()
