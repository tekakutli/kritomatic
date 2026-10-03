#!/usr/bin/env python3
"""
Nautilus right-click: dispatch background removal to the plain or prompted
tool via router.py, depending on PROMPT_TEXT.
Place in ~/.local/share/nautilus/scripts/ or a submenu directory.
"""

import json
import subprocess
import sys
from pathlib import Path

# ==================== CONFIGURATION ====================
# Set this to your prompt text, or leave empty for no prompt
PROMPT_TEXT = ""  # Example: "eggs" or "remove text" etc.

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


def main():
    file_paths = sys.argv[1:]
    if not file_paths:
        print("No files selected.")
        return

    if PROMPT_TEXT:
        tool_name = "bg_remove_prompted"
        extra_params = {"DEFAULT_PROMPT": PROMPT_TEXT}
    else:
        tool_name = "bg_remove"
        extra_params = {}

    for file_path in file_paths:
        instruction = {
            "tool": tool_name,
            "params": {"IMAGE_PATH": file_path, **extra_params},
        }
        print(f"Running: {tool_name} on {file_path}")
        result = run_tool(instruction)
        if result.returncode == 0:
            print(f"✓ {file_path}")
            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
        else:
            print(f"✗ {file_path}")
            if result.stdout:
                print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            if result.stderr:
                print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)


if __name__ == "__main__":
    main()
