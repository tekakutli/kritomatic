#!/bin/bash

# ============================================
# DIRECTORY IMAGES TO PDF CONVERTER
# ============================================
# Converts all images in a directory to a single PDF
# Each image gets its own page, filling the entire page
# Horizontal images are automatically rotated to vertical
# Supports per-edge margins
# NOTE: Only processes images in the top-level directory (no subdirectories)
#
# USAGE:
#   ./script.sh
# ============================================

# ========== CONFIGURATION - EDIT THESE VALUES ==========
INPUT_DIR="/tmp/"                    # Directory containing images
OUTPUT_PDF="output.pdf"              # Output PDF filename

# Per-edge margins in pixels (150px = 0.5 inches at 300 DPI)
MARGIN_TOP=250
MARGIN_BOTTOM=250
MARGIN_LEFT=250
MARGIN_RIGHT=250

# Set to 1 for RAW mode (exact pixels, no scaling), 0 for normal mode
RAW_MODE=0
# ===================================================

# US Letter size at 300 DPI (fixed, do not change)
PAGE_WIDTH=2550
PAGE_HEIGHT=3300

# Calculate live area per page (subtract margins from each side)
LIVE_WIDTH=$((PAGE_WIDTH - MARGIN_LEFT - MARGIN_RIGHT))
LIVE_HEIGHT=$((PAGE_HEIGHT - MARGIN_TOP - MARGIN_BOTTOM))

# Check if directory exists
if [ ! -d "$INPUT_DIR" ]; then
    echo "Error: Directory '$INPUT_DIR' does not exist."
    exit 1
fi

# Check ImageMagick
if ! command -v magick &> /dev/null && ! command -v convert &> /dev/null; then
    echo "Error: ImageMagick not found. Please install it first."
    exit 1
fi

CMD=$(command -v magick 2>/dev/null || command -v convert)

# Check Ghostscript for normal mode
if [ $RAW_MODE -eq 0 ] && ! command -v gs &> /dev/null; then
    echo "Warning: Ghostscript not found. Falling back to RAW mode."
    RAW_MODE=1
fi

# ========== FIND ALL IMAGES ==========
echo "=============================================="
echo "Directory Images to PDF Converter"
echo "=============================================="

# Supported image extensions
IMAGE_EXTS="jpg jpeg png gif bmp tiff tif webp"

# Find all images in the top-level directory only (no subdirectories)
IMAGES=()
for ext in $IMAGE_EXTS; do
    while IFS= read -r file; do
        if [ -f "$file" ]; then
            IMAGES+=("$file")
        fi
    done < <(find "$INPUT_DIR" -maxdepth 1 -type f -iname "*.$ext" 2>/dev/null | sort)
done

