#!/usr/bin/env python3
"""
captionize_krita.py — Captionize an image by driving Krita via the
Kritomatic daemon.

Every interaction with Krita goes through the daemon.  The only things
this script does locally are: inspect .kra files as zip archives (to
read their dimensions), measure the caption with Pillow (to pick a
font size), copy the input .kra to a temp location so we never mutate
the user's file, and compute the layout geometry.

EMBED_MODE selects how the source image makes it into the new document:

    "file_layer"  (default)
        External PNG reference.  Smallest .kra, but the output depends
        on that PNG staying where it is when the .kra is reopened.

    "baked_png"
        Pixels baked into a paint layer.  Self-contained .kra.

    "kra_group"
        Source .kra imported as a group.  Preserves layers.  Self-
        contained.  Skips the flatten-to-PNG stage.

KEEP_IMAGE_NATIVE_SIZE selects which dimension anchors the layout:

    False (default)
        The canvas anchors.  Strip is carved from the top, source
        shrinks into the area below it.

    True
        The source anchors.  It keeps its native dimensions and the
        canvas grows downward by the strip height.

ROTATE_BEFORE_PROCESSING mirrors captionize.py's flag of the same name,
but is implemented differently.  Where captionize.py rotates the pixel
buffer CW before sizing and CCW after, this script does not rotate any
image data at all — it computes the layout in the "as if rotated" frame
and then transforms the resulting geometry into the final frame.  The
practical result is the same: the label strip ends up as a vertical band
along the LEFT edge of the final image, with the text reading
bottom-to-top, and the source in its original orientation.

Why not rotate the document in Krita: Krita's rotate-image action
rotates each layer's pixel data but leaves transform-mask parameters in
their original coordinate system.  A file layer with a transform mask
ends up shifted in the wrong direction after the rotation.  By keeping
the source in its original orientation and laying out the final frame
directly, there is no transform mask on the source at all, and the
problem disappears.  The label is the only rotated element, and it is
rotated via the SVG transform attribute, which Krita renders correctly
because it is part of the shape rather than a mask.

LABEL TEXT IS WRITTEN IN KRITA'S SVG 2 "INLINE WRAPPED" MODE
============================================================
The label's `add_vector_text` command carries an `inline_size` value,
which the daemon turns into an SVG 2 inline-wrapped text shape: the
style attribute carries `inline-size: <W>;white-space: pre-wrap;` and
the <text> element has NO x/y of its own.  Krita then draws two
vertical inline-size handles at the left and right edges of that
wrapping box, and the text wraps inside it.

The two handles sit, in the shape's own frame, at

    T.map(-inline_size/2, 0)   and   T.map(+inline_size/2, 0)

(alignment="center" puts the anchor at the box's midpoint).  With
`inline_size` set to the full strip axis, those two points land on the
strip's boundaries along its centreline:

    not rotated   (0, strip_h/2)          and  (canvas_w, strip_h/2)
    rotated       (strip_h/2, 0)          and  (strip_h/2, canvas_h)

so the wrapping box spans the entire label band, edge to edge.

This is the same convention cone_playground uses for every text it
exports; see pg_kra.py's INLINE-WRAPPED TEXT section and cone_kra.py's
`_inline_size_for`, which returns the shape's own natural width with no
padding.

Font sizing is separate and stays padded: `compute_font_size` measures
the caption against a safely-inset width, so the glyphs themselves keep
their usual margin from the strip's edges even though the wrapping box
does not.

UNIT CONVERSION — POINTS, NOT PIXELS
====================================
Krita's vector-shape coordinate system is in POINTS.  When Krita saves
a .kra whose document is 300 DPI, it writes the vector layer's SVG as
`<svg width="<N>pt" viewBox="0 0 <N> <M>">`, so one SVG user unit is
one point, and one document pixel is 72/DEFAULT_RESOLUTION of a user
unit.  Every value that reaches the shape — position, font size,
inline-wrapping width — has to be expressed in those units before it is
sent.

At 72 DPI the two systems coincide, which is why cone_playground's
exports work without any conversion.  At any other resolution the
numbers must be scaled by 72/resolution.  `build_label_commands`
applies `dpi_scale = 72.0 / DEFAULT_RESOLUTION` uniformly.

PIPELINE — ONE BATCH PER RUN
============================
The whole daemon-side pipeline is sent as a single batch:

    create source .kra (bitmap input only)
    flatten source .kra → source.png (skipped in "kra_group" mode)
    create the target canvas
    embed the source per EMBED_MODE
    add the vector label
    save the target .kra
    force a fresh composite

Every one of those steps is a command in one `{"id": ..., "commands":
[...]}` payload.  Everything downstream of the flatten reads
`geo` (canvas and label geometry, computed locally from the source
dimensions before the batch is sent), so nothing in the daemon's
sequence depends on information the script does not already have.
Sending the whole pipeline as one message means one client
connection, one batch ID, one defer window, one round trip.

The final `get_document_thumbnail` command is the redraw: it calls
`doc.projection()` directly, which forces a whole-stack composite,
without going through the deferred `refresh()` helper that the rest
of the batch uses.  The daemon's other handlers rely on that helper,
whose pending queue is dropped at end-of-batch (FLUSH_ON_END=False in
`kritomatic_daemon/utils/refresh.py`), so without this last command
the canvas can show stale regions until Krita's own incremental
repaints catch up.
"""

