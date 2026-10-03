#!/usr/bin/env python3
"""
fg2canvas_rmbg.py

Remove an image's background, then place the foreground on a canvas painted
with the image's original background color (or a custom color).

The script:
  1. Removes the background using a ComfyUI RMBG workflow (the file set in
     DEFAULT_WORKFLOW, next to this script). The intermediate result is
     written next to the input with a "_fg" suffix.
  2. Determines the background color: either detects it from the edges of the
     original image, or uses CUSTOM_HEX_COLOR if set.
  3. Either expands the canvas around the foreground (default) or just fills
     the background in place (if SKIP_EXPANSION is True).
  4. Composites the foreground onto the result.

The final output is written next to the input, with a suffix derived from the
chosen operation ("_expanded.png" or "_filled.png").

By default, the input image path is taken from the IMAGE_PATH variable below.
If a positional argument is given on the command line, it overrides
IMAGE_PATH.

The only flag is --color / -c, which overrides CUSTOM_HEX_COLOR.

Usage:
    python fg2canvas_rmbg.py [image_path] [--color HEX]

Examples:
    # Use IMAGE_PATH, auto-detected color, expansion on
    python fg2canvas_rmbg.py

    # Override input path
    python fg2canvas_rmbg.py photo.png

    # Custom background color (overrides CUSTOM_HEX_COLOR)
    python fg2canvas_rmbg.py photo.png --color ff0000
"""

# ===== CONFIGURABLE SETTINGS =====
# Default input image; overridden by the positional argument
IMAGE_PATH = "path/to/image.png"

# Custom background color (hex). If empty, the original background color is
# detected from the image edges. Can be overridden per-run with --color.
CUSTOM_HEX_COLOR = ""

# Set to True to skip canvas expansion and just change the background color
SKIP_EXPANSION = False

# Expansion proportion relative to image dimensions (0 to 1)
EXPAND_PROPORTION = 0.5  # Add this much extra space around the image

# Set to True to expand proportionally in both dimensions, False to expand independently
EXPAND_BOTH_DIMENSIONS = True
EXPAND_WIDTH_PROPORTION = 0.5
EXPAND_HEIGHT_PROPORTION = 0.5

# Background color detection edge thickness
EDGE_THICKNESS = 10

# Enable debug output
DEBUG = False
# =================================

import subprocess
import sys
import os
import re
import json
import requests
import time
import tempfile
import shutil
import argparse
from pathlib import Path
from collections import Counter
from PIL import Image

# ComfyUI configuration
COMFYUI_URL = "http://127.0.0.1:8188"
SCRIPT_DIR = Path(__file__).parent
DEFAULT_WORKFLOW = SCRIPT_DIR / "comfy_workflow_rmbg.json"

def upload_image(file_path):
    """Upload an image to ComfyUI's server and return the filename."""
    url = f"{COMFYUI_URL}/upload/image"

    # Create unique filename using timestamp
    file_path_obj = Path(file_path)
    unique_name = f"{file_path_obj.stem}_{int(time.time())}{file_path_obj.suffix}"

    with open(file_path, 'rb') as f:
        files = {'image': (unique_name, f, 'image/png')}
        data = {'overwrite': 'true'}
        response = requests.post(url, files=files, data=data)

    if response.status_code == 200:
        return response.json()['name']
    else:
        raise Exception(f"Upload failed: {response.status_code}")

def queue_prompt(workflow):
    """Send a workflow to ComfyUI and get the prompt ID."""
    response = requests.post(f"{COMFYUI_URL}/prompt", json={"prompt": workflow})

    if response.status_code == 200:
        return response.json()['prompt_id']
    else:
        raise Exception(f"Queue failed: {response.status_code}")

def wait_for_completion(prompt_id):
    """Wait for a prompt to complete."""
    while True:
        response = requests.get(f"{COMFYUI_URL}/history/{prompt_id}")
        if response.status_code == 200:
            history = response.json()
            if prompt_id in history:
                return history[prompt_id]
        time.sleep(1)

def get_output_images(history):
    """Extract output images from workflow history."""
    images = []
    for node_out in history.get('outputs', {}).values():
        if 'images' in node_out:
            images.extend(node_out['images'])
    return images

