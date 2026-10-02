#!/usr/bin/env python3
"""
Nautilus right-click: fill background using fg2canvas_rmbg.py with -s.
Place in ~/.local/share/nautilus/scripts/ or a submenu directory.
"""

import sys
import subprocess
from pathlib import Path

# ==================== CONFIGURATION ====================
USE_S_FLAG = True
# ======================================================

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "fg2canvas_rmbg.py"


def main():
    file_paths = sys.argv[1:]
    if not file_paths:
        print("No files selected.")
        return

    for file_path in file_paths:
        escaped_path = f'"{file_path}"'
        if USE_S_FLAG:
            cmd = f'python3 "{SCRIPT_PATH}" -i {escaped_path} -s'
        else:
            cmd = f'python3 "{SCRIPT_PATH}" -i {escaped_path}'

        print(f"Running: {cmd}")
        try:
            subprocess.run(cmd, shell=True, check=True)
        except subprocess.CalledProcessError as e:
            print(f"Error running script on {file_path}: {e}")


if __name__ == "__main__":
    main()
