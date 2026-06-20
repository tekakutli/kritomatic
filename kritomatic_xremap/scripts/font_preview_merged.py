#!/usr/bin/env python3
"""
Font Preview Merger - Fast version using Pillow
Loops through all fonts in ~/.local/share/fonts/, generates preview images,
adds font name and path labels, and merges them vertically.
Also outputs a text file with all font paths.
"""

import os
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import argparse
import re

# Configuration
TEXT_TO_RENDER = "The quick brown fox jumps over the lazy dog"
FONT_SIZE = 60
TEXT_COLOR = "black"
BG_COLOR = "white"
OUTPUT_FILE = "font_preview_merged.png"
FONT_DIR = os.path.expanduser("~/.local/share/fonts/")
LABEL_FONT_SIZE = 24
LABEL_COLOR = "#2c3e50"
LABEL_BG_COLOR = "#f0f0f0"
PATH_LABEL_FONT_SIZE = 16
PATH_LABEL_COLOR = "#666666"
ENABLE_LABELS = True

def get_all_fonts():
    """Get list of all installed fonts from ~/.local/share/fonts/."""
    fonts = []

    font_dir = Path(FONT_DIR)
    if not font_dir.exists():
        print(f"Font directory not found: {FONT_DIR}")
        return []

    print(f"Scanning fonts in: {FONT_DIR}")

    # Walk through the font directory
    for ext in ['*.ttf', '*.otf', '*.ttc']:
        for font_file in font_dir.rglob(ext):
            font_path = str(font_file)

            # Try to get the font family name using fc-list
            try:
                result = subprocess.run(
                    ['fc-list', '-f', '%{family}', font_path],
                    capture_output=True,
                    text=True,
                    check=True
                )
                family_name = result.stdout.strip()
                if family_name:
                    family_name = family_name.split(',')[0].strip()
                else:
                    family_name = font_file.stem.replace('-', ' ').replace('_', ' ')
            except:
                family_name = font_file.stem.replace('-', ' ').replace('_', ' ')

            # Clean up the name
            family_name = re.sub(r'\b(Bold|Italic|Regular|Light|Thin|Medium|Black|Extra|Semi|Ultra|Oblique|Roman|Book|Demi|Heavy)\b', '', family_name, flags=re.I)
            family_name = re.sub(r'\s+', ' ', family_name).strip()

            if family_name:
                fonts.append({
                    'name': family_name,
                    'path': font_path
                })

    # Deduplicate by path
    seen_paths = set()
    unique_fonts = []
    for font in fonts:
        if font['path'] not in seen_paths:
            seen_paths.add(font['path'])
            unique_fonts.append(font)

    print(f"Found {len(unique_fonts)} fonts in {FONT_DIR}")
    return unique_fonts

def trim_image(img):
    """
    Trim empty borders from an image.
    Returns a cropped image with empty space removed.
    """
    bbox = img.getbbox()
    if bbox:
        return img.crop(bbox)
    return img

def create_text_preview(font_path, text, font_size, text_color, bg_color):
    """
    Create a text image using Pillow (much faster than Wand).
    Returns a PIL Image object.
    """
    # Load the font
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception as e:
        print(f"  Error loading font: {e}")
        return None

    # Create a temporary image to render text (larger to allow for measurement)
    temp_img = Image.new('RGBA', (2000, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp_img)

    # Draw text in the temporary image
    draw.text((10, 10), text, fill=text_color, font=font)

    # Trim to remove empty space
    trimmed = trim_image(temp_img)

    # Get the actual text dimensions
    text_width, text_height = trimmed.size

    # Add padding
    padding = 20
    img_width = text_width + (padding * 2)
    img_height = text_height + (padding * 2)

    # Create final image
    if bg_color == 'transparent':
        img = Image.new('RGBA', (img_width, img_height), (0, 0, 0, 0))
    else:
        img = Image.new('RGB', (img_width, img_height), bg_color)

    # Paste the trimmed text onto the final image with padding
    img.paste(trimmed, (padding, padding), trimmed if trimmed.mode == 'RGBA' else None)

    return img

def add_font_label(image, font_name, font_path, label_font_size=24,
                   label_color="#2c3e50", label_bg="#f0f0f0",
                   path_label_size=16, path_label_color="#666666"):
    """
    Add labels with the font name and path at the bottom of the image, left-aligned.
    Returns a new PIL Image.
    """
    # Try to load fonts for the labels
    try:
        label_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", label_font_size)
    except:
        try:
            label_font = ImageFont.truetype("/usr/share/fonts/noto/NotoSans-Regular.ttf", label_font_size)
        except:
            try:
                label_font = ImageFont.truetype("/usr/share/fonts/TTF/JetBrainsMono-Regular.ttf", label_font_size)
            except:
                label_font = ImageFont.load_default()

    try:
        path_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", path_label_size)
    except:
        try:
            path_font = ImageFont.truetype("/usr/share/fonts/noto/NotoSans-Regular.ttf", path_label_size)
        except:
            try:
                path_font = ImageFont.truetype("/usr/share/fonts/TTF/JetBrainsMono-Regular.ttf", path_label_size)
            except:
                path_font = ImageFont.load_default()

    # Create a temporary image to measure label text
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)

    # Measure font name label
    try:
        bbox = temp_draw.textbbox((0, 0), font_name, font=label_font)
        name_width = bbox[2] - bbox[0]
        name_height = bbox[3] - bbox[1]
    except AttributeError:
        name_width, name_height = temp_draw.textsize(font_name, font=label_font)

    # Measure path label
    try:
        bbox = temp_draw.textbbox((0, 0), font_path, font=path_font)
        path_width = bbox[2] - bbox[0]
        path_height = bbox[3] - bbox[1]
    except AttributeError:
        path_width, path_height = temp_draw.textsize(font_path, font=path_font)

    # Add padding around text
    label_padding_h = 15
    label_padding_v = 8
    name_label_height = name_height + (label_padding_v * 2)
    path_label_height = path_height + (label_padding_v * 2)
    total_label_height = name_label_height + path_label_height

    # Create a new image with extra height for the labels
    new_height = image.height + total_label_height
    new_img = Image.new('RGB', (image.width, new_height), label_bg)

    # Paste the original image
    new_img.paste(image, (0, 0))

    # Draw the font name label - left aligned with padding
    draw = ImageDraw.Draw(new_img)
    text_x = label_padding_h
    text_y = image.height + label_padding_v
    draw.text((text_x, text_y), font_name, fill=label_color, font=label_font)

    # Draw the path label - left aligned with padding, below the name
    path_y = image.height + name_label_height + label_padding_v
    draw.text((text_x, path_y), font_path, fill=path_label_color, font=path_font)

    return new_img