import argparse
import json
import math
import os
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path


# ========== CONFIGURATION — mirrors captionize.py ==========
INPUT_IMAGE = None
TEXT = None

OUTPUT_SUFFIX = "_labeled"

LABEL_HEIGHT_RATIO = 0.3
SHRINK_RATIO = 1.0

KEEP_IMAGE_NATIVE_SIZE = False

TRIM_SIDE_MARGINS = True

LABEL_COLOR = "#000000"
LABEL_BACKGROUND = "#ffffff"
LABEL_PADDING_X = 20

LABEL_VERTICAL_PADDING_FRACTION = 0.15

DEFAULT_RESOLUTION = 300

# The font family used in the SVG's `font-family` attribute.  Krita
# resolves this to a system font and renders the text with it.
LABEL_FONT_FAMILY = "DejaVu Sans"

# Font file paths per family.  `compute_font_size` measures with PIL,
# which loads exactly one file.  If that file's font does not match the
# family Krita renders with, the computed size is off by the ratio
# between the two fonts' advance widths — a few percent, which is
# enough to overflow the strip (or the canvas axis, in the rotated
# case) by a visible amount.  The recipe prefers the entry for
# LABEL_FONT_FAMILY; if that family has no installed file, it falls
# back to a rough estimate with a wider safety margin.
LABEL_FONT_FILES = {
    "DejaVu Sans": [
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu-lgc/DejaVuSans.ttf",
    ],
    "Noto Sans": [
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSans-Regular.otf",
    ],
}

# Fraction of the axis left as usable space by the font-size recipe.
# PIL's measurement and Krita's SVG renderer disagree by a percent or
# two even for the same font file, from hinting and subpixel rounding.
# Two percent of the axis is invisible in the layout and covers the
# discrepancy.
#
# This factor applies to FONT SIZING only.  The wrapping box (the
# inline-size) spans the full axis — see compute_inline_size.
LABEL_FONT_SAFETY = 0.98

# "file_layer" | "baked_png" | "kra_group"
EMBED_MODE = "file_layer"

# Mirror of captionize.py's flag.  When True, the strip appears as a
# vertical band along the LEFT edge of the output, with the text
# reading bottom-to-top.  See the module docstring for why we don't
# actually rotate anything in Krita.
ROTATE_BEFORE_PROCESSING = False
# ==========================================================


_HERE = Path(__file__).resolve().parent
for _cand in (_HERE, _HERE / "src", _HERE.parent / "src"):
    if (_cand / "kritomatic").is_dir() and str(_cand) not in sys.path:
        sys.path.insert(0, str(_cand))

try:
    from kritomatic.batch import BatchExecutor
    from kritomatic.client import KritaClient
except ImportError:
    print("Error: the 'kritomatic' Python package is not importable.")
    print("Install it with:  pipx install -e .    (from the repo root)")
    sys.exit(1)


VALID_EMBED_MODES = ("file_layer", "baked_png", "kra_group")


# --------------------------------------------------------------------------
#  Reading the source (local, no Krita)
# --------------------------------------------------------------------------

