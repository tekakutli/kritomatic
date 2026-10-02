#!/usr/bin/env python3
"""
Stage 4 — Directory Images to PDF Grid Converter
Reads composites from COMPOSITES_DIR and writes a grid PDF to OUTPUT_PDF_PATH.
"""

import os
import sys
import tempfile
from pathlib import Path
from PIL import Image

from paths import COMPOSITES_DIR, OUTPUT_PDF_PATH

# ========== CONFIGURATION ==========
INPUT_DIR = str(COMPOSITES_DIR)
OUTPUT_PDF = str(OUTPUT_PDF_PATH)

GRID_ROWS = 3
GRID_COLS = 3

MARGIN_TOP = 250
MARGIN_BOTTOM = 250
MARGIN_LEFT = 250
MARGIN_RIGHT = 250

GAP = 20

PAGE_WIDTH = 2550
PAGE_HEIGHT = 3300

ROTATE_HORIZONTAL = True
# ===================================


class ImageGridToPDF:
    def __init__(self):
        self.input_dir = INPUT_DIR
        self.output_pdf = OUTPUT_PDF
        self.grid_rows = GRID_ROWS
        self.grid_cols = GRID_COLS
        self.margin_top = MARGIN_TOP
        self.margin_bottom = MARGIN_BOTTOM
        self.margin_left = MARGIN_LEFT
        self.margin_right = MARGIN_RIGHT
        self.gap = GAP
        self.page_width = PAGE_WIDTH
        self.page_height = PAGE_HEIGHT
        self.rotate_horizontal = ROTATE_HORIZONTAL

        self.live_width = self.page_width - self.margin_left - self.margin_right
        self.live_height = self.page_height - self.margin_top - self.margin_bottom

        self.cell_width = (self.live_width - (self.grid_cols - 1) * self.gap) // self.grid_cols
        self.cell_height = (self.live_height - (self.grid_rows - 1) * self.gap) // self.grid_rows

        self.image_width = self.cell_width
        self.image_height = self.cell_height

        self.image_exts = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.tif', '.webp'}

    def find_images(self):
        images = []
        dir_path = Path(self.input_dir)

        if not dir_path.exists():
            print(f"Error: Directory '{self.input_dir}' does not exist.")
            sys.exit(1)

        for ext in self.image_exts:
            for file_path in dir_path.glob(f'*{ext}'):
                if file_path.suffix.lower() in self.image_exts:
                    images.append(str(file_path))
            if ext in ['.jpg', '.jpeg', '.png', '.gif']:
                for file_path in dir_path.glob(f'*{ext.upper()}'):
                    images.append(str(file_path))

        images = sorted(set(images))
        return images

    def process_image(self, image_path):
        try:
            img = Image.open(image_path)

            if img.mode in ('RGBA', 'LA', 'P'):
                rgb_img = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'RGBA':
                    rgb_img.paste(img, mask=img.split()[-1])
                else:
                    rgb_img.paste(img)
                img = rgb_img
            elif img.mode != 'RGB':
                img = img.convert('RGB')

            width, height = img.size
            if self.rotate_horizontal and width > height:
                print(f"  → Horizontal image detected, rotating 90°...")
                img = img.rotate(-90, expand=True)
                width, height = img.size
                print(f"  → Rotated to: {width}×{height}")

            img_ratio = width / height
            cell_ratio = self.cell_width / self.cell_height

            if img_ratio > cell_ratio:
                new_width = self.cell_width
                new_height = int(self.cell_width / img_ratio)
            else:
                new_height = self.cell_height
                new_width = int(self.cell_height * img_ratio)

            img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

            final_img = Image.new('RGB', (self.cell_width, self.cell_height), 'white')
            x_offset = (self.cell_width - img.width) // 2
            y_offset = (self.cell_height - img.height) // 2
            final_img.paste(img, (x_offset, y_offset))

            return final_img

        except Exception as e:
            print(f"  Error processing image: {e}")
            return None

    def create_grid_page(self, page_images):
        page = Image.new('RGB', (self.page_width, self.page_height), 'white')

        start_x = self.margin_left
        start_y = self.margin_top

        for idx, img in enumerate(page_images):
            if img is None:
                continue

            row = idx // self.grid_cols
            col = idx % self.grid_cols

            x = start_x + col * (self.cell_width + self.gap)
            y = start_y + row * (self.cell_height + self.gap)

            page.paste(img, (x, y))

        return page

    def convert_to_pdf(self):
        print("=" * 46)
        print("Directory Images to PDF Grid Converter")
        print("=" * 46)

        images = self.find_images()
        total_images = len(images)

        if total_images == 0:
            print(f"Error: No images found in '{self.input_dir}'")
            print(f"Supported formats: {', '.join(sorted(self.image_exts))}")
            print("Note: Subdirectories are NOT searched.")
            sys.exit(1)

        print(f"Found {total_images} image(s) in: {self.input_dir}")
        print(f"Grid: {self.grid_rows}×{self.grid_cols}")
        print(f"Margins - Top:{self.margin_top} Bottom:{self.margin_bottom} Left:{self.margin_left} Right:{self.margin_right}")
        print(f"Gap between images: {self.gap}px")
        print(f"Live area: {self.live_width}×{self.live_height} px")
        print(f"Cell size: {self.cell_width}×{self.cell_height} px")
        print(f"Rotate horizontal: {'Yes' if self.rotate_horizontal else 'No'}")
        print("=" * 46)
        print()

        images_per_page = self.grid_rows * self.grid_cols
        total_pages = (total_images + images_per_page - 1) // images_per_page

        print(f"Processing {total_images} images into {total_pages} page(s)...")

        with tempfile.TemporaryDirectory() as temp_dir:
            page_paths = []

            for page_num in range(total_pages):
                start_idx = page_num * images_per_page
                end_idx = min(start_idx + images_per_page, total_images)
                page_images = []

                print(f"\n[Page {page_num + 1}/{total_pages}]")

                for i in range(start_idx, end_idx):
                    img_path = images[i]
                    img_num = i + 1
                    print(f"  Processing image {img_num}/{total_images}: {Path(img_path).name}")

                    processed_img = self.process_image(img_path)
                    page_images.append(processed_img)

                print(f"  Creating grid page {page_num + 1}...")
                page = self.create_grid_page(page_images)

                page_path = os.path.join(temp_dir, f'page_{page_num + 1:03d}.png')
                page.save(page_path, 'PNG')
                page_paths.append(page_path)

                print(f"  ✓ Page {page_num + 1} ready")

            print(f"\nCombining {total_pages} page(s) into PDF...")

            try:
                first_image = Image.open(page_paths[0])
                if first_image.mode != 'RGB':
                    first_image = first_image.convert('RGB')

                other_images = []
                for path in page_paths[1:]:
                    img = Image.open(path)
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    other_images.append(img)

                first_image.save(
                    self.output_pdf,
                    'PDF',
                    save_all=True,
                    append_images=other_images,
                    quality=95,
                    dpi=(300, 300)
                )

                print(f"\n{'=' * 46}")
                print(f"✓ Done! PDF created: {self.output_pdf}")
                print(f"  Total pages: {total_pages}")
                print(f"  Total images: {total_images}")
                print(f"  Grid layout: {self.grid_rows}×{self.grid_cols}")
                print(f"  Page size: {self.page_width}×{self.page_height} px at 300 DPI")
                print(f"  Margins - Top:{self.margin_top} Bottom:{self.margin_bottom} Left:{self.margin_left} Right:{self.margin_right}")
                print(f"{'=' * 46}")

            except Exception as e:
                print(f"Error creating PDF: {e}")
                sys.exit(1)


def main():
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Error: PIL/Pillow not found.")
        print("Please install: pip install Pillow")
        sys.exit(1)

    converter = ImageGridToPDF()
    converter.convert_to_pdf()


if __name__ == "__main__":
    main()
