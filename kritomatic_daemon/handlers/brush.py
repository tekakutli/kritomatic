from krita import *
from ..decorators import command

class BrushHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'set_brush_size':
            return self.set_brush_size(params.get('value', 50))
        elif cmd_type == 'set_brush_opacity':
            return self.set_brush_opacity(params.get('value', 100))
        elif cmd_type == 'set_brush_flow':
            return self.set_brush_flow(params.get('value', 100))
        elif cmd_type == 'set_brush_blending_mode':
            return self.set_brush_blending_mode(params.get('value', 'normal'))
        elif cmd_type == 'set_brush_preset':
            return self.set_brush_preset(params.get('value', ''))
        elif cmd_type == 'list_brush_presets':
            return self.list_brush_presets()
        elif cmd_type == 'get_brush_properties':
            return self.get_brush_properties()
        elif cmd_type == 'set_foreground_color':
            return self.set_foreground_color(params.get('color', '#000000'))
        elif cmd_type == 'set_background_color':
            return self.set_background_color(params.get('color', '#000000'))
        elif cmd_type == 'select_opaque':
            return self.select_opaque(params.get('mode', 'replace'))
        return {'success': False, 'message': f'Unknown brush command: {cmd_type}'}

    @command(
        category='brush',
        help_text='Set brush size in pixels',
        args={
            'value': {'type': 'int', 'required': True, 'help': 'Brush size in pixels (1-1000)'}
        }
    )
    def set_brush_size(self, size):
        try:
            view = Krita.instance().activeWindow().activeView()
            size = max(1, min(1000, size))
            view.setBrushSize(float(size))
            return {'success': True, 'message': f'Brush size set to {size}px', 'data': {'size': size}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Set brush opacity percentage',
        args={
            'value': {'type': 'int', 'required': True, 'help': 'Opacity percentage (0-100)'}
        }
    )
    def set_brush_opacity(self, opacity):
        try:
            view = Krita.instance().activeWindow().activeView()
            opacity = max(0, min(100, opacity))
            view.setPaintingOpacity(opacity / 100.0)
            return {'success': True, 'message': f'Brush opacity set to {opacity}%', 'data': {'opacity': opacity}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Set brush flow percentage',
        args={
            'value': {'type': 'int', 'required': True, 'help': 'Flow percentage (0-100)'}
        }
    )
    def set_brush_flow(self, flow):
        try:
            view = Krita.instance().activeWindow().activeView()
            flow = max(0, min(100, flow))
            view.setPaintingFlow(flow / 100.0)
            return {'success': True, 'message': f'Brush flow set to {flow}%', 'data': {'flow': flow}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Set brush blending mode',
        args={
            'value': {'type': 'str', 'required': True, 'help': 'Blending mode (normal, multiply, screen, etc.)'}
        }
    )
    def set_brush_blending_mode(self, mode):
        try:
            view = Krita.instance().activeWindow().activeView()
            view.setCurrentBlendingMode(mode.lower())
            return {'success': True, 'message': f'Blending mode set to {mode}', 'data': {'mode': mode}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Switch to a brush preset',
        args={
            'value': {'type': 'str', 'required': True, 'help': 'Brush preset name (partial match works)'}
        }
    )
    def set_brush_preset(self, preset_name):
        try:
            all_presets = Krita.instance().resources('preset')
            for name, resource in all_presets.items():
                if preset_name.lower() in name.lower():
                    view = Krita.instance().activeWindow().activeView()
                    view.setCurrentBrushPreset(resource)
                    return {'success': True, 'message': f'Switched to preset: {name}', 'data': {'preset': name}}
            return {'success': False, 'message': f'Preset "{preset_name}" not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='List all brush presets',
        args={}
    )
    def list_brush_presets(self):
        try:
            all_presets = Krita.instance().resources('preset')
            presets = list(all_presets.keys())
            return {'success': True, 'message': f'Found {len(presets)} presets', 'data': {'presets': presets[:50]}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Get current brush properties (opacity, flow, alpha lock)',
        args={}
    )
    def get_brush_properties(self):
        try:
            view = Krita.instance().activeWindow().activeView()
            props = {
                'opacity': view.paintingOpacity() * 100,
                'flow': view.paintingFlow() * 100,
                'alpha_lock': view.globalAlphaLock()
            }
            return {'success': True, 'message': 'Properties retrieved', 'data': props}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Set foreground color',
        args={
            'color': {'type': 'str', 'required': True, 'help': 'Hex color (e.g., #ff0000)'}
        }
    )
    def set_foreground_color(self, color_hex):
        try:
            view = Krita.instance().activeWindow().activeView()
            color_hex = color_hex.lstrip('#')
            r = int(color_hex[0:2], 16)
            g = int(color_hex[2:4], 16)
            b = int(color_hex[4:6], 16)
            current_color = view.foregroundColor()
            current_color.setComponents([r/255.0, g/255.0, b/255.0, 1.0])
            view.setForeGroundColor(current_color)
            return {'success': True, 'message': f'Foreground color set to #{color_hex}', 'data': {'hex': color_hex, 'rgb': (r, g, b)}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Set background color',
        args={
            'color': {'type': 'str', 'required': True, 'help': 'Hex color (e.g., #0000ff)'}
        }
    )
    def set_background_color(self, color_hex):
        try:
            view = Krita.instance().activeWindow().activeView()
            color_hex = color_hex.lstrip('#')
            r = int(color_hex[0:2], 16)
            g = int(color_hex[2:4], 16)
            b = int(color_hex[4:6], 16)
            current_color = view.backgroundColor()
            current_color.setComponents([r/255.0, g/255.0, b/255.0, 1.0])
            view.setBackGroundColor(current_color)
            return {'success': True, 'message': f'Background color set to #{color_hex}', 'data': {'hex': color_hex, 'rgb': (r, g, b)}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='brush',
        help_text='Select opaque pixels of current layer',
        args={
            'mode': {'type': 'str', 'default': 'replace', 'choices': ['replace', 'add', 'subtract', 'intersect'], 'help': 'Selection mode'}
        }
    )
    def select_opaque(self, mode='replace'):
        try:
            app = Krita.instance()
            action_name = 'selectopaque'
            if mode == 'add':
                action_name = 'selectopaque_add'
            elif mode == 'subtract':
                action_name = 'selectopaque_subtract'
            elif mode == 'intersect':
                action_name = 'selectopaque_intersect'
            action = app.action(action_name)
            if action:
                action.trigger()
                return {'success': True, 'message': f'Selected opaque pixels (mode: {mode})', 'data': {'mode': mode}}
            return {'success': False, 'message': f'Action not found: {action_name}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
