import html
import json
import math
import re
from krita import Krita
from PyQt6.QtGui import QTransform
from PyQt6.QtCore import QPointF
from ..decorators import command, AutoDispatchHandler
from ..utils.refresh import refresh


# ==========================================================================
# SVG parsing helpers
# ==========================================================================

_TEXT_BODY_RE = re.compile(r'<text[^>]*>(.*?)</text>', re.DOTALL)
_TAG_RE       = re.compile(r'<[^>]+>')
_ATTR_RE      = re.compile(r'\b([a-zA-Z-]+)="([^"]*)"')
_STYLE_RE     = re.compile(r'style="([^"]*)"')
_STYLE_PROP_RE = re.compile(r'([a-zA-Z-]+)\s*:\s*([^;]+)')

_INLINE_SIZE_RE  = re.compile(r'inline-size:\s*([0-9.]+)')
_TEXT_ANCHOR_RE  = re.compile(r'text-anchor="([^"]*)"')


def _parse_text_svg(svg):
    out = {}

    m = _TEXT_BODY_RE.search(svg)
    if m:
        raw = m.group(1)
        # Krita emits <tspan> elements when a run inside the string
        # carries its own style (font, weight, stretch, ...).  The
        # `text` field this function produces is meant to be the
        # visible characters, not the SVG markup, so strip the
        # nested tags and decode entities.
        #
        # Order matters.  Unescaping first would turn a literal
        # "&lt;tspan&gt;" the user typed into "<tspan>", which the
        # tag pass would then eat.  Stripping first leaves
        # entity-encoded angle brackets intact through the tag pass,
        # and the unescape restores them as literal characters in
        # the visible text.
        raw = _TAG_RE.sub('', raw)
        raw = html.unescape(raw)
        out['text'] = raw

    attrs = dict(_ATTR_RE.findall(svg))

    style_props = {}
    sm = _STYLE_RE.search(svg)
    if sm:
        style_props = {k.strip(): v.strip()
                       for k, v in _STYLE_PROP_RE.findall(sm.group(1))}

    for key in ('x', 'y'):
        raw = attrs.get(key)
        if raw is not None:
            try:
                out[key] = float(raw)
            except ValueError:
                pass

    if 'fill' in attrs:
        out['color'] = attrs['fill']

    ff = attrs.get('font-family') or style_props.get('font-family')
    if ff:
        out['font_family'] = ff.strip("'\"")

    fs = attrs.get('font-size') or style_props.get('font-size')
    if fs:
        try:
            out['font_size'] = float(fs)
        except ValueError:
            pass

    anchor = attrs.get('text-anchor', '')
    if anchor == 'middle':
        out['alignment'] = 'center'
    elif anchor == 'end':
        out['alignment'] = 'right'
    else:
        out['alignment'] = 'left'

    if 'inline-size' in style_props:
        try:
            out['inline_size'] = float(style_props['inline-size'])
        except (TypeError, ValueError):
            pass
    if 'white-space' in style_props:
        out['white_space'] = style_props['white-space']

    return out


def _apply_transform(shape, qtransform):
    """Apply an arbitrary QTransform to a shape, then nudge Krita into
    recomputing its cached bounds.

    Without the nudge, rotated or sheared text renders clipped: Krita
    caches the shape's bounds at creation and does not recompute them
    after setTransformation.  A user editing the text in the vector
    editor triggers the same relayout this method performs
    programmatically, which is why the clipping disappears as soon as
    the text is touched by hand.
    """
    if not hasattr(shape, 'setTransformation'):
        return
    shape.setTransformation(qtransform)

    for method_name, args in (
        ('update', ()),
        ('updateAbsoluteGeometry', ()),
        ('setShapeChanged', (True,)),
    ):
        if hasattr(shape, method_name):
            try:
                getattr(shape, method_name)(*args)
                return
            except Exception:
                continue


def _is_text_shape(shape):
    try:
        return '<text' in shape.toSvg()
    except Exception:
        return False


def _find_text_shape(layer):
    """Return the first text shape in a vector layer, or None."""
    try:
        for s in layer.shapes():
            if _is_text_shape(s):
                return s
    except Exception:
        pass
    return None


def _collect_vector_layers(doc):
    found = []

    def walk(node):
        if node.type() == 'vectorlayer':
            found.append(node)
        for child in node.childNodes():
            walk(child)

    walk(doc.rootNode())
    return found


# ==========================================================================
# Inline-size handle utilities
# ==========================================================================
#
# Krita's SVG 2 "inline wrapped" text mode draws two vertical handles
# at the wrapping area's left and right edges.  Those edges sit, in
# the text shape's local frame:
#
#     text-anchor="start"    left = 0     right = +I
#     text-anchor="middle"   left = -I/2  right = +I/2
#     text-anchor="end"      left = -I    right = 0
#
# where I is the shape's inline-size.  In the parent frame the edges
# are the images of those local points under the shape's
# transformation.

_ALPHA = {'start': 0.0,  'middle': -0.5, 'end': -1.0}
_BETA  = {'start': 1.0,  'middle':  0.5, 'end':  0.0}
_GAMMA = {'start': 0.5,  'middle':  0.0, 'end': -0.5}


def _get_inline_size(shape):
    try:
        svg = shape.toSvg()
    except Exception:
        return None
    m = _INLINE_SIZE_RE.search(svg)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _get_anchor_mode(shape):
    try:
        svg = shape.toSvg()
    except Exception:
        return 'start'
    m = _TEXT_ANCHOR_RE.search(svg)
    if not m:
        return 'start'
    v = m.group(1)
    if v in ('start', 'middle', 'end'):
        return v
    if v == 'left':   return 'start'
    if v == 'right':  return 'end'
    if v == 'center': return 'middle'
    return 'start'


def _get_transformation(shape):
    if hasattr(shape, 'transformation'):
        try:
            t = shape.transformation()
            if t is not None:
                return t
        except Exception:
            pass
    return QTransform()


def _linear_sq(T):
    return T.m11() * T.m11() + T.m12() * T.m12()


def _make_transform_like(T, new_m31, new_m32):
    return QTransform(
        T.m11(), T.m12(), T.m13(),
        T.m21(), T.m22(), T.m23(),
        new_m31, new_m32, T.m33(),
    )


