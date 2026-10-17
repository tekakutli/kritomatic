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
from .comfyui import ComfyUIHandler
from .introspect import IntrospectHandler
from .daemon import DaemonHandler
from .panel import PanelHandler
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
            'comfyui': ComfyUIHandler(),
            'introspect': IntrospectHandler(),
            'daemon': DaemonHandler(),
            'panel': PanelHandler(),
        }

        # The panel handler builds its description from the comfyui
        # state. It learns that state two ways: bus events fire on every
        # state-mutating comfyui command (keeps it current during a
        # session), and this puller, which lets `panel_description`
        # fetch the current state on demand (covers the fresh-Krita case
        # where no command has fired yet).
        #
        # The lambda is deferred, so the comfyui handler only needs to
        # exist by the time the panel handler calls it, not now.
        self.handlers['panel'].set_comfy_status_puller(
            lambda: self.handlers['comfyui'].comfy_status({})
        )

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
                        # Defensive copy: the batch loop mutates its
                        # per-command dict below (adds `_batch_id`), and
                        # callers that reuse a bundle dict must not see
                        # their entries acquire keys they did not put
                        # there.
                        cmd = dict(cmd)

                        self._enter_batchmode_for_active_doc(seen_docs)

                        # Propagate the bundle id to each command as
                        # `_batch_id`, so handlers that need a per-batch
                        # identifier (notably comfy_generate, and by
                        # extension comfy_cancel targeting it) see the
                        # same id the caller used to open the batch.
                        # Commands that already carry their own
                        # `_batch_id` (the CLI sets one on every command
                        # in BatchExecutor._process_command) are left
                        # alone; the caller knows best.
                        if batch_id and '_batch_id' not in cmd:
                            cmd['_batch_id'] = batch_id

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
