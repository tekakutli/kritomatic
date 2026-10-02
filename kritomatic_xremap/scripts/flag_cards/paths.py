#!/usr/bin/env python3
"""
Shared path constants for the flag/text overlay pipeline.

Everything the pipeline reads or writes is defined here, so stages never
need to hardcode a /tmp path or an external directory.
"""

from pathlib import Path

# Pipeline root and its produced subdirectories
PIPELINE_ROOT   = Path("/tmp/flag_cards")
FLAGS_DIR       = PIPELINE_ROOT / "flags"
TEXT_IMAGES_DIR = PIPELINE_ROOT / "text_images"
COMPOSITES_DIR  = PIPELINE_ROOT / "composites"
OUTPUT_PDF_PATH = PIPELINE_ROOT / "output.pdf"

# External inputs (not produced by this pipeline)
BASE_IMAGE     = Path("/home/tekakutli/Downloads/cable_5_labeled.png")
COMPANIONS_DIR = Path("/home/tekakutli/Documents/pera/FotosCompaneros")
