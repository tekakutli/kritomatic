#!/usr/bin/env python3
"""
Create a new Krita document from clipboard image
"""

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
    # Get image from clipboard
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