def download_image(image_info, save_path):
    """Download an image from ComfyUI."""
    filename = image_info.get('filename')
    subfolder = image_info.get('subfolder', '')
    url = f"{COMFYUI_URL}/view?filename={filename}&subfolder={subfolder}&type=output"

    response = requests.get(url)
    if response.status_code == 200:
        with open(save_path, 'wb') as f:
            f.write(response.content)
        return True
    return False

def remove_background(image_path):
    """Remove background using ComfyUI RMBG workflow"""
    # Check if workflow exists
    if not DEFAULT_WORKFLOW.exists():
        print(f"Error: Workflow not found at {DEFAULT_WORKFLOW}")
        return None

    # Load workflow fresh each time
    with open(DEFAULT_WORKFLOW, 'r', encoding='utf-8') as f:
        workflow = json.load(f)

    # Upload image with unique name
    uploaded_filename = upload_image(image_path)

    # Find LoadImage node and set the image
    for node_id, node_data in workflow.items():
        if node_data.get('class_type') == 'LoadImage':
            workflow[node_id]['inputs']['image'] = uploaded_filename
            break

    # Queue workflow
    prompt_id = queue_prompt(workflow)

    # Wait for completion
    history = wait_for_completion(prompt_id)

    # Get output images
    images = get_output_images(history)
    if not images:
        print("Error: No output images generated")
        return None

    # Save intermediate output next to input with "_fg" suffix
    input_path = Path(image_path)
    output_path = input_path.parent / f"{input_path.stem}_fg.png"

    # Download and save
    if download_image(images[0], output_path):
        print(f"✓ Background removed successfully")
        if DEBUG:
            print(f"  Output: {output_path}")
        return str(output_path)
    else:
        print("Error: Failed to download output image")
        return None

def detect_background_color(image_path, edge_thickness=10):
    """Detect background color by sampling edges of the image."""
    # Open image and convert to RGB
    img = Image.open(image_path).convert('RGB')
    width, height = img.size

    # Collect pixels from all four edges
    edge_pixels = []

    # Top edge
    top_region = img.crop((0, 0, width, edge_thickness))
    edge_pixels.extend(list(top_region.getdata()))

    # Bottom edge
    bottom_region = img.crop((0, height - edge_thickness, width, height))
    edge_pixels.extend(list(bottom_region.getdata()))

    # Left edge (excluding corners already sampled)
    if height > 2 * edge_thickness:
        left_region = img.crop((0, edge_thickness, edge_thickness, height - edge_thickness))
        edge_pixels.extend(list(left_region.getdata()))

    # Right edge (excluding corners already sampled)
    if height > 2 * edge_thickness:
        right_region = img.crop((width - edge_thickness, edge_thickness, width, height - edge_thickness))
        edge_pixels.extend(list(right_region.getdata()))

    # Find the most common color
    color_counts = Counter(edge_pixels)
    most_common_color = color_counts.most_common(1)[0][0]

    # Convert to hex
    hex_color = '#{:02x}{:02x}{:02x}'.format(most_common_color[0], most_common_color[1], most_common_color[2])

    return hex_color

def validate_hex_color(hex_color):
    """Validate and normalize hex color string"""
    hex_color = hex_color.strip().upper()

    # Add # if missing
    if not hex_color.startswith('#'):
        hex_color = '#' + hex_color

    # Check format
    if re.match(r'^#[0-9A-Fa-f]{6}$', hex_color):
        return hex_color
    elif re.match(r'^#[0-9A-Fa-f]{3}$', hex_color):
        # Expand 3-digit hex to 6-digit
        hex_color = '#' + ''.join([c*2 for c in hex_color[1:]])
        return hex_color
    else:
        return None

def get_image_dimensions(image_path):
    """Get image dimensions using PIL"""
    try:
        with Image.open(image_path) as img:
            return img.width, img.height
    except Exception as e:
        print(f"Error getting image dimensions: {e}")
        return None, None

