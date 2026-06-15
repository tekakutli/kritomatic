#!/home/tekakutli/code/kritomatic-auxiliary/bin/python
from PIL import Image
import sys
from collections import Counter
import warnings

# Suppress deprecation warnings for cleaner output
warnings.filterwarnings("ignore", category=DeprecationWarning)

def detect_background_color(image_path, edge_thickness=10):
    """
    Detect background color by sampling edges of the image.
    """
    # Open image and convert to RGB to ensure consistent format
    img = Image.open(image_path).convert('RGB')
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
    hex_color = '#{:02x}{:02x}{:02x}'.format(most_common_color[0], most_common_color[1], most_common_color[2])
    
    return most_common_color, hex_color

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python color-thief-py.py <image_path>")
        sys.exit(1)
    
    image_path = sys.argv[1]
    rgb_color, hex_color = detect_background_color(image_path)
    
    # Clean output format - easy to parse
    print(f"{hex_color}")
