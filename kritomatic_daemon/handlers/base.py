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
from ..decorators import build_dispatch_map
from ..utils.refresh import begin_defer, end_defer


# Command names that reach a handler's execute method without being
# registered through @command.  These are legacy aliases: they work
# today because a handler's own if/elif chain happens to list them,
# but they do not appear in the auto-generated schema.  Preserved
# here so that callers using the older name keep working after the
# dispatch map replaced the top-level if/elif chain.
_ALIASES = {
    'add_to_palette': 'palette',
}


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
        # Map {command_name: handler_instance}, built by introspecting
        # the @command markers on every handler's methods (and, for
        # container handlers like LayerHandler, on their declared
        # sub-handlers).  Replaces the top-level if/elif chain that
        # used to live in _dispatch.  Rebuilt on every CommandHandler
        # construction — see handlers/daemon.py for the reload path
        # that relies on this.
        self._cmd_map = build_dispatch_map(self.handlers.values())

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

        handler = self._cmd_map.get(cmd_type)
        if handler is None:
            # Legacy aliases — command names that reach a handler's
            # execute method without having been registered via
            # @command, and so are absent from the auto-built map.
            alias_target = _ALIASES.get(cmd_type)
            if alias_target:
                handler = self.handlers.get(alias_target)
        if handler is None:
            return {'success': False,
                    'message': f'Unknown command type: {cmd_type}'}
        return handler.execute(cmd_type, command)
