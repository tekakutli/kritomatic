#!/usr/bin/env python3
"""
Overlay multiple images onto a single base image with parallel sequential overlays.
Matches images by index across multiple overlay directories.
"""

from PIL import Image, ImageChops, ImageDraw
import argparse
import os
import sys
import math
import json
from pathlib import Path


# =============== USER CONFIGURABLE VARIABLES ===============
# Define overlay configurations - images will be matched by index across all configs

OVERLAY_CONFIGS = [
    # Pass 1: Companion photos (index 0)
    {
        "name": "companion_photos",
        "input_dir": "/home/tekakutli/Documents/pera/FotosCompaneros",
        "position": "center",
        "custom_x": None,
        "custom_y": None,
        "use_fixed_size": False,
        "fixed_width": 200,
        "fixed_height": 200,
        "size_percentage": 0.8,
        "rotation_angle": 0,
        "rotation_expand": True,
        "opacity": 1.0,
        "trim_to_base": True,
    },

    # Pass 2: Flags (index 1)
    {
        "name": "flags",
        "input_dir": "/tmp/flags",
        "position": "center",
        "custom_x": 25,
        "custom_y": 110,
        "use_fixed_size": False,
        "fixed_width": 200,
        "fixed_height": 200,
        "size_percentage": 0.23,
        "rotation_angle": 0,
        "rotation_expand": True,
        "opacity": 1.0,
        "trim_to_base": True,
    },

    # Pass 3: Generated images (index 2)
    {
        "name": "generated_images",
        "input_dir": "/tmp/generated_images/",
        "position": "center",
        "custom_x": 190,
        "custom_y": 160,
        "use_fixed_size": False,
        "fixed_width": 200,
        "fixed_height": 200,
        "size_percentage": 0.23,
        "rotation_angle": 45,
        "rotation_expand": True,
        "opacity": 1.0,
        "trim_to_base": True,
    },
]

# Global settings
BASE_IMAGE_PATH = "/tmp/00494f9e044d6f67fd38bc7f787312ab.png"
OUTPUT_DIRECTORY = "/tmp/output"
OUTPUT_SUFFIX = "_composite"  # Suffix for output filenames
SKIP_EXISTING = False

# =============== END USER CONFIGURABLE VARIABLES ===============


def calculate_overlay_size(base_image, overlay_image, use_fixed_size=False,
                          fixed_width=200, fixed_height=200, max_percentage=0.333):
    """
    Calculate the new size for the overlay image while maintaining aspect ratio.
    """
    if use_fixed_size:
        return fixed_width, fixed_height

    base_width, base_height = base_image.size
    overlay_width, overlay_height = overlay_image.size

    # Calculate maximum allowed dimensions
    max_width = int(base_width * max_percentage)
    max_height = int(base_height * max_percentage)

    # Calculate scaling factors
    width_ratio = max_width / overlay_width
    height_ratio = max_height / overlay_height

    # Use the smaller ratio to ensure both dimensions fit
    scale_factor = min(width_ratio, height_ratio)

    # If overlay is already smaller than max size, keep original size
    if scale_factor > 1:
        return overlay_width, overlay_height

    new_width = int(overlay_width * scale_factor)
    new_height = int(overlay_height * scale_factor)

    return new_width, new_height