def _get_inline_size_from_svg(svg):
    """Return the numeric inline-size parsed out of a text SVG, or
    None.  Used to identify a specific shape by its inline-size value
    rather than by list position."""
    m = _INLINE_SIZE_RE.search(svg)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def _rebuild_inline_shape(layer, old_shape, new_I, new_T):
    """Replace `old_shape` on `layer` with the same SVG but a new
    inline-size, and apply `new_T`.

    Only the numeric inline-size value in the style attribute is
    touched.  Fill, stroke, font-size, text-anchor,
    dominant-baseline, and the text body itself are preserved
    character-for-character.

    The new shape is found by matching the inline-size value that
    was just written, not by taking `layer.shapes()[-1]`.  Krita's
    shape container order is not guaranteed to be creation order,
    so index -1 can pick the OLD shape; when that happens the
    transform is set on the shape about to be removed and the new
    shape is left with an identity transform (which lands it at the
    document origin).
    """
    svg = old_shape.toSvg()
    svg_new = _INLINE_SIZE_RE.sub(
        'inline-size: %.6f' % new_I, svg, count=1,
    )

    doc = Krita.instance().activeDocument()
    if doc is None:
        raise RuntimeError("no active document")
    wrapper = (
        '<svg width="%d" height="%d" xmlns="http://www.w3.org/2000/svg">'
        '%s</svg>'
    ) % (doc.width(), doc.height(), svg_new)

    layer.addShapesFromSvg(wrapper)

    # Find the newly-added shape by its inline-size value.
    new_shape = None
    for s in layer.shapes():
        try:
            s_I = _get_inline_size_from_svg(s.toSvg())
        except Exception:
            continue
        if s_I is None:
            continue
        if abs(s_I - new_I) < 1e-6:
            new_shape = s
            break
    if new_shape is None:
        raise RuntimeError("new shape not found after rebuild")

    new_shape.setTransformation(new_T)
    for m in ('update', 'updateAbsoluteGeometry'):
        if hasattr(new_shape, m):
            try:
                getattr(new_shape, m)()
                break
            except Exception:
                continue

    # Force the transform to be committed before the old shape is
    # removed.  Inside a batch, refresh(doc) is deferred; a direct
    # refreshProjection here ensures the renderer is looking at the
    # new shape's state when the old one goes away.
    try:
        doc.refreshProjection()
    except Exception:
        pass

    old_shape.remove()

    return new_shape


# ==========================================================================
# Pixel-measured word boxes
# ==========================================================================

def _source_words(text):
    out = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not text[i].isspace():
            i += 1
        out.append(text[start:i])
    return out


def _merge_runs(runs, target_count):
    runs = list(runs)
    while len(runs) > target_count and len(runs) > 1:
        gaps = [(runs[i + 1][0] - runs[i][1], i)
                for i in range(len(runs) - 1)]
        gaps.sort()
        _, idx = gaps[0]
        runs[idx] = (runs[idx][0], runs[idx + 1][1])
        del runs[idx + 1]
    return runs


def _words_from_pixels(layer, transform, text):
    try:
        b = layer.bounds()
    except Exception:
        return []
    bx, by = int(b.x()), int(b.y())
    bw, bh = int(b.width()), int(b.height())
    if bw <= 0 or bh <= 0:
        return []

    try:
        raw = layer.projectionPixelData(bx, by, bw, bh)
    except Exception:
        return []
    if not raw:
        return []
    pixels = bytes(raw)
    if len(pixels) < bw * bh * 4:
        return []

    t_inv, ok = transform.inverted()
    if not ok:
        return []
    mi11 = t_inv.m11(); mi12 = t_inv.m12()
    mi21 = t_inv.m21(); mi22 = t_inv.m22()
    mi31 = t_inv.m31(); mi32 = t_inv.m32()

    ink = []
    for yy in range(bh):
        base = yy * bw
        dy = by + yy + 0.5
        for xx in range(bw):
            if pixels[(base + xx) * 4 + 3] > 0:
                dx = bx + xx + 0.5
                lx = dx * mi11 + dy * mi21 + mi31
                ly = dx * mi12 + dy * mi22 + mi32
                ink.append((lx, ly))

    if not ink:
        return []

    xs_sorted = sorted(p[0] for p in ink)
    span = xs_sorted[-1] - xs_sorted[0]
    threshold = max(4.0, span / 40.0)

    runs = []
    cur_start = xs_sorted[0]
    prev = xs_sorted[0]
    for x in xs_sorted[1:]:
        if x - prev > threshold:
            runs.append((cur_start, prev))
            cur_start = x
        prev = x
    runs.append((cur_start, prev))

    words = _source_words(text)
    target = max(1, len(words))
    runs = _merge_runs(runs, target)

    result = []
    for k, (x0, x1) in enumerate(runs):
        lo = x0 - 0.5
        hi = x1 + 0.5
        band = [p for p in ink if lo <= p[0] <= hi]
        if not band:
            continue
        lxs = [p[0] for p in band]
        lys = [p[1] for p in band]
        wx = min(lxs)
        wy = min(lys)
        ww = max(lxs) - wx
        wh = max(lys) - wy

        local_corners = [
            (wx, wy),
            (wx + ww, wy),
            (wx + ww, wy + wh),
            (wx, wy + wh),
        ]
        doc_corners = [transform.map(QPointF(c[0], c[1]))
                       for c in local_corners]
        label = words[k] if k < len(words) else ""
        result.append({
            'word': label,
            'corners': [[p.x(), p.y()] for p in doc_corners],
        })
    return result


# ==========================================================================
# Multi-line (inline-wrapped) word boxes
# ==========================================================================
#
# SVG 2 inline-wrapped text flows across multiple lines within a single
# text shape.  Two words on adjacent lines overlap in x, so the
# x-projection gap test used by `_words_from_pixels` cannot separate
# them: the whole wrapped block clusters into one run and the result is
# a single box around the entire paragraph.  Clustering in y first
# (into text lines) and then in x within each line recovers the
# per-word boxes without changing how pre-positioned text is measured.

def _split_ink_into_lines(ink):
    """Partition ink pixels into text lines by y-coordinate.

    Within a line, ink varies in y over roughly the font's cap-height,
    so consecutive y-values are dense (usually step 1).  Between lines
    there is a gutter of roughly line-height minus cap-height.  The
    largest gap between consecutive y-values is therefore a real
    gutter when the text has multiple lines, and tiny when it has one.
    Splitting at half that gap separates lines without touching
    within-line variation.
    """
    if not ink:
        return []
    ink_by_y = sorted(ink, key=lambda p: p[1])
    ys = [p[1] for p in ink_by_y]

    if len(ys) < 2:
        threshold = 1.0
    else:
        gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
        largest = max(gaps)
        threshold = max(2.0, largest * 0.5)

    lines = []
    cur = [ink_by_y[0]]
    prev_y = ink_by_y[0][1]
    for p in ink_by_y[1:]:
        if p[1] - prev_y > threshold:
            lines.append(cur)
            cur = [p]
        else:
            cur.append(p)
        prev_y = p[1]
    lines.append(cur)
    return lines