TOTAL_IMAGES=${#IMAGES[@]}

if [ $TOTAL_IMAGES -eq 0 ]; then
    echo "Error: No images found in '$INPUT_DIR'"
    echo "Supported formats: ${IMAGE_EXTS}"
    echo "Note: Subdirectories are NOT searched."
    exit 1
fi

echo "Found $TOTAL_IMAGES image(s) in: $INPUT_DIR"
echo "Mode: $([ $RAW_MODE -eq 1 ] && echo "RAW (exact pixels)" || echo "NORMAL (scaled to fit US Letter)")"
echo "Margins - Top:${MARGIN_TOP} Bottom:${MARGIN_BOTTOM} Left:${MARGIN_LEFT} Right:${MARGIN_RIGHT}"
echo "Live area: ${LIVE_WIDTH}×${LIVE_HEIGHT} px"
echo "=============================================="
echo ""

# Create temporary directory
TEMP_DIR=$(mktemp -d)
echo "Processing images..."

# Process each image
for i in "${!IMAGES[@]}"; do
    IMAGE="${IMAGES[$i]}"
    PAGE_NUM=$((i + 1))

    echo "[$PAGE_NUM/$TOTAL_IMAGES] Processing: $(basename "$IMAGE")"

    # Get original image dimensions
    ORIG_WIDTH=$($CMD "$IMAGE" -format "%w" info:)
    ORIG_HEIGHT=$($CMD "$IMAGE" -format "%h" info:)

    # Check if image is horizontal (width > height)
    CURRENT_IMAGE="$IMAGE"
    if [ $ORIG_WIDTH -gt $ORIG_HEIGHT ]; then
        echo "  → Horizontal image detected, rotating 90°..."
        ROTATED_IMAGE="$TEMP_DIR/rotated_$$_$(basename "$IMAGE")"
        $CMD "$IMAGE" -rotate 90 "$ROTATED_IMAGE"
        CURRENT_IMAGE="$ROTATED_IMAGE"
        NEW_DIMS=$($CMD "$CURRENT_IMAGE" -format "%w×%h" info:)
        echo "  → Rotated to: $NEW_DIMS"
    fi

    # Resize the image to FIT within the live area (maintain aspect ratio, show entire image)
    # Do NOT use -extent here - let the image keep its natural dimensions after resize
    echo "  → Resizing to fit within ${LIVE_WIDTH}×${LIVE_HEIGHT} (keeping entire image visible)..."
    $CMD "$CURRENT_IMAGE" \
        -resize "${LIVE_WIDTH}x${LIVE_HEIGHT}" \
        "$TEMP_DIR/fitted_$PAGE_NUM.png"

    # Get the actual dimensions after resize
    RESIZED_WIDTH=$($CMD "$TEMP_DIR/fitted_$PAGE_NUM.png" -format "%w" info:)
    RESIZED_HEIGHT=$($CMD "$TEMP_DIR/fitted_$PAGE_NUM.png" -format "%h" info:)

    # Calculate centered position within the live area
    X_OFFSET=$((MARGIN_LEFT + (LIVE_WIDTH - RESIZED_WIDTH) / 2))
    Y_OFFSET=$((MARGIN_TOP + (LIVE_HEIGHT - RESIZED_HEIGHT) / 2))

    echo "  → Placing ${RESIZED_WIDTH}×${RESIZED_HEIGHT} image at position (${X_OFFSET}, ${Y_OFFSET})..."

    # Place the resized image onto a US Letter page at the calculated position
    $CMD -size "${PAGE_WIDTH}x${PAGE_HEIGHT}" xc:white \
        -draw "image over ${X_OFFSET},${Y_OFFSET} ${RESIZED_WIDTH},${RESIZED_HEIGHT} '$TEMP_DIR/fitted_$PAGE_NUM.png'" \
        "$TEMP_DIR/page_$PAGE_NUM.png"

    # Cleanup rotated temp file if it was created
    if [ -n "$ROTATED_IMAGE" ] && [ -f "$ROTATED_IMAGE" ]; then
        rm -f "$ROTATED_IMAGE"
    fi

    echo "  ✓ Page $PAGE_NUM ready"
done

echo ""
echo "Combining pages into PDF..."

# Combine all pages into a single PDF
$CMD "$TEMP_DIR"/page_*.png "$TEMP_DIR/raw_pixel_perfect.pdf"

if [ $RAW_MODE -eq 1 ]; then
    # RAW MODE: Keep the pixel-perfect PDF as final output
    mv "$TEMP_DIR/raw_pixel_perfect.pdf" "$OUTPUT_PDF"
    echo ""
    echo "=============================================="
    echo "✓ Done! RAW PDF created: $OUTPUT_PDF"
    echo "  Total pages: $TOTAL_IMAGES"
    echo "  This PDF uses EXACT pixel dimensions (no scaling)."
else
    # NORMAL MODE: Scale to fit US Letter paper using Ghostscript
    echo "  Scaling to fit US Letter paper..."
    gs -sDEVICE=pdfwrite -dCompatibilityLevel=1.4 \
       -dPDFFitPage \
       -dFIXEDMEDIA \
       -sPAPERSIZE=letter \
       -dNOPAUSE -dQUIET -dBATCH \
       -sOutputFile="$OUTPUT_PDF" \
       "$TEMP_DIR/raw_pixel_perfect.pdf" \
       2>/dev/null

    echo ""
    echo "=============================================="
    echo "✓ Done! PDF created: $OUTPUT_PDF"
    echo "  Total pages: $TOTAL_IMAGES"
    echo "  This PDF has been scaled to fit US Letter paper."
fi

# Cleanup temporary files
rm -rf "$TEMP_DIR"

echo "  Margins - Top:${MARGIN_TOP} Bottom:${MARGIN_BOTTOM} Left:${MARGIN_LEFT} Right:${MARGIN_RIGHT}"
echo "=============================================="
