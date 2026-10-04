"""
Meta-commands for the Kritomatic daemon itself.

reload_daemon re-imports every kritomatic_daemon.* submodule so edits
to handler code take effect without restarting Krita. It works by
swapping the handler instance behind the extension's _HandlerBox; the
server thread is left alone. See kritomatic_daemon/kritomatic_daemon.py
for the box's definition and rationale.

What gets reloaded:
  - every kritomatic_daemon.* submodule EXCEPT the two below
  - the command handler tree

What does NOT get reloaded:
  - kritomatic_daemon/__init__.py
      Its import side effect is addExtension(), which registers the
      extension with Krita. Re-running it would double-register.
  - kritomatic_daemon/kritomatic_daemon.py
      The extension class itself. The live instance's methods are
      bound to this class, and reloading it would silently orphan the
      running instance.

  Edits to those two files still require a Krita restart.

After the reload, the next `kritomatic` CLI invocation will see a
schema-version mismatch and auto-refresh its cache if the schema
changed. If the edits only affect handler logic, no refresh occurs.
"""

import sys
import traceback

from krita import Krita
from ..decorators import command


class DaemonHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'reload_daemon':
            return self.reload_daemon(params)
        return {'success': False, 'message': f'Unknown daemon command: {cmd_type}'}

    @command(
        category='daemon',
        help_text='Reload the Kritomatic daemon in-place, picking up handler '
                  'edits without restarting Krita',
        args={}
    )
    def reload_daemon(self, params):
        ext = self._find_extension()
        if ext is None:
            return {'success': False,
                    'message': 'KritomaticDaemon extension not found'}

        if getattr(ext, '_reload_in_progress', False):
            return {'success': False,
                    'message': 'A reload is already in progress'}
        ext._reload_in_progress = True

        try:
            # 1. Drop submodules. Two must be preserved:
            #      kritomatic_daemon
            #          its __init__ calls addExtension, which must not
            #          run a second time
            #      kritomatic_daemon.kritomatic_daemon
            #          the extension class; the live instance is bound
            #          to it
            keep = {'kritomatic_daemon', 'kritomatic_daemon.kritomatic_daemon'}
            to_drop = [
                name for name in list(sys.modules.keys())
                if name.startswith('kritomatic_daemon.') and name not in keep
            ]
            for name in to_drop:
                del sys.modules[name]

            # 2. Fresh handler tree. Importing base re-imports every
            #    handler module transitively, so the new tree reflects
            #    whatever is on disk right now.
            from kritomatic_daemon.handlers.base import CommandHandler
            new_handler = CommandHandler()

            # 3. Wire it in. On the first reload, the server's signal is
            #    still connected to the startup handler's bound method;
            #    we detach that and attach the box. On every subsequent
            #    reload, the box is already in place and we just swap
            #    the handler behind it.
            if not hasattr(ext, '_handler_box'):
                old_handler = getattr(ext, 'handler', None)
                server = getattr(ext, 'server', None)
                if server is None:
                    ext._reload_in_progress = False
                    return {'success': False,
                            'message': 'Daemon server is not running; cannot reload.'}

                # Build the box on the extension if it isn't there yet.
                # It is normally created in KritomaticDaemon.__init__;
                # this branch only fires if someone is running an older
                # extension class that predates the box.
                from kritomatic_daemon.kritomatic_daemon import _HandlerBox
                box = _HandlerBox(new_handler)

                # Detach the direct connection to the old handler.
                # PyQt compares bound methods by (__self__, __func__),
                # so a fresh `old_handler.handle_command` matches the
                # one stored at connect time. Fall back to clearing
                # all connections if the per-callable form is rejected.
                if old_handler is not None:
                    try:
                        server.command_received.disconnect(old_handler.handle_command)
                    except (TypeError, RuntimeError):
                        try:
                            server.command_received.disconnect()
                        except (TypeError, RuntimeError):
                            pass

                server.command_received.connect(box.dispatch)
                ext._handler_box = box
            else:
                ext._handler_box.handler = new_handler

            # Keep ext.handler in sync for any external reader.
            ext.handler = new_handler

        except Exception as e:
            ext._reload_in_progress = False
            traceback.print_exc()
            return {'success': False, 'message': f'Reload failed: {e}'}

        ext._reload_in_progress = False
        print(f"✅ Kritomatic daemon reloaded ({len(to_drop)} module(s) dropped)")
        return {
            'success': True,
            'message': f'Reloaded: dropped {len(to_drop)} module(s), '
                       f'rebuilt handler tree. Server not restarted.'
        }

    def _find_extension(self):
        for e in Krita.instance().extensions():
            if type(e).__name__ == 'KritomaticDaemon':
                return e
        return None
