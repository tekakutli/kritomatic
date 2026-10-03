#!/usr/bin/env python3
"""
clipboard2caption.py

Caption an image and open the result in Emacs.

The input image can come from either a file path or the system clipboard:

  - File path: pass it as a positional argument, or set the IMAGE_PATH
    variable. The path is checked for existence and file-ness; a leading ~
    is expanded.
  - Clipboard: if no file path is given (positional argument absent and
    IMAGE_PATH empty), the image is read from the clipboard via
    xclip/wl-paste and written to a temporary PNG for processing.

The script then:
  1. Runs a caption script (llamacpp_caption_images.py next to this file)
     on the image.
  2. Saves the caption to OUTPUT_DIR and optionally copies it to the
     clipboard.
  3. Opens the output file with emacsclient (or falls back to emacs).
  4. If the image came from the clipboard, optionally cleans up the
     temporary PNG (see CLEANUP_TEMP_IMAGE).

Behavior is controlled by variables in the CONFIGURABLE SETTINGS block:

  - IMAGE_PATH:          Default input image path (overridden by positional arg).
  - DEFAULT_PROMPT:      Prompt mode passed to the caption script.
  - OUTPUT_DIR:          Where caption output files are written.
  - COPY_TO_CLIPBOARD:   If True, copy the generated caption back to the clipboard.
  - OPEN_EDITOR:         If True, open the output file in Emacs.
  - CLEANUP_TEMP_IMAGE:  If True, delete the temp clipboard image after a short
                         delay. Only relevant when the image came from the
                         clipboard. Defaults to False, so the temp PNG is kept.

The only command-line flag is --prompt / -p, which overrides DEFAULT_PROMPT.

Usage:
    python clipboard2caption.py [image_path] [--prompt TEXT]

Examples:
    # Read from clipboard (default)
    python clipboard2caption.py

    # Caption a file path
    python clipboard2caption.py photo.png

    # Custom prompt
    python clipboard2caption.py photo.png --prompt "Text Extractor"
"""

import subprocess
import tempfile
import os
import sys
import time
import threading
from pathlib import Path
import shutil
import argparse

# ===== CONFIGURABLE SETTINGS =====
IMAGE_PATH = ""                    # Optional input image path. If empty, reads from clipboard.
CAPTION_SCRIPT_PATH = str(Path(__file__).parent / "llamacpp_caption_images.py")
DEFAULT_PROMPT = "Text Extractor"
OUTPUT_DIR = "/tmp/clipboard_captions"
COPY_TO_CLIPBOARD = True           # Copy the caption text back to the clipboard
OPEN_EDITOR = True                 # Open the output file in Emacs
CLEANUP_TEMP_IMAGE = False         # Delete the temp clipboard image after captioning (default: keep it)
# =================================

def ensure_output_dir():
    """Create output directory if it doesn't exist"""
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    return OUTPUT_DIR

def get_clipboard_image():
    """Extract image from clipboard using available Linux tools"""

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

def cleanup_temp_file(tmp_path, delay=5):
    """Delete temp file after delay"""
    def delayed_delete():
        time.sleep(delay)
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
                print(f"Cleaned up image: {tmp_path}")
        except Exception as e:
            print(f"Cleanup error: {e}")

    thread = threading.Thread(target=delayed_delete, daemon=True)
    thread.start()

def open_with_emacs(file_path):
    """Open file with emacsclient"""
    try:
        # Check if emacsclient is available
        if not shutil.which('emacsclient'):
            print("⚠️ emacsclient not found. Falling back to emacs...")
            subprocess.Popen(['emacs', file_path])
            print(f"📝 Opened {file_path} in Emacs")
            return

        # Try to open with emacsclient (creates frame if emacs daemon is running)
        # -n: don't wait, -c: create new frame, -a: alternate editor if daemon not running
        result = subprocess.run(
            ['emacsclient', '-n', '-a', 'emacs', file_path],
            check=False
        )

        if result.returncode == 0:
            print(f"📝 Opened {file_path} in Emacs (emacsclient)")
        else:
            # Fallback to regular emacs
            subprocess.Popen(['emacs', file_path])
            print(f"📝 Opened {file_path} in Emacs (fallback)")

    except Exception as e:
        print(f"⚠️ Could not open Emacs: {e}")
        print(f"📁 File saved at: {file_path}")

