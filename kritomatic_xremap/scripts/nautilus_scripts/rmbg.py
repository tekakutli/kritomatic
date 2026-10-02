#!/usr/bin/env python3
"""
Nautilus right-click script for background removal.
Place in ~/.local/share/nautilus/scripts/
"""

import sys
import os
import subprocess

# ==================== CONFIGURATION ====================
# Set this to your prompt text, or leave empty for no prompt
PROMPT_TEXT = ""  # Example: "eggs" or "remove text" etc.
# ======================================================

# Path to the scripts directory
SCRIPTS_DIR = "/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts"

# Script names
SCRIPT_WITH_PROMPT = "remove_background_text_input.py"
SCRIPT_WITHOUT_PROMPT = "remove_background.py"

def main():
    # Get selected file paths from command line arguments
    file_paths = sys.argv[1:]

    if not file_paths:
        print("No files selected.")
        return

    # Determine which script to use
    if PROMPT_TEXT:
        # Use the script that requires a prompt
        script_name = SCRIPT_WITH_PROMPT
        script_path = os.path.join(SCRIPTS_DIR, script_name)

        # Build the command with the prompt
        for file_path in file_paths:
            # Escape spaces and special characters in the file path
            escaped_path = f'"{file_path}"'

            # Build the full command
            cmd = f'python3 "{script_path}" --image {escaped_path} --prompt "{PROMPT_TEXT}"'
            print(f"Running: {cmd}")

            # Execute the command
            try:
                subprocess.run(cmd, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error running script on {file_path}: {e}")
    else:
        # Use the script without a prompt
        script_name = SCRIPT_WITHOUT_PROMPT
        script_path = os.path.join(SCRIPTS_DIR, script_name)

        for file_path in file_paths:
            # Escape spaces and special characters in the file path
            escaped_path = f'"{file_path}"'

            # Build the full command
            cmd = f'python3 "{script_path}" {escaped_path}'
            print(f"Running: {cmd}")

            # Execute the command
            try:
                subprocess.run(cmd, shell=True, check=True)
            except subprocess.CalledProcessError as e:
                print(f"Error running script on {file_path}: {e}")

if __name__ == "__main__":
    main()
