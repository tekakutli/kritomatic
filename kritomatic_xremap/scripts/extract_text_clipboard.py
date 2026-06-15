#!/usr/bin/env python3
"""
Capture image from clipboard and process it with caption script,
saving output to a temporary file and opening with emacsclient
"""

import subprocess
import tempfile
import os
import sys
import time
import threading
from pathlib import Path
import shutil

# Configuration
CAPTION_SCRIPT_PATH = "/home/tekakutli/code/llama-models/caption_directory.py"
DEFAULT_PROMPT = "Text Extractor"
OUTPUT_DIR = "/tmp/clipboard_captions"

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

    # Build command
    cmd = [CAPTION_SCRIPT_PATH, image_path]

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
        title = 'Clipboard Captioner'

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
    import argparse
    parser = argparse.ArgumentParser(description='Caption clipboard image')
    parser.add_argument('-p', '--prompt', default=DEFAULT_PROMPT,
                       help=f'Prompt mode for caption script (default: {DEFAULT_PROMPT})')
    parser.add_argument('--no-cleanup', action='store_true',
                       help='Don\'t delete temporary image file')
    parser.add_argument('--no-clipboard-copy', action='store_true',
                       help='Don\'t copy text to clipboard')
    parser.add_argument('--no-editor', action='store_true',
                       help='Don\'t open editor automatically')
    args = parser.parse_args()

    # Get image from clipboard
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

    # Run caption script on the image
    success, output_file = run_caption_on_image(tmp_path, args.prompt)

    if success:
        show_notification(f"Caption generated with prompt: {args.prompt}",
                         is_error=False, output_file=output_file)

        # Open with emacsclient
        if not args.no_editor and output_file and os.path.exists(output_file):
            open_with_emacs(output_file)
    else:
        show_notification("Failed to caption image", is_error=True)

    # Cleanup image temp file
    if not args.no_cleanup:
        cleanup_temp_file(tmp_path)
    else:
        print(f"📁 Keeping temporary image: {tmp_path}")

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