def get_kra_dimensions(path):
    """Parse width and height out of a .kra's maindoc.xml."""
    with zipfile.ZipFile(path) as zf:
        main = next((n for n in zf.namelist()
                     if n.endswith("maindoc.xml") and "/" not in n), None)
        if main is None:
            main = next((n for n in zf.namelist()
                         if n.endswith("maindoc.xml")), None)
        if main is None:
            raise ValueError(f"No maindoc.xml found in {path}")
        with zf.open(main) as f:
            tree = ET.parse(f)
        for elem in tree.getroot().iter():
            if "width" in elem.attrib and "height" in elem.attrib:
                return int(elem.attrib["width"]), int(elem.attrib["height"])
    raise ValueError(f"Could not locate width/height in {path}")


def get_image_dimensions(path):
    """Dimensions of any Krita-readable bitmap, via Pillow."""
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def is_kra(path):
    return path.suffix.lower() == ".kra"


# --------------------------------------------------------------------------
#  Geometry — mirrors captionize.py
# --------------------------------------------------------------------------

def compute_geometry(src_w, src_h):
    """
    Compute layout.  If ROTATE_BEFORE_PROCESSING is True, first computes
    everything in the "rotated frame" (as if the source had been rotated
    90 degrees CW), then transforms the geometry into the final frame by
    applying the inverse rotation (90 degrees CCW).

    The returned fields are always in the frame that will actually be
    drawn — that is, if ROTATE_BEFORE_PROCESSING is True, the canvas,
    content position, and label position are all expressed in the final
    (rotated) frame, and the source is expected to be embedded in its
    original orientation.

    Fields:
      canvas_w, canvas_h   final canvas dimensions
      strip_h              strip thickness (height when horizontal, width
                           when vertical)
      image_area_h         height of the content area in the rotated
                           frame (informational only)
      scaled_w, scaled_h   content size in the final frame
      img_x, img_y         content top-left in the final frame
      label_x, label_y     label anchor in the final frame
      label_rotation       degrees; 0 for horizontal, -90 for reading
                           bottom-to-top
      label_axis           length of the strip's long axis; used as the
                           text-width constraint when sizing the font
                           and as the inline-size of the wrapping box
      rotated              True if ROTATE_BEFORE_PROCESSING was applied
    """
    if ROTATE_BEFORE_PROCESSING:
        rot_w, rot_h = src_h, src_w
    else:
        rot_w, rot_h = src_w, src_h

    strip_h = max(0, int(rot_h * LABEL_HEIGHT_RATIO))

    if KEEP_IMAGE_NATIVE_SIZE:
        canvas_w = rot_w
        canvas_h = rot_h + strip_h
        image_area_h = rot_h
        scaled_w = rot_w
        scaled_h = rot_h
        img_x = 0
        img_y = strip_h
    else:
        t = max(0.0, min(1.0, SHRINK_RATIO))
        growth = round(strip_h * (1.0 - t))
        canvas_h = rot_h + growth
        image_area_h = canvas_h - strip_h

        s = min(1.0, image_area_h / rot_h) if rot_h > 0 else 1.0
        scaled_w = max(1, int(round(rot_w * s)))
        scaled_h = max(1, int(round(rot_h * s)))

        if TRIM_SIDE_MARGINS and 0 < scaled_w < rot_w:
            canvas_w = scaled_w
        else:
            canvas_w = rot_w

        img_x = (canvas_w - scaled_w) // 2
        img_y = strip_h + (image_area_h - scaled_h) // 2

    if not ROTATE_BEFORE_PROCESSING:
        return {
            "canvas_w": canvas_w,
            "canvas_h": canvas_h,
            "strip_h": strip_h,
            "image_area_h": image_area_h,
            "scaled_w": scaled_w,
            "scaled_h": scaled_h,
            "img_x": img_x,
            "img_y": img_y,
            "label_x": canvas_w / 2,
            "label_y": strip_h / 2,
            "label_rotation": 0,
            "label_axis": canvas_w,
            "rotated": False,
        }

    # Transform to the final frame by rotating the rotated-frame layout
    # 90 degrees CCW.  The map (x, y) -> (y, W - 1 - x) on a WxH canvas
    # gives an HxW output.  Applied to the content rect and the label
    # anchor in turn.
    final_canvas_w = canvas_h
    final_canvas_h = canvas_w
    final_img_x = img_y
    final_img_y = canvas_w - img_x - scaled_w
    final_scaled_w = scaled_h
    final_scaled_h = scaled_w
    final_label_x = strip_h / 2
    final_label_y = canvas_w / 2

    return {
        "canvas_w": final_canvas_w,
        "canvas_h": final_canvas_h,
        "strip_h": strip_h,
        "image_area_h": image_area_h,
        "scaled_w": final_scaled_w,
        "scaled_h": final_scaled_h,
        "img_x": final_img_x,
        "img_y": final_img_y,
        "label_x": final_label_x,
        "label_y": final_label_y,
        "label_rotation": -90,
        "label_axis": final_canvas_h,
        "rotated": True,
    }


