#!/usr/bin/env python3
"""
Create a repeating pattern from an input image with line offset, shrink, and rotation.
The pattern wraps around properly with no empty spaces on the left.
All transformations maintain the final image dimensions.
The rotated image is NOT cropped - it's fully visible within the tile.

Two modes available:
- Fixed: Uses the specified tile dimensions (original behavior)
- Adaptive: Adjusts tile dimensions based on image aspect ratio for tighter grids
  --adaptive-height: Adjusts tile height based on image aspect ratio
  --adaptive-width: Adjusts tile width based on image aspect ratio (uses image width)
"""

import sys
import argparse
from wand.image import Image
from wand.color import Color
import math

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

def create_pattern_full_rotation(input_path, output_path, tile_width, tile_height, offset,
                                repeat_x=4, repeat_y=4, shrink_percent=100, rotation=0,
                                adaptive_height=False, adaptive_width=False, background_color=None):
    """
    Create a repeating pattern with optional adaptive tile dimensions.

    Args:
        adaptive_height: If True, adjusts tile height to match image height (no gaps)
        adaptive_width: If True, adjusts tile width to match image width
        background_color: Optional color string (hex like '#FFFFFF' or 'FFFFFF',
                         or name like 'white', 'black', etc.).
                         If None, uses transparent background.
    """

    # Store the original tile dimensions for reference
    original_tile_width = tile_width
    original_tile_height = tile_height
    actual_tile_width = tile_width
    actual_tile_height = tile_height

    with Image(filename=input_path) as img:
        # Calculate shrink size
        shrink_ratio = shrink_percent / 100.0

        # Store original dimensions for aspect ratio calculation
        original_width = img.width
        original_height = img.height

        # For rotation, we need to calculate the bounding box
        if rotation != 0 and rotation % 90 != 0:
            # Calculate the bounding box size for the rotated image
            # For a rectangle of size w x h rotated by angle theta,
            # the bounding box dimensions are:
            # w*cos(theta) + h*sin(theta) and w*sin(theta) + h*cos(theta)
            angle_rad = math.radians(rotation % 90)
            cos_a = math.cos(angle_rad)
            sin_a = math.sin(angle_rad)

            # We want the rotated image to fit within the tile
            # So we calculate the maximum size the original image can be
            # before rotation, so that after rotation it fits in the tile

            # For the original image, we need it to be small enough
            # that the bounding box fits in the tile
            # This is a system of equations:
            # w*cos(a) + h*sin(a) = tile_width * shrink_ratio
            # w*sin(a) + h*cos(a) = tile_height * shrink_ratio

            # Solve for w and h
            # Since we want to maintain aspect ratio, we'll use a simpler approach
            # Just use the minimum of the two equations
            max_width = tile_width * shrink_ratio
            max_height = tile_height * shrink_ratio

            # Calculate the maximum size while maintaining aspect ratio
            # Using the bounding box formulas
            original_aspect = img.width / img.height

            # Try to solve for w and h
            # w*cos(a) + h*sin(a) = max_width
            # w*sin(a) + h*cos(a) = max_height
            # With h = w / aspect

            # Solve numerically by trying different scales
            scale = min(max_width / (img.width * cos_a + img.height * sin_a),
                       max_height / (img.width * sin_a + img.height * cos_a))

            # Apply the scale
            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
            img.resize(new_width, new_height)
        else:
            # For 0, 90, 180, 270 degrees, just resize normally
            target_width = int(tile_width * shrink_ratio)
            target_height = int(tile_height * shrink_ratio)

            # Resize to fit within target
            scale_x = target_width / img.width
            scale_y = target_height / img.height
            scale = min(scale_x, scale_y)

            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
            img.resize(new_width, new_height)

        # Apply rotation
        if rotation != 0:
            img.rotate(rotation, background=Color('transparent'))

        # Now the image should fit within the tile without cropping
        # Just in case, ensure it fits
        if img.width > tile_width or img.height > tile_height:
            # Scale down to fit
            scale_x = tile_width / img.width
            scale_y = tile_height / img.height
            scale = min(scale_x, scale_y)

            new_width = int(img.width * scale)
            new_height = int(img.height * scale)
            img.resize(new_width, new_height)

        # Calculate tile dimensions based on modes
        image_aspect = img.width / img.height

        # Adaptive Width: Use image width as tile width
        if adaptive_width:
            # Simply use the image width as the tile width
            actual_tile_width = img.width
            print(f"Adaptive tile width: {actual_tile_width} (was {tile_width})")
        else:
            actual_tile_width = tile_width
            print(f"Fixed tile width: {actual_tile_width}")

        # Adaptive Height: Use image height as tile height (no padding)
        if adaptive_height:
            # Use exactly the image height (no gaps)
            actual_tile_height = img.height
            print(f"Adaptive tile height: {actual_tile_height} (was {tile_height})")
        else:
            actual_tile_height = tile_height
            print(f"Fixed tile height: {actual_tile_height}")

        print(f"Image size in tile: {img.width}x{img.height}")

        # Center the image within the tile
        x_offset_center = (actual_tile_width - img.width) // 2
        y_offset_center = (actual_tile_height - img.height) // 2

        # Final dimensions - use actual tile dimensions
        final_width = actual_tile_width * repeat_x
        final_height = actual_tile_height * repeat_y

        # Parse background color if provided
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

            # Handle alpha channel based on whether we have a background color
            if background_color and bg_color is not None:
                # Remove alpha channel for solid background
                pattern.alpha_channel = 'remove'
            else:
                # Keep alpha channel for transparent background
                pattern.alpha_channel = 'activate'

            # Create a clean background
            with Image(width=final_width, height=final_height, background=bg_color) as background:
                pattern.composite(background, left=0, top=0)

            # Place tiles with wrapping
            for row in range(repeat_y):
                row_offset = (row * offset) % actual_tile_width
                start_col = -1
                end_col = repeat_x + 1

                for col in range(start_col, end_col):
                    x_pos = col * actual_tile_width - row_offset + x_offset_center
                    y_pos = row * actual_tile_height + y_offset_center

                    if x_pos + img.width > 0 and x_pos < final_width:
                        pattern.composite(img, left=x_pos, top=y_pos)

            pattern.save(filename=output_path)
            print(f"Pattern saved to {output_path}")
            print(f"Size: {final_width}x{final_height}")
            print(f"Tiles: {repeat_x}x{repeat_y}")
            print(f"Tile size: {actual_tile_width}x{actual_tile_height} (mode: {'adaptive' if adaptive_height or adaptive_width else 'fixed'})")
            print(f"Shrink: {shrink_percent}%")
            print(f"Rotation: {rotation}°")
            print(f"Image in tile: {img.width}x{img.height}")
            print(f"Background color: {color_display}")
            return pattern

