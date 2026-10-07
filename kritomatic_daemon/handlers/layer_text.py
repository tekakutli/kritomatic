import json
import re
from krita import Krita
from PyQt5.QtGui import QTransform
from ..decorators import command
from ..utils.refresh import refresh


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
        return {'success': False, 'message': f'Unknown text command: {cmd_type}'}

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
            '--rotation': {'type': 'float', 'default': 0, 'help': 'Rotation in degrees around (x, y); positive is clockwise'}
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
            elif alignment == "right":
                text_align = ' text-anchor="end"'

            if not color.startswith('#'):
                color = '#' + color

            # The text is always added WITHOUT rotation in the SVG,
            # so Krita produces a plain TextShape that the text editor
            # can open.  Rotation is applied afterwards via
            # setTransformation, then a layout refresh is forced.
            #
            # Why not put the rotation in the SVG:
            #   - `transform=` on <text>: silently dropped by Krita's
            #     parser, text ends up un-rotated.
            #   - `<g transform=...><text/></g>`: rotation preserved,
            #     bounds correct — but Krita wraps the result in a
            #     GroupShape, and the TextShape inside it is not
            #     directly editable from the canvas.
            #   - `setTransformation` alone: rotation stored, but the
            #     shape's cached bounds don't account for the rotated
            #     extents, so the glyphs get clipped.  Editing the
            #     text in the vector editor fixes it because that
            #     triggers a relayout; we call the same underlying
            #     update here.
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

            if rotation and shapes_after:
                # The newly added shape is the one whose text content
                # matches.  Identify by SVG content rather than by
                # object identity: Krita's SIP bindings create a
                # fresh Python wrapper every time shapes() is called,
                # so id-based matching across two calls silently
                # picks the oldest shape.
                marker = f'>{text}<'
                new_shape = None
                for s in shapes_after:
                    try:
                        if marker in s.toSvg():
                            new_shape = s
                            break
                    except Exception:
                        continue
                if new_shape is None:
                    new_shape = shapes_after[-1]

                if hasattr(new_shape, 'setTransformation'):
                    t = QTransform()
                    t.translate(x, y)
                    t.rotate(rotation)
                    t.translate(-x, -y)
                    new_shape.setTransformation(t)

                    # Force a layout / bounds recompute.  Try each of
                    # the plausible method names in turn; different
                    # Krita builds expose different sets of them, and
                    # one of these is what the text editor's delete
                    # keypress ends up triggering under the hood.
                    for method_name, args in (
                        ('update', ()),
                        ('updateAbsoluteGeometry', ()),
                        ('setShapeChanged', (True,)),
                    ):
                        if hasattr(new_shape, method_name):
                            try:
                                getattr(new_shape, method_name)(*args)
                                break
                            except Exception:
                                continue

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

            import re
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
