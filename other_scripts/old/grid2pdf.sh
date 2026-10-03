#!/bin/bash

# ============================================
# DYNAMIC GRID TO PDF CONVERTER (PER-EDGE MARGINS)
# ============================================
# A script that scales and splits an image into parts for printing.
# Each page can have different margins per edge (top, bottom, left, right).
#
# USAGE:
#   ./script.sh image.jpg                # Normal mode (scales to fit US Letter)
#   ./script.sh image.jpg --raw          # Raw mode (exact pixel dimensions, no scaling)
#   ./script.sh image.jpg -r             # Raw mode (short flag)
# ============================================

# ========== YOU ONLY EDIT THESE VALUES ==========
INPUT_IMAGE="$1"                    # First argument: path to your image
OUTPUT_PDF="output.pdf"             # Output PDF filename

# Check for raw mode flag
RAW_MODE=0
if [ "$2" = "--raw" ] || [ "$2" = "-r" ]; then
    RAW_MODE=1
fi

C=2                                 # How many pages across (columns)
R=3                                 # How many pages down (rows)

# Per-edge margins in pixels (150px = 0.5 inches at 300 DPI)
MARGIN_TOP=50
MARGIN_BOTTOM=250
MARGIN_LEFT=150
MARGIN_RIGHT=150
# ===================================================

# US Letter size at 300 DPI (fixed, do not change)
PAGE_WIDTH=2550
PAGE_HEIGHT=3300

# Calculate live area per page (subtract margins from each side)
LIVE_WIDTH=$((PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT))
LIVE_HEIGHT=$((PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM))

# Calculate total grid size
TOTAL_GRID_WIDTH=$((LIVE_WIDTH * C))
TOTAL_GRID_HEIGHT=$((LIVE_HEIGHT * R))

# Calculate total pages
TOTAL_PAGES=$((C * R))

# Check if input image provided
if [ -z "$INPUT_IMAGE" ]; then
    echo "Usage: ./script.sh your_image.jpg [--raw|-r]"
    echo ""
    echo "Options:"
    echo "  --raw, -r    Raw mode: output exact pixel dimensions (no Ghostscript scaling)"
    echo "               (default: scales to fit US Letter paper)"
    echo ""
    echo "Current configuration:"
    echo "  Grid: ${C} columns × ${R} rows"
    echo "  Margins (px): T=${MARGIN_TOP} B=${MARGIN_BOTTOM} L=${MARGIN_LEFT} R=${MARGIN_RIGHT}"
    echo "  Live area per page: ${LIVE_WIDTH}×${LIVE_HEIGHT} px"
    echo "  Total grid size: ${TOTAL_GRID_WIDTH}×${TOTAL_GRID_HEIGHT} px"
    echo "  Total pages: ${TOTAL_PAGES}"
    exit 1
fi

# Check ImageMagick
if ! command -v magick &> /dev/null && ! command -v convert &> /dev/null; then
    echo "Error: ImageMagick not found."
    exit 1
fi

CMD=$(command -v magick 2>/dev/null || command -v convert)

# ========== NEW: Auto-rotate if width > height ==========
echo "=============================================="
echo "Dynamic Grid to PDF Converter (Per-Edge Margins)"
echo "=============================================="

# Get original image dimensions
ORIG_WIDTH=$($CMD "$INPUT_IMAGE" -format "%w" info:)
ORIG_HEIGHT=$($CMD "$INPUT_IMAGE" -format "%h" info:)

echo "Original dimensions: ${ORIG_WIDTH}×${ORIG_HEIGHT}"

if [ $ORIG_WIDTH -gt $ORIG_HEIGHT ]; then
    echo "Width > Height: Rotating image 90 degrees..."
    ROTATED_IMAGE="rotated_$$_${INPUT_IMAGE##*/}"
    $CMD "$INPUT_IMAGE" -rotate 90 "$ROTATED_IMAGE"
    INPUT_IMAGE="$ROTATED_IMAGE"
    echo "Image rotated. New dimensions: $($CMD "$INPUT_IMAGE" -format "%w×%h" info:)"
else
    echo "No rotation needed (Height >= Width)"
fi
# ===================================================

