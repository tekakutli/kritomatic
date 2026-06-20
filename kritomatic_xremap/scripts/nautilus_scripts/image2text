#!/usr/bin/env python3
"""
Nautilus right-click script for text extraction from images.
Place in ~/.local/share/nautilus/scripts/
"""

import sys
import os
import subprocess
import tempfile
import time
import shutil

# ==================== CONFIGURATION ====================
# Path to the extract_text_image.py script
SCRIPT_PATH = "/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/extract_text_image.py"

# Default prompt (matches the script's default)
DEFAULT_PROMPT = "Text Extractor"
# ======================================================

def open_with_emacs(file_path, content=None):
    """Open file with emacsclient, optionally writing content first"""

    # If content is provided, write it to the file
    if content is not None:
        try:
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"📝 Wrote content to: {file_path}")
        except Exception as e:
            print(f"⚠️ Could not write to file: {e}")
            return False

    try:
        # Check if emacsclient is available
        if not shutil.which('emacsclient'):
            print("⚠️ emacsclient not found. Falling back to emacs...")
            subprocess.Popen(['emacs', file_path])
            print(f"📝 Opened {file_path} in Emacs")
            return True

        # Try to open with emacsclient
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
        return True

    except Exception as e:
        print(f"⚠️ Could not open Emacs: {e}")
        print(f"📁 File saved at: {file_path}")
        return False

def main():
    # Get selected file paths from command line arguments
    file_paths = sys.argv[1:]

    if not file_paths:
        print("No files selected.")
        return

    for file_path in file_paths:
        # Expand user home directory if path starts with ~
        if file_path.startswith('~'):
            file_path = os.path.expanduser(file_path)

        # Escape spaces and special characters in the file path
        escaped_path = f'"{file_path}"'

        # Build the command
        cmd = f'python3 "{SCRIPT_PATH}" {escaped_path} -p "{DEFAULT_PROMPT}"'
        print(f"Running: {cmd}")

        # Execute the command
        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if result.returncode == 0:
                print(f"✅ Successfully processed: {file_path}")
            else:
                # The script failed - create a temporary file with the error message
                print(f"❌ Script failed for: {file_path}")
                print(f"Error output: {result.stderr}")

                # Create a temporary file with the error message
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                temp_file = os.path.join(tempfile.gettempdir(), f"extract_text_error_{timestamp}.txt")

                # Create the error message content
                error_content = f"""# Text Extraction Error
# Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
# Image: {file_path}
# Error: Script failed with exit code {result.returncode}
{'=' * 80}

YOU NEED TO RUN LLAMA

{'=' * 80}
Error details:
{result.stderr if result.stderr else 'No error details available'}
"""

                # Open the file in emacs with the error message
                open_with_emacs(temp_file, error_content)

        except subprocess.CalledProcessError as e:
            print(f"Error running script on {file_path}: {e}")

            # Create a temporary file with the error message
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            temp_file = os.path.join(tempfile.gettempdir(), f"extract_text_error_{timestamp}.txt")

            error_content = f"""# Text Extraction Error
# Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
# Image: {file_path}
# Error: {str(e)}
{'=' * 80}

YOU NEED TO RUN LLAMA
"""
            open_with_emacs(temp_file, error_content)
        except Exception as e:
            print(f"Unexpected error: {e}")
            # Try to open emacs with the error message anyway
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            temp_file = os.path.join(tempfile.gettempdir(), f"extract_text_error_{timestamp}.txt")

            error_content = f"""# Text Extraction Error
# Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
# Image: {file_path}
# Error: Unexpected error - {str(e)}
{'=' * 80}

YOU NEED TO RUN LLAMA
"""
            open_with_emacs(temp_file, error_content)

if __name__ == "__main__":
    main()