def _cluster_line_by_x(line_pixels):
    """Cluster one line's ink into x-runs (candidate words)."""
    xs = sorted(p[0] for p in line_pixels)
    x_span = xs[-1] - xs[0]
    threshold = max(4.0, x_span / 40.0)
    runs = []
    cur_start = xs[0]
    prev = xs[0]
    for x in xs[1:]:
        if x - prev > threshold:
            runs.append((cur_start, prev))
            cur_start = x
        prev = x
    runs.append((cur_start, prev))
    return runs


def _words_from_pixels_wrapped(layer, transform, text):
    """Per-word boxes for inline-wrapped text.

    Identical setup to `_words_from_pixels` — same pixel read, same
    inverse transform, same ink extraction.  The difference is the
    clustering: lines first (by y), then words within each line (by
    x).  Reading order is line 1 left-to-right, line 2 left-to-right,
    which is the order `_source_words(text)` yields.
    """
    try:
        b = layer.bounds()
    except Exception:
        return []
    bx, by = int(b.x()), int(b.y())
    bw, bh = int(b.width()), int(b.height())
    if bw <= 0 or bh <= 0:
        return []

    try:
        raw = layer.projectionPixelData(bx, by, bw, bh)
    except Exception:
        return []
    if not raw:
        return []
    pixels = bytes(raw)
    if len(pixels) < bw * bh * 4:
        return []

    t_inv, ok = transform.inverted()
    if not ok:
        return []
    mi11 = t_inv.m11(); mi12 = t_inv.m12()
    mi21 = t_inv.m21(); mi22 = t_inv.m22()
    mi31 = t_inv.m31(); mi32 = t_inv.m32()

    ink = []
    for yy in range(bh):
        base = yy * bw
        dy = by + yy + 0.5
        for xx in range(bw):
            if pixels[(base + xx) * 4 + 3] > 0:
                dx = bx + xx + 0.5
                lx = dx * mi11 + dy * mi21 + mi31
                ly = dx * mi12 + dy * mi22 + mi32
                ink.append((lx, ly))

    if not ink:
        return []

    lines = _split_ink_into_lines(ink)

    # Collect candidate runs as (line_idx, x0, x1).
    runs = []
    for li, line_pixels in enumerate(lines):
        for x0, x1 in _cluster_line_by_x(line_pixels):
            runs.append((li, x0, x1))

    words  = _source_words(text)
    target = max(1, len(words))

    # Over-detection: merge the closest adjacent runs that are on the
    # same line.  A cross-line merge is never valid — those are
    # different words no matter how their x-ranges line up.
    while len(runs) > target:
        best_i = -1
        best_gap = None
        for i in range(len(runs) - 1):
            if runs[i][0] != runs[i + 1][0]:
                continue
            gap = runs[i + 1][1] - runs[i][2]
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_i = i
        if best_i < 0:
            break
        li, x0, _ = runs[best_i]
        _, _, x1 = runs[best_i + 1]
        runs[best_i] = (li, x0, x1)
        del runs[best_i + 1]

    result = []
    for k, (li, x0, x1) in enumerate(runs):
        band = [p for p in lines[li] if x0 - 0.5 <= p[0] <= x1 + 0.5]
        if not band:
            continue
        lxs = [p[0] for p in band]
        lys = [p[1] for p in band]
        wx = min(lxs)
        wy = min(lys)
        ww = max(lxs) - wx
        wh = max(lys) - wy

        local_corners = [
            (wx, wy),
            (wx + ww, wy),
            (wx + ww, wy + wh),
            (wx, wy + wh),
        ]
        doc_corners = [transform.map(QPointF(c[0], c[1]))
                       for c in local_corners]
        label = words[k] if k < len(words) else ""
        result.append({
            'word': label,
            'corners': [[p.x(), p.y()] for p in doc_corners],
        })
    return result


def _bounds_from_words(word_boxes):
    xs = []
    ys = []
    for wb in word_boxes:
        for c in wb['corners']:
            xs.append(c[0])
            ys.append(c[1])
    if not xs:
        return {'x': 0, 'y': 0, 'w': 0, 'h': 0}
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    return {'x': x0, 'y': y0, 'w': x1 - x0, 'h': y1 - y0}


def _fallback_word_box(shape, transform, text):
    try:
        b = shape.boundingBox()
    except Exception:
        return []
    local_corners = [
        (b.x(),             b.y()),
        (b.x() + b.width(), b.y()),
        (b.x() + b.width(), b.y() + b.height()),
        (b.x(),             b.y() + b.height()),
    ]
    doc_corners = [transform.map(QPointF(c[0], c[1]))
                   for c in local_corners]
    return [{
        'word': text,
        'corners': [[p.x(), p.y()] for p in doc_corners],
    }]


