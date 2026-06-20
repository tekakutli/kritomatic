#!/usr/bin/env python3
"""
Create a repeating pattern from a single input image with line offset, shrink, and rotation.
The pattern wraps around properly with no empty spaces on the left.
All transformations maintain the final image dimensions.
The rotated image is cropped to maintain the original dimensions.

Two modes available:
- Fixed: Uses the specified tile height (original behavior)
- Adaptive: Adjusts tile height based on image aspect ratio for tighter grids

Variation modes:
- Single image with N variations rotated at equal intervals
- The original image is included as the first variation (0° rotation)
- Additional variations are rotated by multiples of 360/N degrees
- Images are cropped after rotation to maintain the same dimensions
"""

import sys
import argparse
from wand.image import Image
from wand.color import Color
import math
import random

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

def process_image_with_rotation(original_img, tile_width, tile_height, rotation_angle, shrink_percent):
    """
    Process an image: apply shrink, rotate with cropping, maintain original size.
    The image keeps its dimensions (after shrink) and is cropped during rotation.
    """
    # Create a fresh copy of the image
    with Image(original_img) as img:
        # Apply shrink to the original dimensions first
        shrink_ratio = shrink_percent / 100.0

        # If we have a shrink percentage, resize the original image
        if shrink_percent < 100:
            new_width = int(img.width * shrink_ratio)
            new_height = int(img.height * shrink_ratio)
            img.resize(new_width, new_height)

        # Store the current dimensions (these will be our target crop size)
        crop_width = img.width
        crop_height = img.height

        # Apply rotation if needed
        if rotation_angle != 0:
            # Rotate the image (this will enlarge the canvas)
            img.rotate(rotation_angle, background=Color('transparent'))

            # Now crop the rotated image back to the original dimensions
            # Center the crop
            crop_x = (img.width - crop_width) // 2
            crop_y = (img.height - crop_height) // 2

            # Ensure we don't crop outside the image bounds
            crop_x = max(0, min(crop_x, img.width - crop_width))
            crop_y = max(0, min(crop_y, img.height - crop_height))

            # Crop to original dimensions
            img.crop(crop_x, crop_y, crop_width, crop_height)

        # Return the image as-is (no resizing to fit tile)
        # The image will be placed in the tile with centering
        return img.clone()

def create_rotated_variations(input_path, num_variations, tile_width, tile_height,
                             shrink_percent, base_rotation, adaptive_height):
    """
    Create variations of the input image by rotating it at equal intervals.
    Each variation is cropped to maintain the same dimensions.

    Args:
        num_variations: Number of variations (1 = original only, 2 = original + 180°,
                       4 = original + 90° + 180° + 270°, etc.)

    Returns:
        List of processed image variations with their offsets
    """
    variations = []

    # Load the original image
    with Image(filename=input_path) as original_img:
        print(f"Loading image: {input_path} ({original_img.width}x{original_img.height})")

        # Store a clone of the original for reuse
        original_clone = original_img.clone()

    # Now process variations using the cloned original
    for i in range(num_variations):
        # Calculate rotation angle for this variation
        if num_variations == 1:
            angle = base_rotation
        else:
            angle = base_rotation + (i * 360.0 / num_variations)

        # Normalize angle to 0-360
        angle = angle % 360

        # Process the image with rotation and cropping (no resizing to fit tile)
        processed_img = process_image_with_rotation(
            original_clone, tile_width, tile_height, angle, shrink_percent
        )

        # Calculate tile height based on mode
        if adaptive_height:
            image_aspect = processed_img.width / processed_img.height

            if image_aspect > 1:
                padding_ratio = 1.1
                actual_tile_height = int(processed_img.height * padding_ratio)
            else:
                actual_tile_height = tile_height

            actual_tile_height = max(1, actual_tile_height)
        else:
            actual_tile_height = tile_height

        # Center the image within the tile
        # If the image is larger than the tile, we'll crop it
        # If it's smaller, we'll center it
        x_offset_center = (tile_width - processed_img.width) // 2
        y_offset_center = (actual_tile_height - processed_img.height) // 2

        # Store variation info with a clone to keep it open
        variations.append({
            'image': processed_img.clone(),
            'x_offset': x_offset_center,
            'y_offset': y_offset_center,
            'tile_height': actual_tile_height,
            'angle': angle,
            'width': processed_img.width,
            'height': processed_img.height
        })

        print(f"  Variation {i+1}: rotation {angle:.1f}° -> {processed_img.width}x{processed_img.height}")

        # Close the processed image to free memory
        processed_img.close()

    # Close the original clone
    original_clone.close()

    return variations

