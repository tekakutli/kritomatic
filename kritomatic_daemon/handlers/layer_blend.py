from krita import Krita
from ..decorators import command

class LayerBlendHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'create_blend_layer':
            return self.create_blend_layer(params)
        return {'success': False, 'message': f'Unknown blend command: {cmd_type}'}

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

            # Create new paint layer
            new_layer = doc.createNode(name, "paintlayer")

            # Insert above current layer
            current_layer.parentNode().addChildNode(new_layer, current_layer)

            # Set the blend mode
            new_layer.setBlendingMode(blend_mode.lower())

            # Make it active
            doc.setActiveNode(new_layer)
            doc.refreshProjection()

            return {
                'success': True,
                'message': f'Created layer "{name}" with blend mode {blend_mode}',
                'data': {'layer_name': name, 'blend_mode': blend_mode}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
