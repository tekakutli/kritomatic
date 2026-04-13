import os
from krita import Krita
from ..decorators import command

class LayerFileHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_file_layer':
            return self.create_file_layer(params)
        elif cmd_type == 'convert_to_file_layer':
            return self.convert_to_file_layer(params)
        return {'success': False, 'message': f'Unknown file layer command: {cmd_type}'}

    @command(
        category='layer',
        help_text='Create a file layer with optional size and position',
        args={
            'name': {'type': 'str', 'required': True, 'help': 'Layer name'},
            'file_path': {'type': 'str', 'required': True, 'help': 'Absolute path to the image file'},
            'width': {'type': 'int', 'required': False, 'help': 'Target width in pixels'},
            'height': {'type': 'int', 'required': False, 'help': 'Target height in pixels'},
            'x': {'type': 'float', 'default': 0, 'help': 'X position in pixels'},
            'y': {'type': 'float', 'default': 0, 'help': 'Y position in pixels'}
        }
    )
    def create_file_layer(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'File Layer')
            file_path = params.get('file_path', '')
            width = params.get('width', None)
            height = params.get('height', None)
            x = params.get('x', 0)
            y = params.get('y', 0)

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            scaling_method = 'ToImageSize' if (width and height) else 'None'
            scaling_filter = 'Bicubic'
            file_layer = doc.createFileLayer(name, file_path, scaling_method, scaling_filter)
            doc.rootNode().addChildNode(file_layer, None)

            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()

            if not (width or height or x != 0 or y != 0):
                doc.refreshProjection()
                return {'success': True, 'message': f'Created file layer "{name}"', 'data': {'name': name, 'file_path': file_path}}

            # Calculate scale
            scale_x, scale_y = 1.0, 1.0
            temp_doc = app.createDocument(1, 1, "__temp__", "RGBA", "U8", "", 72)
            temp_layer = temp_doc.createFileLayer("__temp__", file_path, 'None', 'Bicubic')
            temp_doc.rootNode().addChildNode(temp_layer, None)
            temp_doc.refreshProjection()
            QApplication.processEvents()

            bounds = temp_layer.bounds()
            orig_width, orig_height = bounds.width(), bounds.height()
            temp_doc.close()
            QApplication.processEvents()

            if width and orig_width > 0:
                scale_x = width / orig_width
            if height and orig_height > 0:
                scale_y = height / orig_height
            if width and not height:
                scale_y = scale_x
            if height and not width:
                scale_x = scale_y

            # Create and configure transform mask
            transform_mask_name = f"{name}_transform"
            transform_mask = doc.createTransformMask(transform_mask_name)
            file_layer.addChildNode(transform_mask, None)
            QApplication.processEvents()

            import xml.etree.ElementTree as ET
            xml_str = transform_mask.toXML()
            root = ET.fromstring(xml_str)

            for elem in root.findall('.//scaleX'):
                elem.set('value', str(scale_x))
            for elem in root.findall('.//scaleY'):
                elem.set('value', str(scale_y))
            for elem in root.findall('.//flattenedPerspectiveTransform'):
                elem.set('m31', str(x))
                elem.set('m32', str(y))
            for elem in root.findall('.//transformedCenter'):
                elem.set('x', str(x))
                elem.set('y', str(y))

            transform_mask.fromXML(ET.tostring(root, encoding='unicode'))
            QApplication.processEvents()

            doc.setActiveNode(transform_mask)
            doc.refreshProjection()
            QApplication.processEvents()

            return {
                'success': True,
                'message': f'Created file layer "{name}" with transform',
                'data': {
                    'name': name,
                    'file_path': file_path,
                    'width': width,
                    'height': height,
                    'position': (x, y),
                    'scale': (scale_x, scale_y),
                    'transform_mask': transform_mask_name
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Convert a regular layer to a file layer (exports to .kra and re-imports)',
        args={
            'layer_name': {'type': 'str', 'required': True, 'help': 'Name of the layer to convert'},
            'output_path': {'type': 'str', 'required': False, 'help': 'Path to save the exported file (auto-generated if not provided)'}
        }
    )
    def convert_to_file_layer(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            output_path = params.get('output_path', None)

            src_layer = doc.nodeByName(layer_name)
            if not src_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            # Store original position and parent
            original_parent = src_layer.parentNode()
            children = original_parent.childNodes()
            original_position = None
            for i, child in enumerate(children):
                if child == src_layer:
                    original_position = i
                    break

            # Generate random hash (8 characters, no date)
            import random
            import string
            random_hash = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

            # Generate output path if not provided
            if not output_path:
                from pathlib import Path

                doc_path = doc.fileName()
                if doc_path:
                    doc_dir = Path(doc_path).parent
                    doc_name = Path(doc_path).stem
                else:
                    doc_dir = Path.home()
                    doc_name = "untitled"

                # Create a directory with the document name
                export_dir = doc_dir / f"{doc_name}_layers"
                export_dir.mkdir(exist_ok=True)

                # File name is just the random hash
                output_path = str(export_dir / f"{random_hash}.kra")
            elif not output_path.endswith('.kra'):
                output_path += '.kra'

            # Create a temporary document with just this layer
            bounds = src_layer.bounds()
            width = bounds.width() if bounds.width() > 0 else doc.width()
            height = bounds.height() if bounds.height() > 0 else doc.height()

            temp_doc = app.createDocument(
                width, height, "__temp_export__",
                doc.colorModel(), doc.colorDepth(),
                doc.colorProfile(), doc.resolution()
            )

            # Copy the layer to temp document
            duplicated_layer = src_layer.duplicate()
            temp_doc.rootNode().addChildNode(duplicated_layer, None)
            temp_doc.refreshProjection()

            # Save as .kra file with random hash name
            temp_doc.saveAs(output_path)

            # Close temp document
            temp_doc.close()

            # Delete original layer
            src_layer.remove()

            # Create a group with the original layer name
            group_layer = doc.createGroupLayer(layer_name)

            # Create file layer with random hash as name
            scaling_method = 'ToImageSize'
            scaling_filter = 'Bicubic'
            file_layer = doc.createFileLayer(random_hash, output_path, scaling_method, scaling_filter)

            # Add file layer to group
            group_layer.addChildNode(file_layer, None)

            # Insert the group at the original position
            current_children = original_parent.childNodes()
            if original_position is not None and original_position < len(current_children):
                target_sibling = current_children[original_position]
                original_parent.addChildNode(group_layer, target_sibling)
            else:
                original_parent.addChildNode(group_layer, None)

            doc.setActiveNode(group_layer)
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Converted layer "{layer_name}" to group containing file layer',
                'data': {
                    'layer_name': layer_name,
                    'file_layer_name': random_hash,
                    'random_hash': random_hash,
                    'output_path': output_path,
                    'export_dir': str(export_dir) if 'export_dir' in dir() else None,
                    'position': original_position
                }
            }

        except Exception as e:
            return {'success': False, 'message': str(e)}
