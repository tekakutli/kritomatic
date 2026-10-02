#!/usr/bin/env python3
"""
Stage 2 — Text to Image Generator
Renders each string in TEXT_LIST as an image via text_as_image.py.
Writes to TEXT_IMAGES_DIR from paths.py.
"""

import os
import sys
import subprocess
from pathlib import Path

from paths import TEXT_IMAGES_DIR

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

# Target script in the parent scripts/ directory
TEXT_GENERATOR_SCRIPT = Path(__file__).resolve().parent.parent / "text_as_image.py"
# =========================


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

        cmd = [
            sys.executable,
            str(TEXT_GENERATOR_SCRIPT),
            "--text", text,
            "--output", output_path,
            "--bg-color", BG_COLOR,
        ]

        print(f"🔄 [{index}/{len(TEXT_LIST)}] Generating: '{text}'")
        print(f"   → Output: {output_path}")

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"   ✅ Success: {filename}")
            success_count += 1
            if result.stdout:
                print(f"   📤 Output: {result.stdout.strip()}")
        except subprocess.CalledProcessError as e:
            print(f"   ❌ Error generating '{text}'")
            print(f"   Error code: {e.returncode}")
            if e.stderr:
                print(f"   Error message: {e.stderr.strip()}")
        except FileNotFoundError:
            print(f"   ❌ Error: Python script not found at {TEXT_GENERATOR_SCRIPT}")
            print("   Please check the path and update TEXT_GENERATOR_SCRIPT")
            break
        except Exception as e:
            print(f"   ❌ Unexpected error: {str(e)}")

        print("-" * 50)

    print(f"\n✨ Done. {success_count}/{len(TEXT_LIST)} images generated in: {OUTPUT_DIR}")


if __name__ == "__main__":
    generate_images()
