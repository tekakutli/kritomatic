"""
text_metrics.py — per-word bounding boxes for Krita text shapes.

Given a text shape's parameters — its content, font family, point
size, SVG anchor position, alignment, and any affine transform Krita
has applied via Shape.setTransformation — compute the bounding box of
every word in the string.

The text board uses this to draw word-level overlays inside each card.
The user sees not just the shape's overall rectangle, but the extent
of each individual word, so word-level alignment and formatting
become visible at a glance.

Conventions
-----------
* `x` and `y` in the input are the SVG anchor point — the same
  numbers Krita writes into the `<text x= y=>` element.
* `alignment` is the shape's resolved text-anchor: 'left', 'center',
  or 'right'.
* `transform`, when given, is a QTransform (Qt row-vector convention).
  It is applied to every word's corner; the mapped quad becomes the
  word's `corners`, and the axis-aligned bounding box of that quad
  becomes `x, y, w, h`.

Output record
-------------
    {
        word:    "hello",
        x, y, w, h:      axis-aligned bbox in document pixels
        corners:         [[x0,y0], [x1,y1], [x2,y2], [x3,y3]]
    }
"""

from PyQt5.QtGui import QFont, QFontMetricsF
from PyQt5.QtCore import QPointF


def _font_metrics(font_family, font_size):
    font = QFont(font_family or "sans-serif")
    font.setPointSizeF(float(font_size or 12))
    return QFontMetricsF(font)


def word_boxes(text, font_family, font_size, x, y, alignment="left",
               transform=None):
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

        if transform is not None:
            mapped = []
            for cx, cy in corners:
                p = transform.map(QPointF(cx, cy))
                mapped.append([p.x(), p.y()])
            corners = mapped

        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]

        out.append({
            "word":    word,
            "x":       min(xs),
            "y":       min(ys),
            "w":       max(xs) - min(xs),
            "h":       max(ys) - min(ys),
            "corners": corners,
        })

    return out
