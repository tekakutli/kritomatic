from krita import *
from ..utils.color_utils import ColorUtils
from ..decorators import command
import os
import zipfile
import tempfile
from PyQt5.QtCore import QStandardPaths

class PaletteHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'add_to_palette':
            return self.add_color_to_palette(params.get('palette_name', ''))
        elif cmd_type == 'create_palette':
            return self.create_palette(
                params.get('palette_name', ''),
                params.get('columns', 8),
                params.get('rows', 8)
            )
        elif cmd_type == 'activate_palette':
            return self.activate_palette(params.get('palette_name', ''))
        elif cmd_type == 'list_palettes':
            return self.list_palettes()
        return {'success': False, 'message': f'Unknown palette command: {cmd_type}'}

    @command(
        category='palette',
        help_text='Add current foreground color to a palette',
        args={
            'palette_name': {'type': 'str', 'required': True, 'help': 'Name of the palette'}
        }
    )
    def add_color_to_palette(self, palette_name):
        try:
            app = Krita.instance()
            view = app.activeWindow().activeView()
            current_color = view.foregroundColor()

            palettes = app.resources('palette')
            target_name = None
            for name in palettes.keys():
                if name.lower() == palette_name.lower():
                    target_name = name
                    break

            if not target_name:
                return {'success': False, 'message': f'Palette "{palette_name}" not found'}

            target = Palette(palettes[target_name])
            swatch = Swatch()
            swatch.setColor(current_color)
            target.addEntry(swatch, "")

            color_info = ColorUtils.extract_color_info(current_color)
            return {'success': True, 'message': f'Added color to "{target_name}"', 'data': color_info}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='palette',
        help_text='Create a new palette (requires Krita restart to appear)',
        args={
            'palette_name': {'type': 'str', 'required': True, 'help': 'Name of the new palette'},
            'columns': {'type': 'int', 'default': 8, 'help': 'Number of columns'},
            'rows': {'type': 'int', 'default': 8, 'help': 'Number of rows'}
        }
    )
    def create_palette(self, name, columns=8, rows=8):
        try:
            app = Krita.instance()
            palettes = app.resources('palette')
            for n in palettes.keys():
                if n.lower() == name.lower():
                    return {'success': False, 'message': f'Palette "{name}" already exists'}

            resource_dir = QStandardPaths.writableLocation(QStandardPaths.AppLocalDataLocation)
            palettes_dir = os.path.join(resource_dir, 'palettes')
            if not os.path.exists(palettes_dir):
                os.makedirs(palettes_dir)

            palette_file = os.path.join(palettes_dir, f"{name}.kpl")

            with tempfile.TemporaryDirectory() as tmpdir:
                mimetype_path = os.path.join(tmpdir, 'mimetype')
                with open(mimetype_path, 'w') as f:
                    f.write('application/x-krita-palette')

                colorset_path = os.path.join(tmpdir, 'colorset.xml')
                xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<Colorset name="{name}" comment="Created via WebSocket" columns="{columns}" rows="{rows}" readonly="false" version="1.0">
</Colorset>'''

                with open(colorset_path, 'w', encoding='utf-8') as f:
                    f.write(xml)

                with zipfile.ZipFile(palette_file, 'w', zipfile.ZIP_DEFLATED) as zf:
                    zf.write(mimetype_path, 'mimetype', compress_type=zipfile.ZIP_STORED)
                    zf.write(colorset_path, 'colorset.xml')

            return {'success': True, 'message': f'Palette "{name}" created. Restart Krita to see it.',
                    'data': {'path': palette_file, 'columns': columns, 'rows': rows}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='palette',
        help_text='Activate a palette in the Palette Docker',
        args={
            'palette_name': {'type': 'str', 'required': True, 'help': 'Name of the palette to activate'}
        }
    )
    def activate_palette(self, name):
        try:
            app = Krita.instance()
            docker = None
            for d in app.activeWindow().dockers():
                if d.objectName() == 'PaletteDocker':
                    docker = d
                    break
            if not docker:
                return {'success': False, 'message': 'Palette Docker not found'}
            if hasattr(docker, 'loadPalette'):
                docker.loadPalette(name)
                return {'success': True, 'message': f'Activated "{name}"'}
            return {'success': False, 'message': 'Cannot load palette'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='palette',
        help_text='List all available palettes',
        args={}
    )
    def list_palettes(self):
        try:
            app = Krita.instance()
            palettes = app.resources('palette')
            names = list(palettes.keys())
            return {'success': True, 'message': f'Found {len(names)} palettes', 'data': {'palettes': names}}
        except Exception as e:
            return {'success': False, 'message': str(e)}