echo "Mode: $([ $RAW_MODE -eq 1 ] && echo "RAW (exact pixels)" || echo "NORMAL (scaled to fit US Letter)")"
echo "Grid: ${C}×${R} (${TOTAL_PAGES} pages)"
echo "Margins - Top:${MARGIN_TOP} Bottom:${MARGIN_BOTTOM} Left:${MARGIN_LEFT} Right:${MARGIN_RIGHT}"
echo "Live area: ${LIVE_WIDTH}×${LIVE_HEIGHT} px"
echo "Total grid: ${TOTAL_GRID_WIDTH}×${TOTAL_GRID_HEIGHT} px"
echo "=============================================="

echo ""
echo "Step 1/3: Resizing image to ${TOTAL_GRID_WIDTH}×${TOTAL_GRID_HEIGHT}..."
$CMD "$INPUT_IMAGE" \
    -resize "${TOTAL_GRID_WIDTH}x${TOTAL_GRID_HEIGHT}" \
    -background white \
    -gravity center \
    -extent "${TOTAL_GRID_WIDTH}x${TOTAL_GRID_HEIGHT}" \
    canvas.png

echo "Step 2/3: Splitting into ${C}×${R} parts..."
$CMD canvas.png -crop "${C}x${R}@" +repage +adjoin page_%d.png

# Rename files
for i in $(seq 0 $((TOTAL_PAGES - 1))); do
    if [ -f "page_$i.png" ]; then
        mv "page_$i.png" "temp_page_$((i+1)).png"
    fi
done

echo "Step 3/3: Placing each part onto US Letter pages with per-edge margins..."
# Create each PDF page
for i in $(seq 1 $TOTAL_PAGES); do
    # Create a white canvas the size of a US Letter page
    $CMD -size "${PAGE_WIDTH}x${PAGE_HEIGHT}" xc:white \
        -draw "image over ${MARGIN_LEFT},${MARGIN_TOP} ${LIVE_WIDTH},${LIVE_HEIGHT} 'temp_page_$i.png'" \
        "raw_page_$i.png"
    echo "  Processed page $i of $TOTAL_PAGES"
done

# Combine all raw pages into a single pixel-perfect PDF
$CMD raw_page_*.png raw_pixel_perfect.pdf

# Cleanup raw PNG pages
rm -f raw_page_*.png

if [ $RAW_MODE -eq 1 ]; then
    # RAW MODE: Keep the pixel-perfect PDF as final output
    mv raw_pixel_perfect.pdf "$OUTPUT_PDF"
    echo ""
    echo "=============================================="
    echo "Done! RAW PDF created: $OUTPUT_PDF"
    echo "Pages: $TOTAL_PAGES"
    echo "This PDF uses EXACT pixel dimensions (no scaling)."
else
    # NORMAL MODE: Scale to fit US Letter paper using Ghostscript
    echo "Step 4/4: Scaling to fit US Letter paper..."
    gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 \
       -dPDFFitPage \
       -dFIXEDMEDIA \
       -sPAPERSIZE=letter \
       -dNOPAUSE -dQUIET -dBATCH \
       -sOutputFile="$OUTPUT_PDF" \
       raw_pixel_perfect.pdf

    rm raw_pixel_perfect.pdf

    echo ""
    echo "=============================================="
    echo "Done! PDF created: $OUTPUT_PDF"
    echo "Pages: $TOTAL_PAGES"
    echo "This PDF has been scaled to fit US Letter paper."
fi

# Cleanup temporary files
rm -f canvas.png temp_page_*.png

# Cleanup rotated image if it was created
if [ -n "$ROTATED_IMAGE" ] && [ -f "$ROTATED_IMAGE" ]; then
    rm -f "$ROTATED_IMAGE"
fi

echo "Margins - Top:${MARGIN_TOP} Bottom:${MARGIN_BOTTOM} Left:${MARGIN_LEFT} Right:${MARGIN_RIGHT}"
echo ""
echo "PRINTING INSTRUCTIONS:"
echo "  - Print all pages (actual size, no scaling)"
echo "  - Cut off the white margins"
echo "  - Tape/glue pages together in a ${C}×${R} grid"
echo "=============================================="
