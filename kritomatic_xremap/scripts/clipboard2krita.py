#!/usr/bin/env python3
"""
clipboard2krita.py

Create a new Krita document from an image.

By default, the input image is read from the system clipboard. Alternatively,
the input can be an image file on disk, specified either by the IMAGE_PATH
variable below or by a positional argument on the command line (which
overrides IMAGE_PATH). If neither is set, the clipboard is used.

The script:
  1. Gets the input image (from a file, or from the clipboard via xclip/wl-paste).
  2. Writes it to a temporary PNG file.
  3. Launches Krita with `--template <temp.png>` so it opens a new document
     based on the image.
  4. Cleans up the temp file after a short delay.

Usage:
    python clipboard2krita.py [image_path]

Examples:
    # Read from clipboard
    python clipboard2krita.py

    # Override with a file path
    python clipboard2krita.py photo.png
"""

import subprocess
import tempfile
import os
import sys
import time
import threading

# ===== CONFIGURABLE SETTINGS =====
IMAGE_PATH = ""   # Optional input image path. If empty, reads from clipboard.
# =================================

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

def cleanup_temp_file(tmp_path, delay=10):
    """Delete temp file after delay"""
    def delayed_delete():
        time.sleep(delay)
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except Exception as e:
            print(f"Cleanup error: {e}")

    thread = threading.Thread(target=delayed_delete, daemon=True)
    thread.start()

def launch_krita_with_image(image_data):
    """Save image to temp file and launch Krita"""

    # Create temporary file with .png extension
    with tempfile.NamedTemporaryFile(
        suffix='.png',
        delete=False,
        prefix='krita_clipboard_'
    ) as tmp_file:
        tmp_file.write(image_data)
        tmp_path = tmp_file.name

    # Launch Krita with template flag
    try:
        subprocess.Popen(['krita', '--template', tmp_path])

        # Schedule cleanup after Krita loads
        cleanup_temp_file(tmp_path, delay=10)

        return True
    except FileNotFoundError:
        show_notification("Krita not found! Is it installed?")
        os.unlink(tmp_path)
        return False

def show_notification(message, is_error=True):
    """Show desktop notification"""
    try:
        icon = 'dialog-error' if is_error else 'dialog-information'
        subprocess.Popen([
            'notify-send',
            'Krita Clipboard' if is_error else 'Success',
            message,
            '-i', icon,
            '-t', '3000'
        ])
    except FileNotFoundError:
        print(message)

def main():
    # Parse command line arguments
    import argparse
    parser = argparse.ArgumentParser(description='Open an image in Krita')
    parser.add_argument('image_path', nargs='?', default=None,
                       help='Optional input image path (overrides IMAGE_PATH variable; '
                            'if unset, reads from clipboard)')
    args = parser.parse_args()

    # Resolve the input image path:
    #   1. positional argument
    #   2. IMAGE_PATH variable
    #   3. None -> read from clipboard
    image_path = args.image_path or IMAGE_PATH or None

    if image_path:
        if not os.path.exists(image_path):
            msg = f"Input file not found: {image_path}"
            print(f"❌ {msg}")
            show_notification(msg)
            sys.exit(1)
        print(f"📁 Using input image file: {image_path}")
        try:
            with open(image_path, 'rb') as f:
                image_data = f.read()
        except Exception as e:
            print(f"Error reading input file: {e}")
            show_notification(f"Error reading input file: {e}")
            sys.exit(1)
    else:
        # Get image from clipboard
        print("📋 Reading image from clipboard...")
        image_data = get_clipboard_image()

        if not image_data:
            show_notification("No image found in clipboard")
            sys.exit(1)

    # Launch Krita with the image
    if launch_krita_with_image(image_data):
        show_notification("Opening new Krita document...", is_error=False)
    else:
        show_notification("Failed to launch Krita")
        sys.exit(1)

if __name__ == "__main__":
    main()
