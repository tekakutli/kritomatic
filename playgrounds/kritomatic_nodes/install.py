#!/usr/bin/env python3
"""Symlink this directory (kritomatic_nodes) into a ComfyUI install.

The script lives inside kritomatic_nodes/, so its own location is the
source.  No need to look up a git root — just resolve the script's
parent and link it in.

    python kritomatic_nodes/install.py

To point at a non-default ComfyUI install, either edit COMFYUI_PATH
below or set the COMFYUI_DIR environment variable.  COMFYUI_PATH
wins if both are set.
"""
import os
import shutil
import sys
from pathlib import Path


# Edit this to hardcode a ComfyUI install path (e.g. "/mnt/models/ComfyUI").
# Leave empty to fall back to COMFYUI_DIR, then ~/ComfyUI.
COMFYUI_PATH = ""


def _resolve_comfyui() -> Path:
    if COMFYUI_PATH:
        return Path(COMFYUI_PATH).expanduser()
    env = os.environ.get("COMFYUI_DIR")
    if env:
        return Path(env).expanduser()
    return Path.home() / "ComfyUI"


def main() -> int:
    src = Path(__file__).resolve().parent
    dst = _resolve_comfyui() / "custom_nodes" / "kritomatic_nodes"

    if src == dst:
        print(f"error: {src} is already the link target.", file=sys.stderr)
        return 1
    if not dst.parent.is_dir():
        print(f"error: {dst.parent} does not exist.", file=sys.stderr)
        print("Edit COMFYUI_PATH at the top of this script, or set "
              "COMFYUI_DIR (e.g. /mnt/models/ComfyUI).", file=sys.stderr)
        return 1

    if dst.is_symlink():
        dst.unlink()
    elif dst.exists():
        print(f"error: {dst} exists and is not a symlink; refusing to remove.",
              file=sys.stderr)
        print("Move it aside manually if you want to replace it.",
              file=sys.stderr)
        return 1

    for pycache in src.rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache, ignore_errors=True)

    os.symlink(src, dst)
    print(f"linked {dst} -> {src}")
    print("restart ComfyUI to pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
