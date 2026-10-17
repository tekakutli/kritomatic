"""
Meta-commands for the Kritomatic daemon itself.

reload_daemon re-imports every kritomatic_daemon.* submodule so edits
to handler code take effect without restarting Krita.  It works by
swapping the handler instance behind the extension's handler box; the
server thread is left alone, and its signal connection to the box is
never touched.

What gets reloaded:
  - every kritomatic_daemon.* submodule EXCEPT the modules below
  - the command handler tree (rebuilt from disk)

What does NOT get reloaded:
  - kritomatic_daemon/__init__.py
      Its import side effect is addExtension() + addDockWidgetFactory(),
      both of which must run exactly once per Krita session.

  - kritomatic_daemon/kritomatic_daemon.py
      The extension class itself.  The live instance's methods are
      bound to this class, and reloading it would silently orphan the
      running instance.

  - kritomatic_daemon/panel.*
      Live QDockWidget instances reference the docker, renderer, and
      connection classes directly.  Dropping the modules would leave
      those instances pointing at stale class objects; new widget
      constructions from the factory would use old code, and any late
      attribute lookups would be confusing to debug.  The panel's
      *handler* (kritomatic_daemon.handlers.panel) is not kept — it is
      where provider logic lives, and where hot-reload matters most.

  - kritomatic_daemon.server.*
      The server subtree owns long-lived objects: a running QThread
      (WebSocketServer), a running EventServer thread, and — most
      importantly — the event_bus module-level `_bus` singleton.

      The event bus is the cooperative channel between publishers
      (ComfyUIHandler, PanelHandler) and subscribers (EventServer's
      per-client reader threads).  If the bus module were dropped and
      re-imported, publishers would write to a new `_bus` while every
      already-connected EventServer client stayed subscribed to the
      old one.  Events published by the freshly-built handlers would
      never reach any connected docker or web panel until that client
      disconnected and reconnected — which, on an idle healthy socket,
      they never do.  Silent, permanent staleness is the failure mode.

      Keeping this subtree means the new handlers and the old
      subscribers meet at the same `_bus`, `clear_listeners()` at the
      top of the reload correctly empties the listener list that the
      new handlers will repopulate, and every already-connected client
      sees the next published event as though nothing happened.

Side effects before the module drop
===================================
Two teardown steps must run *before* the module drop, against the
modules that are still loaded:

  1. Event-bus listener cleanup. The old PanelHandler subscribed to
     the bus in __init__; without this it keeps receiving events after
     being replaced, and `panel_rebuild` at the end of this function
     would publish to a duplicate listener.

  2. Shared ComfyUI client shutdown. `kritomatic_daemon.utils.comfyui_client`
     holds a module-level `_shared_client` whose `_watch_thread` is an
     open WebSocket reader against the running ComfyUI server. Dropping
     the module orphans that thread; the thread itself keeps running
     (it is a daemon thread) and keeps the connection alive. On the
     next comfy_connect, a second client is built and a second watcher
     is started; both stay open against ComfyUI indefinitely. Repeated
     reloads accumulate orphaned connections.

     `reset_client()` (from the same module) unwatches the client,
     joins the watcher thread up to 5 seconds, and clears `_shared_client`.
     After the drop, the new module's `_shared_client` starts as None,
     and the next `get_client()` builds a single fresh client. The
     watcher thread started by the old client is gone.

After the reload, the next `kritomatic` CLI invocation will see a
schema-version mismatch and auto-refresh its cache if the schema
changed.
"""

import sys
import traceback

from krita import Krita
from ..decorators import command, AutoDispatchHandler


class DaemonHandler(AutoDispatchHandler):

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
            # ---- Pre-drop teardown, against the modules still loaded ----

            # 1. Clear event-bus listeners. The old PanelHandler
            #    subscribed in its __init__; without this it keeps
            #    receiving bus events after being dropped, and the
            #    panel_rebuild at the end of this function publishes
            #    to a duplicate listener.
            try:
                from ..server.event_bus import get_event_bus
                get_event_bus().clear_listeners()
            except Exception:
                pass

            # 2. Shut down the shared ComfyUI client so its watcher
            #    thread stops. Without this, dropping the
            #    comfyui_client module orphans the thread and every
            #    subsequent comfy_connect opens a second WebSocket to
            #    ComfyUI. reset_client() is a no-op when the client
            #    was never built.
            try:
                from ..utils.comfyui_client import reset_client
                reset_client()
            except Exception:
                traceback.print_exc()

            # ---- Drop submodules ----

            keep = {
                'kritomatic_daemon',
                'kritomatic_daemon.kritomatic_daemon',
            }
            keep_prefixes = (
                'kritomatic_daemon.panel.',
                'kritomatic_daemon.server.',
            )
            keep_exact = {
                'kritomatic_daemon.panel',
                'kritomatic_daemon.server',
            }
            to_drop = []
            for name in list(sys.modules.keys()):
                if not name.startswith('kritomatic_daemon.'):
                    continue
                if name in keep:
                    continue
                if name in keep_exact:
                    continue
                if any(name.startswith(p) for p in keep_prefixes):
                    continue
                to_drop.append(name)
            for name in to_drop:
                del sys.modules[name]

            # ---- Fresh handler tree ----

            from kritomatic_daemon.handlers.base import CommandHandler
            new_handler = CommandHandler()

            # ---- Swap the handler behind the extension's box ----

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

        # Best-effort republish of the panel description, so any
        # already-connected docker or web panel sees a fresh panel
        # event immediately instead of waiting for the next state
        # mutation to trigger one. Failure here does not invalidate
        # the reload — the next user interaction will refresh them
        # anyway — so a raised exception is logged but the reload is
        # still reported as successful.
        try:
            new_handler.execute('panel_rebuild', {})
        except Exception:
            traceback.print_exc()

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
