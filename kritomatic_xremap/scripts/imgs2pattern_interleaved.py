#!/usr/bin/env python3
"""
imgs2pattern_interleaved.py

Create an interleaved repeating pattern from 3 input images.

Row layout alternates between two types:

  Row 1 (and odd rows):  [Img1] [Img2] [Img1] [Img2] ...
  Row 2 (and even rows): [Img3] [Img1] [Img3] [Img1] ...  (offset by 1)

This makes Img1 in row 2 align below Img2 in row 1 (centered). All images
are shrunk to the height of the smallest image, so the grid is tight. The
pattern supports vertical and horizontal overlapping/gaps as percentages,
and a continuous row offset with seamless wrapping.

The output is always written next to image 1, with a "_pattern_interleaved"
suffix (e.g. img1.png -> img1_pattern_interleaved.png).

All behavior is configured through variables in the CONFIGURABLE SETTINGS
block below. The only command-line flag is --help; the three optional
positional arguments are the input image paths, each overriding its
corresponding variable.

Usage:
    python imgs2pattern_interleaved.py [img1] [img2] [img3]

Examples:
    # Use the three IMAGE*_PATH variables
    python imgs2pattern_interleaved.py

    # Override all three
    python imgs2pattern_interleaved.py a.png b.png c.png

    # Override just image 1 (images 2 and 3 still come from variables)
    python imgs2pattern_interleaved.py a.png
"""

import sys
import argparse
from pathlib import Path
from wand.image import Image
from wand.color import Color
import math

# ===== CONFIGURABLE SETTINGS =====
# Input images; each is overridden by its corresponding positional argument
IMAGE1_PATH = "path/to/image1.png"   # Appears in every row
IMAGE2_PATH = "path/to/image2.png"   # Appears in odd rows
IMAGE3_PATH = "path/to/image3.png"   # Appears in even rows, offset

# Number of repeats
REPEAT_X = 4
REPEAT_Y = 4

# Tile dimensions. None = auto (uses max image dimension).
TILE_WIDTH = None
TILE_HEIGHT = None

# Shrink percentage (1-100). 100 = full size.
SHRINK_PERCENT = 100

# Rotation angle in degrees (0-360)
ROTATION = 0

# Background color: hex ("#FFFFFF" or "FFFFFF") or common color name
# (e.g. "white", "black", "red"). None = transparent.
BACKGROUND_COLOR = None

# Spacing as percentage of tile dimension (-100 to 100).
# Positive = overlap, negative = gap.
VERTICAL_OVERLAP = 0.0
HORIZONTAL_OVERLAP = 0.0

# Horizontal offset per row, in pixels. Positive shifts right, negative
# shifts left. Continuous with seamless wrapping.
OFFSET = 0
# =================================

# Common color names mapping to hex values
COMMON_COLORS = {
    'white': '#FFFFFF',
    'black': '#000000',
    'red': '#FF0000',
    'green': '#00FF00',
    'blue': '#0000FF',
    'yellow': '#FFFF00',
    'cyan': '#00FFFF',
    'magenta': '#FF00FF',
    'gray': '#808080',
    'grey': '#808080',
    'darkgray': '#A9A9A9',
    'darkgrey': '#A9A9A9',
    'lightgray': '#D3D3D3',
    'lightgrey': '#D3D3D3',
    'darkred': '#8B0000',
    'darkgreen': '#006400',
    'darkblue': '#00008B',
    'darkyellow': '#BDB76B',
    'darkcyan': '#008B8B',
    'darkmagenta': '#8B008B',
    'lightred': '#FF6347',
    'lightgreen': '#90EE90',
    'lightblue': '#ADD8E6',
    'lightyellow': '#FFFFE0',
    'lightcyan': '#E0FFFF',
    'lightmagenta': '#FF77FF',
    'orange': '#FFA500',
    'darkorange': '#FF8C00',
    'gold': '#FFD700',
    'silver': '#C0C0C0',
    'maroon': '#800000',
    'olive': '#808000',
    'purple': '#800080',
    'teal': '#008080',
    'navy': '#000080',
    'aqua': '#00FFFF',
    'fuchsia': '#FF00FF',
    'lime': '#00FF00',
    'coral': '#FF7F50',
    'salmon': '#FA8072',
    'khaki': '#F0E68C',
    'plum': '#DDA0DD',
    'violet': '#EE82EE',
    'orchid': '#DA70D6',
    'lavender': '#E6E6FA',
    'tan': '#D2B48C',
    'brown': '#A52A2A',
    'chocolate': '#D2691E',
    'sienna': '#A0522D',
    'beige': '#F5F5DC',
    'mint': '#98FF98',
    'peach': '#FFDAB9',
    'apricot': '#FBCEB1',
    'rose': '#FF007F',
    'ruby': '#E0115F',
    'emerald': '#50C878',
    'sapphire': '#0F52BA',
    'amethyst': '#9966CC',
}