def _find_label_font_file():
    """The font file that matches LABEL_FONT_FAMILY, or None.

    compute_font_size uses PIL to measure the caption.  PIL uses the
    file it loads.  Krita uses whichever font the SVG's font-family
    attribute resolves to.  If those two are not the same physical file,
    the measured width does not match the rendered width, and the
    recipe picks a size that is systematically off in whichever
    direction the two fonts' advance widths differ.
    """
    for path in LABEL_FONT_FILES.get(LABEL_FONT_FAMILY, []):
        if os.path.exists(path):
            return path
    return None


def compute_font_size(text, strip_h, canvas_w):
    """Pick a font size that keeps the caption inside the strip.

    The max width used here is PADDED — the caption keeps a margin from
    the strip's edges even though the inline-wrapping box itself spans
    the full axis (see compute_inline_size).  Font size and wrapping
    box are sized independently on purpose: the box says where the text
    is allowed to wrap, the font size says how big it is, and the two
    margins do not have to agree.
    """
    axis_pad = int(canvas_w * 0.05)   # 5% margin at each end of the axis
    max_w = int((canvas_w - 2 * max(LABEL_PADDING_X, axis_pad)) * LABEL_FONT_SAFETY)
    max_h = int(strip_h * (1 - 2 * LABEL_VERTICAL_PADDING_FRACTION))
    if max_w <= 0 or max_h <= 0 or not text:
        return 12

    font_path = _find_label_font_file()
    if font_path is not None:
        try:
            from PIL import ImageFont
            lo, hi = 1, max(2, int(max_h * 2))
            best = 8
            while lo <= hi:
                mid = (lo + hi) // 2
                font = ImageFont.truetype(font_path, mid)
                try:
                    w = font.getlength(text)
                except AttributeError:
                    w = font.getsize(text)[0]
                ascent, descent = font.getmetrics()
                line_h = ascent + descent
                if w <= max_w and line_h <= max_h:
                    best = mid
                    lo = mid + 1
                else:
                    hi = mid - 1
            return best
        except Exception:
            pass

    # Fallback: no font file for the requested family.  Estimate from
    # average character width with a wider safety margin, because the
    # estimate cannot know the real font metrics.
    by_width = (max_w * 0.9) / max(1, len(text)) / 0.55
    by_height = max_h / 1.2
    return max(8, int(min(by_width, by_height)))


def compute_inline_size(axis_length):
    """Width of the inline text-wrapping box along the strip's long axis.

    The box spans the FULL strip axis — no padding.  The two
    inline-size handles therefore land exactly on the canvas boundaries
    along the strip's centreline:

        not rotated   (0, strip_h/2)    and  (canvas_w, strip_h/2)
        rotated       (strip_h/2, 0)    and  (strip_h/2, canvas_h)

    This matches cone_playground's convention (see `_inline_size_for`
    in cone_kra.py, which returns the shape's own natural width with no
    padding).  Font sizing stays padded independently — see
    compute_font_size — so the caption's glyphs keep their own margin
    even though the wrapping box does not.

    Returned in DOCUMENT PIXELS.  `build_label_commands` multiplies by
    the DPI scale before it goes into the SVG.
    """
    return max(1, int(axis_length))