def create_pattern_with_variations(input_path, output_path, tile_width, tile_height, offset,
                                  repeat_x=4, repeat_y=4, shrink_percent=100, rotation=0,
                                  adaptive_height=False, background_color=None,
                                  num_variations=1, image_mode='sequential', seed=None):
    """
    Create a repeating pattern using rotated variations of a single image.

    Args:
        num_variations: Number of variations to create (1 = original only)
        image_mode: 'sequential' or 'random'
        seed: Random seed for reproducibility (only for random mode)
        adaptive_height: If True, adjusts tile height based on image aspect ratio
        background_color: Optional color string
    """

    # Set random seed if provided
    if seed is not None:
        random.seed(seed)

    # Store the original tile height for reference
    actual_tile_height = tile_height

    # Create rotated variations
    variations = create_rotated_variations(
        input_path, num_variations, tile_width, tile_height,
        shrink_percent, rotation, adaptive_height
    )

    if not variations:
        print("Error: No variations created")
        sys.exit(1)

    # Update actual_tile_height (use the maximum if adaptive)
    if adaptive_height:
        for var in variations:
            actual_tile_height = max(actual_tile_height, var['tile_height'])
    else:
        actual_tile_height = tile_height

    print(f"\nCreated {len(variations)} variations")
    print(f"Tile height: {actual_tile_height} (mode: {'adaptive' if adaptive_height else 'fixed'})")
    print(f"Image mode: {image_mode}")

    # Final dimensions - use actual tile height
    final_width = tile_width * repeat_x
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
            pattern.alpha_channel = 'remove'
        else:
            pattern.alpha_channel = 'activate'

        # Create a clean background
        with Image(width=final_width, height=final_height, background=bg_color) as background:
            pattern.composite(background, left=0, top=0)

        # Place tiles with wrapping
        variation_index = 0

        for row in range(repeat_y):
            row_offset = (row * offset) % tile_width
            start_col = -1
            end_col = repeat_x + 1

            for col in range(start_col, end_col):
                # Select variation based on mode
                if image_mode == 'random':
                    var = random.choice(variations)
                else:
                    # Sequential selection (cycling through variations)
                    var = variations[variation_index % len(variations)]
                    variation_index += 1

                img = var['image']
                x_offset_center = var['x_offset']
                y_offset_center = var['y_offset']

                # Recalculate y_offset_center if tile height differs
                if adaptive_height:
                    y_offset_center = (actual_tile_height - img.height) // 2

                x_pos = col * tile_width - row_offset + x_offset_center
                y_pos = row * actual_tile_height + y_offset_center

                # Only place if it overlaps with the final image
                if x_pos + img.width > 0 and x_pos < final_width:
                    pattern.composite(img, left=x_pos, top=y_pos)

        pattern.save(filename=output_path)
        print(f"\nPattern saved to {output_path}")
        print(f"Size: {final_width}x{final_height}")
        print(f"Tiles: {repeat_x}x{repeat_y}")
        print(f"Tile size: {tile_width}x{actual_tile_height}")
        print(f"Shrink: {shrink_percent}%")
        print(f"Base rotation: {rotation}°")
        print(f"Variations: {num_variations}")
        print(f"Background color: {color_display}")
        return pattern

def main():
    parser = argparse.ArgumentParser(
        description='Create repeating pattern with rotated variations of a single image'
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
                       help='Base rotation angle in degrees (0-360) (default: 0)')
    parser.add_argument('--adaptive-height', action='store_true',
                       help='Enable adaptive tile height for tighter grids on wide images')
    parser.add_argument('--fixed-height', action='store_true',
                       help='Use fixed tile height (default behavior)')
    parser.add_argument('--background-color', type=str, default=None,
                       help='Background color: hex (e.g., #FFFFFF or FFFFFF) or '
                            'color name (e.g., white, black, red, blue, etc.). '
                            'If not provided, uses transparent background.')
    parser.add_argument('--variations', type=int, default=1,
                       help='Number of rotated variations (1=original only, 2=original+180°, '
                            '4=original+90°+180°+270°, etc.) (default: 1)')
    parser.add_argument('--image-mode', choices=['sequential', 'random'], default='sequential',
                       help='Image selection mode: sequential (cycle through variations) or random (default: sequential)')
    parser.add_argument('--seed', type=int, default=None,
                       help='Random seed for reproducibility (only used with --image-mode random)')

    args = parser.parse_args()

    # Determine height mode
    if args.adaptive_height:
        adaptive_mode = True
    elif args.fixed_height:
        adaptive_mode = False
    else:
        adaptive_mode = False

    # Validate shrink percentage
    if args.shrink < 1 or args.shrink > 100:
        print("Error: Shrink percentage must be between 1 and 100")
        sys.exit(1)

    # Validate rotation
    if args.rotation < 0 or args.rotation >= 360:
        print("Error: Base rotation must be between 0 and 360")
        sys.exit(1)

    # Validate variations
    if args.variations < 1:
        print("Error: Number of variations must be at least 1")
        sys.exit(1)

    try:
        create_pattern_with_variations(
            args.input, args.output,
            args.tile_width, args.tile_height,
            args.offset, args.repeat_x, args.repeat_y,
            args.shrink, args.rotation,
            adaptive_height=adaptive_mode,
            background_color=args.background_color,
            num_variations=args.variations,
            image_mode=args.image_mode,
            seed=args.seed
        )
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
