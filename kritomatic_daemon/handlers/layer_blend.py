from krita import Krita
from ..utils.refresh import refresh
from ..utils.layer_resolve import resolve as resolve_node, register as register_node
from ..decorators import command, AutoDispatchHandler


class LayerBlendHandler(AutoDispatchHandler):

    @command(
        category='layer',
        help_text='Create a new layer with blend mode above current layer',
        args={
            '--name': {'type': 'str', 'required': True, 'help': 'Layer name'},
            '--blend_mode': {'type': 'str', 'required': True,
                           'choices': ['normal', 'multiply', 'screen', 'overlay', 'add',
                                      'difference', 'color', 'saturation', 'hue', 'luminosity'],
                           'help': 'Blending mode for the layer'}
        }
    )
    def create_blend_layer(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            name = params.get('name', 'Blend Layer')
            blend_mode = params.get('blend_mode', 'normal')

            current_layer = doc.activeNode()
            if not current_layer:
                return {'success': False, 'message': 'No active layer'}

            new_layer = doc.createNode(name, "paintlayer")
            current_layer.parentNode().addChildNode(new_layer, current_layer)
            new_layer.setBlendingMode(blend_mode.lower())

            doc.setActiveNode(new_layer)
            register_node(name, new_layer)
            refresh(doc)

            return {
                'success': True,
                'message': f'Created layer "{name}" with blend mode {blend_mode}',
                'data': {'layer_name': name, 'blend_mode': blend_mode,
                         'node_uuid': new_layer.uniqueId().toString()}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='layer',
        help_text='Set the blend mode of an existing layer',
        args={
            '--layer_name': {'type': 'str', 'default': '',
                             'help': 'Target layer name; empty = active layer'},
            '--blend_mode': {'type': 'str', 'required': True,
                             'choices': ['normal', 'multiply', 'screen', 'overlay', 'add',
                                        'difference', 'color', 'saturation', 'hue', 'luminosity'],
                             'help': 'Blending mode to apply'}
        }
    )
    def set_layer_blend_mode(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            layer_name = params.get('layer_name', '')
            blend_mode = params.get('blend_mode', 'normal').lower()

            if layer_name:
                target = resolve_node(doc, layer_name)
                if not target:
                    return {'success': False,
                            'message': f'Layer "{layer_name}" not found'}
            else:
                target = doc.activeNode()
                if not target:
                    return {'success': False, 'message': 'No active layer'}

            target.setBlendingMode(blend_mode)
            refresh(doc)

            return {
                'success': True,
                'message': f'Blend mode of "{target.name()}" set to {blend_mode}',
                'data': {'layer_name': target.name(),
                         'blend_mode': blend_mode}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
