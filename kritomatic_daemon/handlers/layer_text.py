import re
from krita import Krita

class LayerTextHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'add_vector_text':
            return self.add_vector_text(params)
        elif cmd_type == 'update_vector_text':
            return self.update_vector_text(params)
        elif cmd_type == 'list_shapes':
            return self.list_shapes(params)
        elif cmd_type == 'replace_all_text':
            return self.replace_all_text(params)
        return {'success': False, 'message': f'Unknown text command: {cmd_type}'}

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

            svg = f'''<svg width="{canvas_width}" height="{canvas_height}" xmlns="http://www.w3.org/2000/svg">
      <text font-family="{font_family}" font-size="{font_size}" fill="{color}" x="{x}" y="{y}"{text_align}>{text}</text>
    </svg>'''

            target_layer.addShapesFromSvg(svg)
            doc.refreshProjection()

            return {'success': True, 'message': f'Added text to "{layer_name}"', 'data': {'text': text, 'font': font_family, 'size': font_size, 'position': (x, y), 'canvas': (canvas_width, canvas_height)}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

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
                    # Capture transform
                    if hasattr(shape, 'transformation'):
                        transform = shape.transformation()
                    break

            if shape_to_remove is None:
                return {'success': False, 'message': f'Text "{old_text}" not found on layer "{layer_name}"'}

            # Replace text content
            new_svg = original_svg.replace(old_text, new_text)
            complete_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {new_svg}
    </svg>'''

            shape_to_remove.remove()
            target_layer.addShapesFromSvg(complete_svg)

            # Apply transform to the newly added shape
            if transform:
                all_shapes = list(target_layer.shapes())
                if all_shapes:
                    new_shape = all_shapes[-1]
                    if hasattr(new_shape, 'setTransformation'):
                        new_shape.setTransformation(transform)

            doc.refreshProjection()

            return {'success': True, 'message': f'Updated text from "{old_text}" to "{new_text}" on "{layer_name}"'}

        except Exception as e:
            return {'success': False, 'message': str(e)}

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
                shapes_data = []  # Store (svg, transform) pairs

                for shape in layer.shapes():
                    svg = shape.toSvg()
                    if old_text in svg:
                        # Get the transform if it exists
                        transform = None
                        if hasattr(shape, 'transformation'):
                            transform = shape.transformation()

                        new_svg = svg.replace(old_text, new_text)
                        shapes_data.append((new_svg, transform))
                        replacements_in_layer += 1

                if replacements_in_layer > 0:
                    # Remove all shapes from this layer
                    for shape in list(layer.shapes()):
                        shape.remove()

                    # Add new shapes with preserved transforms
                    for svg, transform in shapes_data:
                        complete_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {svg}
    </svg>'''
                        layer.addShapesFromSvg(complete_svg)

                        # Apply transform to the newly added shape
                        if transform:
                            # Get the most recently added shape
                            all_shapes = list(layer.shapes())
                            if all_shapes:
                                new_shape = all_shapes[-1]
                                if hasattr(new_shape, 'setTransformation'):
                                    new_shape.setTransformation(transform)

                    total_replacements += replacements_in_layer
                    layers_modified += 1
                    print(f"  ✓ Updated {replacements_in_layer} text(s) in layer '{layer.name()}'")

            doc.refreshProjection()

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
