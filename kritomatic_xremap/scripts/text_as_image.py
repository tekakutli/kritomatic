#!/usr/bin/env python3
"""
text_as_image.py

Generate a text image with customizable font, size, color, and background.
Uses Pillow for fast rendering.

The text to render comes from the TEXT_TO_RENDER variable, or from an
optional positional argument that overrides it. All styling and layout
options are configured via variables in the CONFIGURABLE SETTINGS block,
including the output path (OUTPUT_FILE).

Three rendering paths are chosen automatically based on the settings:

  - MULTILINE = True: use create_multiline_text().
  - SHADOW = True, or STROKE_COLOR set with STROKE_WIDTH > 0: use
    create_text_image_with_effects().
  - Otherwise: use create_text_image().

Usage:
    python text_as_image.py [text]

Examples:
    # Use TEXT_TO_RENDER variable
    python text_as_image.py

    # Override with a string
    python text_as_image.py "Hello, world!"
"""

import sys
import argparse
import os
from PIL import Image, ImageDraw, ImageFont

# ===== CONFIGURABLE SETTINGS =====
# The text to render. Overridden by the positional argument if provided.
TEXT_TO_RENDER = "Hello, world!"      # Use "\\n" for line breaks in multiline mode

# Output image path
OUTPUT_FILE = "text_as_image.png"

# Font
FONT_PATH = None                      # Path to a font file, or None to use fallbacks
FONT_SIZE = 100                       # Font size in points

# Colors
TEXT_COLOR = "black"
BG_COLOR = "transparent"              # "transparent" or a color name / hex

# Layout
PADDING = 20                          # Pixels of space around the text (0 = tight fit)

# Text effects
STROKE_COLOR = None                   # Stroke color for text outline, or None
STROKE_WIDTH = 0                      # Stroke width in pixels (0 = no stroke)
SHADOW = False                        # Enable drop shadow
SHADOW_OFFSET = (5, 5)                # Shadow offset as (x, y)
SHADOW_COLOR = "rgba(0,0,0,0.5)"      # Shadow color

# Multiline
MULTILINE = False                     # If True, treat the text as multiline
LINE_SPACING = 1.5                    # Line spacing multiplier for multiline
# =================================

def get_text_metrics(draw, text, font, stroke_width=0):
    """
    Get accurate text metrics including ascent and descent.
    Accounts for stroke width if present.
    Returns (width, height, ascent, descent).
    """
    try:
        # Get bounding box
        bbox = draw.textbbox((0, 0), text, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]
        ascent = -bbox[1]  # Distance from top to baseline
        descent = bbox[3] - ascent  # Distance from baseline to bottom

        # Add stroke width to dimensions
        if stroke_width > 0:
            width += stroke_width * 2
            height += stroke_width * 2
            ascent += stroke_width
            descent += stroke_width

        return width, height, ascent, descent
    except AttributeError:
        # Fallback for older PIL
        width, height = draw.textsize(text, font=font)
        # Approximate ascent/descent
        ascent = int(height * 0.8)
        descent = height - ascent

        # Add stroke width to dimensions
        if stroke_width > 0:
            width += stroke_width * 2
            height += stroke_width * 2
            ascent += stroke_width
            descent += stroke_width

        return width, height, ascent, descent

def trim_image(img):
    """
    Trim empty borders from an image.
    Returns a cropped image with empty space removed.
    """
    # Convert to RGB if necessary for comparison
    if img.mode == 'RGBA':
        # For RGBA, we need to check alpha channel
        bbox = img.getbbox()
        if bbox:
            return img.crop(bbox)
        return img
    else:
        # For RGB, use getbbox
        bbox = img.getbbox()
        if bbox:
            return img.crop(bbox)
        return img

