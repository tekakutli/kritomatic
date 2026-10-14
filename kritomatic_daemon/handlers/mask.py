from krita import *
from ..decorators import command, AutoDispatchHandler
from ..utils.layer_resolve import resolve as resolve_node, register as register_node


class MaskHandler(AutoDispatchHandler):

    @command(
        category='mask',
        help_text='Add a selection mask to a layer',
        args={
            '--layer_name': {'type': 'str', 'required': True, 'help': 'Name of the target layer'},
            '--use_current_selection': {'type': 'bool', 'default': False, 'help': 'Use current global selection'}
        },
        arg_map={'use_current_selection': 'use_selection'},
    )
    def add_selection_mask(self, layer_name, use_selection=False):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            target = resolve_node(doc, layer_name)
            if not target:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}

            original = doc.activeNode()
            doc.setActiveNode(target)

            Krita.instance().action('add_new_transparency_mask').trigger()
            new_mask = doc.activeNode()

            desired_name = f"{layer_name}_selection"
            new_mask.setName(desired_name)

            if use_selection:
                selection = doc.selection()
                if selection and not selection.isNull():
                    new_mask.setPixelData(selection.pixelData())

            Krita.instance().action('convert_to_selection_mask').trigger()

            doc.activeNode().setName(desired_name)
            register_node(desired_name, doc.activeNode())

            doc.setActiveNode(original)
            doc.refreshProjection()

            return {'success': True,
                    'message': f'Added selection mask to "{layer_name}"',
                    'data': {'mask_name': desired_name}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='mask',
        help_text='Add a selection mask to the currently active layer',
        args={
            '--use_current_selection': {'type': 'bool', 'default': False, 'help': 'Use current global selection'}
        },
        arg_map={'use_current_selection': 'use_selection'},
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
