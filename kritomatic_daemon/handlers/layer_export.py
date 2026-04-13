import os
import random
import string
from datetime import datetime
from pathlib import Path
from krita import Krita

class LayerExportHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'move_layer_to_new_document':
            return self.move_layer_to_new_document(params)
        elif cmd_type == 'export_layer_to_file':
            return self.export_layer_to_file(params)
        return {'success': False, 'message': f'Unknown export command: {cmd_type}'}

    def move_layer_to_new_document(self, params):
        try:
            app = Krita.instance()
            src_doc = app.activeDocument()
            if not src_doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            new_doc_name = params.get('new_doc_name', None)

            src_layer = src_doc.nodeByName(layer_name)
            if not src_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            bounds = src_layer.bounds()
            width = bounds.width() if bounds.width() > 0 else src_doc.width()
            height = bounds.height() if bounds.height() > 0 else src_doc.height()

            doc_name = new_doc_name or layer_name
            resolution = src_doc.resolution()
            color_model = src_doc.colorModel()
            color_depth = src_doc.colorDepth()
            profile = src_doc.colorProfile()

            dst_doc = app.createDocument(width, height, doc_name, color_model, color_depth, profile, resolution)

            duplicated_layer = src_layer.duplicate()
            dst_doc.rootNode().addChildNode(duplicated_layer, None)
            app.activeWindow().addView(dst_doc)
            dst_doc.refreshProjection()

            return {
                'success': True,
                'message': f'Moved layer "{layer_name}" to new document "{doc_name}"',
                'data': {'layer_name': layer_name, 'new_document': doc_name, 'width': width, 'height': height}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def export_layer_to_file(self, params):
        try:
            app = Krita.instance()
            src_doc = app.activeDocument()
            if not src_doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            output_path = params.get('output_path', None)

            src_layer = src_doc.nodeByName(layer_name)
            if not src_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original_parent = src_layer.parentNode()
            children = original_parent.childNodes()
            original_position = None
            for i, child in enumerate(children):
                if child == src_layer:
                    original_position = i
                    break

            if not output_path:
                doc_path = src_doc.fileName()
                if doc_path:
                    doc_dir = Path(doc_path).parent
                    doc_name = Path(doc_path).stem
                else:
                    doc_dir = Path.home()
                    doc_name = "untitled"

                export_dir = doc_dir / f"{doc_name}_layers"
                export_dir.mkdir(exist_ok=True)

                random_seq = ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))
                output_path = str(export_dir / f"{random_seq}.kra")
            elif not output_path.endswith('.kra'):
                output_path += '.kra'

            bounds = src_layer.bounds()
            width = bounds.width() if bounds.width() > 0 else src_doc.width()
            height = bounds.height() if bounds.height() > 0 else src_doc.height()

            temp_doc = app.createDocument(width, height, "__temp_export__", src_doc.colorModel(), src_doc.colorDepth(), src_doc.colorProfile(), src_doc.resolution())
            duplicated_layer = src_layer.duplicate()
            temp_doc.rootNode().addChildNode(duplicated_layer, None)
            temp_doc.refreshProjection()
            temp_doc.saveAs(output_path)
            temp_doc.close()

            src_layer.remove()

            scaling_method = 'ToImageSize'
            scaling_filter = 'Bicubic'
            file_layer = src_doc.createFileLayer(layer_name, output_path, scaling_method, scaling_filter)

            current_children = original_parent.childNodes()
            if original_position is not None and original_position < len(current_children):
                original_parent.addChildNode(file_layer, current_children[original_position])
            else:
                original_parent.addChildNode(file_layer, None)

            src_doc.refreshProjection()

            return {
                'success': True,
                'message': f'Exported layer "{layer_name}" to file layer',
                'data': {'layer_name': layer_name, 'output_path': output_path, 'position': original_position}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
