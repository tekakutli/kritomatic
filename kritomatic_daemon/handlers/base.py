import json
from .brush import BrushHandler
from .layer import LayerHandler
from .palette import PaletteHandler
from .mask import MaskHandler
from .transform import TransformHandler
from .document import DocumentHandler


class CommandHandler:
    def __init__(self):
        self.handlers = {
            'brush': BrushHandler(),
            'layer': LayerHandler(),
            'palette': PaletteHandler(),
            'mask': MaskHandler(),
            'transform': TransformHandler(),
            'document': DocumentHandler()
        }

    def handle_command(self, command, client_socket):
        try:
            if 'commands' in command:
                results = []
                batch_id = command.get('id', None)

                for i, cmd in enumerate(command['commands']):
                    cmd_type = cmd.get('type')
                    result = self._dispatch(cmd)
                    results.append({
                        'index': i,
                        'command': cmd_type,
                        'status': 'success' if result.get('success') else 'error',
                        'message': result.get('message', ''),
                        'data': result.get('data', None)
                    })

                response = {
                    'status': 'batch_complete',
                    'total': len(results),
                    'successful': sum(1 for r in results if r['status'] == 'success'),
                    'failed': sum(1 for r in results if r['status'] == 'error'),
                    'results': results
                }
                if batch_id:
                    response['id'] = batch_id

            else:
                cmd_type = command.get('type')
                result = self._dispatch(command)
                response = {
                    'status': 'success' if result.get('success') else 'error',
                    'command': cmd_type,
                    'message': result.get('message', ''),
                    'data': result.get('data', None)
                }

            try:
                client_socket.send(json.dumps(response).encode('utf-8'))
            except:
                pass

        except Exception as e:
            error_response = {
                'status': 'error',
                'message': f'Processing error: {str(e)}'
            }
            try:
                client_socket.send(json.dumps(error_response).encode('utf-8'))
            except:
                pass

    def _dispatch(self, command):
        """Route command to appropriate handler"""
        cmd_type = command.get('type')

        # Brush commands
        if cmd_type in ['set_brush_size', 'set_brush_opacity', 'set_brush_flow',
                        'set_brush_blending_mode', 'set_brush_preset', 'list_brush_presets',
                        'get_brush_properties', 'set_foreground_color', 'set_background_color',
                        'select_opaque']:
            return self.handlers['brush'].execute(cmd_type, command)

        # Layer commands
        elif cmd_type in ['create_layer', 'list_layers', 'set_active_layer', 'rename_active_layer',
                          'rename_layer_by_name', 'move_layer_to_group', 'move_active_layer_to_group',
                          'create_file_layer', 'create_blend_layer', 'fill_layer', 'fill_selection',
                          'add_vector_text', 'update_vector_text', 'list_shapes', 'replace_all_text',
                          'move_layer_to_new_document']:
            return self.handlers['layer'].execute(cmd_type, command)

        # Palette commands
        elif cmd_type in ['add_to_palette', 'create_palette', 'activate_palette', 'list_palettes']:
            return self.handlers['palette'].execute(cmd_type, command)

        # Mask commands
        elif cmd_type in ['add_selection_mask', 'add_selection_mask_to_active']:
            return self.handlers['mask'].execute(cmd_type, command)

        # Transform commands
        elif cmd_type in ['create_transform_mask', 'transform_mask']:
            return self.handlers['transform'].execute(cmd_type, command)

        # Document commands
        elif cmd_type in ['get_current_dimensions', 'create_new_from_current',
                          'create_new_with_dimensions', 'get_all_documents', 'save_document']:
            return self.handlers['document'].execute(cmd_type, command)

        else:
            return {'success': False, 'message': f'Unknown command type: {cmd_type}'}
