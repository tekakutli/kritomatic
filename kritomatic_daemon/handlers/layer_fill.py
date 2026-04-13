from krita import Krita
from ..decorators import command

class LayerFillHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'fill_layer':
            return self.fill_layer(params)
        elif cmd_type == 'fill_selection':
            return self.fill_selection(params)
        return {'success': False, 'message': f'Unknown fill command: {cmd_type}'}

    @command(
        category='layer',
        help_text='Fill a layer with a specific color',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the layer to fill'},
            '--color': {'type': 'str', 'required': False, 'help': 'Hex color to fill with (e.g., #ff0000)'},
            '--foreground': {'type': 'bool', 'default': False, 'help': 'Use current foreground color'},
            '--background': {'type': 'bool', 'default': False, 'help': 'Use current background color'}
        }
    )
    def fill_layer(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            view = app.activeWindow().activeView()
            if not view:
                return {'success': False, 'message': 'No active view'}

            layer_name = params.get('layer_name', '')
            color_hex = params.get('color', None)
            use_foreground = params.get('foreground', False)
            use_background = params.get('background', False)

            target_layer = doc.nodeByName(layer_name)
            if not target_layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original_active = doc.activeNode()
            doc.setActiveNode(target_layer)

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

            original_foreground = view.foregroundColor()
            view.setForeGroundColor(color)

            select_all = app.action('select_all')
            if select_all:
                select_all.trigger()

            fill_action = app.action('fill_selection_foreground_color')
            if fill_action:
                fill_action.trigger()

            deselect = app.action('deselect')
            if deselect:
                deselect.trigger()

            view.setForeGroundColor(original_foreground)
            doc.setActiveNode(original_active)
            doc.refreshProjection()

            color_desc = color_hex or ('foreground' if use_foreground else 'background')
            return {'success': True, 'message': f'Filled layer "{layer_name}" with {color_desc}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Fill the current selection with a specific color',
        args={
            '--color': {'type': 'str', 'required': False, 'help': 'Hex color to fill with (e.g., #ff0000)'},
            '--foreground': {'type': 'bool', 'default': False, 'help': 'Use current foreground color'},
            '--background': {'type': 'bool', 'default': False, 'help': 'Use current background color'}
        }
    )
    def fill_selection(self, params):
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            view = app.activeWindow().activeView()
            if not view:
                return {'success': False, 'message': 'No active view'}

            selection = doc.selection()
            if selection is None:
                return {'success': False, 'message': 'No active selection'}

            color_hex = params.get('color', None)
            use_foreground = params.get('foreground', False)
            use_background = params.get('background', False)

            original_foreground = view.foregroundColor()

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
                return {'success': False, 'message': 'No color specified'}

            view.setForeGroundColor(color)
            fill_action = app.action('fill_selection_foreground_color')
            if fill_action:
                fill_action.trigger()

            view.setForeGroundColor(original_foreground)
            doc.refreshProjection()

            color_desc = color_hex or ('foreground' if use_foreground else 'background')
            return {'success': True, 'message': f'Filled selection with {color_desc}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