def parse_color(color_input):
    """
    Parse color input which can be:
    - Hex value: #FFFFFF or FFFFFF
    - Color name: white, black, red, etc.

    Returns a Color object or None if invalid
    """
    if color_input is None:
        return None

    color_input = color_input.strip()

    # Check if it's a hex value (starts with # or is 6 hex digits)
    if color_input.startswith('#') or (len(color_input) == 6 and all(c in '0123456789ABCDEFabcdef' for c in color_input)):
        # Ensure hex has # prefix
        if not color_input.startswith('#'):
            color_input = '#' + color_input
        try:
            return Color(color_input)
        except Exception:
            return None

    # Check if it's a common color name (case insensitive)
    color_lower = color_input.lower()
    if color_lower in COMMON_COLORS:
        try:
            return Color(COMMON_COLORS[color_lower])
        except Exception:
            return None

    # Try to use Wand's built-in color parsing as fallback
    try:
        return Color(color_input)
    except Exception:
        return None

def prepare_image(image_path, target_height=None, target_width=None, shrink_percent=100, rotation=0):
    """
    Load and prepare an image with optional shrink and rotation.
    Returns the prepared image and its dimensions.
    """
    with Image(filename=image_path) as img:
        # Apply shrink
        if shrink_percent != 100:
            shrink_ratio = shrink_percent / 100.0
            new_width = int(img.width * shrink_ratio)
            new_height = int(img.height * shrink_ratio)
            img.resize(new_width, new_height)

        # Apply rotation
        if rotation != 0:
            img.rotate(rotation, background=Color('transparent'))

        # Store dimensions before potential resizing
        width = img.width
        height = img.height

        # Resize to target dimensions if specified (maintains aspect ratio)
        if target_width is not None and target_height is not None:
            # Calculate scale to fit within target while maintaining aspect ratio
            scale_x = target_width / width
            scale_y = target_height / height
            scale = min(scale_x, scale_y)

            new_width = int(width * scale)
            new_height = int(height * scale)
            img.resize(new_width, new_height)

            # Update dimensions
            width = img.width
            height = img.height

        # Create a copy that we can return (since we need to close the context)
        return img.clone()