def generate_font_preview(font_info):
    """Generate a preview image for a single font using Pillow."""
    font_name = font_info['name']
    font_path = font_info['path']

    # Create the text image
    img = create_text_preview(
        font_path,
        TEXT_TO_RENDER,
        FONT_SIZE,
        TEXT_COLOR,
        BG_COLOR
    )

    if img is None:
        return None

    # Add label if enabled
    if ENABLE_LABELS:
        img = add_font_label(
            img,
            font_name,
            font_path,
            LABEL_FONT_SIZE,
            LABEL_COLOR,
            LABEL_BG_COLOR,
            PATH_LABEL_FONT_SIZE,
            PATH_LABEL_COLOR
        )

    return img

def merge_images_vertically(images, output_path):
    """Merge multiple PIL Images vertically into one."""
    if not images:
        print("No images to merge.")
        return False

    # Calculate total height
    total_height = sum(img.height for img in images)
    max_width = max(img.width for img in images)

    # Create new image
    merged = Image.new('RGB', (max_width, total_height), color='white')

    # Paste images
    y_offset = 0
    for img in images:
        merged.paste(img, (0, y_offset))
        y_offset += img.height

    # Save merged image
    merged.save(output_path)
    print(f"Merged image saved to: {output_path}")
    return True

def save_font_paths(fonts, output_path):
    """
    Save all font paths to a text file.
    """
    txt_path = Path(output_path).with_suffix('.txt')

    try:
        with open(txt_path, 'w') as f:
            f.write(f"# Font paths from: {FONT_DIR}\n")
            f.write(f"# Total fonts: {len(fonts)}\n")
            f.write(f"# Generated: {Path(output_path).name}\n")
            f.write("#" + "="*70 + "\n\n")

            for i, font in enumerate(fonts, 1):
                f.write(f"{i:04d}. {font['name']}\n")
                f.write(f"     {font['path']}\n\n")

        print(f"Font paths saved to: {txt_path}")
        return True
    except Exception as e:
        print(f"Error saving font paths: {e}")
        return False

def test_single_font(font_info):
    """Test generating a preview for a single font."""
    font_name = font_info['name']
    font_path = font_info['path']

    print(f"\nTesting font: {font_name}")
    print(f"Font path: {font_path}")

    img = generate_font_preview(font_info)

    if img:
        # Save to a temporary file for viewing
        temp_path = f"/tmp/test_{re.sub(r'[^a-zA-Z0-9_]', '_', font_name)}.png"
        img.save(temp_path)
        print(f"✓ Success! Image created: {temp_path}")
        print(f"  Image size: {img.size}")
        return True
    else:
        print(f"✗ Failed")
        return False