def main():
    parser = argparse.ArgumentParser(
        description='Create repeating pattern with offset, shrink, and rotation (no cropping)'
    )
    parser.add_argument('--input', required=True, help='Input image path')
    parser.add_argument('--output', required=True, help='Output image path')
    parser.add_argument('--tile-width', type=int, required=True, help='Width of each tile')
    parser.add_argument('--tile-height', type=int, required=True, help='Height of each tile')
    parser.add_argument('--offset', type=int, required=True, help='Horizontal offset per row (pixels)')
    parser.add_argument('--repeat-x', type=int, default=4,
                       help='Number of repeats horizontally (default: 4)')
    parser.add_argument('--repeat-y', type=int, default=4,
                       help='Number of repeats vertically (default: 4)')
    parser.add_argument('--shrink', type=int, default=100,
                       help='Shrink percentage (1-100). 100 = full size (default: 100)')
    parser.add_argument('--rotation', type=float, default=0,
                       help='Rotation angle in degrees (0-360) (default: 0)')
    parser.add_argument('--adaptive-height', action='store_true',
                       help='Enable adaptive tile height (uses image height, eliminates vertical gaps)')
    parser.add_argument('--adaptive-width', action='store_true',
                       help='Enable adaptive tile width (uses image width)')
    parser.add_argument('--background-color', type=str, default=None,
                       help='Background color: hex (e.g., #FFFFFF or FFFFFF) or '
                            'color name (e.g., white, black, red, blue, etc.). '
                            'If not provided, uses transparent background.')

    args = parser.parse_args()

    # Validate shrink percentage
    if args.shrink < 1 or args.shrink > 100:
        print("Error: Shrink percentage must be between 1 and 100")
        sys.exit(1)

    # Validate rotation
    if args.rotation < 0 or args.rotation >= 360:
        print("Error: Rotation must be between 0 and 360")
        sys.exit(1)

    try:
        create_pattern_full_rotation(
            args.input, args.output,
            args.tile_width, args.tile_height,
            args.offset, args.repeat_x, args.repeat_y,
            args.shrink, args.rotation,
            adaptive_height=args.adaptive_height,
            adaptive_width=args.adaptive_width,
            background_color=args.background_color
        )
    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
