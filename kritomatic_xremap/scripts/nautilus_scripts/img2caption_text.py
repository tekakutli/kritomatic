#!/usr/bin/env python3
"""
Nautilus right-click: caption an image via the clipboard2caption tool
(router.py), with the "Text Extractor" prompt. On failure, opens an error
file in Emacs.
Place in ~/.local/share/nautilus/scripts/ or a submenu directory.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ==================== CONFIGURATION ====================
DEFAULT_PROMPT = "Text Extractor"

# Interpreter used to run router.py (and, through it, the tool scripts).
# Must be the venv python so the tools can import requests / PIL / wand.
VENV_PYTHON = str(Path(__file__).resolve().parents[3] / ".venv" / "bin" / "python")
# ======================================================

ROUTER_PATH = Path(__file__).resolve().parent.parent / "router.py"


def run_tool(instruction):
    return subprocess.run(
        [VENV_PYTHON, str(ROUTER_PATH), json.dumps(instruction)],
        capture_output=True,
        text=True,
        check=False,
    )


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


def write_error_file(file_path, result):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    temp_file = os.path.join(
        tempfile.gettempdir(), f"extract_text_error_{timestamp}.txt"
    )
    error_content = f"""# Text Extraction Error
# Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
# Image: {file_path}
# Error: router exited with code {result.returncode}
{'=' * 80}

YOU NEED TO RUN LLAMA

{'=' * 80}
Error details:
{result.stderr if result.stderr else 'No error details available'}
"""
    open_with_emacs(temp_file, error_content)


def main():
    file_paths = sys.argv[1:]
    if not file_paths:
        print("No files selected.")
        return

    for file_path in file_paths:
        if file_path.startswith('~'):
            file_path = os.path.expanduser(file_path)

        instruction = {
            "tool": "clipboard2caption",
            "params": {
                "IMAGE_PATH": file_path,
                "DEFAULT_PROMPT": DEFAULT_PROMPT,
            },
        }
        print(f"Running: clipboard2caption on {file_path}")

        try:
            result = run_tool(instruction)

            if result.returncode == 0:
                print(f"\u2705 Successfully processed: {file_path}")
                if result.stdout:
                    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            else:
                print(f"\u274C Script failed for: {file_path}")
                if result.stdout:
                    print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
                if result.stderr:
                    print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
                write_error_file(file_path, result)

        except Exception as e:
            print(f"Unexpected error: {e}")
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            temp_file = os.path.join(
                tempfile.gettempdir(), f"extract_text_error_{timestamp}.txt"
            )
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