# --------------------------------------------------------------------------
#  Command builders — each returns a LIST of commands
# --------------------------------------------------------------------------
# Every builder produces plain command dicts.  main() concatenates them
# into one batch and sends the batch once.  See the module docstring,
# PIPELINE — ONE BATCH PER RUN.
# --------------------------------------------------------------------------

def convert_commands(bitmap_path, kra_path, src_w, src_h):
    """Turn the input bitmap into a .kra, save it, close it.

    Closing is important: the flatten stage that follows re-opens the
    same path fresh from disk, and a doc left open by this stage would
    collide with that open.
    """
    return [
        {
            "type": "create_new_with_dimensions",
            "name": bitmap_path.stem,
            "width": src_w,
            "height": src_h,
            "resolution": DEFAULT_RESOLUTION,
        },
        {
            "type": "create_file_layer",
            "name": "source",
            "file_path": str(bitmap_path),
        },
        {
            "type": "save_document",
            "file_path": str(kra_path),
        },
        {
            "type": "close_document",
        },
    ]


def flatten_commands(kra_path, png_path):
    """Flatten the source .kra to a PNG, entirely inside Krita.

    One-shot: opens the .kra from disk, exports, closes.  No view is
    added; the user-facing session is untouched.
    """
    return [{
        "type": "export_file_to_image",
        "input_path": str(kra_path),
        "output_path": str(png_path),
    }]


def canvas_commands(geo, doc_name):
    """Create the target canvas."""
    return [{
        "type": "create_new_with_dimensions",
        "name": doc_name,
        "width": geo["canvas_w"],
        "height": geo["canvas_h"],
        "resolution": DEFAULT_RESOLUTION,
    }]


def embed_commands(geo, src_png_path, src_kra_path, kra_group_scale):
    """Embed the source into the target document.

    Three modes, matching EMBED_MODE:
      "file_layer"  — external PNG reference with an auto-created
                      transform mask that scales+places it.
      "baked_png"   — pixels copied into a paint layer.
      "kra_group"   — the source .kra imported as a group, then
                      resized+placed by a transform mask.
    """
    cmds = [
        {
            "type": "create_layer",
            "name": "background",
            "layer_type": "paintlayer",
            "position": "bottom",
        },
        {
            "type": "fill_layer",
            "layer_name": "background",
            "color": LABEL_BACKGROUND,
        },
    ]

    if EMBED_MODE == "file_layer":
        cmds.append({
            "type": "create_file_layer",
            "name": "source",
            "file_path": str(src_png_path),
            "width": geo["scaled_w"],
            "height": geo["scaled_h"],
            "x": geo["img_x"],
            "y": geo["img_y"],
        })

    elif EMBED_MODE == "baked_png":
        cmds.append({
            "type": "embed_image_as_layer",
            "name": "source",
            "file_path": str(src_png_path),
            "width": geo["scaled_w"],
            "height": geo["scaled_h"],
            "x": geo["img_x"],
            "y": geo["img_y"],
        })

    elif EMBED_MODE == "kra_group":
        cmds.append({
            "type": "import_kra_as_group",
            "name": "source",
            "file_path": str(src_kra_path),
        })
        cmds.append({
            "type": "create_transform_mask",
            "layer_name": "source",
            "mask_name": "source_transform",
        })
        cmds.append({
            "type": "transform_mask",
            "mask_name": "source_transform",
            "scale_x": kra_group_scale[0],
            "scale_y": kra_group_scale[1],
            "translate_x": geo["img_x"],
            "translate_y": geo["img_y"],
        })

    return cmds


