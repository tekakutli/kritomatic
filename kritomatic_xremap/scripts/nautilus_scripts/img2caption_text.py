#!/usr/bin/env python3
"""
Nautilus right-click: extract text from image via img2caption.py.
Place in ~/.local/share/nautilus/scripts/ or a submenu directory.
"""

import sys
import os
import subprocess
import tempfile
import time
import shutil
from pathlib import Path

# ==================== CONFIGURATION ====================
SCRIPT_PATH = Path(__file__).resolve().parent.parent / "img2caption.py"
DEFAULT_PROMPT = "Text Extractor"
# ======================================================


def open_with_emacs(file_path, content=None):
    """Open file with emacsclient, optionally writing content first"""
    if content is not None:
        try:
            with open(file_path, 'w') as f:
                f.write(content)
            print(f"\U0001F4DD Wrote content to: {file_path}")
        except Exception as e:
            print(f"\u26A0\uFE0F Could not write to file: {e}")
            return False

    try:
        if not shutil.which('emacsclient'):
            print("\u26A0\uFE0F emacsclient not found. Falling back to emacs...")
            subprocess.Popen(['emacs', file_path])
            print(f"\U0001F4DD Opened {file_path} in Emacs")
            return True

        result = subprocess.run(
            ['emacsclient', '-n', '-a', 'emacs', file_path],
            check=False
        )

        if result.returncode == 0:
            print(f"\U0001F4DD Opened {file_path} in Emacs (emacsclient)")
        else:
            subprocess.Popen(['emacs', file_path])
            print(f"\U0001F4DD Opened {file_path} in Emacs (fallback)")
        return True

    except Exception as e:
        print(f"\u26A0\uFE0F Could not open Emacs: {e}")
        print(f"\U0001F4C1 File saved at: {file_path}")
        return False


def main():
    file_paths = sys.argv[1:]
    if not file_paths:
        print("No files selected.")
        return

    for file_path in file_paths:
        if file_path.startswith('~'):
            file_path = os.path.expanduser(file_path)

        escaped_path = f'"{file_path}"'
        cmd = f'python3 "{SCRIPT_PATH}" {escaped_path} -p "{DEFAULT_PROMPT}"'
        print(f"Running: {cmd}")

        try:
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)

            if result.returncode == 0:
                print(f"\u2705 Successfully processed: {file_path}")
            else:
                print(f"\u274C Script failed for: {file_path}")
                print(f"Error output: {result.stderr}")

                timestamp = time.strftime("%Y%m%d_%H%M%S")
                temp_file = os.path.join(tempfile.gettempdir(), f"extract_text_error_{timestamp}.txt")

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
                open_with_emacs(temp_file, error_content)

        except subprocess.CalledProcessError as e:
            print(f"Error running script on {file_path}: {e}")

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
