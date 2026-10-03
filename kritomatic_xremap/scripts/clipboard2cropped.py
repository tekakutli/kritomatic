#!/usr/bin/env python3
"""
clipboard2cropped.py

Crop an image (proportional or fixed center crop) and save the result into
STORAGE_DIR.

By default, the input image is read from the system clipboard. Alternatively,
the input can be an image file on disk, specified either by the IMAGE_PATH
variable below or by a positional argument on the command line (which
overrides IMAGE_PATH). If neither is set, the clipboard is used.

Cropping behavior is controlled by variables in the CONFIGURABLE SETTINGS
block:

  - If HORIZONTAL_KEEP_RATIO or VERTICAL_KEEP_RATIO is < 1.0, a proportional
    center crop is performed using those ratios.
  - If BOTH ratios are 1.0, a fixed CROP_WIDTH x CROP_HEIGHT center crop is
    performed instead.
  - The result is written to STORAGE_DIR with a timestamped filename.

Usage:
    python clipboard2cropped.py [image_path]

Examples:
    # Read from clipboard
    python clipboard2cropped.py

    # Override with a file path
    python clipboard2cropped.py photo.png
"""

# ===== CONFIGURABLE SETTINGS =====
# Optional input image path. If empty, reads from clipboard.
IMAGE_PATH = ""

# Set to values between 0 and 1 to keep a proportional slice from the center
# Set to 1.0 to use CROP_WIDTH and CROP_HEIGHT instead
HORIZONTAL_KEEP_RATIO = 0.3333   # Keep this proportion of width from center (e.g., 0.3333 = middle 1/3)
VERTICAL_KEEP_RATIO = 1.0        # Keep this proportion of height from center (e.g., 0.5 = middle half)

# Only used if BOTH HORIZONTAL_KEEP_RATIO and VERTICAL_KEEP_RATIO are set to 1.0
CROP_WIDTH = 700                 # Fallback crop width
CROP_HEIGHT = 700                # Fallback crop height

# STORAGE_DIR = '/tmp/stored/'  # Change this to set the storage directory
STORAGE_DIR = '/home/tekakutli/files/Pictures/krita/twitter/captions/symbols/background/cleaned_up/logos/warningLogo/'  # Change this to set the storage directory
STORAGE_DIR = '/tmp/cropped/'  # Change this to set the storage directory

# =================================

import subprocess
import tempfile
import os
import sys
import time
import threading

def get_clipboard_image():
    """Extract image from clipboard using available Linux tools"""

    # Try different methods based on environment
    methods = [
        # X11 method
        ['xclip', '-selection', 'clipboard', '-t', 'image/png', '-o'],
        # Wayland method
        ['wl-paste', '--type', 'image/png'],
        # Fallback to xclip without type spec
        ['xclip', '-selection', 'clipboard', '-o']
    ]

    for method in methods:
        try:
            result = subprocess.run(
                method,
                capture_output=True,
                check=False
            )
            if result.stdout and len(result.stdout) > 100:  # Likely valid image data
                return result.stdout
        except FileNotFoundError:
            continue

    return None

def ensure_directory_exists(directory):
    """Create directory if it doesn't exist"""
    if not os.path.exists(directory):
        os.makedirs(directory)
        print(f"Created directory: {directory}")

def get_image_dimensions(input_path):
    """Get image dimensions using ImageMagick"""
    try:
        result = subprocess.run(
            ['magick', 'identify', '-format', '%wx%h', input_path],
            capture_output=True,
            text=True,
            check=True
        )
        width, height = map(int, result.stdout.strip().split('x'))
        return width, height
    except subprocess.CalledProcessError as e:
        print(f"Error getting image dimensions: {e}")
        return None, None

def crop_proportional_center(input_path, output_path, horizontal_ratio, vertical_ratio):
    """Crop to keep a proportional slice from the center of the image"""
    try:
        # Get original dimensions
        width, height = get_image_dimensions(input_path)
        if width is None or height is None:
            return False

        # Calculate crop dimensions
        crop_width = int(width * horizontal_ratio)
        crop_height = int(height * vertical_ratio)

        # Calculate starting positions (to center the crop)
        crop_x = (width - crop_width) // 2
        crop_y = (height - crop_height) // 2

        # Use ImageMagick to crop
        subprocess.run(
            ['magick', input_path, '-crop', f'{crop_width}x{crop_height}+{crop_x}+{crop_y}', '+repage', output_path],
            check=True,
            capture_output=True
        )
        print(f"Cropped to proportional center: {crop_width}x{crop_height} (from {width}x{height})")
        print(f"  Horizontal: kept {horizontal_ratio*100:.1f}% (center {crop_width}/{width}px)")
        print(f"  Vertical: kept {vertical_ratio*100:.1f}% (center {crop_height}/{height}px)")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error cropping image: {e}")
        return False
    except FileNotFoundError:
        print("ImageMagick 'magick' command not found. Please install ImageMagick.")
        return False