def run_caption_on_image(image_path, prompt=None):
    """Run caption script on the image file and capture output"""

    # Ensure output directory exists
    ensure_output_dir()

    # Create output file in subdirectory
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    output_file = os.path.join(OUTPUT_DIR, f"caption_{timestamp}.txt")

    # Build command. Use sys.executable so the caption script runs under
    # the same interpreter that is running this script, which inherits the
    # venv's site-packages (llamacpp_caption_images.py needs `requests`).
    cmd = [sys.executable, CAPTION_SCRIPT_PATH, image_path]

    if prompt:
        cmd.extend(['-p', prompt])

    try:
        print(f"Running: {' '.join(cmd)}")
        print(f"Output will be saved to: {output_file}")

        # Run command and capture output
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False
        )

        # Write stdout to file
        with open(output_file, 'w') as f:
            f.write(f"# Caption generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Prompt: {prompt}\n")
            f.write(f"# Image: {image_path}\n")
            f.write("-" * 80 + "\n\n")

            if result.stdout:
                f.write(result.stdout)
            if result.stderr:
                f.write("\n\n=== ERRORS/WARNINGS ===\n")
                f.write(result.stderr)

        # Also print to terminal for immediate feedback
        if result.stdout:
            print("\n=== Caption Output ===")
            print(result.stdout)

        if result.stderr:
            print("\n=== Errors/Warnings ===")
            print(result.stderr, file=sys.stderr)

        print(f"\n✅ Output saved to: {output_file}")

        # Optionally copy to primary clipboard for easy pasting
        if COPY_TO_CLIPBOARD:
            try:
                # Copy to clipboard (X11)
                if result.stdout:
                    subprocess.run(['xclip', '-selection', 'clipboard'],
                                  input=result.stdout, text=True, check=False)
                    # Also to primary selection
                    subprocess.run(['xclip', '-selection', 'primary'],
                                  input=result.stdout, text=True, check=False)
                    print("📋 Text also copied to clipboard")
            except FileNotFoundError:
                pass  # xclip not available

        return result.returncode == 0, output_file

    except FileNotFoundError:
        print(f"Error: Caption script not found at {CAPTION_SCRIPT_PATH}")
        show_notification(f"Caption script not found!", is_error=True)
        return False, None
    except Exception as e:
        print(f"Error running caption script: {e}")
        return False, None

def show_notification(message, is_error=False, output_file=None):
    """Show desktop notification"""
    try:
        icon = 'dialog-error' if is_error else 'dialog-information'
        title = 'Captioner'

        # Add output file path to notification if available
        if output_file and not is_error:
            message = f"{message}\nSaved to: {output_file}"

        subprocess.Popen([
            'notify-send',
            title,
            message,
            '-i', icon,
            '-t', '5000'  # Longer timeout to read file path
        ])
    except FileNotFoundError:
        print(f"Notification: {message}")

def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description='Caption an image from a file path or from the clipboard'
    )
    parser.add_argument('image_path', nargs='?', default=None,
                        help='Optional input image path (overrides IMAGE_PATH variable; '
                             'if unset, reads from clipboard)')
    parser.add_argument('-p', '--prompt', default=DEFAULT_PROMPT,
                        help=f'Prompt mode for caption script (default: {DEFAULT_PROMPT})')
    args = parser.parse_args()

    # Resolve the input source:
    #   1. positional argument
    #   2. IMAGE_PATH variable
    #   3. None -> read from clipboard
    raw_path = args.image_path or IMAGE_PATH or None
    tmp_path = None  # track for cleanup (only set on the clipboard path)

    if raw_path:
        # --- File path branch ---
        image_path = os.path.expanduser(raw_path)  # expand ~

        if not os.path.exists(image_path):
            msg = f"Image file not found: {image_path}"
            print(f"❌ {msg}")
            show_notification(msg, is_error=True)
            sys.exit(1)

        if not os.path.isfile(image_path):
            msg = f"Path is not a file: {image_path}"
            print(f"❌ {msg}")
            show_notification(msg, is_error=True)
            sys.exit(1)

        print(f"📁 Processing image: {image_path}")

        # Warn on suspiciously small files
        try:
            file_size = os.path.getsize(image_path)
            if file_size < 100:
                print(f"⚠️ Warning: Image file is very small ({file_size} bytes)")
        except Exception as e:
            print(f"⚠️ Warning: Could not read file size: {e}")

    else:
        # --- Clipboard branch ---
        print("📋 Reading image from clipboard...")
        image_data = get_clipboard_image()

        if not image_data:
            msg = "No image found in clipboard"
            print(f"❌ {msg}")
            show_notification(msg, is_error=True)
            sys.exit(1)

        print(f"✅ Captured {len(image_data)} bytes of image data")

        # Create temporary file for image
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        with tempfile.NamedTemporaryFile(
            suffix='.png',
            delete=False,
            prefix=f'clipboard_caption_{timestamp}_'
        ) as tmp_file:
            tmp_file.write(image_data)
            tmp_path = tmp_file.name

        print(f"📁 Saved image to: {tmp_path}")
        image_path = tmp_path

    # Run caption script on the image
    success, output_file = run_caption_on_image(image_path, args.prompt)

    if success:
        show_notification(f"Caption generated with prompt: {args.prompt}",
                          is_error=False, output_file=output_file)

        # Open with emacsclient
        if OPEN_EDITOR and output_file and os.path.exists(output_file):
            open_with_emacs(output_file)
    else:
        show_notification("Failed to caption image", is_error=True)

    # Cleanup temp file (only if we created one from the clipboard)
    if tmp_path:
        if CLEANUP_TEMP_IMAGE:
            cleanup_temp_file(tmp_path)
        else:
            print(f"📁 Keeping temporary image: {tmp_path}")

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
