import json
import hashlib

from krita import Krita

from .brush import BrushHandler
from .layer import LayerHandler
from .palette import PaletteHandler
from .mask import MaskHandler
from .document import DocumentHandler
from .view import ViewHandler
from .window import WindowHandler
from .diffusion import DiffusionHandler
from .introspect import IntrospectHandler
from .daemon import DaemonHandler
from ..registry import get_command_registry
from ..utils.refresh import begin_defer, end_defer


class CommandHandler:
    def __init__(self):
        self.handlers = {
            'brush': BrushHandler(),
            'layer': LayerHandler(),
            'palette': PaletteHandler(),
            'mask': MaskHandler(),
            'document': DocumentHandler(),
            'view': ViewHandler(),
            'window': WindowHandler(),
            'diffusion': DiffusionHandler(),
            'introspect': IntrospectHandler(),
            'daemon': DaemonHandler()
        }

    def _send_line(self, client_socket, payload):
        try:
            data = (json.dumps(payload) + '\n').encode('utf-8')
            client_socket.sendall(data)
        except Exception:
            pass

    def _enter_batchmode_for_active_doc(self, seen):
        doc = Krita.instance().activeDocument()
        if doc is None:
            return
        key = id(doc)
        if key in seen:
            try:
                doc.setBatchmode(True)
            except Exception:
                pass
            return
        try:
            prev = doc.batchmode()
        except Exception:
            prev = False
        seen[key] = (doc, prev)
        try:
            doc.setBatchmode(True)
        except Exception:
            pass

    def _slim_results(self, results):
        return [
            {k: v for k, v in r.items() if k != 'data'}
            for r in results
        ]

    def handle_command(self, command, client_socket):
        try:
            if 'commands' in command:
                batch_id = command.get('id', None)
                commands = command.get('commands', [])
                total = len(commands)
                results = []

                begin_defer()
                seen_docs = {}
                summary_sent = False
                try:
                    for i, cmd in enumerate(commands):
                        self._enter_batchmode_for_active_doc(seen_docs)

                        cmd_type = cmd.get('type')

                        try:
                            result = self._dispatch(cmd)
                            ok = bool(result.get('success'))
                            entry = {
                                'index': i,
                                'command': cmd_type,
                                'status': 'success' if ok else 'error',
                                'message': result.get('message', ''),
                                'data': result.get('data', None),
                            }
                        except Exception as e:
                            entry = {
                                'index': i,
                                'command': cmd_type,
                                'status': 'error',
                                'message': f'Processing error: {e}',
                                'data': None,
                            }

                        results.append(entry)

                        self._send_line(client_socket, {
                            'type': 'progress',
                            'index': i,
                            'total': total,
                            'result': entry,
                        })

                    summary = {
                        'type': 'batch_complete',
                        'status': 'batch_complete',
                        'total': total,
                        'successful': sum(1 for r in results if r['status'] == 'success'),
                        'failed': sum(1 for r in results if r['status'] == 'error'),
                        'results': self._slim_results(results),
                    }
                    if batch_id:
                        summary['id'] = batch_id
                    self._send_line(client_socket, summary)
                    summary_sent = True
                finally:
                    try:
                        end_defer()
                    except Exception:
                        pass

                    for doc, prev in seen_docs.values():
                        try:
                            doc.setBatchmode(prev)
                        except Exception:
                            pass

                    if not summary_sent:
                        self._send_line(client_socket, {
                            'type': 'batch_complete',
                            'status': 'batch_complete',
                            'total': total,
                            'successful': sum(1 for r in results if r['status'] == 'success'),
                            'failed': sum(1 for r in results if r['status'] == 'error'),
                            'results': self._slim_results(results),
                            'error': 'Batch loop aborted before completion',
                        })

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
                self._send_line(client_socket, response)

        except Exception as e:
            self._send_line(client_socket, {
                'status': 'error',
                'message': f'Processing error: {str(e)}'
            })

    def _dispatch(self, command):
        cmd_type = command.get('type')

        if cmd_type == 'get_schema':
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
                          'embed_image_as_layer', 'import_kra_as_group',
                          'fill_layer', 'fill_selection', 'move_layer_to_new_document',
                          'export_layer_to_file', 'apply_color_to_alpha', 'add_color_to_alpha_mask',
                          'create_transform_mask', 'transform_mask',
                          'set_perspective_transform_mask', 'fit_to_canvas']:
            return self.handlers['layer'].execute(cmd_type, command)

        # Vector shapes: text and polygons
        elif cmd_type in ['add_vector_text', 'add_vector_polygon',
                          'update_vector_text', 'list_shapes', 'replace_all_text',
                          'extract_all_text']:
            return self.handlers['layer'].execute(cmd_type, command)

        # Palette commands
        elif cmd_type in ['add_to_palette', 'create_palette', 'activate_palette', 'list_palettes']:
            return self.handlers['palette'].execute(cmd_type, command)

        # Mask commands
        elif cmd_type in ['add_selection_mask', 'add_selection_mask_to_active']:
            return self.handlers['mask'].execute(cmd_type, command)

        # Document commands
        elif cmd_type in ['get_current_dimensions', 'create_new_from_current',
                          'create_new_with_dimensions', 'get_all_documents', 'save_document',
                          'open_document', 'close_document', 'export_document',
                          'export_file_to_image', 'rotate_document', 'rotate_kra_file']:
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

        # Introspect commands
        elif cmd_type in ['list_actions', 'list_filters', 'list_resources',
                          'list_dockers', 'describe_active', 'list_extensions',
                          'get_node_xml', 'describe_vector_layer']:
            return self.handlers['introspect'].execute(cmd_type, command)

        # Daemon meta-commands
        elif cmd_type in ['reload_daemon']:
            return self.handlers['daemon'].execute(cmd_type, command)

        else:
            return {'success': False, 'message': f'Unknown command type: {cmd_type}'}