def create_text_image(output_path, text, font_path=None, font_size=100,
                     text_color='black', bg_color='transparent',
                     padding=20):
    """
    Create an image with text using Pillow (fast).

    Padding: Adds space around the text.
    - padding=0 means NO extra space (tight fit, no cropping)
    - padding=20 means 20 pixels of space on all sides
    """
    # Load the font
    font = None
    if font_path and os.path.exists(font_path):
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception as e:
            print(f"Warning: Could not load font {font_path}: {e}")

    # Fallback fonts if specified font fails
    if font is None:
        fallback_fonts = [
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/ubuntu/Ubuntu-Regular.ttf',
            '/usr/share/fonts/noto/NotoSans-Regular.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            'C:/Windows/Fonts/arial.ttf'
        ]
        for f in fallback_fonts:
            if os.path.exists(f):
                try:
                    font = ImageFont.truetype(f, font_size)
                    break
                except:
                    continue

        # Ultimate fallback
        if font is None:
            font = ImageFont.load_default()

    # Create a temporary image to measure text
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)

    # Get accurate text metrics (no stroke for basic version)
    width, height, ascent, descent = get_text_metrics(temp_draw, text, font, stroke_width=0)

    # If text measurement failed, estimate
    if width == 0 or height == 0:
        width = int(len(text) * font_size * 0.55)
        height = int(font_size * 1.2)
        ascent = int(height * 0.8)
        descent = height - ascent

    # Create a temporary image to render text (larger to allow for measurement)
    # Use a generous size to ensure text fits
    temp_render = Image.new('RGBA', (width * 2, height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp_render)

    # Draw text centered in the temporary image
    text_x = (temp_render.width - width) // 2
    text_y = (temp_render.height - height) // 2 + ascent
    draw.text((text_x, text_y), text, fill=text_color, font=font)

    # Trim the image to remove empty space
    trimmed = trim_image(temp_render)

    # Get the actual text dimensions after trimming
    actual_width, actual_height = trimmed.size

    # Calculate final dimensions with padding
    if padding == 0:
        final_width = actual_width
        final_height = actual_height
        offset_x = 0
        offset_y = 0
    else:
        final_width = actual_width + (padding * 2)
        final_height = actual_height + (padding * 2)
        offset_x = padding
        offset_y = padding

    # Create final image
    if bg_color == 'transparent':
        img = Image.new('RGBA', (final_width, final_height), (0, 0, 0, 0))
    else:
        img = Image.new('RGB', (final_width, final_height), bg_color)

    # Paste the trimmed text onto the final image
    img.paste(trimmed, (offset_x, offset_y), trimmed if trimmed.mode == 'RGBA' else None)

    # Save the image
    img.save(output_path)

    print(f"Text image saved to {output_path}")
    print(f"Size: {final_width}x{final_height}")
    print(f"Text dimensions: {actual_width}x{actual_height}")
    print(f"Padding: {padding}px")
    print(f"Text: '{text}'")
    print(f"Font size: {font_size}")
    if font_path:
        print(f"Font: {font_path}")

def create_text_image_with_effects(output_path, text, font_path=None, font_size=100,
                                  text_color='black', bg_color='transparent',
                                  padding=20,
                                  stroke_color=None, stroke_width=0,
                                  shadow=False, shadow_offset=(5, 5),
                                  shadow_color='rgba(0,0,0,0.5)'):
    """
    Create a text image with additional effects like stroke and shadow.
    Uses Pillow for fast rendering.
    """
    # Load the font
    font = None
    if font_path and os.path.exists(font_path):
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception as e:
            print(f"Warning: Could not load font {font_path}: {e}")

    # Fallback fonts
    if font is None:
        fallback_fonts = [
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/ubuntu/Ubuntu-Regular.ttf',
            '/usr/share/fonts/noto/NotoSans-Regular.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            'C:/Windows/Fonts/arial.ttf'
        ]
        for f in fallback_fonts:
            if os.path.exists(f):
                try:
                    font = ImageFont.truetype(f, font_size)
                    break
                except:
                    continue

        if font is None:
            font = ImageFont.load_default()

    # Create a temporary image to measure text
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)

    # Get accurate text metrics WITH stroke width accounted
    width, height, ascent, descent = get_text_metrics(temp_draw, text, font, stroke_width)

    if width == 0 or height == 0:
        width = int(len(text) * font_size * 0.55)
        height = int(font_size * 1.2)
        ascent = int(height * 0.8)
        descent = height - ascent
        if stroke_width > 0:
            width += stroke_width * 2
            height += stroke_width * 2
            ascent += stroke_width
            descent += stroke_width

    # Create a temporary image to render text (larger to allow for effects)
    temp_render = Image.new('RGBA', (width * 2, height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp_render)

    # Center text in temporary image
    text_x = (temp_render.width - width) // 2
    text_y = (temp_render.height - height) // 2 + ascent

    # Draw shadow if enabled
    if shadow:
        shadow_offset_x, shadow_offset_y = shadow_offset
        shadow_color_rgb = (0, 0, 0)
        if shadow_color.startswith('rgba'):
            import re
            match = re.match(r'rgba\((\d+),\s*(\d+),\s*(\d+),\s*[\d.]+\)', shadow_color)
            if match:
                shadow_color_rgb = tuple(map(int, match.groups()))
        elif shadow_color.startswith('#'):
            shadow_color_rgb = tuple(int(shadow_color[i:i+2], 16) for i in (1, 3, 5))

        shadow_x = text_x + shadow_offset_x
        shadow_y = text_y + shadow_offset_y
        draw.text((shadow_x, shadow_y), text, fill=shadow_color_rgb, font=font)

    # Draw stroke if enabled
    if stroke_color and stroke_width > 0:
        offsets = []
        for dx in range(-stroke_width, stroke_width + 1):
            for dy in range(-stroke_width, stroke_width + 1):
                if dx == 0 and dy == 0:
                    continue
                if dx*dx + dy*dy <= stroke_width*stroke_width:
                    offsets.append((dx, dy))

        for dx, dy in offsets:
            draw.text((text_x + dx, text_y + dy), text, fill=stroke_color, font=font)

    # Draw main text
    draw.text((text_x, text_y), text, fill=text_color, font=font)

    # Trim the image to remove empty space
    trimmed = trim_image(temp_render)

    # Get the actual text dimensions after trimming
    actual_width, actual_height = trimmed.size

    # Calculate final dimensions with padding
    if padding == 0:
        final_width = actual_width
        final_height = actual_height
        offset_x = 0
        offset_y = 0
    else:
        final_width = actual_width + (padding * 2)
        final_height = actual_height + (padding * 2)
        offset_x = padding
        offset_y = padding

    # Create final image
    if bg_color == 'transparent':
        img = Image.new('RGBA', (final_width, final_height), (0, 0, 0, 0))
    else:
        img = Image.new('RGB', (final_width, final_height), bg_color)

    # Paste the trimmed text onto the final image
    img.paste(trimmed, (offset_x, offset_y), trimmed if trimmed.mode == 'RGBA' else None)

    # Save the image
    img.save(output_path)

    print(f"Text image saved to {output_path}")
    print(f"Size: {final_width}x{final_height}")
    print(f"Text dimensions: {actual_width}x{actual_height}")
    print(f"Padding: {padding}px")
    print(f"Effects: stroke={stroke_width if stroke_color else 0}, shadow={shadow}")
    if stroke_color and stroke_width > 0:
        print(f"Stroke width: {stroke_width}, Stroke color: {stroke_color}")

def create_multiline_text(output_path, text, font_path=None, font_size=100,
                         text_color='black', bg_color='transparent',
                         padding=20, line_spacing=1.5):
    """
    Create an image with multiline text using Pillow.
    """
    # Split text into lines
    lines = text.split('\n')
    while lines and lines[-1] == '':
        lines.pop()

    if not lines:
        lines = [text]

    print(f"Rendering {len(lines)} lines:")
    for i, line in enumerate(lines):
        print(f"  Line {i+1}: '{line}'")

    # Load the font
    font = None
    if font_path and os.path.exists(font_path):
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception as e:
            print(f"Warning: Could not load font {font_path}: {e}")

    # Fallback fonts
    if font is None:
        fallback_fonts = [
            '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            '/usr/share/fonts/truetype/ubuntu/Ubuntu-Regular.ttf',
            '/usr/share/fonts/noto/NotoSans-Regular.ttf',
            '/System/Library/Fonts/Helvetica.ttc',
            'C:/Windows/Fonts/arial.ttf'
        ]
        for f in fallback_fonts:
            if os.path.exists(f):
                try:
                    font = ImageFont.truetype(f, font_size)
                    break
                except:
                    continue

        if font is None:
            font = ImageFont.load_default()

    # Measure each line
    temp_img = Image.new('RGB', (1, 1))
    temp_draw = ImageDraw.Draw(temp_img)

    max_width = 0
    line_heights = []
    total_text_height = 0
    max_ascent = 0
    max_descent = 0

    for line in lines:
        width, height, ascent, descent = get_text_metrics(temp_draw, line, font, stroke_width=0)

        max_width = max(max_width, width)
        line_heights.append(height)
        total_text_height += height
        max_ascent = max(max_ascent, ascent)
        max_descent = max(max_descent, descent)

    # Add line spacing
    if len(lines) > 1:
        total_text_height += int((len(lines) - 1) * font_size * (line_spacing - 1))

    if max_width == 0 or total_text_height == 0:
        max_width = max([len(line) for line in lines]) * font_size * 0.55
        total_text_height = int(len(lines) * font_size * line_spacing)
        max_ascent = int(font_size * 0.8)
        max_descent = int(font_size * 0.2)

    # Create a temporary image to render text
    temp_render = Image.new('RGBA', (max_width * 2, total_text_height * 2), (0, 0, 0, 0))
    draw = ImageDraw.Draw(temp_render)

    # Draw each line
    y_pos = (temp_render.height - total_text_height) // 2
    for i, line in enumerate(lines):
        width, height, ascent, descent = get_text_metrics(temp_draw, line, font, stroke_width=0)
        text_x = (temp_render.width - max_width) // 2
        text_y = y_pos + ascent
        draw.text((text_x, text_y), line, fill=text_color, font=font)
        y_pos += height + int(font_size * (line_spacing - 1))

    # Trim the image to remove empty space
    trimmed = trim_image(temp_render)

    # Get the actual text dimensions after trimming
    actual_width, actual_height = trimmed.size

    # Calculate final dimensions with padding
    if padding == 0:
        final_width = actual_width
        final_height = actual_height
        offset_x = 0
        offset_y = 0
    else:
        final_width = actual_width + (padding * 2)
        final_height = actual_height + (padding * 2)
        offset_x = padding
        offset_y = padding

    # Create final image
    if bg_color == 'transparent':
        img = Image.new('RGBA', (final_width, final_height), (0, 0, 0, 0))
    else:
        img = Image.new('RGB', (final_width, final_height), bg_color)

    # Paste the trimmed text onto the final image
    img.paste(trimmed, (offset_x, offset_y), trimmed if trimmed.mode == 'RGBA' else None)

    # Save the image
    img.save(output_path)

    print(f"Multiline text image saved to {output_path}")
    print(f"Size: {final_width}x{final_height}")
    print(f"Text dimensions: {actual_width}x{actual_height}")
    print(f"Padding: {padding}px")

def main():
    parser = argparse.ArgumentParser(
        description='Generate a text image. Configure via variables in the script; '
                    'the only positional argument overrides TEXT_TO_RENDER.',
        epilog='Examples:\n'
               '  python text_as_image.py\n'
               '  python text_as_image.py "Hello, world!"\n'
               '  python text_as_image.py "Line1\\nLine2"    (with MULTILINE = True in the script)'
    )
    parser.add_argument('text', nargs='?', default=None,
                        help='Text to render (overrides TEXT_TO_RENDER variable)')
    args = parser.parse_args()

    # Resolve the text: positional overrides variable
    text_to_render = args.text if args.text is not None else TEXT_TO_RENDER
    if not text_to_render:
        print("Error: No text provided (set TEXT_TO_RENDER or pass text as an argument)")
        sys.exit(1)

    try:
        if MULTILINE:
            create_multiline_text(
                OUTPUT_FILE, text_to_render,
                font_path=FONT_PATH,
                font_size=FONT_SIZE,
                text_color=TEXT_COLOR,
                bg_color=BG_COLOR,
                padding=PADDING,
                line_spacing=LINE_SPACING
            )
        elif SHADOW or (STROKE_COLOR and STROKE_WIDTH > 0):
            create_text_image_with_effects(
                OUTPUT_FILE, text_to_render,
                font_path=FONT_PATH,
                font_size=FONT_SIZE,
                text_color=TEXT_COLOR,
                bg_color=BG_COLOR,
                padding=PADDING,
                stroke_color=STROKE_COLOR,
                stroke_width=STROKE_WIDTH,
                shadow=SHADOW,
                shadow_offset=SHADOW_OFFSET,
                shadow_color=SHADOW_COLOR
            )
        else:
            create_text_image(
                OUTPUT_FILE, text_to_render,
                font_path=FONT_PATH,
                font_size=FONT_SIZE,
                text_color=TEXT_COLOR,
                bg_color=BG_COLOR,
                padding=PADDING
            )
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
