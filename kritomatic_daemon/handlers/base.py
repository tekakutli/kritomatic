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
from .comfyui import ComfyUIHandler
from .introspect import IntrospectHandler
from .daemon import DaemonHandler
from ..registry import get_command_registry
from ..decorators import AutoDispatchHandler
from ..utils.refresh import begin_defer, end_defer
from ..utils.layer_resolve import begin_batch, end_batch, set_scope


class CommandHandler(AutoDispatchHandler):
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
            'comfyui': ComfyUIHandler(),
            'introspect': IntrospectHandler(),
            'daemon': DaemonHandler()
        }
        self._sub_handlers = list(self.handlers.values())
        super().__init__()

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
                begin_batch()
                seen_docs = {}
                summary_sent = False
                try:
                    for i, cmd in enumerate(commands):
                        self._enter_batchmode_for_active_doc(seen_docs)

                        # Each command carries a scope tag; the executor
                        # assigns it. Commands without one (a bare
                        # single-command payload, or a caller that did
                        # not tag) get None, which disables scope
                        # resolution and falls back to name/UUID lookup.
                        set_scope(cmd.get('_scope'))

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

                    try:
                        end_batch()
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
            return {
                'success': True,
                'message': 'Command registry retrieved',
                'version': version,
                'data': registry,
            }

        return self.execute(cmd_type, command)