def crop_fixed_center(input_path, output_path):
    """Crop image to CROP_WIDTH x CROP_HEIGHT from center (fallback behavior)"""
    try:
        subprocess.run(
            ['magick', input_path, '-gravity', 'center', '-extent', f'{CROP_WIDTH}x{CROP_HEIGHT}', output_path],
            check=True,
            capture_output=True
        )
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error cropping image: {e}")
        return False
    except FileNotFoundError:
        print("ImageMagick 'magick' command not found. Please install ImageMagick.")
        return False

def process_clipboard_image(image_data):
    """Save image, crop it (proportional or fixed), and store in STORAGE_DIR"""

    # Create the stored directory if it doesn't exist
    ensure_directory_exists(STORAGE_DIR)

    # Save original image to /tmp/
    original_path = '/tmp/clipboard_image.png'
    try:
        with open(original_path, 'wb') as f:
            f.write(image_data)
        print(f"Original image saved to: {original_path}")
    except Exception as e:
        print(f"Error saving original image: {e}")
        return False, None

    # Generate output path in STORAGE_DIR
    timestamp = int(time.time())
    output_path = os.path.join(STORAGE_DIR, f'cropped_image_{timestamp}.png')

    # Check if we should use proportional cropping or fixed dimensions
    use_proportional = (HORIZONTAL_KEEP_RATIO < 1.0) or (VERTICAL_KEEP_RATIO < 1.0)

    if use_proportional:
        success = crop_proportional_center(original_path, output_path, HORIZONTAL_KEEP_RATIO, VERTICAL_KEEP_RATIO)
        crop_description = f"proportional crop ({HORIZONTAL_KEEP_RATIO*100:.1f}% width, {VERTICAL_KEEP_RATIO*100:.1f}% height)"
    else:
        success = crop_fixed_center(original_path, output_path)
        crop_description = f"{CROP_WIDTH}x{CROP_HEIGHT} fixed center crop"

    if success:
        print(f"Cropped image saved to: {output_path}")

        # Optional: Clean up original image after delay
        cleanup_temp_file(original_path, delay=5)

        return True, crop_description
    else:
        # Clean up original if cropping failed
        cleanup_temp_file(original_path, delay=1)
        return False, crop_description

def process_input_file(image_path):
    """Read an input image file from disk, then crop and store it.

    Unlike the clipboard path, this does not create or clean up a temp file;
    it works directly on the given file.
    """
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return False, None

    # Read bytes so the downstream path is identical to the clipboard flow
    try:
        with open(image_path, 'rb') as f:
            image_data = f.read()
    except Exception as e:
        print(f"Error reading input file: {e}")
        return False, None

    return process_clipboard_image(image_data)

def cleanup_temp_file(tmp_path, delay=10):
    """Delete temp file after delay"""
    def delayed_delete():
        time.sleep(delay)
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                print(f"Cleaned up: {tmp_path}")
        except Exception as e:
            print(f"Cleanup error: {e}")

    thread = threading.Thread(target=delayed_delete, daemon=True)
    thread.start()

def show_notification(message, is_error=True):
    """Show desktop notification"""
    try:
        icon = 'dialog-error' if is_error else 'dialog-information'
        subprocess.Popen([
            'notify-send',
            'Clipboard Image Processor' if is_error else 'Success',
            message,
            '-i', icon,
            '-t', '3000'
        ])
    except FileNotFoundError:
        print(message)

def main():
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Crop clipboard image or a file')
    parser.add_argument('image_path', nargs='?', default=None,
                       help='Optional input image path (overrides IMAGE_PATH variable; '
                            'if unset, reads from clipboard)')
    args = parser.parse_args()

    # Validate ratios
    if not (0 < HORIZONTAL_KEEP_RATIO <= 1.0):
        print(f"Error: HORIZONTAL_KEEP_RATIO must be between 0 and 1, got {HORIZONTAL_KEEP_RATIO}")
        sys.exit(1)

    if not (0 < VERTICAL_KEEP_RATIO <= 1.0):
        print(f"Error: VERTICAL_KEEP_RATIO must be between 0 and 1, got {VERTICAL_KEEP_RATIO}")
        sys.exit(1)

    # Resolve the input image path:
    #   1. positional argument
    #   2. IMAGE_PATH variable
    #   3. None -> read from clipboard
    image_path = args.image_path or IMAGE_PATH or None

    if image_path:
        print(f"📁 Using input image file: {image_path}")
        success, crop_description = process_input_file(image_path)
    else:
        # Get image from clipboard
        print("📋 Reading image from clipboard...")
        image_data = get_clipboard_image()

        if not image_data:
            show_notification("No image found in clipboard")
            sys.exit(1)

        # Process the image (save, crop, store)
        success, crop_description = process_clipboard_image(image_data)

    if success:
        if (HORIZONTAL_KEEP_RATIO < 1.0) or (VERTICAL_KEEP_RATIO < 1.0):
            show_notification(f"Image cropped to center {HORIZONTAL_KEEP_RATIO*100:.1f}% width × {VERTICAL_KEEP_RATIO*100:.1f}% height and saved to {STORAGE_DIR}", is_error=False)
        else:
            show_notification(f"Image cropped to {CROP_WIDTH}x{CROP_HEIGHT} and saved to {STORAGE_DIR}", is_error=False)
    else:
        show_notification(f"Failed to crop image ({crop_description})")
        sys.exit(1)

if __name__ == "__main__":
    main()