def label_commands(geo, caption, font_size, out_path):
    """Draw the caption as inline-wrapped vector text and save the .kra.

    The text is written in Krita's SVG 2 inline-wrapped mode: the
    daemon builds the shape's style with `inline-size: <W>; white-space:
    pre-wrap;` and leaves the <text> element itself without x/y.  With
    alignment="center" and the daemon's dominant-baseline="middle", the
    text's VISUAL CENTRE sits at the shape's local origin (0, 0).

    POSITIONING — WHY x/y AND transform BOTH MATTER
    ===============================================
    The daemon's add_vector_text, wrapped branch, does two things we
    have to work with rather than against:

      1.  If the caller passes x == 0 AND y == 0 with alignment ==
          "center", it REPLACES them with the canvas centre.  So x=0,
          y=0 does not mean "at the origin" here; it means "let the
          daemon pick".  We therefore always pass the real target
          (label_x, label_y) as x, y — both non-zero in every layout
          this script produces, so the substitution never fires.

      2.  When `transform` is supplied, the daemon computes

              dx2 = m11 * x + m21 * y + dx
              dy2 = m12 * x + m22 * y + dy
              t   = QTransform(m11, m12, m21, m22, dx2, dy2)

          and applies t to the shape.  Because t's fixed-point
          arithmetic maps local (0, 0) to (dx2, dy2), the shape's
          visual centre lands at (dx2, dy2).  We want that to be
          (label_x, label_y).  With x = label_x and y = label_y
          already fed into the formula's first two terms, we set

              dx = label_x - (m11 * label_x + m21 * label_y)
              dy = label_y - (m12 * label_x + m22 * label_y)

          so that dx2 = label_x and dy2 = label_y.

    The daemon's formula is that of "rotate around (x, y)" applied to
    a shape whose local origin starts at (0, 0).  Rather than fight
    it, we use it: the pivot is the anchor and the compensating dx/dy
    make the pivot itself the fixed point.

    UNIT CONVERSION
    ===============
    Every value that reaches the shape is in SVG POINTS, not document
    pixels.  `dpi_scale = 72 / DEFAULT_RESOLUTION` performs the
    conversion uniformly.  At 72 DPI the scale is 1 and this is a
    no-op, which is why cone_playground's exports work without any
    conversion.  See the module docstring for the full explanation.

    INLINE-SIZE WIDTH
    =================
    compute_inline_size returns the full strip axis, no padding, so the
    two inline-size handles land on the canvas boundaries.  See that
    function's docstring for the two concrete positions.
    """
    cmds = []
    if caption and geo["strip_h"] > 0:
        dpi_scale = 72.0 / DEFAULT_RESOLUTION

        inline_size = compute_inline_size(geo["label_axis"]) * dpi_scale

        # The daemon's schema declares font_size as an int.
        font_size_svg = max(1, int(round(font_size * dpi_scale)))

        x = geo["label_x"] * dpi_scale
        y = geo["label_y"] * dpi_scale

        # If x and y both land on zero the daemon treats that as "use
        # the canvas centre".  Nudge off the exact origin so a label
        # that legitimately wants to sit at (0, 0) is not overridden.
        # The shift is sub-pixel in doc units.
        if x == 0:
            x = 1e-4
        if y == 0:
            y = 1e-4

        theta_rad = math.radians(geo["label_rotation"])
        cos_t = math.cos(theta_rad)
        sin_t = math.sin(theta_rad)

        # Qt row-vector order: [m11, m12, m21, m22, dx, dy].
        # For a rotation by θ in Krita's y-down frame:
        #   m11 = cos θ, m12 = sin θ, m21 = -sin θ, m22 = cos θ
        m11, m12, m21, m22 = cos_t, sin_t, -sin_t, cos_t

        dx = x - (m11 * x + m21 * y)
        dy = y - (m12 * x + m22 * y)
        transform_array = [m11, m12, m21, m22, dx, dy]

        cmds.append({
            "type": "create_layer",
            "name": "label",
            "layer_type": "vectorlayer",
            "position": "top",
        })
        cmds.append({
            "type": "set_active_layer",
            "name": "label",
        })
        cmds.append({
            "type": "add_vector_text",
            "layer_name": "label",
            "text": caption,
            "font_family": LABEL_FONT_FAMILY,
            "font_size": font_size_svg,
            # Pass the actual desired anchor.  Non-zero x/y with
            # alignment="center" prevents the daemon from substituting
            # the canvas centre; the transform's compensating dx/dy
            # then make this anchor the transform's fixed point.
            "x": x,
            "y": y,
            "rotation": 0.0,
            "alignment": "center",
            "color": LABEL_COLOR,
            "transform": json.dumps(transform_array),
            # Full-axis width.  With the anchor at the strip centre,
            # the two inline-size handles land on the canvas
            # boundaries: (0, strip_h/2) and (canvas_w, strip_h/2)
            # not rotated, or (strip_h/2, 0) and (strip_h/2, canvas_h)
            # rotated.
            "inline_size": inline_size,
        })
    cmds.append({
        "type": "save_document",
        "file_path": str(out_path),
    })
    return cmds


