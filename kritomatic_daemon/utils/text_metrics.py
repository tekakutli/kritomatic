"""
text_metrics.py — per-word bounding boxes for Krita text shapes.

Given a text shape's parameters — its content, font family, point
size, SVG anchor position, alignment, and any affine transform Krita
has applied via Shape.setTransformation — compute the bounding box of
every word in the string.

Conventions
-----------
* `x` and `y` are the anchor point of the text baseline, in document
  pixels.  Krita's SVG emission writes y as the baseline for left-
  and right-aligned text, and as the vertical center for center-
  aligned text; the caller is responsible for having resolved that
  before calling in.
* `font_size` is a size in document pixels, not points.  Qt's
  QFontMetricsF interprets its input according to how the font was
  configured, so we set the pixel size explicitly.
* `alignment` is the resolved text-anchor: 'left', 'center', or
  'right'.

Output record
-------------
    {
        word:    "hello",
        x, y, w, h:      axis-aligned bbox in document pixels
        corners:         [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
    }
"""

from PyQt5.QtGui import QFont, QFontMetricsF


def _font_metrics(font_family, font_size):
    font = QFont(font_family or "sans-serif")
    # font_size arrives in document pixels, so configure the QFont in
    # pixels.  setPointSizeF would interpret the same number as
    # points, which is a different physical size (and on HiDPI
    # displays an entirely different pixel size).
    px = int(round(float(font_size or 12)))
    if px < 1:
        px = 1
    font.setPixelSize(px)
    return QFontMetricsF(font)


def word_boxes(text, font_family, font_size, x, y, alignment="left"):
    if not text:
        return []

    fm      = _font_metrics(font_family, font_size)
    ascent  = fm.ascent()
    height  = fm.height()
    full_w  = fm.horizontalAdvance(text)

    if alignment == "center":
        base_x = x - full_w / 2.0
    elif alignment == "right":
        base_x = x - full_w
    else:
        base_x = x

    out = []
    n   = len(text)
    i   = 0
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not text[i].isspace():
            i += 1
        word = text[start:i]

        prefix_before = text[:start]
        prefix_after  = text[:i]
        x0 = base_x + fm.horizontalAdvance(prefix_before)
        x1 = base_x + fm.horizontalAdvance(prefix_after)
        top    = y - ascent
        bottom = top + height

        corners = [[x0, top], [x1, top], [x1, bottom], [x0, bottom]]

        out.append({
            "word":    word,
            "x":       min(c[0] for c in corners),
            "y":       min(c[1] for c in corners),
            "w":       max(c[0] for c in corners) - min(c[0] for c in corners),
            "h":       max(c[1] for c in corners) - min(c[1] for c in corners),
            "corners": corners,
        })

    return out