def change_background_color(foreground_path, hex_color):
    """Replace transparent background with solid color (no expansion)"""
    try:
        # Convert hex to RGB tuple
        hex_color = hex_color.lstrip('#')
        rgb_color = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

        # Open foreground image (with transparency)
        foreground = Image.open(foreground_path)

        # Create new canvas with same dimensions as foreground
        canvas = Image.new('RGB', (foreground.width, foreground.height), rgb_color)

        # Composite foreground onto canvas
        canvas.paste(foreground, (0, 0), foreground if foreground.mode == 'RGBA' else None)

        # Save output. Derive the filename from the original input stem by
        # stripping the intermediate "_fg" suffix, so an input named e.g.
        # "photo_output.png" produces "photo_output_filled.png" rather than
        # colliding with the substring "_output".
        p = Path(foreground_path)
        stem = p.stem[:-len("_fg")] if p.stem.endswith("_fg") else p.stem
        output_path = str(p.with_name(f"{stem}_filled{p.suffix}"))
        canvas.save(output_path, 'PNG')

        print(f"✓ Changed background color to {hex_color} (no expansion)")
        print(f"  Dimensions unchanged: {foreground.width}x{foreground.height}")
        print(f"  Saved to: {output_path}")

        return output_path

    except Exception as e:
        print(f"Error in change_background_color: {e}")
        return None

def expand_and_composite(original_path, foreground_path, hex_color, skip_expansion=False):
    """Expand canvas and composite foreground onto it using PIL"""
    if skip_expansion:
        return change_background_color(foreground_path, hex_color)

    try:
        # Get original dimensions
        width, height = get_image_dimensions(original_path)
        if width is None:
            return False

        # Calculate new dimensions
        if EXPAND_BOTH_DIMENSIONS:
            expand_width = EXPAND_PROPORTION
            expand_height = EXPAND_PROPORTION
        else:
            expand_width = EXPAND_WIDTH_PROPORTION
            expand_height = EXPAND_HEIGHT_PROPORTION

        new_width = int(width * (1 + expand_width))
        new_height = int(height * (1 + expand_height))

        if DEBUG:
            print(f"Original dimensions: {width}x{height}")
            print(f"New dimensions: {new_width}x{new_height}")
            print(f"Background color: {hex_color}")

        # Convert hex to RGB tuple
        hex_color = hex_color.lstrip('#')
        rgb_color = tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

        # Create new canvas with background color
        canvas = Image.new('RGB', (new_width, new_height), rgb_color)

        # Open foreground image (with transparency)
        foreground = Image.open(foreground_path)

        # Calculate position to center the foreground
        x_offset = (new_width - foreground.width) // 2
        y_offset = (new_height - foreground.height) // 2

        # Composite foreground onto canvas
        canvas.paste(foreground, (x_offset, y_offset), foreground if foreground.mode == 'RGBA' else None)

        # Save output. Same stem-derivation as change_background_color.
        p = Path(foreground_path)
        stem = p.stem[:-len("_fg")] if p.stem.endswith("_fg") else p.stem
        output_path = str(p.with_name(f"{stem}_expanded{p.suffix}"))
        canvas.save(output_path, 'PNG')

        print(f"✓ Expanded canvas from {width}x{height} to {new_width}x{new_height}")
        print(f"  Added {expand_width*100:.1f}% to width, {expand_height*100:.1f}% to height")
        print(f"  Background color: {hex_color}")
        print(f"  Saved to: {output_path}")

        return output_path

    except Exception as e:
        print(f"Error in expand_and_composite: {e}")
        return None

def process_image(image_path, custom_hex_color=None):
    """Main function: remove background, detect color (or use custom), expand canvas, composite"""
    # Check if input file exists
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return False, None, None

    # Step 1: Remove background
    print(f"Step 1: Removing background from: {image_path}")
    foreground_path = remove_background(image_path)

    if not foreground_path or not os.path.exists(foreground_path):
        print("Error: Failed to remove background")
        return False, None, None

    # Step 2: Determine background color
    if custom_hex_color:
        # Use custom color if provided
        hex_color = validate_hex_color(custom_hex_color)
        if hex_color:
            print(f"\nStep 2: Using custom background color: {hex_color}")
        else:
            print(f"\nStep 2: Invalid custom color '{custom_hex_color}', detecting automatically...")
            hex_color = detect_background_color(image_path, EDGE_THICKNESS)
            print(f"Detected background color: {hex_color}")
    else:
        # Detect original background color
        print(f"\nStep 2: Detecting original background color")
        hex_color = detect_background_color(image_path, EDGE_THICKNESS)
        print(f"Detected background color: {hex_color}")

    # Step 3: Handle based on SKIP_EXPANSION setting
    print(f"\nStep 3: {'Skipping expansion, changing background color only' if SKIP_EXPANSION else 'Expanding canvas and compositing foreground'}")
    output_path = expand_and_composite(image_path, foreground_path, hex_color, SKIP_EXPANSION)

    if output_path:
        return True, output_path, hex_color
    else:
        return False, None, None

