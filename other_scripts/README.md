# grid2pdf

Convert large images into multi-page PDF grids with configurable per-edge margins.

## Usage

```bash
# For a poster, set OUTPUT_PDF="poster.pdf" in the script
./grid2pdf image.jpg [--raw|-r]
```

## Options
--raw, -r Raw mode: output exact pixel dimensions (no Ghostscript scaling).

## Configuration

Edit the variables inside the script to adjust:

| Variable | Description | Example |
|----------|-------------|---------|
| `C` | Number of columns (pages across) | `2` |
| `R` | Number of rows (pages down) | `3` |
| `MARGIN_TOP` | Top margin in pixels | `50` |
| `MARGIN_BOTTOM` | Bottom margin in pixels | `250` |
| `MARGIN_LEFT` | Left margin in pixels | `150` |
| `MARGIN_RIGHT` | Right margin in pixels | `150` |
| `OUTPUT_PDF` | Output filename | `output.pdf` |


## Requirements

- ImageMagick
- Ghostscript (normal mode only)