def main():
    global TEXT_TO_RENDER, FONT_SIZE, TEXT_COLOR, BG_COLOR, OUTPUT_FILE, FONT_DIR
    global LABEL_FONT_SIZE, LABEL_COLOR, LABEL_BG_COLOR, ENABLE_LABELS
    global PATH_LABEL_FONT_SIZE, PATH_LABEL_COLOR

    parser = argparse.ArgumentParser(description='Generate font preview collage from ~/.local/share/fonts/')
    parser.add_argument('--text', default=TEXT_TO_RENDER, help='Text to render in each font')
    parser.add_argument('--font-size', type=int, default=FONT_SIZE, help='Font size (default: 60)')
    parser.add_argument('--text-color', default=TEXT_COLOR, help='Text color (default: black)')
    parser.add_argument('--bg-color', default=BG_COLOR, help='Background color (default: white)')
    parser.add_argument('--output', default=OUTPUT_FILE, help='Output filename (e.g., preview.png)')
    parser.add_argument('--max-fonts', type=int, default=None, help='Maximum number of fonts to process (for testing)')
    parser.add_argument('--font-filter', default=None, help='Filter fonts by this substring (case insensitive)')
    parser.add_argument('--start-from', type=int, default=1, help='Start processing from this font index (for resuming)')
    parser.add_argument('--test', action='store_true', help='Test with a single font and show detailed output')
    parser.add_argument('--test-font', default=None, help='Test a specific font name (with --test)')
    parser.add_argument('--font-dir', default=FONT_DIR, help=f'Font directory to scan (default: {FONT_DIR})')
    parser.add_argument('--label-font-size', type=int, default=LABEL_FONT_SIZE, help='Font size for name labels (default: 24)')
    parser.add_argument('--label-color', default=LABEL_COLOR, help='Color for font name labels (default: #2c3e50)')
    parser.add_argument('--label-bg', default=LABEL_BG_COLOR, help='Background color for label area (default: #f0f0f0)')
    parser.add_argument('--path-label-size', type=int, default=PATH_LABEL_FONT_SIZE, help='Font size for path labels (default: 16)')
    parser.add_argument('--path-label-color', default=PATH_LABEL_COLOR, help='Color for path labels (default: #666666)')
    parser.add_argument('--no-labels', action='store_true', help='Disable font name labels')
    parser.add_argument('--no-txt', action='store_true', help='Disable text file output with font paths')
    args = parser.parse_args()

    # Update global config
    TEXT_TO_RENDER = args.text
    FONT_SIZE = args.font_size
    TEXT_COLOR = args.text_color
    BG_COLOR = args.bg_color
    OUTPUT_FILE = args.output
    FONT_DIR = args.font_dir
    LABEL_FONT_SIZE = args.label_font_size
    LABEL_COLOR = args.label_color
    LABEL_BG_COLOR = args.label_bg
    PATH_LABEL_FONT_SIZE = args.path_label_size
    PATH_LABEL_COLOR = args.path_label_color
    ENABLE_LABELS = not args.no_labels

    if ENABLE_LABELS:
        print(f"Font name labels enabled (font size: {LABEL_FONT_SIZE}, left-aligned)")
        print(f"Path labels enabled (font size: {PATH_LABEL_FONT_SIZE}, left-aligned)")
    else:
        print("Labels disabled")

    print(f"Fetching fonts from: {FONT_DIR}")
    fonts = get_all_fonts()

    if not fonts:
        print("No fonts found in ~/.local/share/fonts/!")
        return

    # If test mode, test a single font
    if args.test:
        if args.test_font:
            test_font = None
            for font in fonts:
                if args.test_font.lower() in font['name'].lower():
                    test_font = font
                    break
            if test_font:
                test_single_font(test_font)
            else:
                print(f"Font '{args.test_font}' not found")
        else:
            test_single_font(fonts[0])
        return

    # Filter fonts if requested
    if args.font_filter:
        filter_lower = args.font_filter.lower()
        fonts = [f for f in fonts if filter_lower in f['name'].lower()]
        print(f"Filtered to {len(fonts)} fonts matching '{args.font_filter}'")

    if args.max_fonts:
        fonts = fonts[:args.max_fonts]

    if args.start_from > 1:
        fonts = fonts[args.start_from - 1:]
        print(f"Starting from font index {args.start_from}")

    print(f"Processing {len(fonts)} fonts.\n")

    # Generate all previews
    preview_images = []
    failed_fonts = []
    success_count = 0

    for i, font_info in enumerate(fonts, 1):
        font_name = font_info['name']

        print(f"[{i}/{len(fonts)}]: {font_name}")

        # Generate preview
        img = generate_font_preview(font_info)

        if img:
            preview_images.append(img)
            success_count += 1
            print(f"  ✓ Generated ({success_count} so far)")
        else:
            failed_fonts.append(font_name)
            print(f"  ✗ Failed")

    # Show summary
    print(f"\n{'='*50}")
    print(f"Summary:")
    print(f"  Successfully generated: {len(preview_images)}")
    print(f"  Failed: {len(failed_fonts)}")

    if not preview_images:
        print("\nNo previews generated successfully!")
        return

    # Save the merged image
    print(f"\nMerging {len(preview_images)} images vertically...")
    merge_images_vertically(preview_images, args.output)

    # Save the text file with font paths (unless disabled)
    if not args.no_txt:
        print(f"\nSaving font paths to text file...")
        save_font_paths(fonts, args.output)

    print(f"\nDone! Output: {args.output}")

if __name__ == "__main__":
    main()