def get_overlay_position(base_width, base_height, overlay_width, overlay_height,
                        position='center', custom_x=None, custom_y=None):
    """
    Calculate the overlay position based on position string or custom coordinates.
    """
    positions = {
        'center': ((base_width - overlay_width) // 2, (base_height - overlay_height) // 2),
        'top-left': (0, 0),
        'top-right': (base_width - overlay_width, 0),
        'bottom-left': (0, base_height - overlay_height),
        'bottom-right': (base_width - overlay_width, base_height - overlay_height),
    }

    # Check for custom coordinates first
    if custom_x is not None and custom_y is not None:
        return custom_x, custom_y

    # Use position string
    pos = position.lower() if position else 'center'
    return positions.get(pos, positions['center'])


def rotate_image(image, angle, expand=True):
    """
    Rotate an image by a given angle.
    """
    if angle == 0:
        return image, image.size

    rotated = image.rotate(angle, expand=expand, resample=Image.Resampling.BICUBIC)
    return rotated, rotated.size


def get_visible_region_mask(base_image, overlay_position, overlay_size):
    """
    Create a mask that represents the visible (non-transparent) region of the base image
    at the overlay's position.
    """
    base_width, base_height = base_image.size
    overlay_width, overlay_height = overlay_size
    x, y = overlay_position

    # Extract the base alpha channel
    base_alpha = base_image.split()[3]

    # Crop the alpha channel to the overlay region
    left = max(0, x)
    top = max(0, y)
    right = min(base_width, x + overlay_width)
    bottom = min(base_height, y + overlay_height)

    # If overlay is completely outside the base image
    if left >= right or top >= bottom:
        return None

    # Crop the alpha channel to the region where overlay will be placed
    visible_mask = base_alpha.crop((left, top, right, bottom))

    # Create a full size mask (same as overlay size) with zeros
    full_mask = Image.new('L', (overlay_width, overlay_height), 0)

    # Calculate where the visible region starts within the overlay
    paste_x = max(0, -x)
    paste_y = max(0, -y)

    # Paste the visible mask at the correct position
    full_mask.paste(visible_mask, (paste_x, paste_y))

    return full_mask


def process_single_overlay(base_image, overlay_image, position='center',
                          max_percentage=0.333, opacity=1.0, trim_to_base=True,
                          custom_x=None, custom_y=None, use_fixed_size=False,
                          fixed_width=200, fixed_height=200, rotation_angle=0,
                          rotation_expand=True):
    """
    Process a single overlay image and return it ready for compositing.
    """
    # Calculate the overlay size
    new_width, new_height = calculate_overlay_size(
        base_image, overlay_image, use_fixed_size, fixed_width, fixed_height, max_percentage
    )

    # Resize overlay
    overlay_resized = overlay_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    # Apply rotation if needed
    if rotation_angle != 0:
        overlay_rotated, (rotated_width, rotated_height) = rotate_image(
            overlay_resized, rotation_angle, rotation_expand
        )
    else:
        overlay_rotated = overlay_resized
        rotated_width, rotated_height = new_width, new_height

    # Calculate position
    base_width, base_height = base_image.size

    # Use rotated size for positioning
    x, y = get_overlay_position(
        base_width, base_height, rotated_width, rotated_height,
        position, custom_x, custom_y
    )

    # Trim to base visibility if requested
    if trim_to_base:
        # Get the visible region mask from the base image
        visibility_mask = get_visible_region_mask(base_image, (x, y), (rotated_width, rotated_height))

        # If overlay is completely outside visible area, return empty image
        if visibility_mask is not None:
            # Apply the visibility mask to the overlay's alpha channel
            overlay_alpha = overlay_rotated.split()[3]

            # Combine the overlay's own alpha with the visibility mask
            combined_alpha = ImageChops.multiply(overlay_alpha, visibility_mask)

            # Create a new overlay with the combined alpha
            r, g, b, _ = overlay_rotated.split()
            overlay_final = Image.merge('RGBA', (r, g, b, combined_alpha))
        else:
            # Return fully transparent image if outside visible area
            overlay_final = Image.new('RGBA', (rotated_width, rotated_height), (0, 0, 0, 0))
    else:
        overlay_final = overlay_rotated

    # Apply opacity if needed
    if opacity < 1.0:
        alpha = overlay_final.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        overlay_final.putalpha(alpha)

    return overlay_final, (x, y), (new_width, new_height, rotated_width, rotated_height)


def get_image_files(directory):
    """
    Get all image files in a directory, sorted alphabetically.
    """
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    image_files = []

    if not os.path.exists(directory):
        return []

    for file in Path(directory).iterdir():
        if file.is_file() and file.suffix.lower() in image_extensions:
            image_files.append(file)

    return sorted(image_files)


def create_composite_image(base_image, overlay_files_by_config, configs, output_path):
    """
    Create a composite image by applying all overlays for a specific index.
    """
    # Start with a copy of the base image
    result = base_image.copy()
    overlay_details = []

    # Process each overlay config for this index
    for config_idx, (config, overlay_path) in enumerate(zip(configs, overlay_files_by_config)):
        if overlay_path is None:
            # Skip if no overlay file for this index
            continue

        try:
            # Load overlay image
            overlay = Image.open(overlay_path).convert('RGBA')

            # Process the overlay
            overlay_processed, position, sizes = process_single_overlay(
                result,  # Use current result as base for visibility trimming
                overlay,
                position=config.get('position', 'center'),
                max_percentage=config.get('size_percentage', 0.333),
                opacity=config.get('opacity', 1.0),
                trim_to_base=config.get('trim_to_base', True),
                custom_x=config.get('custom_x'),
                custom_y=config.get('custom_y'),
                use_fixed_size=config.get('use_fixed_size', False),
                fixed_width=config.get('fixed_width', 200),
                fixed_height=config.get('fixed_height', 200),
                rotation_angle=config.get('rotation_angle', 0),
                rotation_expand=config.get('rotation_expand', True)
            )

            # Paste overlay with alpha channel
            result.paste(overlay_processed, position, overlay_processed)

            orig_w, orig_h, rotated_w, rotated_h = sizes
            overlay_details.append({
                'name': config['name'],
                'file': overlay_path.name,
                'position': position,
                'original_size': (orig_w, orig_h),
                'rotated_size': (rotated_w, rotated_h)
            })

        except Exception as e:
            print(f"  ✗ Error processing overlay {config['name']} from {overlay_path.name}: {e}")
            continue

    # Save result
    result.save(output_path)
    return overlay_details


def process_parallel_overlays(base_image_path, configs, output_dir, output_suffix="_composite", skip_existing=False):
    """
    Process all overlays in parallel, matching by index across all configs.
    """
    # Validate base image
    if not os.path.exists(base_image_path):
        print(f"Error: Base image '{base_image_path}' not found")
        return False

    # Load base image once
    try:
        base = Image.open(base_image_path).convert('RGBA')
    except Exception as e:
        print(f"Error opening base image: {e}")
        return False

    # Create output directory
    os.makedirs(output_dir, exist_ok=True)

    # Get all image files from each config directory
    all_image_files = []
    max_files = 0

    print(f"Loading overlay files from {len(configs)} directories...")
    for i, config in enumerate(configs):
        input_dir = config.get('input_dir')
        if not input_dir:
            print(f"Warning: Config '{config.get('name', i)}' has no input_dir")
            all_image_files.append([])
            continue

        files = get_image_files(input_dir)
        all_image_files.append(files)
        max_files = max(max_files, len(files))
        print(f"  {config['name']}: {len(files)} files")

    if max_files == 0:
        print("Error: No image files found in any input directory")
        return False

    print(f"\nCreating composites for {max_files} image sets...")
    print("-" * 60)

    processed = 0
    skipped = 0
    failed = 0

    # Process each index
    for idx in range(max_files):
        # Get the overlay file for each config at this index
        overlay_files = []
        has_files = False

        for config_idx, files in enumerate(all_image_files):
            if idx < len(files):
                overlay_files.append(files[idx])
                has_files = True
            else:
                overlay_files.append(None)  # No file for this config at this index

        if not has_files:
            continue

        # Create output filename
        # Use the first available file's name as base for the output filename
        first_file = next((f for f in overlay_files if f is not None), None)
        if first_file:
            stem = first_file.stem
            suffix = first_file.suffix
            output_filename = f"{stem}{output_suffix}{suffix}"
        else:
            output_filename = f"composite_{idx:04d}{output_suffix}.png"

        output_path = os.path.join(output_dir, output_filename)

        # Skip if output already exists and skip_existing is True
        if skip_existing and os.path.exists(output_path):
            print(f"Skipping composite {idx+1}/{max_files} ({output_filename}) - already exists")
            skipped += 1
            continue

        try:
            # Create composite
            overlay_details = create_composite_image(
                base, overlay_files, configs, output_path
            )

            # Print summary
            overlay_names = [d['name'] for d in overlay_details]
            print(f"✓ Composite {idx+1}/{max_files}: {output_filename}")
            print(f"  Overlays applied: {', '.join(overlay_names)}")

            # Check if any overlays were skipped due to missing files
            missing = [configs[i]['name'] for i, f in enumerate(overlay_files) if f is None]
            if missing:
                print(f"  Skipped (no file): {', '.join(missing)}")

            processed += 1

        except Exception as e:
            print(f"✗ Error creating composite {idx+1}/{max_files}: {e}")
            failed += 1

    # Print summary
    print("-" * 60)
    print(f"Summary:")
    print(f"  Composites created: {processed}")
    if skipped > 0:
        print(f"  Skipped: {skipped}")
    if failed > 0:
        print(f"  Failed: {failed}")
    print(f"  Total image sets: {max_files}")
    print(f"\nOutput saved to: {output_dir}")

    return True


def main():
    parser = argparse.ArgumentParser(
        description='Create composite images by matching overlays by index across multiple directories',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Use configurations defined at top of script
  %(prog)s

  # Override base image
  %(prog)s -base custom_base.png

  # Use JSON config file
  %(prog)s -config overlays.json

  # Override output directory
  %(prog)s -o /tmp/custom_output
        """
    )

    parser.add_argument('-base', '--base-image',
                       help='Override the base image path')
    parser.add_argument('-config', '--config-file',
                       help='JSON config file with overlay configurations')
    parser.add_argument('-o', '--output-dir',
                       help='Override output directory')
    parser.add_argument('--skip-existing',
                       action='store_true',
                       help='Skip processing if output file already exists')

    args = parser.parse_args()

    # Get configurations
    if args.config_file:
        # Load from JSON file
        try:
            with open(args.config_file, 'r') as f:
                configs = json.load(f)
                if isinstance(configs, dict) and 'overlay_configs' in configs:
                    overlay_configs = configs['overlay_configs']
                    base_image_path = configs.get('base_image', BASE_IMAGE_PATH)
                else:
                    overlay_configs = configs
                    base_image_path = BASE_IMAGE_PATH
        except Exception as e:
            print(f"Error loading config file: {e}")
            sys.exit(1)
    else:
        overlay_configs = OVERLAY_CONFIGS
        base_image_path = BASE_IMAGE_PATH

    # Override base image if specified
    if args.base_image:
        base_image_path = args.base_image

    # Override output directory if specified
    output_dir = args.output_dir if args.output_dir else OUTPUT_DIRECTORY

    # Set skip existing
    skip_existing = args.skip_existing if args.skip_existing else SKIP_EXISTING

    if not overlay_configs:
        print("Error: No overlay configurations found")
        sys.exit(1)

    print(f"Parallel overlay compositing")
    print(f"Base image: {base_image_path}")
    print(f"Number of overlay layers: {len(overlay_configs)}")
    print(f"Output directory: {output_dir}")
    print("-" * 60)

    # Process the parallel overlays
    process_parallel_overlays(
        base_image_path,
        overlay_configs,
        output_dir,
        OUTPUT_SUFFIX,
        skip_existing
    )


if __name__ == "__main__":
    main()
