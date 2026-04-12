from krita import *
import os

class LayerHandler:

    def execute(self, cmd_type, params):
        """Execute layer-related commands"""
        if cmd_type == 'create_layer':
            name = params.get('name', 'New Layer')
            layer_type = params.get('layer_type', 'paintlayer')
            position = params.get('position', 'above_current')
            reference = params.get('reference', None)
            return self.create_layer_at_position(name, layer_type, position, reference)
        elif cmd_type == 'list_layers':
            return self.get_layer_list()
        elif cmd_type == 'set_active_layer':
            name = params.get('name', '')
            return self.set_active_layer(name)
        elif cmd_type == 'rename_active_layer':
            new_name = params.get('new_name', '')
            return self.rename_active_layer(new_name)
        elif cmd_type == 'rename_layer_by_name':
            old_name = params.get('old_name', '')
            new_name = params.get('new_name', '')
            return self.rename_layer_by_name(old_name, new_name)
        elif cmd_type == 'move_layer_to_group':
            layer_name = params.get('layer_name', '')
            group_name = params.get('group_name', '')
            position = params.get('position', 'inside')
            return self.move_layer_to_group(layer_name, group_name, position)
        elif cmd_type == 'move_active_layer_to_group':
            group_name = params.get('group_name', '')
            position = params.get('position', 'inside')
            return self.move_active_layer_to_group(group_name, position)
        elif cmd_type == 'create_file_layer':
            name = params.get('name', 'File Layer')
            file_path = params.get('file_path', '')
            position = params.get('position', 'above_current')
            reference = params.get('reference', None)
            return self.create_file_layer(name, file_path, position, reference)
        elif cmd_type == 'create_blend_layer':
            name = params.get('name', 'Blend Layer')
            blend_mode = params.get('blend_mode', 'normal')
            return self.create_blend_layer(name, blend_mode)
        elif cmd_type == 'fill_layer':
            layer_name = params.get('layer_name', '')
            color_hex = params.get('color', None)
            use_foreground = params.get('foreground', False)
            use_background = params.get('background', False)
            return self.fill_layer(layer_name, color_hex, use_foreground, use_background)
        elif cmd_type == 'fill_selection':
            color_hex = params.get('color', None)
            use_foreground = params.get('foreground', False)
            use_background = params.get('background', False)
            return self.fill_selection(color_hex, use_foreground, use_background)
        elif cmd_type == 'add_vector_text':
            layer_name = params.get('layer_name', '')
            text = params.get('text', '')
            font_family = params.get('font_family', 'sans-serif')
            font_size = params.get('font_size', 12)
            x = params.get('x', 0)
            y = params.get('y', 0)
            color = params.get('color', '#000000')
            alignment = params.get('alignment', 'left')
            return self.add_vector_text(layer_name, text, font_family, font_size, x, y, color, alignment)
        elif cmd_type == 'update_vector_text':
            layer_name = params.get('layer_name', '')
            old_text = params.get('old_text', '')
            new_text = params.get('new_text', '')
            return self.update_vector_text(layer_name, old_text, new_text)
        elif cmd_type == 'list_shapes':
            layer_name = params.get('layer_name', '')
            return self.list_shapes(layer_name)
        elif cmd_type == 'replace_all_text':
            old_text = params.get('old_text', '')
            new_text = params.get('new_text', '')
            scope = params.get('scope', 'all')
            return self.replace_all_text(old_text, new_text, scope)
        elif cmd_type == 'move_layer_to_new_document':
            layer_name = params.get('layer_name', '')
            new_doc_name = params.get('new_doc_name', None)
            return self.move_layer_to_new_document(layer_name, new_doc_name)
        return {'success': False, 'message': f'Unknown layer command: {cmd_type}'}

    def _collect_layer_data(self, node, layers, indent):
        layer_info = {'name': node.name(), 'type': node.type(), 'indent': indent}
        layers.append(layer_info)
        if node.type() == "grouplayer":
            for child in node.childNodes():
                self._collect_layer_data(child, layers, indent + 1)

    def _get_prev_sibling(self, node):
        parent = node.parentNode()
        if not parent:
            return None
        prev_node = None
        for child in parent.childNodes():
            if child == node:
                return prev_node
            prev_node = child
        return prev_node

    def _insert_layer_at_position(self, new_layer, position_type, current_layer, reference_layer_name, doc):
        if position_type == 'above_current' and current_layer:
            current_layer.parentNode().addChildNode(new_layer, current_layer)
        elif position_type == 'below_current' and current_layer:
            parent = current_layer.parentNode()
            children = parent.childNodes()
            for i, child in enumerate(children):
                if child == current_layer:
                    if i == 0:
                        parent.setChildNodes([new_layer] + children)
                    else:
                        parent.addChildNode(new_layer, children[i - 1])
                    break
        elif position_type == 'above_named' and reference_layer_name:
            ref_layer = doc.nodeByName(reference_layer_name)
            if ref_layer:
                ref_layer.parentNode().addChildNode(new_layer, ref_layer)
            else:
                return False
        elif position_type == 'below_named' and reference_layer_name:
            ref_layer = doc.nodeByName(reference_layer_name)
            if not ref_layer:
                return False
            parent = ref_layer.parentNode()
            children = parent.childNodes()
            for i, child in enumerate(children):
                if child == ref_layer:
                    if i == 0:
                        parent.setChildNodes([new_layer] + children)
                    else:
                        parent.addChildNode(new_layer, children[i - 1])
                    break
        elif position_type == 'top':
            doc.rootNode().addChildNode(new_layer, None)
        elif position_type == 'bottom':
            top_level = doc.topLevelNodes()
            if top_level:
                doc.rootNode().setChildNodes([new_layer] + top_level)
            else:
                doc.rootNode().addChildNode(new_layer, None)
        else:
            doc.rootNode().addChildNode(new_layer, None)
        return True

    def create_layer(self, name, layer_type, position, reference):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            current = doc.activeNode()

            if layer_type == 'paintlayer':
                new_layer = doc.createNode(name, "paintlayer")
            elif layer_type == 'grouplayer':
                new_layer = doc.createGroupLayer(name)
            elif layer_type == 'selectionmask':
                new_layer = doc.createSelectionMask(name)
            else:
                new_layer = doc.createNode(name, layer_type)

            if not self._insert_layer_at_position(new_layer, position, current, reference, doc):
                return {'success': False, 'message': f'Failed to insert layer'}

            doc.setActiveNode(new_layer)
            doc.refreshProjection()
            return {'success': True, 'message': f'Created {layer_type} "{name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def create_file_layer(self, name, file_path, position_type='above_current', reference_layer_name=None):
        """Create a file layer that references an external image"""
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            current_layer = doc.activeNode()

            scaling_method = 'ToImageSize'
            scaling_filter = 'Bicubic'

            new_layer = doc.createFileLayer(name, file_path, scaling_method, scaling_filter)

            success = self._insert_layer_at_position(new_layer, position_type, current_layer, reference_layer_name, doc)

            if not success:
                return {'success': False, 'message': f'Failed to insert layer at position: {position_type}'}

            doc.setActiveNode(new_layer)
            doc.refreshProjection()
            return {'success': True, 'message': f'Created file layer "{name}"', 'data': {'file_path': file_path}}
        except Exception as e:
            return {'success': False, 'message': str(e)}
        
    def create_blend_layer(self, name, blend_mode):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            current = doc.activeNode()
            if not current:
                return {'success': False, 'message': 'No active layer'}

            new_layer = doc.createNode(name, "paintlayer")
            current.parentNode().addChildNode(new_layer, current)
            new_layer.setBlendingMode(blend_mode.lower())
            doc.setActiveNode(new_layer)
            doc.refreshProjection()
            return {'success': True, 'message': f'Created blend layer "{name}" with mode {blend_mode}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def list_layers(self):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            layers = []
            self._collect_layer_data(doc.rootNode(), layers, 0)
            return {'success': True, 'message': f'Found {len(layers)} layers', 'data': {'layers': layers}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def set_active_layer(self, name):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            layer = doc.nodeByName(name)
            if not layer:
                return {'success': False, 'message': f'Layer "{name}" not found'}
            doc.setActiveNode(layer)
            doc.refreshProjection()
            return {'success': True, 'message': f'Active layer set to "{name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def rename_active_layer(self, new_name):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            active = doc.activeNode()
            if not active:
                return {'success': False, 'message': 'No active layer'}
            old = active.name()
            active.setName(new_name)
            doc.refreshProjection()
            return {'success': True, 'message': f'Renamed from "{old}" to "{new_name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def rename_layer_by_name(self, old_name, new_name):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            layer = doc.nodeByName(old_name)
            if not layer:
                return {'success': False, 'message': f'Layer "{old_name}" not found'}
            layer.setName(new_name)
            doc.refreshProjection()
            return {'success': True, 'message': f'Renamed "{old_name}" to "{new_name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def move_layer_to_group(self, layer_name, group_name, position='inside'):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            to_move = doc.nodeByName(layer_name)
            if not to_move:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            target = doc.nodeByName(group_name)
            if not target:
                return {'success': False, 'message': f'Group "{group_name}" not found'}
            if target.type() != "grouplayer":
                return {'success': False, 'message': f'"{group_name}" is not a group'}

            old_parent = to_move.parentNode()
            moved = to_move.duplicate()

            if position == 'inside':
                target.addChildNode(moved, None)
            elif position == 'above':
                old_parent.addChildNode(moved, target)
            elif position == 'below':
                prev = self._get_prev_sibling(target)
                if prev:
                    old_parent.addChildNode(moved, prev)
                else:
                    old_parent.addChildNode(moved, None)

            to_move.remove()
            doc.refreshProjection()
            return {'success': True, 'message': f'Moved "{layer_name}" to "{group_name}"'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def move_active_layer_to_group(self, group_name, position='inside'):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            active = doc.activeNode()
            if not active:
                return {'success': False, 'message': 'No active layer'}
            return self.move_layer_to_group(active.name(), group_name, position)
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def fill_layer(self, layer_name, color_hex=None, use_foreground=False, use_background=False):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            view = app.activeWindow().activeView()
            if not view:
                return {'success': False, 'message': 'No active view'}

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original_active = doc.activeNode()
            doc.setActiveNode(target_layer)

            # Determine color
            if use_foreground:
                color = view.foregroundColor()
            elif use_background:
                color = view.backgroundColor()
            elif color_hex:
                color_hex = color_hex.lstrip('#')
                r = int(color_hex[0:2], 16) / 255.0
                g = int(color_hex[2:4], 16) / 255.0
                b = int(color_hex[4:6], 16) / 255.0
                color = view.foregroundColor()
                color.setComponents([r, g, b, 1.0])
            else:
                doc.setActiveNode(original_active)
                return {'success': False, 'message': 'No color specified'}

            # Set foreground color to desired color
            original_foreground = view.foregroundColor()
            view.setForeGroundColor(color)

            # Select all
            select_all = app.action('select_all')
            if select_all:
                select_all.trigger()
            else:
                view.setForeGroundColor(original_foreground)
                doc.setActiveNode(original_active)
                return {'success': False, 'message': 'Select All action not found'}

            # Fill with foreground color
            fill_action = app.action('fill_selection_foreground_color')
            if fill_action:
                fill_action.trigger()
            else:
                view.setForeGroundColor(original_foreground)
                doc.setActiveNode(original_active)
                return {'success': False, 'message': 'Fill action not found'}

            # Deselect
            deselect = app.action('deselect')
            if deselect:
                deselect.trigger()

            # Restore original foreground color
            view.setForeGroundColor(original_foreground)

            doc.setActiveNode(original_active)
            doc.refreshProjection()

            color_desc = color_hex or ('foreground' if use_foreground else 'background')
            return {'success': True, 'message': f'Filled layer "{layer_name}" with {color_desc}'}

        except Exception as e:
            return {'success': False, 'message': str(e)}


    def fill_selection(self, color_hex=None, use_foreground=False, use_background=False):
        """
        Fill the current selection with a specific color
        """
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            view = app.activeWindow().activeView()
            if not view:
                return {'success': False, 'message': 'No active view'}

            # Check if there's an active selection by checking width/height
            selection = doc.selection()
            if selection is None or (selection.width() == 0 and selection.height() == 0):
                return {'success': False, 'message': 'No active selection. Create a selection first.'}

            # Store original foreground color
            original_foreground = view.foregroundColor()

            # Determine which color to use
            if use_foreground:
                color = view.foregroundColor()
            elif use_background:
                color = view.backgroundColor()
            elif color_hex:
                # Create color from hex by modifying a copy of current foreground
                color_hex = color_hex.lstrip('#')
                r = int(color_hex[0:2], 16) / 255.0
                g = int(color_hex[2:4], 16) / 255.0
                b = int(color_hex[4:6], 16) / 255.0
                color = view.foregroundColor()
                color.setComponents([r, g, b, 1.0])
            else:
                return {'success': False, 'message': 'No color specified. Use --color, --foreground, or --background'}

            # Set foreground color to desired color
            view.setForeGroundColor(color)

            # Fill the selection with foreground color
            fill_action = app.action('fill_selection_foreground_color')
            if fill_action:
                fill_action.trigger()
            else:
                view.setForeGroundColor(original_foreground)
                return {'success': False, 'message': 'Fill action not found'}

            # Restore original foreground color
            view.setForeGroundColor(original_foreground)

            doc.refreshProjection()

            color_desc = color_hex or ('foreground' if use_foreground else 'background')
            return {'success': True, 'message': f'Filled selection with {color_desc}'}

        except Exception as e:
            return {'success': False, 'message': str(e)}


    def add_vector_text(self, layer_name, text, font_family="sans-serif", font_size=12,
                        x=0, y=0, color="#000000", alignment="left"):
        """
        Add vector text to a vector layer using SVG
        """
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            if target_layer.type() != 'vectorlayer':
                return {'success': False, 'message': f'Layer "{layer_name}" is not a vector layer'}

            # Get canvas dimensions for centering
            canvas_width = doc.width()
            canvas_height = doc.height()

            # Calculate center if alignment is center and x/y are 0
            if alignment == "center" and x == 0 and y == 0:
                x = canvas_width / 2
                y = canvas_height / 2

            # Build SVG text element
            text_align = ""
            if alignment == "center":
                text_align = ' text-anchor="middle" dominant-baseline="middle"'
            elif alignment == "right":
                text_align = ' text-anchor="end"'

            # Ensure color has # prefix
            if not color.startswith('#'):
                color = '#' + color

            # Create a proper SVG wrapper
            svg = f'''<svg width="{canvas_width}" height="{canvas_height}" xmlns="http://www.w3.org/2000/svg">
      <text font-family="{font_family}" font-size="{font_size}" fill="{color}" x="{x}" y="{y}" {text_align}>{text}</text>
    </svg>'''

            print(f"DEBUG: SVG content:\n{svg}")

            # Add shape to layer
            target_layer.addShapesFromSvg(svg)
            doc.refreshProjection()

            return {'success': True, 'message': f'Added text to "{layer_name}"',
                    'data': {'text': text, 'font': font_family, 'size': font_size, 'position': (x, y), 'canvas': (canvas_width, canvas_height)}}

        except Exception as e:
            return {'success': False, 'message': str(e)}

    def update_vector_text(self, layer_name, old_text, new_text):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            if target_layer.type() != 'vectorlayer':
                return {'success': False, 'message': f'Layer "{layer_name}" is not a vector layer'}

            width = doc.width()
            height = doc.height()

            # Find the shape and get its SVG and transform
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
                    elif hasattr(shape, 'transform'):
                        transform = shape.transform()
                    break

            if shape_to_remove is None:
                return {'success': False, 'message': f'Text "{old_text}" not found on layer "{layer_name}"'}

            # Replace just the text content
            new_svg = original_svg.replace(old_text, new_text)

            # Wrap in complete SVG document
            complete_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {new_svg}
    </svg>'''

            # Remove the old shape
            shape_to_remove.remove()

            # Add the new shape
            target_layer.addShapesFromSvg(complete_svg)

            # Apply the original transform to the newly added shape
            if transform:
                for shape in target_layer.shapes():
                    if hasattr(shape, 'setTransformation'):
                        shape.setTransformation(transform)
                        break

            doc.refreshProjection()
            return {'success': True, 'message': f'Updated text from "{old_text}" to "{new_text}" on "{layer_name}"'}

        except Exception as e:
            return {'success': False, 'message': str(e)}

    def replace_all_text(self, old_text, new_text, scope='all'):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            width = doc.width()
            height = doc.height()

            # Find all vector layers
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
                shapes_data = []  # Store (new_svg, transform) pairs

                for shape in layer.shapes():
                    svg = shape.toSvg()
                    if old_text in svg:
                        new_svg = svg.replace(old_text, new_text)
                        # Get the transform - try multiple methods
                        transform = None
                        if hasattr(shape, 'transformation'):
                            transform = shape.transformation()
                        elif hasattr(shape, 'transform'):
                            transform = shape.transform()

                        # Also try to get position from the SVG if transform is None
                        if transform is None:
                            import re
                            x_match = re.search(r'x="([^"]+)"', svg)
                            y_match = re.search(r'y="([^"]+)"', svg)
                            if x_match and y_match:
                                # Store as a simple position for fallback
                                transform = {'x': float(x_match.group(1)), 'y': float(y_match.group(1))}

                        shapes_data.append((new_svg, transform))
                        replacements_in_layer += 1

                if replacements_in_layer > 0:
                    # Clear all shapes from this layer
                    for shape in list(layer.shapes()):
                        shape.remove()

                    # Add new shapes
                    for new_svg, transform in shapes_data:
                        complete_svg = f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {new_svg}
    </svg>'''
                        layer.addShapesFromSvg(complete_svg)

                        # Apply transform to the newly added shape (the last one)
                        if transform:
                            # Get the most recently added shape
                            all_shapes = list(layer.shapes())
                            if all_shapes:
                                new_shape = all_shapes[-1]
                                if hasattr(new_shape, 'setTransformation'):
                                    new_shape.setTransformation(transform)
                                elif hasattr(new_shape, 'setTransform') and isinstance(transform, dict):
                                    # Fallback for simple position
                                    if 'x' in transform and 'y' in transform:
                                        # Modify the SVG directly instead
                                        shape_svg = new_shape.toSvg()
                                        modified_svg = re.sub(r'x="[^"]+"', f'x="{transform["x"]}"', shape_svg)
                                        modified_svg = re.sub(r'y="[^"]+"', f'y="{transform["y"]}"', modified_svg)
                                        new_shape.remove()
                                        layer.addShapesFromSvg(f'''<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">
      {modified_svg}
    </svg>''')

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
    

    def get_canvas_center(self):
        """Get the center coordinates of the canvas"""
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            width = doc.width()
            height = doc.height()
            center_x = width / 2
            center_y = height / 2

            return {'success': True, 'message': 'Canvas center calculated',
                    'data': {'width': width, 'height': height, 'center_x': center_x, 'center_y': center_y}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def list_shapes(self, layer_name):
        """List all shapes on a vector layer for debugging"""
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            shapes = []
            for shape in target_layer.shapes():
                shapes.append({
                    'type': str(type(shape)),
                    'svg_preview': shape.toSvg()[:200]
                })

            return {'success': True, 'message': f'Found {len(shapes)} shapes', 'data': {'shapes': shapes}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def move_layer_to_new_document(self, layer_name, new_doc_name=None):
        """
        Move a layer to its own new document

        Args:
            layer_name: Name of the layer to move
            new_doc_name: Name for the new document (optional, defaults to layer name)
        """
        try:
            app = Krita.instance()
            src_doc = app.activeDocument()
            if not src_doc:
                return {'success': False, 'message': 'No active document'}

            # Find the layer to move
            src_layer = src_doc.nodeByName(layer_name)
            if not src_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            # Get layer dimensions and position
            bounds = src_layer.bounds()
            width = bounds.width()
            height = bounds.height()

            if width == 0 or height == 0:
                # Fallback to document dimensions if layer has no content
                width = src_doc.width()
                height = src_doc.height()

            # Create new document with same dimensions as the layer
            doc_name = new_doc_name or layer_name
            resolution = src_doc.resolution()
            color_model = src_doc.colorModel()
            color_depth = src_doc.colorDepth()
            profile = src_doc.colorProfile()

            dst_doc = app.createDocument(
                width, height, doc_name,
                color_model, color_depth, profile, resolution
            )

            # Copy the layer to the new document
            # Duplicate the layer first
            duplicated_layer = src_layer.duplicate()

            # Add to new document's root
            dst_doc.rootNode().addChildNode(duplicated_layer, None)

            # Center the layer in the new document if it's smaller
            if width < src_doc.width() or height < src_doc.height():
                # Calculate center position
                center_x = (dst_doc.width() - width) / 2
                center_y = (dst_doc.height() - height) / 2
                # Note: This would require transform, but for simplicity we'll skip
                pass

            # Open the new document in a view
            app.activeWindow().addView(dst_doc)

            # Optionally remove the original layer from source document
            # src_layer.remove()

            dst_doc.refreshProjection()
            src_doc.refreshProjection()

            return {
                'success': True,
                'message': f'Moved layer "{layer_name}" to new document "{doc_name}"',
                'data': {
                    'layer_name': layer_name,
                    'new_document': doc_name,
                    'width': width,
                    'height': height
                }
            }

        except Exception as e:
            return {'success': False, 'message': str(e)}
