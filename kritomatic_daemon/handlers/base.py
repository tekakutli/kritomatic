import json
import hashlib
from .brush import BrushHandler
from .layer import LayerHandler
from .palette import PaletteHandler
from .mask import MaskHandler
from .transform import TransformHandler
from .document import DocumentHandler
from .view import ViewHandler
from .window import WindowHandler
from .diffusion import DiffusionHandler
from ..registry import get_command_registry

class CommandHandler:
    def __init__(self):
        self.handlers = {
            'brush': BrushHandler(),
            'layer': LayerHandler(),
            'palette': PaletteHandler(),
            'mask': MaskHandler(),
            'transform': TransformHandler(),
            'document': DocumentHandler(),
            'view': ViewHandler(),
            'window': WindowHandler(),
            'diffusion': DiffusionHandler()
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
                if cmd_type == 'get_schema' and 'version' in result:
                    response['version'] = result['version']
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

        if cmd_type == 'get_schema':
            import hashlib
            import json
            from ..registry import get_command_registry

            registry = get_command_registry()
            registry_str = json.dumps(registry, sort_keys=True)
            version = hashlib.md5(registry_str.encode()).hexdigest()[:8]
            result = {
                    'success': True,
                    'message': 'Command registry retrieved',
                    'version': version,
                    'data': registry
            }
            return result

        # Brush commands
        if cmd_type in ['set_brush_size', 'set_brush_opacity', 'set_brush_flow',
                        'set_brush_blending_mode', 'set_brush_preset', 'list_brush_presets',
                        'get_brush_properties', 'set_foreground_color', 'set_background_color',
                        'select_opaque']:
            return self.handlers['brush'].execute(cmd_type, command)

        # Layer commands (non-text)
        elif cmd_type in ['create_layer', 'list_layers', 'set_active_layer', 'rename_active_layer',
                          'rename_layer_by_name', 'move_layer_to_group', 'move_active_layer_to_group',
                          'create_file_layer', 'convert_to_file_layer', 'create_blend_layer',
                          'fill_layer', 'fill_selection', 'move_layer_to_new_document',
                          'export_layer_to_file', 'apply_color_to_alpha', 'add_color_to_alpha_mask',
                          'create_transform_mask', 'transform_mask']:
            return self.handlers['layer'].execute(cmd_type, command)

        # Text commands
        elif cmd_type in ['add_vector_text', 'update_vector_text', 'list_shapes', 'replace_all_text',
                          'extract_all_text']:
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

        # View commands
        elif cmd_type in ['push', 'pop', 'toggle', 'fit', 'fit_width', 'fit_height',
                          'zoom_to', 'zoom_in', 'zoom_out', 'reset', 'get_state']:
            return self.handlers['view'].execute(cmd_type, command)

        # Window commands
        elif cmd_type in ['toggle_detached', 'toggle_fullscreen', 'toggle_dockers',
                          'toggle_docker_titles', 'new_window']:
            return self.handlers['window'].execute(cmd_type, command)

        # Diffusion commands
        elif cmd_type in ['list_workflows', 'switch_workflow', 'get_params', 'set_param', 'generate', 'export_params', 'import_params']:
            return self.handlers['diffusion'].execute(cmd_type, command)

        else:
            return {'success': False, 'message': f'Unknown command type: {cmd_type}'}
