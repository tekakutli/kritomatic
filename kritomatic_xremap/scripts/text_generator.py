#!/usr/bin/env python3
"""
Generate a text image with customizable font, size, color, and background.
Uses Pillow for fast rendering (10-20x faster than Wand).
Maintains the same API as the original Wand version.
"""

import sys
import argparse
import os
from PIL import Image, ImageDraw, ImageFont, ImageChops

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
        description='Generate a text image for use with the pattern generator'
    )
    parser.add_argument('--output', required=True, help='Output image path')
    parser.add_argument('--text', required=True, help='Text to render. For multiline, use: --multiline "Line1\\nLine2\\nLine3"')
    parser.add_argument('--font', help='Path to font file')
    parser.add_argument('--font-size', type=int, default=100,
                       help='Font size in points (default: 100)')
    parser.add_argument('--text-color', default='black',
                       help='Text color (default: black)')
    parser.add_argument('--bg-color', default='transparent',
                       help='Background color (default: transparent)')
    parser.add_argument('--padding', type=int, default=20,
                       help='Padding around text in pixels. 0 = tight fit (default: 20)')
    parser.add_argument('--stroke-color', help='Stroke color for text outline')
    parser.add_argument('--stroke-width', type=int, default=0,
                       help='Stroke width for text outline')
    parser.add_argument('--shadow', action='store_true',
                       help='Add shadow to text')
    parser.add_argument('--shadow-offset', default='5,5',
                       help='Shadow offset as "x,y" (default: 5,5)')
    parser.add_argument('--shadow-color', default='rgba(0,0,0,0.5)',
                       help='Shadow color (default: rgba(0,0,0,0.5))')
    parser.add_argument('--multiline', action='store_true',
                       help='Treat text as multiline (split on newlines)')
    parser.add_argument('--line-spacing', type=float, default=1.5,
                       help='Line spacing for multiline text (default: 1.5)')

    args = parser.parse_args()

    try:
        if args.multiline:
            create_multiline_text(
                args.output, args.text,
                font_path=args.font,
                font_size=args.font_size,
                text_color=args.text_color,
                bg_color=args.bg_color,
                padding=args.padding,
                line_spacing=args.line_spacing
            )
        elif args.shadow or (args.stroke_color and args.stroke_width > 0):
            shadow_offset = tuple(map(int, args.shadow_offset.split(',')))

            create_text_image_with_effects(
                args.output, args.text,
                font_path=args.font,
                font_size=args.font_size,
                text_color=args.text_color,
                bg_color=args.bg_color,
                padding=args.padding,
                stroke_color=args.stroke_color,
                stroke_width=args.stroke_width,
                shadow=args.shadow,
                shadow_offset=shadow_offset,
                shadow_color=args.shadow_color
            )
        else:
            create_text_image(
                args.output, args.text,
                font_path=args.font,
                font_size=args.font_size,
                text_color=args.text_color,
                bg_color=args.bg_color,
                padding=args.padding
            )
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
