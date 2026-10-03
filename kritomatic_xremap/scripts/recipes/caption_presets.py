#!/usr/bin/env python3
"""
caption_presets.py

Run a single image through several caption prompts (presets) in sequence.
Each preset asks the same vision model to describe the image from a
different angle, so the file this produces is six different "reads" of
the same picture:

    LLM                    — described as if for another language model
    Descriptive            — plain long description
    Explicit Adult (NSFW)  — no sanitization, aligned to actual content
    Straightforward        — concrete, no mood, no speculation
    e621 tag list          — comma-separated tags
    Text Extractor         — literal on-screen text only

Each preset is dispatched through the project's router (router.py),
which is the single entry point for the tool scripts and the single
source of truth for what each tool accepts. The router runs under the
project venv's python.

The output file begins with a header recording the image path, so that
interpret_image.py can detect whether a batch file corresponds to the
image it was asked to interpret. Its path is fixed:

    /tmp/caption_batch.txt

Usage:
    caption_presets.py <image_path>

Examples:
    caption_presets.py photo.png
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ===== CONFIGURATION =====
# This file lives at:
#     kritomatic/kritomatic_xremap/scripts/recipes/caption_presets.py
SCRIPT_DIR   = Path(__file__).resolve().parent            # recipes/
SCRIPTS_DIR  = SCRIPT_DIR.parent                          # scripts/
PROJECT_ROOT = SCRIPTS_DIR.parent.parent                  # kritomatic/
VENV_PYTHON  = PROJECT_ROOT / ".venv" / "bin" / "python"
ROUTER       = SCRIPTS_DIR / "router.py"

# Fixed output path. interpret_image.py reads from this same location.
OUTPUT_PATH = Path("/tmp/caption_batch.txt")

# Each entry: (prompt_label, word_limit, max_tokens)
# word_limit may be None to omit the word-limit instruction.
PRESETS = [
    ("LLM",                   300, 800),
    ("Descriptive",           200, 800),
    ("Explicit Adult (NSFW)", 200, 800),
    ("Straightforward",       300, 800),
    ("e621 tag list",         300, 800),
    ("Text Extractor",        None, 800),
]
# =========================


def build_instruction(image_path: str, label: str,
                      word_limit, max_tokens: int) -> str:
    """Return the router instruction JSON for one preset."""
    params = {
        "IMAGE_PATH": image_path,
        "DEFAULT_PROMPT_LABEL": label,
        "MAX_TOKENS": max_tokens,
    }
    if word_limit is not None:
        params["WORD_LIMIT"] = word_limit
    return json.dumps({"tool": "llamacpp_caption_images", "params": params})


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run an image through several caption presets.",
    )
    parser.add_argument("image_path", help="Path to the input image.")
    args = parser.parse_args()

    image_path = Path(args.image_path).expanduser().resolve()
    if not image_path.is_file():
        print(f"Error: not a file: {image_path}", file=sys.stderr)
        return 1

    if not VENV_PYTHON.is_file():
        print(f"Error: venv python not found at {VENV_PYTHON}",
              file=sys.stderr)
        return 1
    if not ROUTER.is_file():
        print(f"Error: router not found at {ROUTER}", file=sys.stderr)
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"Image:   {image_path}")
    print(f"Presets: {len(PRESETS)}")
    print(f"Output:  {OUTPUT_PATH}")
    print("=" * 60)

    failures = 0
    with open(OUTPUT_PATH, "w", encoding="utf-8") as out:
        # Header, consumed by interpret_image.py.
        out.write(f"IMAGE: {image_path}\n")
        out.write(f"GENERATED_AT: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write("\n")
        out.flush()

        for i, (label, word_limit, max_tokens) in enumerate(PRESETS, 1):
            print(f"[{i}/{len(PRESETS)}] {label}")

            out.write("=" * 60 + "\n")
            out.write(f"PRESET: {label}\n")
            out.write(f"WORD_LIMIT: {word_limit}\n")
            out.write(f"MAX_TOKENS: {max_tokens}\n")
            out.write("=" * 60 + "\n\n")
            out.flush()

            result = subprocess.run(
                [str(VENV_PYTHON), str(ROUTER),
                 build_instruction(str(image_path), label,
                                   word_limit, max_tokens)],
                stdout=out,
                check=False,
            )

            out.write("\n\n")
            out.flush()

            if result.returncode != 0:
                failures += 1
                print(f"  FAILED (exit {result.returncode})",
                      file=sys.stderr)
            else:
                print("  ok")

    succeeded = len(PRESETS) - failures
    print("=" * 60)
    print(f"Done. {succeeded}/{len(PRESETS)} succeeded.")
    print(f"Saved to: {OUTPUT_PATH}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
