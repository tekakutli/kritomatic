import json
import math
import re
from krita import Krita
from PyQt5.QtGui import QTransform
from ..decorators import command
from ..utils.refresh import refresh


# ==========================================================================
# SVG parsing helpers
# ==========================================================================

_TEXT_BODY_RE = re.compile(r'<text[^>]*>([^<]*)</text>')
_ATTR_RE = re.compile(r'\b([a-zA-Z-]+)="([^"]*)"')
_STYLE_RE = re.compile(r'style="([^"]*)"')
_STYLE_PROP_RE = re.compile(r'([a-zA-Z-]+)\s*:\s*([^;]+)')


def _parse_text_svg(svg):
    out = {}

    m = _TEXT_BODY_RE.search(svg)
    if m:
        out['text'] = m.group(1)

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


class LayerTextHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'add_vector_text':
            return self.add_vector_text(params)
        elif cmd_type == 'add_vector_polygon':
            return self.add_vector_polygon(params)
        elif cmd_type == 'update_vector_text':
            return self.update_vector_text(params)
        elif cmd_type == 'list_shapes':
            return self.list_shapes(params)
        elif cmd_type == 'replace_all_text':
            return self.replace_all_text(params)
        elif cmd_type == 'extract_all_text':
            return self.extract_all_text(params)
        elif cmd_type == 'list_text_layers':
            return self.list_text_layers(params)
        elif cmd_type == 'get_layer_text_metadata':
            return self.get_layer_text_metadata(params)
        elif cmd_type == 'patch_layer_text':
            return self.patch_layer_text(params)
        return {'success': False, 'message': f'Unknown text command: {cmd_type}'}

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
                                    'Replaces --rotation when given.  Accepts either a '
                                    'JSON string or a native list (batch form).'},
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
                    print(f"  ✓ Updated {replacements_in_layer} text(s) in layer '{layer.name()}'")

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

            if new_shape is not None and rotation_deg:
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
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