def redraw_commands():
    """Force a fresh whole-stack composite.

    The daemon's handlers call a `refresh()` helper that, inside a
    batch, queues the request instead of compositing; end_defer()
    drops the queue (FLUSH_ON_END=False in
    kritomatic_daemon/utils/refresh.py), so the canvas can show stale
    regions until something else triggers a composite.

    `get_document_thumbnail` calls `doc.projection()` directly, which
    is a synchronous, whole-stack composite that does not go through
    the refresh() helper.  Sent last in the batch, it composites the
    finished document once, after every layer has been created.
    max_size=1 keeps the returned thumbnail itself negligible.
    """
    return [{
        "type": "get_document_thumbnail",
        "max_size": 1,
    }]


# --------------------------------------------------------------------------
#  Execution
# --------------------------------------------------------------------------

def run_batch(bundle, label):
    print(f"\n── {label} ──────────────────────────────────────")
    print(f"   {len(bundle['commands'])} command(s) in one batch")
    executor = BatchExecutor()
    try:
        results = executor.execute(bundle)
    finally:
        executor.close()
    failed = [r for r in results if r["status"] == "error"]
    for r in results:
        icon = "✓" if r["status"] == "success" else "✗"
        print(f"  {icon} {r['command']}: {r['message']}")
    if failed:
        raise RuntimeError(
            f"{len(failed)} command(s) failed in {label}: "
            + "; ".join(f"{r['command']} -> {r['message']}" for r in failed)
        )
    return results


# --------------------------------------------------------------------------
#  CLI
# --------------------------------------------------------------------------

def parse_args():
    p = argparse.ArgumentParser(
        prog="captionize_krita.py",
        description=(
            "Embed a caption into an image by driving Krita through the "
            "Kritomatic daemon. Mirrors captionize.py's canvas geometry."
        ),
    )
    p.add_argument("input", nargs="?", default=None,
                   help="Input image path. Falls back to INPUT_IMAGE.")
    p.add_argument("-o", "--output", default=None,
                   help=f"Output .kra path (default: <input>{OUTPUT_SUFFIX}.kra).")
    p.add_argument("--text", default=None,
                   help="Caption text. Falls back to TEXT, then to the filename stem.")
    return p.parse_args()


def resolve_paths(args):
    in_str = args.input if args.input is not None else INPUT_IMAGE
    if in_str is None:
        print("Error: no input image given.")
        print("  Pass one on the command line, or set INPUT_IMAGE.")
        sys.exit(1)

    in_path = Path(in_str).expanduser().resolve()
    if not in_path.is_file():
        print(f"Error: input not found: {in_path}")
        sys.exit(1)

    if args.output:
        out_path = Path(args.output).expanduser().resolve()
    else:
        out_path = in_path.with_name(f"{in_path.stem}{OUTPUT_SUFFIX}.kra")
    if out_path.suffix.lower() != ".kra":
        out_path = out_path.with_suffix(".kra")

    if out_path == in_path:
        print("Error: output path would overwrite the input.")
        sys.exit(1)
    return in_path, out_path


def resolve_caption(args, in_path):
    if args.text is not None:
        return args.text, "custom --text"
    if TEXT is not None:
        return TEXT, "custom TEXT"
    return in_path.stem, "filename"


