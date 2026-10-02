#!/usr/bin/env python3
"""
Stage 3 — Overlay Composer
Composites three per-index overlay layers onto a base image and writes the
result to COMPOSITES_DIR from paths.py.
"""

from PIL import Image, ImageChops
import argparse
import os
import sys
import json
from pathlib import Path

from paths import (
    FLAGS_DIR,
    TEXT_IMAGES_DIR,
    COMPOSITES_DIR,
    BASE_IMAGE,
    COMPANIONS_DIR,
)

# =============== USER CONFIGURABLE VARIABLES ===============

OVERLAY_CONFIGS = [
    # Pass 1: Companion photos
    {
        "name": "companion_photos",
        "input_dir": str(COMPANIONS_DIR),
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

    # Pass 2: Flags
    {
        "name": "flags",
        "input_dir": str(FLAGS_DIR),
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

    # Pass 3: Generated images
    {
        "name": "generated_images",
        "input_dir": str(TEXT_IMAGES_DIR),
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

BASE_IMAGE_PATH = str(BASE_IMAGE)
OUTPUT_DIRECTORY = str(COMPOSITES_DIR)
OUTPUT_SUFFIX = "_composite"
SKIP_EXISTING = False

# =============== END USER CONFIGURABLE VARIABLES ===============


def calculate_overlay_size(base_image, overlay_image, use_fixed_size=False,
                          fixed_width=200, fixed_height=200, max_percentage=0.333):
    if use_fixed_size:
        return fixed_width, fixed_height

    base_width, base_height = base_image.size
    overlay_width, overlay_height = overlay_image.size

    max_width = int(base_width * max_percentage)
    max_height = int(base_height * max_percentage)

    width_ratio = max_width / overlay_width
    height_ratio = max_height / overlay_height
    scale_factor = min(width_ratio, height_ratio)

    if scale_factor > 1:
        return overlay_width, overlay_height

    new_width = int(overlay_width * scale_factor)
    new_height = int(overlay_height * scale_factor)
    return new_width, new_height


def get_overlay_position(base_width, base_height, overlay_width, overlay_height,
                        position='center', custom_x=None, custom_y=None):
    positions = {
        'center': ((base_width - overlay_width) // 2, (base_height - overlay_height) // 2),
        'top-left': (0, 0),
        'top-right': (base_width - overlay_width, 0),
        'bottom-left': (0, base_height - overlay_height),
        'bottom-right': (base_width - overlay_width, base_height - overlay_height),
    }

    if custom_x is not None and custom_y is not None:
        return custom_x, custom_y

    pos = position.lower() if position else 'center'
    return positions.get(pos, positions['center'])


def rotate_image(image, angle, expand=True):
    if angle == 0:
        return image, image.size
    rotated = image.rotate(angle, expand=expand, resample=Image.Resampling.BICUBIC)
    return rotated, rotated.size


def get_visible_region_mask(base_image, overlay_position, overlay_size):
    base_width, base_height = base_image.size
    overlay_width, overlay_height = overlay_size
    x, y = overlay_position

    base_alpha = base_image.split()[3]

    left = max(0, x)
    top = max(0, y)
    right = min(base_width, x + overlay_width)
    bottom = min(base_height, y + overlay_height)

    if left >= right or top >= bottom:
        return None

    visible_mask = base_alpha.crop((left, top, right, bottom))
    full_mask = Image.new('L', (overlay_width, overlay_height), 0)

    paste_x = max(0, -x)
    paste_y = max(0, -y)
    full_mask.paste(visible_mask, (paste_x, paste_y))

    return full_mask


def process_single_overlay(base_image, overlay_image, position='center',
                          max_percentage=0.333, opacity=1.0, trim_to_base=True,
                          custom_x=None, custom_y=None, use_fixed_size=False,
                          fixed_width=200, fixed_height=200, rotation_angle=0,
                          rotation_expand=True):
    new_width, new_height = calculate_overlay_size(
        base_image, overlay_image, use_fixed_size, fixed_width, fixed_height, max_percentage
    )

    overlay_resized = overlay_image.resize((new_width, new_height), Image.Resampling.LANCZOS)

    if rotation_angle != 0:
        overlay_rotated, (rotated_width, rotated_height) = rotate_image(
            overlay_resized, rotation_angle, rotation_expand
        )
    else:
        overlay_rotated = overlay_resized
        rotated_width, rotated_height = new_width, new_height

    base_width, base_height = base_image.size
    x, y = get_overlay_position(
        base_width, base_height, rotated_width, rotated_height,
        position, custom_x, custom_y
    )

    if trim_to_base:
        visibility_mask = get_visible_region_mask(base_image, (x, y), (rotated_width, rotated_height))
        if visibility_mask is not None:
            overlay_alpha = overlay_rotated.split()[3]
            combined_alpha = ImageChops.multiply(overlay_alpha, visibility_mask)
            r, g, b, _ = overlay_rotated.split()
            overlay_final = Image.merge('RGBA', (r, g, b, combined_alpha))
        else:
            overlay_final = Image.new('RGBA', (rotated_width, rotated_height), (0, 0, 0, 0))
    else:
        overlay_final = overlay_rotated

    if opacity < 1.0:
        alpha = overlay_final.split()[3]
        alpha = alpha.point(lambda p: int(p * opacity))
        overlay_final.putalpha(alpha)

    return overlay_final, (x, y), (new_width, new_height, rotated_width, rotated_height)


def get_image_files(directory):
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    image_files = []
    if not os.path.exists(directory):
        return []
    for file in Path(directory).iterdir():
        if file.is_file() and file.suffix.lower() in image_extensions:
            image_files.append(file)
    return sorted(image_files)


def create_composite_image(base_image, overlay_files_by_config, configs, output_path):
    result = base_image.copy()
    overlay_details = []

    for config_idx, (config, overlay_path) in enumerate(zip(configs, overlay_files_by_config)):
        if overlay_path is None:
            continue

        try:
            overlay = Image.open(overlay_path).convert('RGBA')
            overlay_processed, position, sizes = process_single_overlay(
                result,
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

    result.save(output_path)
    return overlay_details


def process_parallel_overlays(base_image_path, configs, output_dir, output_suffix="_composite", skip_existing=False):
    if not os.path.exists(base_image_path):
        print(f"Error: Base image '{base_image_path}' not found")
        return False

    try:
        base = Image.open(base_image_path).convert('RGBA')
    except Exception as e:
        print(f"Error opening base image: {e}")
        return False

    os.makedirs(output_dir, exist_ok=True)

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

    for idx in range(max_files):
        overlay_files = []
        has_files = False

        for config_idx, files in enumerate(all_image_files):
            if idx < len(files):
                overlay_files.append(files[idx])
                has_files = True
            else:
                overlay_files.append(None)

        if not has_files:
            continue

        first_file = next((f for f in overlay_files if f is not None), None)
        if first_file:
            stem = first_file.stem
            suffix = first_file.suffix
            output_filename = f"{stem}{output_suffix}{suffix}"
        else:
            output_filename = f"composite_{idx:04d}{output_suffix}.png"

        output_path = os.path.join(output_dir, output_filename)

        if skip_existing and os.path.exists(output_path):
            print(f"Skipping composite {idx+1}/{max_files} ({output_filename}) - already exists")
            skipped += 1
            continue

        try:
            overlay_details = create_composite_image(
                base, overlay_files, configs, output_path
            )

            overlay_names = [d['name'] for d in overlay_details]
            print(f"✓ Composite {idx+1}/{max_files}: {output_filename}")
            print(f"  Overlays applied: {', '.join(overlay_names)}")

            missing = [configs[i]['name'] for i, f in enumerate(overlay_files) if f is None]
            if missing:
                print(f"  Skipped (no file): {', '.join(missing)}")

            processed += 1

        except Exception as e:
            print(f"✗ Error creating composite {idx+1}/{max_files}: {e}")
            failed += 1

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
  %(prog)s
  %(prog)s -base custom_base.png
  %(prog)s -config overlays.json
  %(prog)s -o /tmp/custom_output
        """
    )

    parser.add_argument('-base', '--base-image', help='Override the base image path')
    parser.add_argument('-config', '--config-file', help='JSON config file with overlay configurations')
    parser.add_argument('-o', '--output-dir', help='Override output directory')
    parser.add_argument('--skip-existing', action='store_true',
                       help='Skip processing if output file already exists')

    args = parser.parse_args()

    if args.config_file:
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

    if args.base_image:
        base_image_path = args.base_image

    output_dir = args.output_dir if args.output_dir else OUTPUT_DIRECTORY
    skip_existing = args.skip_existing if args.skip_existing else SKIP_EXISTING

    if not overlay_configs:
        print("Error: No overlay configurations found")
        sys.exit(1)

    print(f"Parallel overlay compositing")
    print(f"Base image: {base_image_path}")
    print(f"Number of overlay layers: {len(overlay_configs)}")
    print(f"Output directory: {output_dir}")
    print("-" * 60)

    process_parallel_overlays(
        base_image_path,
        overlay_configs,
        output_dir,
        OUTPUT_SUFFIX,
        skip_existing
    )


if __name__ == "__main__":
    main()
