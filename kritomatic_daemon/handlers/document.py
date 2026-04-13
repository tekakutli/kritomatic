from krita import *
from ..decorators import command

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
        return {'success': False, 'message': f'Unknown document command: {cmd_type}'}

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
                '--data': {
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
