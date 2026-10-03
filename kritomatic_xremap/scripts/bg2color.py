#!/usr/bin/env python3
"""
bg2color.py

Detect the dominant background color of an image by sampling pixels along its
outer edges. Prints the detected color as a hex string, e.g. #f0f0f0.

By default, the image path is taken from the IMAGE_PATH variable below.
If a single positional argument is given on the command line, it overrides
IMAGE_PATH.

Usage:
    python bg2color.py [image_path]

"""

from PIL import Image
from collections import Counter
import warnings
import sys

# Suppress deprecation warnings for cleaner output
warnings.filterwarnings("ignore", category=DeprecationWarning)

# === DEFAULT INPUT ===
IMAGE_PATH = "path/to/image.png"  # <-- change this to your default image file
# =====================


def detect_background_color(image_path, edge_thickness=10):
    """
    Detect background color by sampling edges of the image.

    Args:
        image_path (str): Path to the input image.
        edge_thickness (int): Thickness in pixels of the border region to sample.

    Returns:
        tuple: (RGB tuple, hex string) for the most common edge color.
    """
    # Open image and convert to RGB to ensure consistent format
    img = Image.open(image_path).convert("RGB")
    width, height = img.size

    # Collect pixels from all four edges
    edge_pixels = []

    # Top edge
    top_region = img.crop((0, 0, width, edge_thickness))
    edge_pixels.extend(list(top_region.getdata()))

    # Bottom edge
    bottom_region = img.crop((0, height - edge_thickness, width, height))
    edge_pixels.extend(list(bottom_region.getdata()))

    # Left edge (excluding corners already sampled)
    if height > 2 * edge_thickness:
        left_region = img.crop((0, edge_thickness, edge_thickness, height - edge_thickness))
        edge_pixels.extend(list(left_region.getdata()))

    # Right edge (excluding corners already sampled)
    if height > 2 * edge_thickness:
        right_region = img.crop((width - edge_thickness, edge_thickness, width, height - edge_thickness))
        edge_pixels.extend(list(right_region.getdata()))

    # Find the most common color (for solid backgrounds)
    color_counts = Counter(edge_pixels)
    most_common_color = color_counts.most_common(1)[0][0]

    # Convert to hex
    hex_color = "#{:02x}{:02x}{:02x}".format(
        most_common_color[0],
        most_common_color[1],
        most_common_color[2],
    )

    return most_common_color, hex_color


if __name__ == "__main__":
    if len(sys.argv) > 2:
        print("Usage: python bg2color.py [image_path]")
        sys.exit(1)

    # Use positional argument if provided, otherwise fall back to IMAGE_PATH
    image_path = sys.argv[1] if len(sys.argv) == 2 else IMAGE_PATH

    rgb_color, hex_color = detect_background_color(image_path)

    # Clean output format - easy to parse
    print(f"{hex_color}")