def create_interleaved_pattern(img1_path, img2_path, img3_path, output_path,
                              repeat_x=4, repeat_y=4, shrink_percent=100, rotation=0,
                              tile_width=None, tile_height=None, background_color=None,
                              vertical_overlap=0.0, horizontal_overlap=0.0,
                              offset=0):
    """
    Create an interleaved repeating pattern from 3 images.

    Args:
        img1_path: Path to first image (appears in all rows)
        img2_path: Path to second image (appears in odd rows)
        img3_path: Path to third image (appears in even rows, offset)
        repeat_x: Number of repeats horizontally
        repeat_y: Number of repeats vertically
        shrink_percent: Shrink percentage (1-100)
        rotation: Rotation angle in degrees
        tile_width: Optional fixed tile width (if None, uses max image width)
        tile_height: Optional fixed tile height (if None, uses max image height)
        background_color: Optional background color
        vertical_overlap: Vertical spacing as percentage of tile height.
                         Positive = overlap, Negative = gap
                         Range: -100 to 100
        horizontal_overlap: Horizontal spacing as percentage of tile width.
                           Positive = overlap, Negative = gap
                           Range: -100 to 100
        offset: Horizontal offset per row (pixels). Positive shifts right, negative shifts left.
               Continuous with seamless wrapping - no empty spaces.
    """

    # Load and prepare images (without resizing to target yet)
    img1 = prepare_image(img1_path, shrink_percent=shrink_percent, rotation=rotation)
    img2 = prepare_image(img2_path, shrink_percent=shrink_percent, rotation=rotation)
    img3 = prepare_image(img3_path, shrink_percent=shrink_percent, rotation=rotation)

    # Get original dimensions
    img1_width, img1_height = img1.width, img1.height
    img2_width, img2_height = img2.width, img2.height
    img3_width, img3_height = img3.width, img3.height

    print(f"Original Image 1: {img1_width}x{img1_height}")
    print(f"Original Image 2: {img2_width}x{img2_height}")
    print(f"Original Image 3: {img3_width}x{img3_height}")

    # Find the smallest height among all images
    min_height = min(img1_height, img2_height, img3_height)
    print(f"Smallest height: {min_height}px")

    # Resize all images to the smallest height (maintaining aspect ratio)
    # Image 1
    if img1_height != min_height:
        scale = min_height / img1_height
        new_width = int(img1_width * scale)
        img1.resize(new_width, min_height)
        print(f"Image 1 resized: {img1_width}x{img1_height} -> {img1.width}x{img1.height}")

    # Image 2
    if img2_height != min_height:
        scale = min_height / img2_height
        new_width = int(img2_width * scale)
        img2.resize(new_width, min_height)
        print(f"Image 2 resized: {img2_width}x{img2_height} -> {img2.width}x{img2.height}")

    # Image 3
    if img3_height != min_height:
        scale = min_height / img3_height
        new_width = int(img3_width * scale)
        img3.resize(new_width, min_height)
        print(f"Image 3 resized: {img3_width}x{img3_height} -> {img3.width}x{img3.height}")

    # Get new dimensions
    img1_width, img1_height = img1.width, img1.height
    img2_width, img2_height = img2.width, img2.height
    img3_width, img3_height = img3.width, img3.height

    print(f"Final Image 1: {img1_width}x{img1_height}")
    print(f"Final Image 2: {img2_width}x{img2_height}")
    print(f"Final Image 3: {img3_width}x{img3_height}")

    # Determine tile size
    # For interleaving, we need tiles of equal size
    # Use the maximum dimensions to fit all images
    if tile_width is None:
        # Auto-calculate: use max width
        tile_width = max(img1_width, img2_width, img3_width)

    if tile_height is None:
        # Auto-calculate: use max height (should be min_height if all resized)
        tile_height = max(img1_height, img2_height, img3_height)

    print(f"Base tile size: {tile_width}x{tile_height}")

    # Apply spacing calculations
    # vertical_step: positive = overlap, negative = gap
    # Convert percentage to decimal: 100% = 1.0, -50% = -0.5
    vertical_step = tile_height * (1 - vertical_overlap / 100.0)
    horizontal_step = tile_width * (1 - horizontal_overlap / 100.0)

    # Ensure we don't go to zero or negative steps (which would cause infinite stacking)
    # We'll allow very small steps but not zero or negative
    if abs(vertical_step) < 1 and vertical_step != 0:
        vertical_step = 1 if vertical_step > 0 else -1
    if abs(horizontal_step) < 1 and horizontal_step != 0:
        horizontal_step = 1 if horizontal_step > 0 else -1

    # Determine spacing type description
    vertical_desc = f"{abs(vertical_overlap)}% {'overlap' if vertical_overlap > 0 else 'gap' if vertical_overlap < 0 else 'no overlap'}"
    horizontal_desc = f"{abs(horizontal_overlap)}% {'overlap' if horizontal_overlap > 0 else 'gap' if horizontal_overlap < 0 else 'no overlap'}"

    print(f"Vertical step: {vertical_step:.2f}px ({vertical_desc})")
    print(f"Horizontal step: {horizontal_step:.2f}px ({horizontal_desc})")

    # Center each image within its tile
    img1_x_offset = (tile_width - img1_width) // 2
    img1_y_offset = (tile_height - img1_height) // 2

    img2_x_offset = (tile_width - img2_width) // 2
    img2_y_offset = (tile_height - img2_height) // 2

    img3_x_offset = (tile_width - img3_width) // 2
    img3_y_offset = (tile_height - img3_height) // 2

    # Final dimensions (using stepped sizes)
    # We need to ensure the pattern covers the full area
    # Calculate the total width and height based on steps

    # For gaps (negative overlap), we need to add extra space
    # For overlaps (positive), we need to calculate the total span
    if vertical_step >= 0:
        final_height = int(vertical_step * (repeat_y - 1) + tile_height)
    else:
        # For negative steps (gaps), we need to account for the gaps between tiles
        # The total height is: tile_height + abs(vertical_step) * (repeat_y - 1)
        final_height = int(tile_height + abs(vertical_step) * (repeat_y - 1))

    if horizontal_step >= 0:
        final_width = int(horizontal_step * (repeat_x - 1) + tile_width)
    else:
        # For negative steps (gaps), we need to account for the gaps between tiles
        final_width = int(tile_width + abs(horizontal_step) * (repeat_x - 1))

    # Ensure minimum size
    final_width = max(final_width, tile_width)
    final_height = max(final_height, tile_height)

    print(f"Final pattern size: {final_width}x{final_height}")

    # Parse background color
    bg_color = None
    color_display = None

    if background_color:
        bg_color = parse_color(background_color)
        if bg_color is None:
            print(f"Warning: Invalid color '{background_color}'. Using transparent background.")
            bg_color = Color('transparent')
            color_display = 'transparent'
        else:
            color_display = background_color
            print(f"Using background color: {background_color}")
    else:
        bg_color = Color('transparent')
        color_display = 'transparent'

    # Create the pattern
    with Image(width=final_width, height=final_height) as pattern:
        # Fill with background color
        pattern.background_color = bg_color

        # Handle alpha channel
        if background_color and bg_color is not None:
            pattern.alpha_channel = 'remove'
        else:
            pattern.alpha_channel = 'activate'

        # Create a clean background
        with Image(width=final_width, height=final_height, background=bg_color) as background:
            pattern.composite(background, left=0, top=0)

        # Place tiles with continuous offset and seamless wrapping
        for row in range(repeat_y):
            # Determine row type: 0 = odd row (Img1 + Img2), 1 = even row (Img3 + Img1)
            row_type = row % 2

            # Calculate continuous row offset (no modulo wrapping)
            row_offset = row * offset

            # Calculate y position with spacing
            if vertical_step >= 0:
                y_pos_base = int(row * vertical_step)
            else:
                # For gaps, position tiles with gaps between them
                y_pos_base = int(row * (tile_height + abs(vertical_step)))

            # Calculate the effective horizontal step for this row
            # We need to consider that offset shifts the pattern
            effective_step = horizontal_step if horizontal_step >= 0 else (tile_width + abs(horizontal_step))

            # Calculate how many extra columns we need to cover the offset
            # We need enough columns to cover the full width plus the offset
            if effective_step != 0:
                extra_cols = int(abs(row_offset) / effective_step) + 3
            else:
                extra_cols = 3

            # For positive offset, we need extra columns on the right
            # For negative offset, we need extra columns on the left
            if offset >= 0:
                min_col = -1
                max_col = repeat_x + extra_cols
            else:
                min_col = -extra_cols
                max_col = repeat_x + 1

            for col in range(min_col, max_col):
                # Determine which image to place at this position
                if row_type == 0:
                    # Odd row: [Img1] [Img2] [Img1] [Img2] ...
                    if col % 2 == 0:
                        # Even column: Image 1
                        img_to_place = img1
                        x_offset = img1_x_offset
                        y_offset = img1_y_offset
                    else:
                        # Odd column: Image 2
                        img_to_place = img2
                        x_offset = img2_x_offset
                        y_offset = img2_y_offset
                else:
                    # Even row: OFFSET by one - [Img3] [Img1] [Img3] [Img1] ...
                    if col % 2 == 0:
                        # Even column: Image 3 (offset)
                        img_to_place = img3
                        x_offset = img3_x_offset
                        y_offset = img3_y_offset
                    else:
                        # Odd column: Image 1 (offset)
                        img_to_place = img1
                        x_offset = img1_x_offset
                        y_offset = img1_y_offset

                # Calculate x position with horizontal spacing and continuous row offset
                if horizontal_step >= 0:
                    # Base position with horizontal spacing
                    base_x = col * horizontal_step
                else:
                    # For gaps, position tiles with gaps between them
                    base_x = col * (tile_width + abs(horizontal_step))

                # Apply continuous row offset (no wrapping)
                x_pos = base_x - row_offset + x_offset
                y_pos = y_pos_base + y_offset

                # Place the image if it's within bounds
                if x_pos + img_to_place.width > 0 and x_pos < final_width:
                    if y_pos < final_height:
                        pattern.composite(img_to_place, left=int(x_pos), top=int(y_pos))

        # Save the pattern
        pattern.save(filename=output_path)
        print(f"Pattern saved to {output_path}")
        print(f"Size: {final_width}x{final_height}")
        print(f"Tiles: {repeat_x}x{repeat_y}")
        print(f"Tile size: {tile_width}x{tile_height}")
        print(f"All images resized to height: {min_height}px")
        print(f"Vertical spacing: {vertical_desc}")
        print(f"Horizontal spacing: {horizontal_desc}")
        print(f"Row offset: {offset}px per row (continuous with seamless wrapping)")
        print(f"Shrink: {shrink_percent}%")
        print(f"Rotation: {rotation}°")
        print(f"Background color: {color_display}")
        print(f"Pattern alternates:")
        print(f"  Row 1: [Img1, Img2, Img1, Img2, ...]")
        print(f"  Row 2: [Img3, Img1, Img3, Img1, ...] (offset by 1)")
        return pattern

