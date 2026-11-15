#!/usr/bin/env python3
"""
Nautilus right-click: copy the full paths of all selected files to the
clipboard (one absolute path per line).
Place in ~/.local/share/nautilus/scripts/ or a submenu directory.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

# ==================== CONFIGURATION ====================
# Separator between paths in the clipboard payload.
# "\n" is safest for paths that contain spaces.
SEPARATOR = "\n"
# ======================================================


def get_clipboard_writer():
    """Return a function that copies text to the system clipboard, or None."""
    # Prefer Wayland native tool when running under Wayland.
    if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy"):
        def wl_copy(text):
            subprocess.run(["wl-copy"], input=text, text=True, check=True)
        return wl_copy

    if shutil.which("xclip"):
        def xclip(text):
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text, text=True, check=True,
            )
        return xclip

    if shutil.which("xsel"):
        def xsel(text):
            subprocess.run(
                ["xsel", "--clipboard", "--input"],
                input=text, text=True, check=True,
            )
        return xsel

    return None


def main():
    file_paths = sys.argv[1:]
    if not file_paths:
        print("No files selected.")
        return

    abs_paths = [str(Path(p).absolute()) for p in file_paths]
    payload = SEPARATOR.join(abs_paths)

    writer = get_clipboard_writer()
    if writer is None:
        print(
            "No clipboard tool found. Install one of: "
            "wl-clipboard (Wayland), xclip or xsel (X11).",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        writer(payload)
    except subprocess.CalledProcessError as exc:
        print(f"Failed to copy to clipboard: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Copied {len(abs_paths)} path(s) to clipboard.")


if __name__ == "__main__":
    main()
