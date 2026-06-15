#!/home/tekakutli/code/kritomatic-auxiliary/bin/python
"""
Expand canvas of background image using detected background color
"""

# ===== CONFIGURABLE SETTINGS =====
# Expansion proportion relative to image dimensions (0 to 1)
# 0.5 means add 50% to each side (total width/height becomes 2x original)
EXPAND_PROPORTION = 0.5  # Add this much extra space around the image

# Set to True to expand proportionally in both dimensions, False to expand independently
EXPAND_BOTH_DIMENSIONS = True  # If True, uses EXPAND_PROPORTION for both width and height
# Override specific dimensions if EXPAND_BOTH_DIMENSIONS is False
EXPAND_WIDTH_PROPORTION = 0.5   # Horizontal expansion (0.5 = add 50% width)
EXPAND_HEIGHT_PROPORTION = 0.5  # Vertical expansion (0.5 = add 50% height)

# Background color detection edge thickness (pixels to sample from edges)
EDGE_THICKNESS = 10

# Enable debug output
DEBUG = False

# =================================

import subprocess
import sys
import os
import re
from pathlib import Path

def background_color(image_path, edge_thickness=10):
    """
    Call the background_color script to detect background color
    Returns hex color string like '#e78c14'
    """
    script_path = "/home/tekakutli/files/org/dotfiles/input_controller/krita_plugin/kritomatic/kritomatic_xremap/scripts/color-thief-py.py"
    
    try:
        result = subprocess.run(
            [script_path, image_path],
            capture_output=True,
            text=True,
            check=True
        )
        # The script now outputs just the hex color
        hex_color = result.stdout.strip()
        
        # Validate hex color format
        if re.match(r'^#[0-9a-fA-F]{6}$', hex_color):
            return hex_color
        else:
            print(f"Warning: Invalid hex color format: {hex_color}")
            return "#000000"  # Default to black
            
    except subprocess.CalledProcessError as e:
        print(f"Error calling background_color: {e}")
        print(f"Error output: {e.stderr}")
        return "#000000"
    except FileNotFoundError:
        print(f"Error: background_color script not found at {script_path}")
        return "#000000"

def expand_canvas(input_path, output_path, hex_color, expand_proportion, expand_both=True, 
                  expand_width_prop=None, expand_height_prop=None):
    """
    Expand canvas of image using ImageMagick
    
    Args:
        input_path: Path to input image
        output_path: Path to output image
        hex_color: Background color in hex format (e.g., '#e78c14')
        expand_proportion: Proportion to expand (0-1)
        expand_both: If True, use same proportion for width and height
        expand_width_prop: Specific width expansion proportion (if expand_both is False)
        expand_height_prop: Specific height expansion proportion (if expand_both is False)
    """
    try:
        # Get original dimensions
        result = subprocess.run(
            ['magick', 'identify', '-format', '%wx%h', input_path],
            capture_output=True,
            text=True,
            check=True
        )
        width, height = map(int, result.stdout.strip().split('x'))
        
        if DEBUG:
            print(f"Original dimensions: {width}x{height}")
        
        # Calculate new dimensions
        if expand_both:
            expand_width = expand_proportion
            expand_height = expand_proportion
        else:
            expand_width = expand_width_prop if expand_width_prop is not None else expand_proportion
            expand_height = expand_height_prop if expand_height_prop is not None else expand_proportion
        
        # Calculate new dimensions (expand by adding proportions to each side)
        new_width = int(width * (1 + expand_width))
        new_height = int(height * (1 + expand_height))
        
        if DEBUG:
            print(f"New dimensions: {new_width}x{new_height}")
            print(f"Background color: {hex_color}")
        
        # Expand canvas using ImageMagick
        subprocess.run(
            ['magick', input_path, 
             '-background', hex_color,
             '-gravity', 'center',
             '-extent', f'{new_width}x{new_height}',
             output_path],
            check=True,
            capture_output=True
        )
        
        print(f"✓ Expanded canvas from {width}x{height} to {new_width}x{new_height}")
        print(f"  Added {expand_width*100:.1f}% to width, {expand_height*100:.1f}% to height")
        print(f"  Background color: {hex_color}")
        print(f"  Saved to: {output_path}")
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"Error expanding canvas: {e}")
        if e.stderr:
            print(f"Error details: {e.stderr.decode() if isinstance(e.stderr, bytes) else e.stderr}")
        return False
    except FileNotFoundError:
        print("Error: ImageMagick 'magick' command not found. Please install ImageMagick.")
        return False
    except Exception as e:
        print(f"Unexpected error: {e}")
        return False

def process_image(image_path, output_path=None):
    """
    Main function to process image: detect background color and expand canvas
    
    Args:
        image_path: Path to input image
        output_path: Optional output path (auto-generated if not provided)
    
    Returns:
        tuple: (success, output_path, hex_color)
    """
    # Check if input file exists
    if not os.path.exists(image_path):
        print(f"Error: Input file not found: {image_path}")
        return False, None, None
    
    # Detect background color
    print(f"Detecting background color for: {image_path}")
    hex_color = background_color(image_path, EDGE_THICKNESS)
    print(f"Detected background color: {hex_color}")
    
    # Generate output path if not provided
    if output_path is None:
        input_file = Path(image_path)
        output_path = input_file.parent / f"{input_file.stem}_expanded{input_file.suffix}"
    
    # Expand canvas
    success = expand_canvas(
        image_path, output_path, hex_color, EXPAND_PROPORTION,
        EXPAND_BOTH_DIMENSIONS, EXPAND_WIDTH_PROPORTION, EXPAND_HEIGHT_PROPORTION
    )
    
    return success, str(output_path), hex_color

def main():
    # Parse command line arguments
    if len(sys.argv) < 2:
        print("Usage: python expand_background.py <image_path> [output_path]")
        print("\nExample:")
        print("  python expand_background.py image.png")
        print("  python expand_background.py image.png expanded_image.png")
        print("\nConfigurable settings at the top of the script:")
        print(f"  EXPAND_PROPORTION = {EXPAND_PROPORTION} (adds {EXPAND_PROPORTION*100}% to each side)")
        print(f"  EXPAND_BOTH_DIMENSIONS = {EXPAND_BOTH_DIMENSIONS}")
        if not EXPAND_BOTH_DIMENSIONS:
            print(f"  EXPAND_WIDTH_PROPORTION = {EXPAND_WIDTH_PROPORTION}")
            print(f"  EXPAND_HEIGHT_PROPORTION = {EXPAND_HEIGHT_PROPORTION}")
        sys.exit(1)
    
    image_path = sys.argv[1]
    output_path = sys.argv[2] if len(sys.argv) > 2 else None
    
    # Process the image
    success, final_output_path, hex_color = process_image(image_path, output_path)
    
    if success:
        print(f"\n✓ Successfully processed!")
        print(f"  Output: {final_output_path}")
        print(f"  Background color used: {hex_color}")
        sys.exit(0)
    else:
        print(f"\n✗ Failed to process image")
        sys.exit(1)

if __name__ == "__main__":
    main()