def main():
    parser = argparse.ArgumentParser(
        description='Create interleaved repeating pattern from 3 images. '
                    'Configure via variables in the script; positional '
                    'arguments override the IMAGE*_PATH variables.',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument('image1', nargs='?', default=None,
                        help='First image path (overrides IMAGE1_PATH)')
    parser.add_argument('image2', nargs='?', default=None,
                        help='Second image path (overrides IMAGE2_PATH)')
    parser.add_argument('image3', nargs='?', default=None,
                        help='Third image path (overrides IMAGE3_PATH)')
    args = parser.parse_args()

    # Resolve input paths: positional overrides variable, per image
    img1_path = args.image1 or IMAGE1_PATH
    img2_path = args.image2 or IMAGE2_PATH
    img3_path = args.image3 or IMAGE3_PATH

    # Validate shrink percentage
    if SHRINK_PERCENT < 1 or SHRINK_PERCENT > 100:
        print(f"Error: SHRINK_PERCENT must be between 1 and 100, got {SHRINK_PERCENT}")
        sys.exit(1)

    # Validate rotation
    if ROTATION < 0 or ROTATION >= 360:
        print(f"Error: ROTATION must be between 0 and 360, got {ROTATION}")
        sys.exit(1)

    # Validate overlap percentages (allow negative for gaps)
    if VERTICAL_OVERLAP < -100 or VERTICAL_OVERLAP > 100:
        print(f"Error: VERTICAL_OVERLAP must be between -100 and 100, got {VERTICAL_OVERLAP}")
        sys.exit(1)

    if HORIZONTAL_OVERLAP < -100 or HORIZONTAL_OVERLAP > 100:
        print(f"Error: HORIZONTAL_OVERLAP must be between -100 and 100, got {HORIZONTAL_OVERLAP}")
        sys.exit(1)

    # Output next to image 1, with "_pattern_interleaved" suffix
    img1_file = Path(img1_path)
    output_path = img1_file.parent / f"{img1_file.stem}_pattern_interleaved{img1_file.suffix}"

    try:
        create_interleaved_pattern(
            img1_path, img2_path, img3_path,
            str(output_path),
            repeat_x=REPEAT_X,
            repeat_y=REPEAT_Y,
            shrink_percent=SHRINK_PERCENT,
            rotation=ROTATION,
            tile_width=TILE_WIDTH,
            tile_height=TILE_HEIGHT,
            background_color=BACKGROUND_COLOR,
            vertical_overlap=VERTICAL_OVERLAP,
            horizontal_overlap=HORIZONTAL_OVERLAP,
            offset=OFFSET
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
