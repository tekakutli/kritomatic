#!/usr/bin/env python3
"""
Create a new Krita document from clipboard image
"""

# ===== CONFIGURABLE SETTINGS =====
CROP_WIDTH = 700   # Change this to set the crop width (e.g., 512, 1024, 2048)
CROP_HEIGHT = 700  # Change this to set the crop height (e.g., 512, 1024, 2048)
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

def crop_image(input_path, output_path):
    """Crop image to CROP_WIDTH x CROP_HEIGHT from center"""
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
    """Save image, crop it, and store in STORAGE_DIR"""

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
        return False

    # Generate output path in STORAGE_DIR
    timestamp = int(time.time())
    output_path = os.path.join(STORAGE_DIR, f'cropped_image_{timestamp}.png')

    # Crop the image
    if crop_image(original_path, output_path):
        print(f"Cropped image saved to: {output_path}")

        # Optional: Clean up original image after delay
        cleanup_temp_file(original_path, delay=5)

        return True
    else:
        # Clean up original if cropping failed
        cleanup_temp_file(original_path, delay=1)
        return False

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
    # Get image from clipboard
    image_data = get_clipboard_image()

    if not image_data:
        show_notification("No image found in clipboard")
        sys.exit(1)

    # Process the image (save, crop, store)
    if process_clipboard_image(image_data):
        show_notification(f"Image cropped to {CROP_WIDTH}x{CROP_HEIGHT} and saved to {STORAGE_DIR}", is_error=False)
    else:
        show_notification("Failed to process image")
        sys.exit(1)

if __name__ == "__main__":
    main()
