#!/usr/bin/env python3
"""
Text to Image Generator
Generates images for a list of text strings using the text_generator.py script
"""

import subprocess
import os
from pathlib import Path

# ===== CONFIGURATION =====
# List of text strings to generate images for
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
    "Burguer"
]
# TEXT_LIST = [
#     "Argentina",
#     "Brasil",
#     "Canadá",
#     "Colombia",
#     "Ecuador",
#     "Egipto",
#     "Inglaterra",
#     "Francia",
#     "Alemania",
#     "Irán",
#     "Japón",
#     "México",
#     "Paises Bajos",
#     "Noruega",
#     "Portugal",
#     "Corea del Sur",
#     "Suiza",
#     "España",
#     "Turquía",
#     "Estados Unidos"
# ]
# TEXT_LIST = [
#     "Hello World",
#     "Python Script",
#     "Image Generator",
#     "Custom Text",
#     "Sample Text 123",
#     "Another Example",
#     "Testing 1 2 3",
#     "Text Generation",
#     "Output Image",
#     "Final Test"
# ]


# Output directory for generated images
OUTPUT_DIR = "/tmp/generated_images"

# Background color for the images
# Set to 'transparent' for transparent background, or any color name/hex value
# Examples: 'white', 'black', '#FFFFFF', '#000000', 'transparent'
BG_COLOR = "white"  # Change this to 'transparent' for transparent background

# Path to the text_generator.py script
# TEXT_GENERATOR_SCRIPT = "/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/text_generator.py"
TEXT_GENERATOR_SCRIPT = "/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/text_generator.py"


# ===== END CONFIGURATION =====


def generate_images():
    """Generate images for all text strings in the list"""

    # Create output directory if it doesn't exist
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print(f"📁 Output directory: {OUTPUT_DIR}")
    print(f"📝 Total texts to process: {len(TEXT_LIST)}")
    print(f"🎨 Background color: {BG_COLOR}")
    print("-" * 50)

    for index, text in enumerate(TEXT_LIST, start=1):
        # Create filename from text (sanitize for filesystem)
        # Replace spaces and special characters with underscores
        safe_filename = "".join(c if c.isalnum() or c in "._- " else "_" for c in text)
        safe_filename = safe_filename.replace(" ", "_")

        # Format: {index:03d}_{text}.png (e.g., 001_Hello_World.png)
        filename = f"{index:03d}_{safe_filename}.png"
        output_path = os.path.join(OUTPUT_DIR, filename)

        # Build the command
        cmd = [
            "python",
            TEXT_GENERATOR_SCRIPT,
            "--text", text,
            "--output", output_path,
            "--bg-color", BG_COLOR
        ]

        print(f"🔄 [{index}/{len(TEXT_LIST)}] Generating: '{text}'")
        print(f"   → Output: {output_path}")

        try:
            # Execute the command
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            print(f"   ✅ Success: {filename}")

            # Print any output from the script (if verbose)
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

    print(f"\n✨ All done! Generated {len(TEXT_LIST)} images in: {OUTPUT_DIR}")


if __name__ == "__main__":
    generate_images()