class LayerTextHandler(AutoDispatchHandler):

    # ------------------------------------------------------------------
    # add_vector_text
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Add vector text to a vector layer',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the target vector layer'},
            '--text': {'type': 'str', 'required': True, 'help': 'Text content'},
            '--font_family': {'type': 'str', 'default': 'sans-serif', 'help': 'Font family (e.g., Arial, sans-serif)'},
            '--font_size': {'type': 'int', 'default': 12, 'help': 'Font size in points'},
            '--x': {'type': 'float', 'default': 0, 'help': 'X position in pixels'},
            '--y': {'type': 'float', 'default': 0, 'help': 'Y position in pixels'},
            '--color': {'type': 'str', 'default': '#000000', 'help': 'Hex color (e.g., #ff0000)'},
            '--alignment': {'type': 'str', 'default': 'left', 'choices': ['left', 'center', 'right'], 'help': 'Text alignment'},
            '--rotation': {'type': 'float', 'default': 0,
                           'help': 'Rotation in degrees around (x, y); positive is '
                                   'clockwise.  Ignored when --transform is given.'},
            '--transform': {'type': 'str', 'required': False,
                            'help': 'Optional 6-element list [m11, m12, m21, m22, dx, dy] '
                                    'defining a full affine QTransform (Qt row-vector '
                                    'convention) to apply to the newly-created shape.  '
                                    'Replaces --rotation when given.'},
            '--inline_size': {'type': 'float', 'required': False,
                              'help': 'Optional wrap width.  When set, the text is '
                                      'created in Krita\'s SVG 2 "inline wrapped" '
                                      'mode and wraps automatically within this width.  '
                                      'Omit for the classic pre-positioned (SVG 1.1) '
                                      'text mode.'},
        }
    )
    def add_vector_text(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            text = params.get('text', '')
            font_family = params.get('font_family', 'sans-serif')
            font_size = params.get('font_size', 12)
            x = params.get('x', 0)
            y = params.get('y', 0)
            color = params.get('color', '#000000')
            alignment = params.get('alignment', 'left')
            rotation = params.get('rotation', 0)
            transform_raw = params.get('transform', None)
            inline_size = params.get('inline_size', None)

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}
            if target_layer.type() != 'vectorlayer':
                return {'success': False, 'message': f'Layer "{layer_name}" is not a vector layer'}

            canvas_width = doc.width()
            canvas_height = doc.height()

            if alignment == "center" and x == 0 and y == 0:
                x = canvas_width / 2
                y = canvas_height / 2

            text_align = ""
            if alignment == "center":
                text_align = ' text-anchor="middle" dominant-baseline="middle"'
            elif alignment == "left":
                text_align = ' text-anchor="start" dominant-baseline="middle"'
            elif alignment == "right":
                text_align = ' text-anchor="end" dominant-baseline="middle"'

            if not color.startswith('#'):
                color = '#' + color

            is_wrapped = False
            if inline_size is not None:
                try:
                    inline_size = float(inline_size)
                except (TypeError, ValueError):
                    inline_size = None
            if inline_size is not None and inline_size > 0:
                is_wrapped = True

            if is_wrapped:
                # Inline-wrapped: no x/y on the <text> element.  Every
                # style property goes inside the style attribute.
                style = (
                    f'inline-size: {inline_size};'
                    f'font-size: {font_size};'
                    f'white-space: pre-wrap;'
                )
                svg = (
                    f'<svg width="{canvas_width}" height="{canvas_height}" '
                    f'xmlns="http://www.w3.org/2000/svg">'
                    f'<text font-family="{font_family}" fill="{color}" '
                    f'style="{style}"{text_align}>{text}</text>'
                    f'</svg>'
                )
            else:
                svg = (
                    f'<svg width="{canvas_width}" height="{canvas_height}" '
                    f'xmlns="http://www.w3.org/2000/svg">'
                    f'<text font-family="{font_family}" '
                    f'font-size="{font_size}" fill="{color}" '
                    f'x="{x}" y="{y}"{text_align}>{text}</text>'
                    f'</svg>'
                )

            target_layer.addShapesFromSvg(svg)
            shapes_after = list(target_layer.shapes())

            marker = f'>{text}<'
            new_shape = None
            for s in shapes_after:
                try:
                    if marker in s.toSvg():
                        new_shape = s
                        break
                except Exception:
                    continue
            if new_shape is None and shapes_after:
                new_shape = shapes_after[-1]

            if new_shape is not None:
                if is_wrapped:
                    # Inline-wrapped shapes carry no x/y in their SVG,
                    # so the shape's local origin is the anchor.  The
                    # transformation must map local (0,0) to (x, y) in
                    # the parent frame, with the rotation applied
                    # around the anchor.
                    #
                    # Qt composes transforms by POST-multiplying, so
                    # to get T = T_rot(θ) · T_trans(x, y) we must call
                    # rotate() BEFORE translate().  Calling translate()
                    # first would give T_trans(x, y) · T_rot(θ), whose
                    # fixed point is (-x, -y) rather than the origin —
                    # every rotated shape would land at the rotated
                    # anchor instead of at (x, y).
                    if transform_raw:
                        try:
                            if isinstance(transform_raw, str):
                                vals = json.loads(transform_raw)
                            else:
                                vals = transform_raw
                            vals = [float(v) for v in vals]
                            if len(vals) == 6:
                                m11, m12, m21, m22, dx, dy = vals
                                dx2 = m11 * x + m21 * y + dx
                                dy2 = m12 * x + m22 * y + dy
                                t = QTransform(m11, m12, m21, m22, dx2, dy2)
                                _apply_transform(new_shape, t)
                        except Exception:
                            pass
                    else:
                        t = QTransform()
                        if rotation:
                            t.rotate(rotation)
                        t.translate(x, y)
                        _apply_transform(new_shape, t)
                else:
                    applied_transform = False
                    if transform_raw:
                        try:
                            if isinstance(transform_raw, str):
                                vals = json.loads(transform_raw)
                            else:
                                vals = transform_raw
                            vals = [float(v) for v in vals]
                            if len(vals) == 6:
                                m11, m12, m21, m22, dx, dy = vals
                                t = QTransform(m11, m12, m21, m22, dx, dy)
                                _apply_transform(new_shape, t)
                                applied_transform = True
                        except Exception:
                            pass

                    if not applied_transform and rotation:
                        t = QTransform()
                        t.translate(x, y)
                        t.rotate(rotation)
                        t.translate(-x, -y)
                        _apply_transform(new_shape, t)

            refresh(doc)

            return {
                'success': True,
                'message': f'Added text to "{layer_name}"',
                'data': {
                    'text': text, 'font': font_family, 'size': font_size,
                    'position': (x, y), 'rotation': rotation,
                    'canvas': (canvas_width, canvas_height),
                    'inline_size': inline_size if is_wrapped else None,
                    'mode': 'inline-wrapped' if is_wrapped else 'pre-positioned',
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # add_vector_polygon
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Add a polygon to a vector layer',
        args={
            '--layer_name': {'type': 'str', 'required': True,
                             'help': 'Name of the target vector layer'},
            '--points': {'type': 'str', 'required': True,
                         'help': 'JSON list of [x, y] coordinate pairs, e.g. '
                                 '"[[10,20],[30,20],[30,40],[10,40]]"'},
            '--fill': {'type': 'str', 'default': '#ffffff',
                       'help': 'Fill color (hex), or "none"'},
            '--fill_opacity': {'type': 'float', 'default': 1.0,
                               'help': 'Fill opacity (0.0 - 1.0)'},
            '--stroke': {'type': 'str', 'default': 'none',
                         'help': 'Stroke color (hex), or "none"'},
            '--stroke_width': {'type': 'float', 'default': 2.0,
                               'help': 'Stroke width in pixels'},
            '--stroke_opacity': {'type': 'float', 'default': 1.0,
                                 'help': 'Stroke opacity (0.0 - 1.0)'},
        }
    )
    def add_vector_polygon(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name    = params.get('layer_name', '')
            points_raw    = params.get('points', '')
            fill          = params.get('fill', '#ffffff')
            fill_opacity  = float(params.get('fill_opacity', 1.0))
            stroke        = params.get('stroke', 'none')
            stroke_width  = float(params.get('stroke_width', 2.0))
            stroke_opacity = float(params.get('stroke_opacity', 1.0))

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False,
                        'message': f'Layer "{layer_name}" not found'}
            if target_layer.type() != 'vectorlayer':
                return {'success': False,
                        'message': f'Layer "{layer_name}" is not a vector layer'}

            if isinstance(points_raw, str):
                pts = json.loads(points_raw)
            else:
                pts = points_raw

            if not pts or len(pts) < 3:
                return {'success': False,
                        'message': 'At least 3 points required'}

            points_attr = ' '.join(
                f'{float(p[0])},{float(p[1])}' for p in pts
            )

            canvas_width  = doc.width()
            canvas_height = doc.height()

            svg = (
                f'<svg width="{canvas_width}" height="{canvas_height}" '
                f'xmlns="http://www.w3.org/2000/svg">'
                f'<polygon points="{points_attr}" '
                f'fill="{fill}" fill-opacity="{fill_opacity}" '
                f'stroke="{stroke}" stroke-width="{stroke_width}" '
                f'stroke-opacity="{stroke_opacity}"/>'
                f'</svg>'
            )

            target_layer.addShapesFromSvg(svg)
            refresh(doc)

            return {
                'success': True,
                'message': f'Added polygon ({len(pts)} points) to '
                           f'"{layer_name}"',
                'data': {'layer_name': layer_name, 'point_count': len(pts)},
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # update_vector_text
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Update existing text on a vector layer',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the target vector layer'},
            '--old_text': {'type': 'str', 'required': True, 'help': 'Current text to replace'},
            '--new_text': {'type': 'str', 'required': True, 'help': 'New text content'}
        }
    )
    def update_vector_text(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            old_text = params.get('old_text', '')
            new_text = params.get('new_text', '')

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}
            if target_layer.type() != 'vectorlayer':
                return {'success': False, 'message': f'Layer "{layer_name}" is not a vector layer'}

            width = doc.width()
            height = doc.height()

            shape_to_remove = None
            original_svg = None
            transform = None

            for shape in target_layer.shapes():
                svg = shape.toSvg()
                if old_text in svg:
                    shape_to_remove = shape
                    original_svg = svg
                    if hasattr(shape, 'transformation'):
                        transform = shape.transformation()
                    break

            if shape_to_remove is None:
                return {'success': False, 'message': f'Text "{old_text}" not found on layer "{layer_name}"'}

            new_svg = original_svg.replace(old_text, new_text)
            complete_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {new_svg}
    </svg>'''

            shape_to_remove.remove()
            target_layer.addShapesFromSvg(complete_svg)

            if transform:
                all_shapes = list(target_layer.shapes())
                if all_shapes:
                    new_shape = all_shapes[-1]
                    if hasattr(new_shape, 'setTransformation'):
                        new_shape.setTransformation(transform)

            refresh(doc)

            return {'success': True, 'message': f'Updated text from "{old_text}" to "{new_text}" on "{layer_name}"'}

        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # list_shapes
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='List all shapes on a vector layer (for debugging)',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the vector layer'}
        }
    )
    def list_shapes(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            shapes = []
            for shape in target_layer.shapes():
                shapes.append({'type': str(type(shape)), 'svg_preview': shape.toSvg()[:200]})

            return {'success': True, 'message': f'Found {len(shapes)} shapes', 'data': {'shapes': shapes}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # replace_all_text
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Replace text across all vector layers',
        args={
            '--old_text': {'type': 'str', 'required': True, 'help': 'Text to find'},
            '--new_text': {'type': 'str', 'required': True, 'help': 'Text to replace with'},
            '--scope': {'type': 'str', 'default': 'all', 'choices': ['all', 'active'], 'help': 'Scope of search (all layers or active layer only)'}
        }
    )
    def replace_all_text(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            old_text = params.get('old_text', '')
            new_text = params.get('new_text', '')
            scope = params.get('scope', 'all')

            width = doc.width()
            height = doc.height()

            vector_layers = []

            def find_vector_layers(node):
                if node.type() == 'vectorlayer':
                    vector_layers.append(node)
                for child in node.childNodes():
                    find_vector_layers(child)

            if scope == 'all':
                find_vector_layers(doc.rootNode())
            else:
                active = doc.activeNode()
                if active and active.type() == 'vectorlayer':
                    vector_layers.append(active)
                else:
                    return {'success': False, 'message': 'Active layer is not a vector layer'}

            if not vector_layers:
                return {'success': False, 'message': 'No vector layers found'}

            total_replacements = 0
            layers_modified = 0

            for layer in vector_layers:
                replacements_in_layer = 0
                shapes_data = []

                for shape in layer.shapes():
                    svg = shape.toSvg()
                    if old_text in svg:
                        transform = None
                        if hasattr(shape, 'transformation'):
                            transform = shape.transformation()

                        new_svg = svg.replace(old_text, new_text)
                        shapes_data.append((new_svg, transform))
                        replacements_in_layer += 1

                if replacements_in_layer > 0:
                    for shape in list(layer.shapes()):
                        shape.remove()

                    for svg, transform in shapes_data:
                        complete_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {svg}
    </svg>'''
                        layer.addShapesFromSvg(complete_svg)

                        if transform:
                            all_shapes = list(layer.shapes())
                            if all_shapes:
                                new_shape = all_shapes[-1]
                                if hasattr(new_shape, 'setTransformation'):
                                    new_shape.setTransformation(transform)

                    total_replacements += replacements_in_layer
                    layers_modified += 1
                    print(f"  \u2713 Updated {replacements_in_layer} text(s) in layer '{layer.name()}'")

            refresh(doc)

            return {
                'success': True,
                'message': f'Replaced "{old_text}" with "{new_text}" across {layers_modified} layer(s), {total_replacements} replacement(s)',
                'data': {
                    'old_text': old_text,
                    'new_text': new_text,
                    'layers_modified': layers_modified,
                    'total_replacements': total_replacements
                }
            }

        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # extract_all_text
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Extract all text from vector text objects in the document',
        args={
            '--layer_name': {'type': 'str', 'required': False, 'help': 'Specific layer name (optional, returns all layers if not specified)'},
            '--output': {'type': 'str', 'required': False, 'help': 'Output file path (optional, prints to console if not specified)'}
        }
    )
    def extract_all_text(self, params):
        """Extract all text from vector text objects in the document"""
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', None)
            output_file = params.get('output', None)

            vector_layers = []

            def find_vector_layers(node):
                if node.type() == 'vectorlayer':
                    vector_layers.append(node)
                for child in node.childNodes():
                    find_vector_layers(child)

            if layer_name:
                target = doc.nodeByName(layer_name)
                if not target:
                    return {'success': False, 'message': f'Layer "{layer_name}" not found'}
                if target.type() == 'vectorlayer':
                    vector_layers = [target]
                else:
                    return {'success': False, 'message': f'Layer "{layer_name}" is not a vector layer'}
            else:
                find_vector_layers(doc.rootNode())

            if not vector_layers:
                return {'success': False, 'message': 'No vector layers found'}

            results = []
            total_text_objects = 0

            for layer in vector_layers:
                layer_texts = []
                for shape in layer.shapes():
                    svg = shape.toSvg()
                    text_matches = re.findall(r'>([^<]+)<', svg)
                    if text_matches:
                        for text in text_matches:
                            if text.strip():
                                layer_texts.append(text.strip())
                                total_text_objects += 1

                if layer_texts:
                    results.append({
                        'layer_name': layer.name(),
                        'texts': layer_texts,
                        'count': len(layer_texts)
                    })

            output_lines = []
            output_lines.append(f"Document: {doc.fileName() if doc.fileName() else 'Untitled'}")
            output_lines.append(f"Total text objects found: {total_text_objects}")
            output_lines.append("")

            for layer_result in results:
                output_lines.append(f"Layer: {layer_result['layer_name']}")
                output_lines.append(f"  Text objects ({layer_result['count']}):")
                for i, text in enumerate(layer_result['texts'], 1):
                    output_lines.append(f"    {i}. {text}")
                output_lines.append("")

            output_text = "\n".join(output_lines)

            if output_file:
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.write(output_text)
                return {
                    'success': True,
                    'message': f'Extracted {total_text_objects} text objects to {output_file}',
                    'data': {
                        'total_text_objects': total_text_objects,
                        'layers': len(results),
                        'output_file': output_file
                    }
                }
            else:
                print(output_text)
                return {
                    'success': True,
                    'message': f'Found {total_text_objects} text objects',
                    'data': {
                        'total_text_objects': total_text_objects,
                        'layers': len(results),
                        'details': results
                    }
                }

        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # list_text_layers
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='List the names of every vector layer that contains at '
                  'least one text shape',
        args={}
    )
    def list_text_layers(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            names = []
            for layer in _collect_vector_layers(doc):
                if _find_text_shape(layer) is not None:
                    names.append(layer.name())

            return {
                'success': True,
                'message': f'{len(names)} text layer(s)',
                'data': {'layers': names},
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # get_layer_text_metadata
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Return full records for every text shape in a layer.  '
                  'Each record carries the shape\'s content, font, size, '
                  'color, position, alignment, rotation, and transform.',
        args={
            '--layer_name': {'type': 'str', 'required': True,
                             'help': 'Name of the vector layer'},
        }
    )
    def get_layer_text_metadata(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            layer = doc.nodeByName(layer_name)
            if not layer:
                return {'success': False,
                        'message': f'Layer "{layer_name}" not found'}
            if layer.type() != 'vectorlayer':
                return {'success': False,
                        'message': f'Layer "{layer_name}" is not a vector layer'}

            try:
                shapes = list(layer.shapes())
            except Exception:
                shapes = []

            records = []
            for i, shape in enumerate(shapes):
                if not _is_text_shape(shape):
                    continue
                try:
                    svg = shape.toSvg()
                except Exception:
                    continue

                rec = {'layer_name': layer_name, 'text_index': i}
                rec.update(_parse_text_svg(svg))

                if hasattr(shape, 'transformation'):
                    try:
                        t = shape.transformation()
                        if t is not None:
                            rec['rotation_deg'] = math.degrees(
                                math.atan2(t.m12(), t.m11())
                            )
                            rec['transform'] = [
                                t.m11(), t.m12(), t.m13(),
                                t.m21(), t.m22(), t.m23(),
                                t.m31(), t.m32(), t.m33(),
                            ]
                    except Exception:
                        pass

                records.append(rec)

            return {
                'success': True,
                'message': f'{len(records)} text shape(s) on "{layer_name}"',
                'data': records,
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # patch_layer_text
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Patch some fields of one text shape in a layer.  Any '
                  'field omitted from the patch is preserved from the '
                  'shape\'s current state.',
        args={
            '--layer_name': {'type': 'str', 'required': True,
                             'help': 'Name of the vector layer'},
            '--text_index': {'type': 'int', 'default': 0,
                             'help': 'Index of the target shape within the '
                                     'layer\'s shapes() list.'},
            '--patch': {'type': 'str', 'required': True,
                        'help': 'JSON object with the fields to change'},
        }
    )
    def patch_layer_text(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            text_index = int(params.get('text_index', 0))
            patch_raw = params.get('patch', '{}')
            if isinstance(patch_raw, str):
                patch = json.loads(patch_raw)
            else:
                patch = patch_raw
            if not isinstance(patch, dict):
                return {'success': False,
                        'message': 'patch must be a JSON object'}

            layer = doc.nodeByName(layer_name)
            if not layer:
                return {'success': False,
                        'message': f'Layer "{layer_name}" not found'}
            if layer.type() != 'vectorlayer':
                return {'success': False,
                        'message': f'Layer "{layer_name}" is not a vector layer'}

            try:
                shapes = list(layer.shapes())
            except Exception:
                shapes = []
            if text_index < 0 or text_index >= len(shapes):
                return {'success': False,
                        'message': f'text_index {text_index} out of range '
                                   f'(layer has {len(shapes)} shape(s))'}
            shape = shapes[text_index]

            if not _is_text_shape(shape):
                return {'success': False,
                        'message': f'shape at index {text_index} is not a '
                                   f'text shape'}

            try:
                svg_current = shape.toSvg()
            except Exception:
                svg_current = ''

            current = _parse_text_svg(svg_current)
            old_transform = None
            if hasattr(shape, 'transformation'):
                try:
                    old_transform = shape.transformation()
                except Exception:
                    old_transform = None

            def merged(key, default):
                if key in patch:
                    return patch[key]
                return current.get(key, default)

            text        = merged('text', '')
            font_family = merged('font_family', 'sans-serif')
            font_size   = merged('font_size', 12)
            color       = merged('color', '#000000')
            x           = merged('x', 0)
            y           = merged('y', 0)
            alignment   = merged('alignment', 'left')

            inline_size = current.get('inline_size', None)
            if 'inline_size' in patch:
                try:
                    inline_size = float(patch['inline_size'])
                except (TypeError, ValueError):
                    inline_size = None
            if inline_size is not None:
                try:
                    inline_size = float(inline_size)
                except (TypeError, ValueError):
                    inline_size = None
            is_wrapped = inline_size is not None and inline_size > 0

            if isinstance(color, str) and not color.startswith('#'):
                color = '#' + color

            text_align = ''
            if alignment == 'center':
                text_align = ' text-anchor="middle" dominant-baseline="middle"'
            elif alignment == 'left':
                text_align = ' text-anchor="start" dominant-baseline="middle"'
            elif alignment == 'right':
                text_align = ' text-anchor="end" dominant-baseline="middle"'

            if 'rotation_deg' in patch:
                rotation_deg = float(patch['rotation_deg'])
            elif old_transform is not None:
                rotation_deg = math.degrees(
                    math.atan2(old_transform.m12(), old_transform.m11())
                )
            else:
                rotation_deg = 0

            W = doc.width()
            H = doc.height()

            shape.remove()

            if is_wrapped:
                style = (
                    f'inline-size: {inline_size};'
                    f'font-size: {font_size};'
                    f'white-space: pre-wrap;'
                )
                svg = (
                    f'<svg width="{W}" height="{H}" '
                    f'xmlns="http://www.w3.org/2000/svg">'
                    f'<text font-family="{font_family}" fill="{color}" '
                    f'style="{style}"{text_align}>{text}</text>'
                    f'</svg>'
                )
            else:
                svg = (
                    f'<svg width="{W}" height="{H}" '
                    f'xmlns="http://www.w3.org/2000/svg">'
                    f'<text font-family="{font_family}" '
                    f'font-size="{font_size}" fill="{color}" '
                    f'x="{x}" y="{y}"{text_align}>{text}</text>'
                    f'</svg>'
                )
            layer.addShapesFromSvg(svg)

            marker = f'>{text}<'
            new_shape = None
            for s in layer.shapes():
                try:
                    if marker in s.toSvg():
                        new_shape = s
                        break
                except Exception:
                    continue
            if new_shape is None:
                all_shapes = list(layer.shapes())
                if all_shapes:
                    new_shape = all_shapes[-1]

            if new_shape is not None:
                if is_wrapped:
                    # Preserve the old transformation if the shape
                    # already had one; otherwise fall back to
                    # rotate-then-translate to put the anchor at
                    # (x, y).
                    if old_transform is not None:
                        _apply_transform(new_shape, old_transform)
                    else:
                        t = QTransform()
                        if rotation_deg:
                            t.rotate(rotation_deg)
                        t.translate(x, y)
                        _apply_transform(new_shape, t)
                elif rotation_deg:
                    t = QTransform()
                    t.translate(x, y)
                    t.rotate(rotation_deg)
                    t.translate(-x, -y)
                    _apply_transform(new_shape, t)

            refresh(doc)

            return {
                'success': True,
                'message': f'Patched "{layer_name}" text {text_index}',
                'data': {
                    'layer_name': layer_name,
                    'text_index': text_index,
                    'applied': sorted(patch.keys()),
                    'mode': 'inline-wrapped' if is_wrapped else 'pre-positioned',
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # move_text_handle
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Move one inline-size handle of an inline-wrapped text '
                  'shape to a target position, holding either the other '
                  'handle or the wrapping area\'s centre fixed.',
        args={
            '--layer_name': {'type': 'str', 'required': True,
                             'help': 'Name of the vector layer'},
            '--text_index': {'type': 'int', 'default': 0,
                             'help': 'Index of the target shape within the '
                                     'layer\'s shapes() list.'},
            '--side': {'type': 'str', 'required': True,
                       'choices': ['left', 'right'],
                       'help': 'Which handle to move.'},
            '--target_x': {'type': 'float', 'required': True,
                           'help': 'Target x position in the layer\'s '
                                   'coordinate system.'},
            '--target_y': {'type': 'float', 'required': True,
                           'help': 'Target y position in the layer\'s '
                                   'coordinate system.'},
            '--pivot': {'type': 'str', 'default': 'other',
                        'choices': ['other', 'center'],
                        'help': 'What stays fixed during the move: the '
                                'other handle, or the wrapping area\'s '
                                'centre.'},
        }
    )
    def move_text_handle(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            text_index = int(params.get('text_index', 0))
            side = params.get('side', '')
            pivot = params.get('pivot', 'other')
            try:
                target_x = float(params.get('target_x', 0))
                target_y = float(params.get('target_y', 0))
            except (TypeError, ValueError):
                return {'success': False,
                        'message': 'target_x and target_y must be numbers'}

            if side not in ('left', 'right'):
                return {'success': False,
                        'message': f'side must be "left" or "right", got "{side}"'}
            if pivot not in ('other', 'center'):
                return {'success': False,
                        'message': f'pivot must be "other" or "center", got "{pivot}"'}

            layer = doc.nodeByName(layer_name)
            if not layer:
                return {'success': False,
                        'message': f'Layer "{layer_name}" not found'}
            if layer.type() != 'vectorlayer':
                return {'success': False,
                        'message': f'Layer "{layer_name}" is not a vector layer'}

            try:
                shapes = list(layer.shapes())
            except Exception:
                shapes = []
            if text_index < 0 or text_index >= len(shapes):
                return {'success': False,
                        'message': f'text_index {text_index} out of range '
                                   f'(layer has {len(shapes)} shape(s))'}
            old_shape = shapes[text_index]

            I_old = _get_inline_size(old_shape)
            if I_old is None:
                return {'success': False,
                        'message': 'target shape is not inline-wrapped '
                                   '(no inline-size in its style)'}

            mode = _get_anchor_mode(old_shape)
            T = _get_transformation(old_shape)
            alpha = _ALPHA[mode]
            beta  = _BETA[mode]
            gamma = _GAMMA[mode]

            d2 = _linear_sq(T)
            if d2 < 1e-15:
                return {'success': False,
                        'message': 'shape has a degenerate transformation'}

            if pivot == 'other':
                if side == 'right':
                    pin_coeff = alpha
                    pin_local = alpha * I_old
                else:
                    pin_coeff = beta
                    pin_local = beta * I_old
            else:
                pin_coeff = gamma
                pin_local = gamma * I_old

            pin_doc = T.map(QPointF(pin_local, 0.0))
            target_coeff = beta if side == 'right' else alpha

            k = target_coeff - pin_coeff
            if abs(k) < 1e-12:
                return {'success': False,
                        'message': 'pivot would coincide with the moving '
                                   'handle'}

            dx = target_x - pin_doc.x()
            dy = target_y - pin_doc.y()
            new_I = (dx * T.m11() + dy * T.m12()) / (k * d2)

            if new_I <= 0:
                return {'success': False,
                        'message': 'target would place the handles on the '
                                   'wrong side of the pivot'}

            new_m31 = pin_doc.x() - pin_coeff * new_I * T.m11()
            new_m32 = pin_doc.y() - pin_coeff * new_I * T.m12()
            T_new = _make_transform_like(T, new_m31, new_m32)

            new_shape = _rebuild_inline_shape(layer, old_shape, new_I, T_new)
            refresh(doc)

            return {
                'success': True,
                'message': f'Moved {side} handle of "{layer_name}" '
                           f'text {text_index}',
                'data': {
                    'layer_name': layer_name,
                    'text_index': text_index,
                    'side': side,
                    'pivot': pivot,
                    'inline_size_before': I_old,
                    'inline_size_after': new_I,
                    'anchor_before': (T.m31(), T.m32()),
                    'anchor_after': (new_m31, new_m32),
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # set_text_inline_size
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Set the inline-size of an inline-wrapped text shape.  '
                  'The shape\'s position and transformation are unchanged; '
                  'both handles move according to the anchor mode.',
        args={
            '--layer_name': {'type': 'str', 'required': True,
                             'help': 'Name of the vector layer'},
            '--text_index': {'type': 'int', 'default': 0,
                             'help': 'Index of the target shape within the '
                                     'layer\'s shapes() list.'},
            '--inline_size': {'type': 'float', 'required': True,
                              'help': 'New inline-size (must be positive).'},
        }
    )
    def set_text_inline_size(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            text_index = int(params.get('text_index', 0))
            try:
                new_I = float(params.get('inline_size', 0))
            except (TypeError, ValueError):
                return {'success': False,
                        'message': 'inline_size must be a number'}

            if new_I <= 0:
                return {'success': False,
                        'message': 'inline_size must be positive'}

            layer = doc.nodeByName(layer_name)
            if not layer:
                return {'success': False,
                        'message': f'Layer "{layer_name}" not found'}
            if layer.type() != 'vectorlayer':
                return {'success': False,
                        'message': f'Layer "{layer_name}" is not a vector layer'}

            try:
                shapes = list(layer.shapes())
            except Exception:
                shapes = []
            if text_index < 0 or text_index >= len(shapes):
                return {'success': False,
                        'message': f'text_index {text_index} out of range '
                                   f'(layer has {len(shapes)} shape(s))'}
            old_shape = shapes[text_index]

            I_old = _get_inline_size(old_shape)
            if I_old is None:
                return {'success': False,
                        'message': 'target shape is not inline-wrapped'}

            T = _get_transformation(old_shape)

            new_shape = _rebuild_inline_shape(layer, old_shape, new_I, T)
            refresh(doc)

            return {
                'success': True,
                'message': f'Set inline-size on "{layer_name}" text '
                           f'{text_index}: {I_old} -> {new_I}',
                'data': {
                    'layer_name': layer_name,
                    'text_index': text_index,
                    'inline_size_before': I_old,
                    'inline_size_after': new_I,
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    # dump_all_text_shapes
    # ------------------------------------------------------------------

    @command(
        category='layer',
        help_text='Return every text shape across every open document, '
                  'with full metadata and per-word bounding boxes '
                  'measured from the layer\'s own rendered pixels.',
        args={}
    )
    def dump_all_text_shapes(self, params):
        try:
            app = Krita.instance()

            records = []
            for doc in app.documents():
                doc_path = doc.fileName() or ''
                if not doc_path:
                    doc_path = ("untitled://" + doc.name()
                                + "#" + str(id(doc)))
                doc_name = doc.name()
                doc_w = doc.width()
                doc_h = doc.height()

                for layer in _collect_vector_layers(doc):
                    try:
                        shapes = list(layer.shapes())
                    except Exception:
                        continue

                    for i, shape in enumerate(shapes):
                        if not _is_text_shape(shape):
                            continue
                        try:
                            svg = shape.toSvg()
                        except Exception:
                            continue

                        rec = _parse_text_svg(svg)
                        text = rec.get('text', '')

                        t = None
                        if hasattr(shape, 'transformation'):
                            try:
                                t = shape.transformation()
                            except Exception:
                                t = None
                        if t is None:
                            t = QTransform()

                        # Inline-wrapped text needs the line-then-word
                        # clustering; pre-positioned text is unaffected
                        # and keeps the original single-line clustering.
                        inline_size = rec.get('inline_size')
                        if inline_size is not None:
                            word_boxes = _words_from_pixels_wrapped(
                                layer, t, text)
                        else:
                            word_boxes = _words_from_pixels(
                                layer, t, text)

                        if not word_boxes:
                            word_boxes = _fallback_word_box(shape, t, text)
                        shape_bounds = _bounds_from_words(word_boxes)

                        ax = rec.get('x', 0)
                        ay = rec.get('y', 0)
                        p = t.map(QPointF(ax, ay))

                        rotation_deg = math.degrees(
                            math.atan2(t.m12(), t.m11()))

                        rec['document_path']   = doc_path
                        rec['document_name']   = doc_name
                        rec['document_width']  = doc_w
                        rec['document_height'] = doc_h
                        rec['layer_name']      = layer.name()
                        rec['text_index']      = i

                        rec['x']            = p.x()
                        rec['y']            = p.y()
                        rec['rotation_deg'] = rotation_deg
                        rec['shape_bounds'] = shape_bounds
                        rec['word_boxes']   = word_boxes

                        records.append(rec)

            return {
                'success': True,
                'message': f'{len(records)} text shape(s) across '
                           f'{len(app.documents())} document(s)',
                'data': {'shapes': records},
            }
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False, 'message': str(e)}