def main():
    args = parse_args()

    if EMBED_MODE not in VALID_EMBED_MODES:
        print(f"Error: EMBED_MODE is {EMBED_MODE!r}, "
              f"must be one of {VALID_EMBED_MODES}.")
        sys.exit(1)

    in_path, out_path = resolve_paths(args)
    caption, caption_source = resolve_caption(args, in_path)

    probe = KritaClient()
    if not probe.connect():
        print("Error: cannot reach the Kritomatic daemon at "
              f"{probe.host}:{probe.port}.")
        print("Make sure Krita is running and the Kritomatic Daemon plugin "
              "is enabled.")
        sys.exit(1)
    probe.close()

    assets_dir = out_path.parent / f".{out_path.stem}_assets"
    assets_dir.mkdir(exist_ok=True)

    src_kra = assets_dir / "source.kra"
    src_png = assets_dir / "source.png"

    # ---- Locally determine source dimensions, before any daemon work.
    # Everything downstream — geometry, font size, inline size, the
    # embed placement — depends only on these, so once they are known
    # the entire daemon-side pipeline is a single batch.
    need_convert = not is_kra(in_path)
    if need_convert:
        src_w, src_h = get_image_dimensions(in_path)
        print(f"Input is a bitmap: {in_path.name}  ({src_w} x {src_h})")
    else:
        # Work on a copy so the user's input file is never touched.
        # The daemon reads from the copy, in the batch below.
        shutil.copyfile(in_path, src_kra)
        src_w, src_h = get_kra_dimensions(src_kra)
        print(f"Input is a .kra: {in_path.name} -> copied to assets  "
              f"({src_w} x {src_h})")

    # ---- Compute geometry, font size, inline size, all locally.
    geo = compute_geometry(src_w, src_h)
    print(f"Canvas:  {geo['canvas_w']} x {geo['canvas_h']}"
          f"   (strip {geo['strip_h']} px)")
    print(f"Image:   {geo['scaled_w']} x {geo['scaled_h']}"
          f" at ({geo['img_x']}, {geo['img_y']})")
    if KEEP_IMAGE_NATIVE_SIZE:
        print("Mode:    native-size source, canvas grown by strip")
    if ROTATE_BEFORE_PROCESSING:
        print("Rotate:  strip on LEFT edge, text reads bottom-to-top "
              "(source stays in original orientation)")

    kra_group_scale = (
        geo["scaled_w"] / max(1, src_w),
        geo["scaled_h"] / max(1, src_h),
    )

    font_size = compute_font_size(caption, geo["strip_h"], geo["label_axis"])
    inline_size = compute_inline_size(geo["label_axis"])
    print(f"Caption: {caption!r}  ({caption_source}, font size ~{font_size},"
          f" inline box {inline_size} px, spans full strip axis)")
    print(f"Embed:   {EMBED_MODE}")

    # ---- Assemble the whole daemon pipeline as one batch.
    commands = []

    if need_convert:
        commands.extend(convert_commands(in_path, src_kra, src_w, src_h))

    if EMBED_MODE == "kra_group":
        print("Embed mode kra_group: skipping flatten-to-PNG stage.")
    else:
        commands.extend(flatten_commands(src_kra, src_png))

    commands.extend(canvas_commands(geo, f"Captionized {in_path.stem}"))
    commands.extend(embed_commands(geo, src_png, src_kra, kra_group_scale))
    commands.extend(label_commands(geo, caption, font_size, out_path))
    commands.extend(redraw_commands())

    bundle = {"id": "captionize", "commands": commands}

    try:
        run_batch(bundle, "captionize (single batch)")
    except RuntimeError as e:
        print(f"\n✗ {e}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("Caption embedded")
    print("=" * 60)
    print(f"  Input:    {in_path}")
    print(f"  Output:   {out_path}  ({geo['canvas_w']} x {geo['canvas_h']})")
    print(f"  Caption:  {caption!r}  ({caption_source})")
    print(f"  Strip:    {geo['strip_h']} px")
    print(f"  SHRINK_RATIO = {SHRINK_RATIO:.3f}")
    print(f"  Embed:    {EMBED_MODE}")
    if ROTATE_BEFORE_PROCESSING:
        print("  Rotate:   yes (strip on LEFT edge, text reads bottom-to-top)")
    else:
        print("  Rotate:   no (strip at top)")
    if KEEP_IMAGE_NATIVE_SIZE:
        print("  Anchor:   source (canvas grew by strip height)")
    else:
        print("  Anchor:   canvas (source shrunk into area below strip)")
    if (not KEEP_IMAGE_NATIVE_SIZE
            and TRIM_SIDE_MARGINS
            and geo["scaled_w"] < min(src_w, src_h)):
        print("  Trim:     side margins trimmed to scaled image width")
    if EMBED_MODE == "file_layer":
        print(f"  Asset:    {src_png}  (external reference; keep it around)")
    else:
        print("  Asset:    none — output .kra is self-contained")
    print("=" * 60)


if __name__ == "__main__":
    main()
