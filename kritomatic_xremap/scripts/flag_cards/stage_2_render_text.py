#!/usr/bin/env python3
"""
Stage 2 — Text to Image Generator
Renders each string in TEXT_LIST as an image by calling text_as_image.py
in-process: import it, patch its module-level variables, call main().

Writes to TEXT_IMAGES_DIR from paths.py.
"""

import os
import sys
from pathlib import Path

from paths import TEXT_IMAGES_DIR

# Make text_as_image.py importable from the parent scripts/ directory.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import text_as_image  # noqa: E402

# ===== CONFIGURATION =====
TEXT_LIST = [
    "Argentina",
    "Brasil",
    "Canadá",
    "Colombia",
    "Ecuador",
    "Egipto",
    "Inglaterra",
    "Francia",
    "Alemania",
    "Irán",
    "Japón",
    "México",
    "Paises Bajos",
    "Noruega",
    "Portugal",
    "Corea del Sur",
    "España",
    "Suiza",
    "Turquía",
    "Estados Unidos",
    "Paraguay",
    "Bélgica",
    "Burguer",
]

OUTPUT_DIR = str(TEXT_IMAGES_DIR)
BG_COLOR = "white"
# =========================


def render_one(text, output_path, bg_color):
    """
    Render a single string by patching text_as_image's module-level
    variables and calling its main().
    """
    text_as_image.OUTPUT_FILE = output_path
    text_as_image.BG_COLOR = bg_color
    text_as_image.TEXT_TO_RENDER = text

    # text_as_image.main() parses sys.argv to pick up the positional text.
    # Neutralize argv to just the text so it takes the positional branch
    # and does not see leftover arguments from this script.
    saved_argv = sys.argv
    sys.argv = ["text_as_image.py", text]
    try:
        text_as_image.main()
    finally:
        sys.argv = saved_argv


def generate_images():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"📁 Output directory: {OUTPUT_DIR}")
    print(f"📝 Total texts to process: {len(TEXT_LIST)}")
    print(f"🎨 Background color: {BG_COLOR}")
    print(f"🐍 Python: {sys.executable}")
    print("-" * 50)

    success_count = 0

    for index, text in enumerate(TEXT_LIST, start=1):
        safe_filename = "".join(c if c.isalnum() or c in "._- " else "_" for c in text)
        safe_filename = safe_filename.replace(" ", "_")

        filename = f"{index:03d}_{safe_filename}.png"
        output_path = os.path.join(OUTPUT_DIR, filename)

        print(f"🔄 [{index}/{len(TEXT_LIST)}] Generating: '{text}'")
        print(f"   → Output: {output_path}")

        try:
            render_one(text, output_path, BG_COLOR)
            print(f"   ✅ Success: {filename}")
            success_count += 1
        except SystemExit as e:
            # text_as_image.main() calls sys.exit(1) on failure
            if e.code in (0, None):
                print(f"   ✅ Success: {filename}")
                success_count += 1
            else:
                print(f"   ❌ Failed (exit code {e.code})")
        except Exception as e:
            print(f"   ❌ Unexpected error: {e}")

        print("-" * 50)

    print(f"\n✨ Done. {success_count}/{len(TEXT_LIST)} images generated in: {OUTPUT_DIR}")


if __name__ == "__main__":
    generate_images()
