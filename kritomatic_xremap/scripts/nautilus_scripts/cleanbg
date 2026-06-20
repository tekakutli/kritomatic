#!/usr/bin/env python3
"""
Nautilus right-click script for background removal and expansion with -s flag.
Place in ~/.local/share/nautilus/scripts/
"""

import sys
import os
import subprocess

# ==================== CONFIGURATION ====================
# Set to True to include the -s flag, False to omit it
USE_S_FLAG = True  # Change to False if you don't want the -s flag
# ======================================================

# Path to the script
SCRIPT_PATH = "/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/remove_and_expand_background.py"

def main():
    # Get selected file paths from command line arguments
    file_paths = sys.argv[1:]

    if not file_paths:
        print("No files selected.")
        return

    for file_path in file_paths:
        # Escape spaces and special characters in the file path
        escaped_path = f'"{file_path}"'

        # Build the full command with -i flag and optionally -s flag
        if USE_S_FLAG:
            cmd = f'python3 "{SCRIPT_PATH}" -i {escaped_path} -s'
        else:
            cmd = f'python3 "{SCRIPT_PATH}" -i {escaped_path}'

        print(f"Running: {cmd}")

        # Execute the command
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error running script on {file_path}: {e}")

if __name__ == "__main__":
    main()
