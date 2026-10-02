#!/usr/bin/env python3
"""
Nautilus right-click: dispatch background removal to the plain or prompted
script depending on PROMPT_TEXT.
Place in ~/.local/share/nautilus/scripts/ or a submenu directory.
"""

import sys
import subprocess
from pathlib import Path

# ==================== CONFIGURATION ====================
# Set this to your prompt text, or leave empty for no prompt
PROMPT_TEXT = ""  # Example: "eggs" or "remove text" etc.
# ======================================================

SCRIPTS_DIR = Path(__file__).resolve().parent.parent
SCRIPT_WITH_PROMPT = SCRIPTS_DIR / "bg_remove_prompted.py"
SCRIPT_WITHOUT_PROMPT = SCRIPTS_DIR / "bg_remove.py"


def main():
    file_paths = sys.argv[1:]
    if not file_paths:
        print("No files selected.")
        return

    if PROMPT_TEXT:
        script_path = SCRIPT_WITH_PROMPT
        for file_path in file_paths:
            escaped_path = f'"{file_path}"'
            cmd = f'python3 "{script_path}" --image {escaped_path} --prompt "{PROMPT_TEXT}"'
            print(f"Running: {cmd}")
            try:
                subprocess.run(cmd, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error running script on {file_path}: {e}")
    else:
        script_path = SCRIPT_WITHOUT_PROMPT
        for file_path in file_paths:
            escaped_path = f'"{file_path}"'
            cmd = f'python3 "{script_path}" {escaped_path}'
            print(f"Running: {cmd}")
            try:
                subprocess.run(cmd, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error running script on {file_path}: {e}")


if __name__ == "__main__":
    main()
