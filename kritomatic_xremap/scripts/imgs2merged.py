#!/usr/bin/env python3
"""
imgs2merged.py

Merge every image in a directory into a single image, appended either
horizontally or vertically, optionally wrapping into a grid.

Along the primary merge axis the images flow side by side. Along the
perpendicular axis, every image is shrunk to match the smallest one
(the shortest image when HORIZONTAL is True, the narrowest when
HORIZONTAL is False), so the strip is uniform along that axis.

If LIMIT_ENABLED is True, the flow wraps after LIMIT images: in
horizontal mode that is the maximum images per row; in vertical mode
it is the maximum images per column. The result is a grid, with each
column (horizontal mode) or row (vertical mode) sized to hold the
widest (resp. tallest) image that lands in it, and images centered
within their cell along the shared axis.

A separator line can optionally be drawn between adjacent images
(between columns and between rows in grid mode).

Behavior is controlled by variables in the CONFIGURABLE SETTINGS
block. The only command-line flag is --help; the two optional
positional arguments are the input directory and the output path,
each overriding its corresponding variable.

Usage:
    python imgs2merged.py [directory] [output]

Examples:
    # Use the INPUT_DIR / OUTPUT_PATH variables
    python imgs2merged.py

    # Override the input directory
    python imgs2merged.py ./photos

    # Override both
    python imgs2merged.py ./photos ./out.png
"""

import sys
import argparse
from pathlib import Path
from wand.image import Image
from wand.color import Color

# ===== CONFIGURABLE SETTINGS =====
# Directory containing the images to merge
INPUT_DIR = "path/to/images"

# Merge direction. True = horizontal (left to right), False = vertical
# (top to bottom).
HORIZONTAL = True

# Wrap into a grid after LIMIT images along the primary direction.
# When LIMIT_ENABLED is True: LIMIT is the maximum images per row
# (horizontal mode) or per column (vertical mode). Images that would
# exceed it flow into a new row (horizontal) or column (vertical).
LIMIT_ENABLED = False
LIMIT = 3

# Output file path. None = auto (next to INPUT_DIR, named after the folder).
OUTPUT_PATH = None

# File extensions to include (case-insensitive)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp",
                    ".tif", ".tiff", ".webp")

# Sort order for files: "name", "name_desc", "mtime", "mtime_desc"
SORT_ORDER = "name"

# Draw a line between adjacent images
SEPARATOR_ENABLED = False
# Separator color: hex ("#000000") or common color name ("black", "red", ...)
SEPARATOR_COLOR = "#000000"
# Separator thickness in pixels
SEPARATOR_THICKNESS = 2

# Background color: hex ("#FFFFFF" or "FFFFFF") or common color name
# (e.g. "white", "black", "red"). None = transparent.
BACKGROUND_COLOR = "white"
# BACKGROUND_COLOR = None
# =================================


def parse_color(color_input):
    """Parse a hex value or color name into a Wand Color, or None."""
    if color_input is None:
        return None
    c = color_input.strip()
    # Bare hex without '#'
    if len(c) == 6 and all(ch in '0123456789ABCDEFabcdef' for ch in c):
        c = '#' + c
    try:
        return Color(c)
    except Exception:
        return None


def _draw_line(canvas, orientation, position, total_w, total_h,
               color, thickness):
    """Draw a separator line across the canvas.

    orientation = "v": vertical line at x = position, spanning the
                       full canvas height.
    orientation = "h": horizontal line at y = position, spanning the
                       full canvas width.

    The line is centered on `position` (half its thickness on either
    side) and clipped to the canvas bounds.
    """
    half = thickness // 2

    if orientation == "v":
        left = position - half
        width = thickness
        if left < 0:
            width += left
            left = 0
        if left + width > total_w:
            width = total_w - left
        if width <= 0:
            return
        with Image(width=width, height=total_h, background=color) as line:
            canvas.composite(line, left=left, top=0)
    else:  # "h"
        top = position - half
        height = thickness
        if top < 0:
            height += top
            top = 0
        if top + height > total_h:
            height = total_h - top
        if height <= 0:
            return
        with Image(width=total_w, height=height, background=color) as line:
            canvas.composite(line, left=0, top=top)


