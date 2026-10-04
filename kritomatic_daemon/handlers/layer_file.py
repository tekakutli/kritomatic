import os
import random
import string
import xml.etree.ElementTree as ET
from pathlib import Path
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage
from PyQt5.QtCore import Qt
from krita import Krita
from ..decorators import command

class LayerFileHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_file_layer':
            return self.create_file_layer(params)
        elif cmd_type == 'convert_to_file_layer':
            return self.convert_to_file_layer(params)
        elif cmd_type == 'embed_image_as_layer':
            return self.embed_image_as_layer(params)
        elif cmd_type == 'import_kra_as_group':
            return self.import_kra_as_group(params)
        return {'success': False, 'message': f'Unknown file layer command: {cmd_type}'}

    @command(
        category='layer',
        help_text='Create a file layer with optional size and position',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Layer name'},
            '--file_path': {'type': 'str', 'required': True, 'help': 'Absolute path to the image file'},
            '--width': {'type': 'int', 'required': False, 'help': 'Target width in pixels'},
            '--height': {'type': 'int', 'required': False, 'help': 'Target height in pixels'},
            '--x': {'type': 'float', 'default': 0, 'help': 'X position in pixels'},
            '--y': {'type': 'float', 'default': 0, 'help': 'Y position in pixels'}
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

            scaling_method = 'None'
            scaling_filter = 'Bicubic'
            file_layer = doc.createFileLayer(name, file_path, scaling_method, scaling_filter)
            doc.rootNode().addChildNode(file_layer, None)

            QApplication.processEvents()

            if not (width or height or x != 0 or y != 0):
                doc.refreshProjection()
                return {'success': True, 'message': f'Created file layer "{name}"',
                        'data': {'name': name, 'file_path': file_path}}

            # Real dimensions, read directly. A file layer inside a temp
            # Krita document is clipped to that document's canvas, so a
            # 1x1 temp doc reports 1x1 regardless of the source's real
            # size. QImage bypasses that.
            qimg = QImage(file_path)
            if qimg.isNull():
                return {'success': False,
                        'message': f'Could not read image dimensions for {file_path}'}
            orig_width = qimg.width()
            orig_height = qimg.height()

            scale_x, scale_y = 1.0, 1.0
            if width and orig_width > 0:
                scale_x = width / orig_width
            if height and orig_height > 0:
                scale_y = height / orig_height
            if width and not height:
                scale_y = scale_x
            if height and not width:
                scale_x = scale_y

            transform_mask_name = f"{name}_transform"
            transform_mask = doc.createTransformMask(transform_mask_name)
            file_layer.addChildNode(transform_mask, None)
            QApplication.processEvents()

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
                    'name': name, 'file_path': file_path,
                    'width': width, 'height': height, 'position': (x, y),
                    'scale': (scale_x, scale_y), 'transform_mask': transform_mask_name
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Embed an image file directly into a paint layer (pixels baked in, no external reference)',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Layer name'},
            '--file_path': {'type': 'str', 'required': True, 'help': 'Path to the image file'},
            '--width': {'type': 'int', 'required': False, 'help': 'Target width in pixels'},
            '--height': {'type': 'int', 'required': False, 'help': 'Target height in pixels'},
            '--x': {'type': 'float', 'default': 0, 'help': 'X position in pixels'},
            '--y': {'type': 'float', 'default': 0, 'help': 'Y position in pixels'}
        }
    )
    def embed_image_as_layer(self, params):
        """
        Read an image, scale it to (width, height), blit the pixels into
        a fresh paint layer at (x, y). The result is a self-contained
        document — nothing on disk is referenced at open time.
        """
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'Embedded')
            file_path = params.get('file_path', '')
            width = params.get('width', None)
            height = params.get('height', None)
            x = int(round(params.get('x', 0)))
            y = int(round(params.get('y', 0)))

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            img = QImage(file_path)
            if img.isNull():
                return {'success': False, 'message': f'Could not read image: {file_path}'}

            if width or height:
                tw = int(width) if width else img.width()
                th = int(height) if height else img.height()
                img = img.scaled(tw, th, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

            img = img.convertToFormat(QImage.Format_RGBA8888)
            iw = img.width()
            ih = img.height()

            # Format_RGBA8888 is exactly 4 bytes per pixel, so bytesPerLine
            # == iw * 4 and there is no row padding. Direct copy works.
            ptr = img.constBits()
            ptr.setsize(img.byteCount())
            raw = bytes(ptr)

            layer = doc.createNode(name, "paintlayer")
            doc.rootNode().addChildNode(layer, None)
            layer.setPixelData(raw, x, y, iw, ih)

            doc.setActiveNode(layer)
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Embedded image into "{name}" ({iw}x{ih}) at ({x}, {y})',
                'data': {'name': name, 'width': iw, 'height': ih, 'x': x, 'y': y}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Import all layers of a source .kra into the active document as a group',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Group layer name'},
            '--file_path': {'type': 'str', 'required': True, 'help': 'Path to source .kra'}
        }
    )
    def import_kra_as_group(self, params):
        """
        Open the source .kra, duplicate each root child into a new group
        in the target document, close the source.

        No sizing is done here — the imported layers land at their
        source coordinates. The batch is expected to follow this with a
        create_transform_mask + transform_mask pair to position and
        scale the group as needed.
        """
        try:
            app = Krita.instance()
            target_doc = app.activeDocument()
            if not target_doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'Imported')
            file_path = params.get('file_path', '')

            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}

            src_doc = app.openDocument(file_path)
            if not src_doc:
                return {'success': False, 'message': f'Failed to open {file_path}'}

            src_w = src_doc.width()
            src_h = src_doc.height()
            layer_count = 0

            # Suppress any save-changes prompt on close; the source doc
            # is never modified, but Krita still asks in some builds.
            src_batch_was = src_doc.batchmode()
            src_doc.setBatchmode(True)
            try:
                group = target_doc.createGroupLayer(name)

                for child in src_doc.rootNode().childNodes():
                    dup = child.duplicate()
                    group.addChildNode(dup, None)
                    layer_count += 1

                target_doc.rootNode().addChildNode(group, None)
                target_doc.setActiveNode(group)
                target_doc.refreshProjection()
            finally:
                src_doc.setBatchmode(src_batch_was)
                src_doc.close()

            return {
                'success': True,
                'message': f'Imported {os.path.basename(file_path)} as group "{name}" '
                           f'({layer_count} layer(s), source {src_w}x{src_h})',
                'data': {
                    'name': name,
                    'source_size': (src_w, src_h),
                    'layer_count': layer_count,
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Convert a regular layer to a file layer (exports to .kra and re-imports)',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the layer to convert'},
            '--output_path': {'type': 'str', 'required': False, 'help': 'Path to save the exported file (auto-generated if not provided)'}
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

            original_parent = src_layer.parentNode()
            children = original_parent.childNodes()
            original_position = None
            for i, child in enumerate(children):
                if child == src_layer:
                    original_position = i
                    break

            random_hash = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

            export_dir = None
            if not output_path:
                doc_path = doc.fileName()
                if doc_path:
                    doc_dir = Path(doc_path).parent
                    doc_name = Path(doc_path).stem
                else:
                    doc_dir = Path.home()
                    doc_name = "untitled"

                export_dir = doc_dir / f"{doc_name}_layers"
                export_dir.mkdir(exist_ok=True)
                output_path = str(export_dir / f"{random_hash}.kra")
            elif not output_path.endswith('.kra'):
                output_path += '.kra'

            bounds = src_layer.bounds()
            width = bounds.width() if bounds.width() > 0 else doc.width()
            height = bounds.height() if bounds.height() > 0 else doc.height()

            temp_doc = app.createDocument(
                width, height, "__temp_export__",
                doc.colorModel(), doc.colorDepth(),
                doc.colorProfile(), doc.resolution()
            )

            duplicated_layer = src_layer.duplicate()
            temp_doc.rootNode().addChildNode(duplicated_layer, None)
            temp_doc.refreshProjection()
            temp_doc.saveAs(output_path)
            temp_doc.close()

            src_layer.remove()

            group_layer = doc.createGroupLayer(layer_name)
            scaling_method = 'ToImageSize'
            scaling_filter = 'Bicubic'
            file_layer = doc.createFileLayer(random_hash, output_path, scaling_method, scaling_filter)
            group_layer.addChildNode(file_layer, None)

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
                    'layer_name': layer_name, 'file_layer_name': random_hash,
                    'random_hash': random_hash, 'output_path': output_path,
                    'export_dir': str(export_dir) if export_dir else None,
                    'position': original_position
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
