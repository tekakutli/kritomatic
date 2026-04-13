from krita import *
from ..decorators import command

class MaskHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'add_selection_mask':
            return self.add_selection_mask(
                params.get('layer_name', ''),
                params.get('use_current_selection', False)
            )
        elif cmd_type == 'add_selection_mask_to_active':
            return self.add_selection_mask_to_active(
                params.get('use_current_selection', False)
            )
        return {'success': False, 'message': f'Unknown mask command: {cmd_type}'}

    @command(
        category='mask',
        help_text='Add a selection mask to a layer',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the target layer'},
            '--use_current_selection': {'type': 'bool', 'default': False, 'help': 'Use current global selection'}
        }
    )
    def add_selection_mask(self, layer_name, use_selection=False):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target = doc.nodeByName(layer_name)
            if not target:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original = doc.activeNode()
            doc.setActiveNode(target)

            # Create transparency mask first (stable)
            Krita.instance().action('add_new_transparency_mask').trigger()

            # Get the newly created mask
            new_mask = doc.activeNode()

            # Set the name BEFORE conversion
            desired_name = f"{layer_name}_selection"
            new_mask.setName(desired_name)

            if use_selection:
                selection = doc.selection()
                if selection and not selection.isNull():
                    new_mask.setPixelData(selection.pixelData())

            # Convert to selection mask
            Krita.instance().action('convert_to_selection_mask').trigger()

            # After conversion, the mask is still the active node
            # Set the name again in case conversion changed it
            doc.activeNode().setName(desired_name)

            doc.setActiveNode(original)
            doc.refreshProjection()

            return {'success': True, 'message': f'Added selection mask to "{layer_name}"', 'data': {'mask_name': desired_name}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='mask',
        help_text='Add a selection mask to the currently active layer',
        args={
            '--use_current_selection': {'type': 'bool', 'default': False, 'help': 'Use current global selection'}
        }
    )
    def add_selection_mask_to_active(self, use_selection=False):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            active = doc.activeNode()
            if not active:
                return {'success': False, 'message': 'No active layer'}
            return self.add_selection_mask(active.name(), use_selection)
        except Exception as e:
            return {'success': False, 'message': str(e)}