def collect_images(directory, extensions, sort_order):
    """Return a sorted list of image file paths in `directory`."""
    p = Path(directory)
    if not p.is_dir():
        raise NotADirectoryError(f"Not a directory: {directory}")
    files = [f for f in p.iterdir()
             if f.is_file() and f.suffix.lower() in extensions]

    if sort_order == "name":
        files.sort(key=lambda f: f.name.lower())
    elif sort_order == "name_desc":
        files.sort(key=lambda f: f.name.lower(), reverse=True)
    elif sort_order == "mtime":
        files.sort(key=lambda f: f.stat().st_mtime)
    elif sort_order == "mtime_desc":
        files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    else:
        print(f"Warning: unknown SORT_ORDER '{sort_order}'. Using 'name'.")
        files.sort(key=lambda f: f.name.lower())

    return files


def load_images(paths):
    """Load every path into an open Wand Image (auto-oriented)."""
    loaded = []
    for p in paths:
        try:
            img = Image(filename=str(p))
            img.auto_orient()
            loaded.append((p, img))
        except Exception as e:
            print(f"Warning: could not load {p}: {e}")
    return loaded


def merge_images(images, horizontal, background_color, output_path,
                 limit_enabled=False, limit=4,
                 separator_enabled=False, separator_color="#000000",
                 separator_thickness=1):
    """
    Composite the images into one canvas.

    horizontal = True : flow left to right, shrink every image's height
                        to the smallest height.
    horizontal = False: flow top to bottom, shrink every image's width
                        to the smallest width.

    If limit_enabled, the flow wraps after `limit` images: new rows
    (horizontal mode) or new columns (vertical mode) are started.
    Cells are aligned: in horizontal mode every column is as wide as
    its widest image; in vertical mode every row is as tall as its
    tallest image. Images are centered within their cell along the
    shared axis.
    """
    n = len(images)

    if horizontal:
        target = min(im.height for _, im in images)
        axis = "height"
    else:
        target = min(im.width for _, im in images)
        axis = "width"

    print(f"Smallest {axis}: {target}px")
    print("Resizing images to match:")

    for path, img in images:
        orig_w, orig_h = img.width, img.height
        if horizontal:
            if img.height != target:
                scale = target / img.height
                new_w = max(1, int(round(img.width * scale)))
                img.resize(new_w, target)
        else:
            if img.width != target:
                scale = target / img.width
                new_h = max(1, int(round(img.height * scale)))
                img.resize(target, new_h)
        if (img.width, img.height) != (orig_w, orig_h):
            print(f"  {path.name}: {orig_w}x{orig_h} -> {img.width}x{img.height}")
        else:
            print(f"  {path.name}: {orig_w}x{orig_h} (unchanged)")

    # Per-line count: how many images fit along the primary flow axis
    # before the flow wraps to the next row (horizontal mode) or the
    # next column (vertical mode).
    if limit_enabled:
        per_line = max(1, int(limit))
    else:
        per_line = n if n > 0 else 1

    # Map each image index to (row, col).
    #   horizontal mode: flow left-to-right, wrap to next row.
    #   vertical mode:   flow top-to-bottom, wrap to next column.
    coords = []
    for i in range(n):
        if horizontal:
            coords.append((i // per_line, i % per_line))
        else:
            coords.append((i % per_line, i // per_line))

    num_rows = max((r for r, _ in coords), default=-1) + 1
    num_cols = max((c for _, c in coords), default=-1) + 1

    # Cell sizes and starting positions.
    if horizontal:
        # Column widths from the widest image in each column.
        col_widths = [0] * num_cols
        for i, (r, c) in enumerate(coords):
            w = images[i][1].width
            if w > col_widths[c]:
                col_widths[c] = w
        col_x = [0] * num_cols
        for c in range(1, num_cols):
            col_x[c] = col_x[c - 1] + col_widths[c - 1]
        row_y = [r * target for r in range(num_rows)]
        total_w = sum(col_widths)
        total_h = num_rows * target
    else:
        # Row heights from the tallest image in each row.
        row_heights = [0] * num_rows
        for i, (r, c) in enumerate(coords):
            h = images[i][1].height
            if h > row_heights[r]:
                row_heights[r] = h
        row_y = [0] * num_rows
        for r in range(1, num_rows):
            row_y[r] = row_y[r - 1] + row_heights[r - 1]
        col_x = [c * target for c in range(num_cols)]
        total_w = num_cols * target
        total_h = sum(row_heights)

    if limit_enabled:
        print(f"Limit: {per_line} image(s) per "
              f"{'row' if horizontal else 'column'}")
    print(f"Grid: {num_rows} row(s) x {num_cols} column(s)")
    print(f"Canvas size: {total_w}x{total_h}")

    # Background
    bg = parse_color(background_color)
    if background_color and bg is None:
        print(f"Warning: invalid color '{background_color}'. Using transparent.")
    use_bg = bg is not None
    if bg is None:
        bg = Color('transparent')

    # Separator color (validated once up front)
    sep_color = None
    if separator_enabled:
        sep_color = parse_color(separator_color)
        if sep_color is None:
            print(f"Warning: invalid separator color '{separator_color}'. "
                  f"Using black.")
            sep_color = Color('#000000')
        print(f"Separator: {separator_thickness}px, "
              f"color '{separator_color}'")

    with Image(width=total_w, height=total_h, background=bg) as canvas:
        if not use_bg:
            canvas.alpha_channel = True
        with Image(width=total_w, height=total_h, background=bg) as fill:
            canvas.composite(fill, left=0, top=0)

        # Composite images at their grid positions.
        for i, (_, img) in enumerate(images):
            r, c = coords[i]
            if horizontal:
                x = col_x[c] + (col_widths[c] - img.width) // 2
                y = row_y[r]
            else:
                x = col_x[c]
                y = row_y[r] + (row_heights[r] - img.height) // 2
            canvas.composite(img, left=x, top=y)

        # Draw separators on top of the images so the lines stay
        # visible even when adjacent images share an opaque edge.
        if separator_enabled and sep_color is not None:
            for r in range(1, num_rows):
                _draw_line(canvas, "h", row_y[r],
                           total_w, total_h,
                           sep_color, separator_thickness)
            for c in range(1, num_cols):
                _draw_line(canvas, "v", col_x[c],
                           total_w, total_h,
                           sep_color, separator_thickness)

        canvas.save(filename=str(output_path))

    print(f"Merged image saved to {output_path}")
    print(f"Images merged: {len(images)}")
    print(f"Direction: {'horizontal' if horizontal else 'vertical'}")
    print(f"Target {axis}: {target}px")


def main():
    parser = argparse.ArgumentParser(
        description='Merge all images in a directory into one image, '
                    'appended horizontally or vertically, optionally '
                    'wrapping into a grid. Configure via variables in '
                    'the script; positional arguments override INPUT_DIR '
                    'and OUTPUT_PATH.',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('directory', nargs='?', default=None,
                        help='Input directory (overrides INPUT_DIR)')
    parser.add_argument('output', nargs='?', default=None,
                        help='Output file path (overrides OUTPUT_PATH)')
    args = parser.parse_args()

    input_dir = args.directory or INPUT_DIR
    output_path = args.output or OUTPUT_PATH
    horizontal = bool(HORIZONTAL)

    # Auto output: "<dir_name>_merged_<direction>.png" next to the input dir
    if not output_path:
        d = Path(input_dir)
        direction = "horizontal" if horizontal else "vertical"
        output_path = str(d.parent / f"{d.name}_merged_{direction}.png")

    try:
        paths = collect_images(input_dir, IMAGE_EXTENSIONS, SORT_ORDER)
    except NotADirectoryError as e:
        print(f"Error: {e}")
        sys.exit(1)

    if not paths:
        print(f"Error: no images found in {input_dir}")
        sys.exit(1)

    print(f"Found {len(paths)} image(s) in {input_dir}")

    images = load_images(paths)
    if not images:
        print("Error: no images could be loaded.")
        sys.exit(1)

    try:
        merge_images(images, horizontal, BACKGROUND_COLOR, output_path,
                     limit_enabled=LIMIT_ENABLED,
                     limit=LIMIT,
                     separator_enabled=SEPARATOR_ENABLED,
                     separator_color=SEPARATOR_COLOR,
                     separator_thickness=SEPARATOR_THICKNESS)
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)
    finally:
        for _, img in images:
            try:
                img.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