def print_usage():
    """Print usage information"""
    print("Usage: fg2canvas_rmbg.py [image_path] [--color HEX]")
    print("\nThis script will:")
    print("  1. Remove background using ComfyUI RMBG")
    print("  2. Detect original background color (or use custom if provided)")
    print("  3. Create an expanded canvas with that color, or just change the")
    print("     background if SKIP_EXPANSION is True")
    print("  4. Composite the foreground object onto it")
    print("\nArguments:")
    print("  image_path                 Optional input image (overrides IMAGE_PATH variable)")
    print("\nOptions:")
    print("  -c, --color HEX            Custom background color (overrides CUSTOM_HEX_COLOR)")
    print("                             Examples: #e78c14, e78c14, #FFF, FFF")
    print("  -h, --help                 Show this help message")
    print("\nConfigurable settings at the top of the script:")
    print(f"  IMAGE_PATH = {IMAGE_PATH}")
    print(f"  CUSTOM_HEX_COLOR = {CUSTOM_HEX_COLOR!r}")
    print(f"  SKIP_EXPANSION = {SKIP_EXPANSION}")
    print(f"  EXPAND_PROPORTION = {EXPAND_PROPORTION}")
    print(f"  EXPAND_BOTH_DIMENSIONS = {EXPAND_BOTH_DIMENSIONS}")
    if not EXPAND_BOTH_DIMENSIONS:
        print(f"  EXPAND_WIDTH_PROPORTION = {EXPAND_WIDTH_PROPORTION}")
        print(f"  EXPAND_HEIGHT_PROPORTION = {EXPAND_HEIGHT_PROPORTION}")
    print("\nExamples:")
    print("  # Auto-detect background color with expansion")
    print("  fg2canvas_rmbg.py image.png")
    print("\n  # Use custom background color with expansion")
    print("  fg2canvas_rmbg.py image.png --color '#ff0000'")
    print("  fg2canvas_rmbg.py image.png -c ff0000")
    print("\n  # Skip expansion (edit SKIP_EXPANSION in the script)")
    print("  fg2canvas_rmbg.py image.png")

def main():
    # Set up argument parser
    parser = argparse.ArgumentParser(
        description="Remove background from image and place on expanded canvas",
        add_help=False  # We'll handle help manually
    )

    parser.add_argument('image_path', nargs='?', default=None,
                        help='Optional input image path (overrides IMAGE_PATH variable)')
    parser.add_argument('-c', '--color',
                        help='Custom background color in hex format (overrides CUSTOM_HEX_COLOR)')
    parser.add_argument('-h', '--help',
                        action='store_true',
                        help='Show this help message')

    # Parse arguments
    args = parser.parse_args()

    # Show help if explicitly requested. (Bare invocation falls through to
    # IMAGE_PATH below, so the router's neutralized sys.argv — which is
    # just [script_path] — reaches process_image and runs the tool.)
    if args.help:
        print_usage()
        sys.exit(0)

    # Resolve the input image path:
    #   1. positional argument
    #   2. IMAGE_PATH variable
    image_path = args.image_path or IMAGE_PATH

    # Resolve the custom color: CLI flag overrides the variable
    custom_hex_color = args.color if args.color else CUSTOM_HEX_COLOR

    # Process the image
    success, final_output_path, hex_color = process_image(image_path, custom_hex_color)

    if success:
        print(f"\n✓ Successfully processed!")
        print(f"  Output: {final_output_path}")
        print(f"  Background color used: {hex_color}")
        sys.exit(0)
    else:
        print(f"\n✗ Failed to process image")
        sys.exit(1)

if __name__ == "__main__":
    main()
