import os
from krita import *
from ..decorators import command
from ..utils.refresh import refresh
from ..utils.krita_actions import find_rotate_action

class DocumentHandler:
    def __init__(self):
        pass

    def execute(self, cmd_type, params):
        """Execute document-related commands"""
        if cmd_type == 'get_current_dimensions':
            return self.get_current_dimensions()
        elif cmd_type == 'create_new_from_current':
            name = params.get('name', 'New Document')
            return self.create_new_from_current(name)
        elif cmd_type == 'create_new_with_dimensions':
            name = params.get('name', 'New Document')
            width = params.get('width', 1920)
            height = params.get('height', 1080)
            resolution = params.get('resolution', 300)
            color_model = params.get('color_model', 'RGBA')
            color_depth = params.get('color_depth', 'U8')
            profile = params.get('profile', '')
            return self.create_new_with_dimensions(name, width, height, resolution, color_model, color_depth, profile)
        elif cmd_type == 'get_all_documents':
            return self.get_all_documents()
        elif cmd_type == 'save_document':
            return self.save_document(params.get('file_path', ''))
        elif cmd_type == 'open_document':
            return self.open_document(params)
        elif cmd_type == 'close_document':
            return self.close_document(params)
        elif cmd_type == 'export_document':
            return self.export_document(params)
        elif cmd_type == 'export_file_to_image':
            return self.export_file_to_image(params)
        elif cmd_type == 'rotate_document':
            return self.rotate_document(params)
        elif cmd_type == 'rotate_kra_file':
            return self.rotate_kra_file(params)
        return {'success': False, 'message': f'Unknown document command: {cmd_type}'}

    def _build_export_info(self, output_path):
        info = InfoObject()
        ext = os.path.splitext(output_path)[1].lower()
        if ext == '.png':
            info.setProperty('compression', 6)
            info.setProperty('interlaced', False)
            info.setProperty('alpha', True)
            info.setProperty('forceSRGB', False)
            info.setProperty('indexed', False)
            info.setProperty('saveSRGBProfile', False)
        elif ext in ('.jpg', '.jpeg'):
            info.setProperty('quality', 90)
            info.setProperty('optimize', True)
            info.setProperty('progressive', False)
            info.setProperty('saveProfile', True)
        elif ext == '.webp':
            info.setProperty('quality', 90)
            info.setProperty('lossless', False)
        elif ext in ('.tif', '.tiff'):
            info.setProperty('compression', 1)
        return info

    def _export_image_silent(self, doc, output_path):
        previous = doc.batchmode()
        doc.setBatchmode(True)
        try:
            info = self._build_export_info(output_path)
            return doc.exportImage(output_path, info)
        finally:
            doc.setBatchmode(previous)

    @command(
        category='doc',
        help_text='Get current document dimensions',
        args={}
    )
    def get_current_dimensions(self):
        """Get dimensions of current active document"""
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            width = doc.width()
            height = doc.height()
            resolution = doc.resolution()
            color_model = doc.colorModel()
            color_depth = doc.colorDepth()
            color_profile = doc.colorProfile()

            return {
                'success': True,
                'message': f'Current document: {width}x{height} @ {resolution} DPI',
                'data': {
                    'width': width,
                    'height': height,
                    'resolution': resolution,
                    'color_model': color_model,
                    'color_depth': color_depth,
                    'color_profile': color_profile
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Create a new document with same dimensions as current',
        args={
            '--name': {'type': 'str', 'default': 'New Document', 'help': 'Name for the new document'}
        }
    )
    def create_new_from_current(self, name="New Document"):
        """Create a new document with same dimensions as current"""
        try:
            current = Krita.instance().activeDocument()
            if not current:
                return {'success': False, 'message': 'No active document to copy dimensions from'}

            width = current.width()
            height = current.height()
            resolution = current.resolution()
            color_model = current.colorModel()
            color_depth = current.colorDepth()
            profile = current.colorProfile()

            app = Krita.instance()
            new_doc = app.createDocument(
                width, height, name,
                color_model, color_depth, profile, resolution
            )

            app.activeWindow().addView(new_doc)

            return {
                'success': True,
                'message': f'Created new document "{name}" with dimensions {width}x{height} @ {resolution} DPI',
                'data': {
                    'name': name,
                    'width': width,
                    'height': height,
                    'resolution': resolution,
                    'color_model': color_model,
                    'color_depth': color_depth
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Create a new document with custom dimensions',
        args={
            '--name': {'type': 'str', 'default': 'New Document', 'help': 'Name for the new document'},
            '--width': {'type': 'int', 'default': 1920, 'help': 'Width in pixels'},
            '--height': {'type': 'int', 'default': 1080, 'help': 'Height in pixels'},
            '--resolution': {'type': 'float', 'default': 300, 'help': 'Resolution in DPI'}
        }
    )
    def create_new_with_dimensions(self, name, width, height, resolution=300,
                                   color_model="RGBA", color_depth="U8", profile=""):
        """Create a new document with custom dimensions"""
        try:
            app = Krita.instance()
            new_doc = app.createDocument(
                width, height, name,
                color_model, color_depth, profile, resolution
            )
            app.activeWindow().addView(new_doc)

            return {
                'success': True,
                'message': f'Created new document "{name}" with dimensions {width}x{height} @ {resolution} DPI',
                'data': {
                    'name': name,
                    'width': width,
                    'height': height,
                    'resolution': resolution,
                    'color_model': color_model,
                    'color_depth': color_depth
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='List all open documents',
        args={}
    )
    def get_all_documents(self):
        """Get list of all open documents"""
        try:
            app = Krita.instance()
            documents = []
            for doc in app.documents():
                documents.append({
                    'name': doc.fileName() if doc.fileName() else 'Untitled',
                    'width': doc.width(),
                    'height': doc.height(),
                    'resolution': doc.resolution(),
                    'modified': doc.modified()
                })

            return {
                'success': True,
                'message': f'Found {len(documents)} open document(s)',
                'data': {'documents': documents}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Save current document to file path',
        args={
            '--file_path': {'type': 'str', 'required': True, 'help': 'File path to save to'}
        }
    )
    def save_document(self, file_path):
        """Save current document to file path"""
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            doc.saveAs(file_path)
            return {
                'success': True,
                'message': f'Saved document to {file_path}'
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Open an existing .kra file as a new document (no view added)',
        args={
            '--file_path': {'type': 'str', 'required': True, 'help': 'Path to a .kra file'}
        }
    )
    def open_document(self, params):
        """Open a .kra file as a document in the running Krita session."""
        try:
            app = Krita.instance()
            file_path = params.get('file_path', '')
            if not os.path.exists(file_path):
                return {'success': False, 'message': f'File not found: {file_path}'}
            doc = app.openDocument(file_path)
            if not doc:
                return {'success': False, 'message': f'Failed to open {file_path}'}
            return {
                'success': True,
                'message': f'Opened {file_path}',
                'data': {'name': doc.name()}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Close a document by name, or the active document if name omitted',
        args={
            '--name': {'type': 'str', 'required': False, 'help': 'Document name (defaults to active)'}
        }
    )
    def close_document(self, params):
        """Close a document without saving."""
        try:
            app = Krita.instance()
            name = params.get('name', None)
            if name:
                doc = next((d for d in app.documents() if d.name() == name), None)
            else:
                doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No matching document'}
            doc_name = doc.name()
            # Suppress the save-changes prompt.
            doc.setBatchmode(True)
            doc.close()
            return {'success': True, 'message': f'Closed {doc_name}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Export a document to an image file (PNG, JPG, etc.)',
        args={
            '--file_path': {'type': 'str', 'required': True, 'help': 'Output image path'},
            '--name': {'type': 'str', 'required': False, 'help': 'Document name (defaults to active)'}
        }
    )
    def export_document(self, params):
        """Export the specified (or active) document to an image file."""
        try:
            app = Krita.instance()
            file_path = params.get('file_path', '')
            name = params.get('name', None)
            if name:
                doc = next((d for d in app.documents() if d.name() == name), None)
            else:
                doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No matching document'}
            ok = self._export_image_silent(doc, file_path)
            if not ok:
                return {'success': False, 'message': f'exportImage returned False for {file_path}'}
            return {'success': True, 'message': f'Exported to {file_path}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='One-shot: open a .kra from disk, export it to an image, close it. '
                  'No view is added; the user-facing session is untouched.',
        args={
            '--input_path': {'type': 'str', 'required': True, 'help': 'Input .kra path'},
            '--output_path': {'type': 'str', 'required': True, 'help': 'Output image path (PNG/JPG/...)'}
        }
    )
    def export_file_to_image(self, params):
        """Open a .kra, flatten it to an image file, close it. Atomic."""
        try:
            app = Krita.instance()
            input_path = params.get('input_path', '')
            output_path = params.get('output_path', '')
            if not os.path.exists(input_path):
                return {'success': False, 'message': f'Input not found: {input_path}'}
            doc = app.openDocument(input_path)
            if not doc:
                return {'success': False, 'message': f'Failed to open {input_path}'}
            try:
                ok = self._export_image_silent(doc, output_path)
            finally:
                doc.setBatchmode(True)
                doc.close()
            if not ok:
                return {'success': False,
                        'message': f'exportImage returned False for {output_path}'}
            return {'success': True,
                    'message': f'Exported {input_path} -> {output_path}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Rotate the active document 90 degrees CW/CCW/180',
        args={
            '--direction': {'type': 'str', 'required': True,
                            'choices': ['cw', 'ccw', '180'],
                            'help': 'Rotation direction'}
        }
    )
    def rotate_document(self, params):
        """
        Rotate the currently active document via Krita's built-in
        image-rotation action. The document's canvas AND all its layers
        rotate together.
        """
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            direction = str(params.get('direction', 'cw')).lower()
            action = find_rotate_action(app, direction)
            if not action:
                return {'success': False,
                        'message': f'No rotate-image action found for direction {direction}. '
                                   f'Run introspect list_actions --substring rotate to see what is available.'}
            action_name = action.objectName() or action.text() or '<unnamed>'
            action.trigger()

            refresh(doc)
            return {
                'success': True,
                'message': f'Rotated document {direction} via {action_name} '
                           f'(now {doc.width()}x{doc.height()})',
                'data': {
                    'direction': direction,
                    'action': action_name,
                    'width': doc.width(),
                    'height': doc.height(),
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='doc',
        help_text='Open a .kra, rotate it 90 degrees, save it back, close it. Atomic.',
        args={
            '--input_path': {'type': 'str', 'required': True, 'help': 'Input .kra path'},
            '--direction': {'type': 'str', 'required': True,
                            'choices': ['cw', 'ccw', '180'],
                            'help': 'Rotation direction'},
            '--output_path': {'type': 'str', 'required': False,
                              'help': 'Output .kra path (defaults to input_path, in-place)'}
        }
    )
    def rotate_kra_file(self, params):
        """
        Open a .kra, rotate it via Krita's image-rotation action, save,
        close. The .kra is added to the active window while it's being
        rotated (Krita's actions are attached to views), then removed
        when we close it.
        """
        try:
            app = Krita.instance()
            input_path = params.get('input_path', '')
            output_path = params.get('output_path', input_path) or input_path
            direction = str(params.get('direction', 'cw')).lower()

            if not os.path.exists(input_path):
                return {'success': False, 'message': f'Input not found: {input_path}'}

            doc = app.openDocument(input_path)
            if not doc:
                return {'success': False, 'message': f'Failed to open {input_path}'}

            try:
                app.activeWindow().addView(doc)
            except Exception:
                pass

            doc.setBatchmode(True)
            try:
                action = find_rotate_action(app, direction)
                if not action:
                    return {'success': False,
                            'message': f'No rotate-image action found for direction '
                                       f'{direction}. Run introspect list_actions --substring '
                                       f'rotate to see what is available.'}
                action_name = action.objectName() or action.text() or '<unnamed>'
                action.trigger()
                refresh(doc)
                new_w = doc.width()
                new_h = doc.height()
                doc.saveAs(output_path)
            finally:
                doc.setBatchmode(True)
                doc.close()

            return {
                'success': True,
                'message': f'Rotated {direction} via {action_name} -> {new_w}x{new_h}',
                'data': {
                    'direction': direction,
                    'action': action_name,
                    'width': new_w,
                    'height': new_h,
                    'output_path': output_path,
                }
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
