"""
Meta-commands for the Kritomatic daemon itself.

reload_daemon re-imports every kritomatic_daemon.* submodule so edits
to handler code take effect without restarting Krita.  It works by
swapping the handler instance behind the extension's handler box; the
server thread is left alone, and its signal connection to the box is
never touched.

What gets reloaded:
  - every kritomatic_daemon.* submodule EXCEPT the two below
  - the command handler tree (rebuilt from disk)

What does NOT get reloaded:
  - kritomatic_daemon/__init__.py
      Its import side effect is addExtension(), which registers the
      extension with Krita. Re-running it would double-register.
  - kritomatic_daemon/kritomatic_daemon.py
      The extension class itself.  The live instance's methods are
      bound to this class, and reloading it would silently orphan the
      running instance.  The _HandlerBox class lives here too, and
      the box instance created at startup stays alive across reloads.

After the reload, the next `kritomatic` CLI invocation will see a
schema-version mismatch and auto-refresh its cache if the schema
changed.  If the edits only affect handler logic, no refresh occurs.

A note on the previous implementation
=====================================
Earlier versions of this file tried to attach a NEW _HandlerBox to
the extension, using `ext._handler_box` as the marker for "have we
done this already".  The extension stored its box on `ext.handler_box`
(no underscore), so the marker was never seen and every reload created
another box, which was then also connected to the server's signal.
The old box stayed connected too, so commands ran twice and edits
appeared not to take effect.  The fix is to reuse the single box the
extension owns and swap only its `.handler` attribute.
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
            #          to it, and the _HandlerBox class lives here
            keep = {'kritomatic_daemon', 'kritomatic_daemon.kritomatic_daemon'}
            to_drop = [
                name for name in list(sys.modules.keys())
                if name.startswith('kritomatic_daemon.') and name not in keep
            ]
            for name in to_drop:
                del sys.modules[name]

            # 2. Fresh handler tree. Importing base re-imports every
            #    handler module transitively, so the new tree reflects
            #    whatever is on disk right now.  The @command decorators
            #    re-register themselves against the freshly-imported
            #    registry as a side effect.
            from kritomatic_daemon.handlers.base import CommandHandler
            new_handler = CommandHandler()

            # 3. Swap the handler behind the extension's box.  The box
            #    was created in KritomaticDaemon.__init__ and its
            #    `dispatch` method is what the server's signal has been
            #    connected to since startup.  Replacing `.handler`
            #    inside it means the next command the signal dispatches
            #    goes through the new tree; nothing else needs to move.
            box = getattr(ext, 'handler_box', None)
            if box is None:
                ext._reload_in_progress = False
                return {'success': False,
                        'message': 'Extension has no handler_box; a restart '
                                   'is required once to bring the running '
                                   'instance in line with the current '
                                   'kritomatic_daemon.py.'}
            box.handler = new_handler

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
