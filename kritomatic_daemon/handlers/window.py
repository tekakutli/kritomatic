import json

from krita import Krita
from ..decorators import command, AutoDispatchHandler


# Dynamic Qt property on the main window holding the remembered set of
# docker visibility, as a JSON string {objectName: bool}.  Presence of a
# non-empty value means "these dockers were hidden by
# toggle_dockers_only, and are still hidden".  Showing clears it.  This
# is the state flag: the existence of the property IS the answer to
# "did we hide them, or did something else?".
#
# The property lives on the QMainWindow, not on the handler, so it is
# per-window and survives reload_daemon.  It does not survive a Krita
# restart, which is correct: the workspace system restores docker
# visibility independently.
_DOCKER_STATE_PROP = "_kritomatic_docker_state_before_hiding"


class WindowHandler(AutoDispatchHandler):

    # ------------------------------------------------------------------
    #  Internal helpers
    # ------------------------------------------------------------------

    def _show_saved_dockers(self, qwin, dockers):
        """Restore the dockers recorded in _DOCKER_STATE_PROP.

        Returns the number of dockers shown.  Clears the property.  If
        no state is recorded, this is a no-op returning 0, so callers
        can invoke it unconditionally.  A recorded state that fails to
        parse is treated as "no state" for robustness; the property is
        still cleared so the flag cannot get stuck.
        """
        saved_raw = qwin.property(_DOCKER_STATE_PROP)
        if not saved_raw:
            return 0

        try:
            saved = json.loads(str(saved_raw))
        except Exception:
            saved = None

        qwin.setProperty(_DOCKER_STATE_PROP, '')

        restored = 0
        for d in dockers:
            show = True if saved is None else bool(saved.get(d.objectName(), False))
            if not show:
                continue
            try:
                d.setVisible(True)
                restored += 1
            except Exception:
                pass
        return restored

    # ------------------------------------------------------------------
    #  Commands
    # ------------------------------------------------------------------

    @command(
        category='window',
        help_text='Toggle detached canvas mode',
        args={}
    )
    def toggle_detached(self):
        """Toggle detached canvas mode"""
        try:
            app = Krita.instance()
            action = app.action('view_detached_canvas')
            if action:
                action.trigger()
                return {'success': True, 'message': 'Toggled detached canvas mode'}
            return {'success': False, 'message': 'Detached canvas action not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Toggle fullscreen mode',
        args={}
    )
    def toggle_fullscreen(self):
        """Toggle fullscreen mode"""
        try:
            app = Krita.instance()
            action = app.action('fullscreen')
            if action:
                action.trigger()
                return {'success': True, 'message': 'Toggled fullscreen mode'}
            return {'success': False, 'message': 'Fullscreen action not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Toggle dockers visibility via Krita\'s Show Dockers action',
        args={}
    )
    def toggle_dockers(self):
        """Toggle all dockers on/off via Krita's view_toggledockers action.

        Delegates to the view_toggledockers action, which is the same
        action bound to Settings -> Dockers -> Show Dockers.  That
        action calls KisMainWindow::toggleDockersVisibility(), which
        saves and restores the *entire* QMainWindow state via
        saveState()/restoreState() -- toolbars included.  The result is
        that this command can change toolbar visibility as a side
        effect (KDE bug 435300).  Use toggle_dockers_only for a
        docker-scoped toggle that never touches toolbars.
        """
        try:
            app = Krita.instance()
            action = app.action('view_toggledockers')
            if action:
                action.trigger()
                return {'success': True, 'message': 'Toggled dockers visibility'}
            return {'success': False, 'message': 'Toggle dockers action not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Toggle docker visibility without affecting toolbars, '
                  'mirroring Settings -> Show Dockers.  Like that action, '
                  'hiding remembers exactly which dockers were visible and '
                  'showing restores only those.  Unlike that action, '
                  'toolbars are never saved or restored, so toolbar '
                  'visibility is left untouched.',
        args={}
    )
    def toggle_dockers_only(self):
        """Toggle visibility of all dockers, scoped to dockers only.

        Direction is decided by the recorded state, not by which
        dockers happen to be visible right now.  If the state property
        is set, we last hid the dockers and this call shows them
        (restoring exactly the ones that were visible then).  If it is
        not set, we either hide the currently-visible dockers and
        record them, or -- if nothing is visible and no state is
        recorded, e.g. the dockers were hidden by some other means --
        show them all as a fallback.
        """
        try:
            app = Krita.instance()
            win = app.activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()
            if qwin is None:
                return {'success': False,
                        'message': 'Active window has no qwindow'}

            dockers = list(win.dockers())
            if not dockers:
                return {'success': False, 'message': 'No dockers found'}

            we_hid_them = bool(qwin.property(_DOCKER_STATE_PROP))
            any_visible = any(d.isVisible() for d in dockers)

            if we_hid_them or not any_visible:
                # SHOW path.
                if we_hid_them:
                    restored = self._show_saved_dockers(qwin, dockers)
                else:
                    # Hidden by something other than this command; no
                    # remembered set to restore from, so show them all.
                    restored = 0
                    for d in dockers:
                        try:
                            d.setVisible(True)
                            restored += 1
                        except Exception:
                            pass

                return {
                    'success': True,
                    'message': f'Restored {restored} docker(s)',
                    'data': {'visible': True, 'affected': restored},
                }

            # HIDE path: record who is visible, then hide everyone.
            state = {}
            for d in dockers:
                try:
                    state[d.objectName()] = bool(d.isVisible())
                except Exception:
                    pass
            qwin.setProperty(_DOCKER_STATE_PROP, json.dumps(state))

            hidden = 0
            for d in dockers:
                try:
                    d.setVisible(False)
                    hidden += 1
                except Exception:
                    pass

            return {
                'success': True,
                'message': f'Hid {hidden} docker(s)',
                'data': {'visible': False, 'affected': hidden},
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Bring a docker to the front of its tab group.  If the '
                  'docker is hidden entirely, it is shown first.  If the '
                  'dockers are currently hidden by toggle_dockers_only, '
                  'they are all restored first so the focus does not '
                  'strand the other dockers.  Matches by internal object '
                  'name, then by displayed window title, '
                  'case-insensitively; run introspect list_dockers to see '
                  'the available names.',
        args={
            '--name': {'type': 'str', 'required': True,
                       'help': 'Docker object name or displayed window '
                               'title'},
        }
    )
    def focus_docker(self, params):
        """Raise a docker within its tab group.

        If the dockers were previously hidden by toggle_dockers_only,
        restore the remembered set first: without this step, showing
        one docker would leave the rest hidden and, worse, leave the
        remembered set stale -- the next toggle_dockers_only would
        record only the one docker we happened to show.

        Otherwise, focus is the tab-flip operation: if the docker
        shares a tab group with others, it becomes the visible tab;
        if it sits alone in its dock area, the call is a no-op
        visually.  If the docker was hidden, setVisible(True) brings
        it back into the layout before raise_() promotes its tab.

        Matching is exact (case-insensitive) against the object name
        first, then the window title.  The two identifiers usually
        differ: object name is the stable internal id
        ("ColorSelectorNg"), window title is the user-facing label
        ("Advanced Color Selector").  Either works; if a title is
        ambiguous across dockers, the command errors rather than
        guess.
        """
        try:
            win = Krita.instance().activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()

            key = (params.get('name') or '').strip()
            if not key:
                return {'success': False, 'message': 'No docker name given'}
            key_lower = key.lower()

            dockers = list(win.dockers())

            # If dockers are currently hidden by toggle_dockers_only,
            # restore them all first.  This is idempotent and cheap
            # when no state is recorded.
            restored = 0
            if qwin is not None:
                restored = self._show_saved_dockers(qwin, dockers)

            by_name = []
            by_title = []
            for d in dockers:
                oname = ''
                title = ''
                try:
                    oname = d.objectName() or ''
                except Exception:
                    pass
                try:
                    title = d.windowTitle() or ''
                except Exception:
                    pass
                if oname.lower() == key_lower:
                    by_name.append(d)
                elif title.lower() == key_lower:
                    by_title.append(d)

            matches = by_name or by_title
            if not matches:
                return {
                    'success': False,
                    'message': f'No docker matches "{key}".  Run '
                               f'introspect list_dockers to see the '
                               f'available names.',
                    'data': {'restored_before_focus': restored},
                }
            if len(matches) > 1:
                names = ', '.join(repr(m.objectName()) for m in matches)
                return {
                    'success': False,
                    'message': f'"{key}" matches {len(matches)} dockers: '
                               f'{names}.  Use an object name to '
                               f'disambiguate.',
                    'data': {'restored_before_focus': restored},
                }

            target = matches[0]
            # setVisible first: if the docker is hidden, raise_() on a
            # hidden widget is a no-op and the tab never appears.  The
            # call is harmless when the docker is already visible.
            target.setVisible(True)
            target.raise_()

            try:
                title = target.windowTitle() or ''
            except Exception:
                title = ''

            msg = f'Focused docker "{target.objectName()}"'
            if restored:
                msg += f' (restored {restored} docker(s) first)'

            return {
                'success': True,
                'message': msg,
                'data': {
                    'name': target.objectName(),
                    'title': title,
                    'restored_before_focus': restored,
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Toggle docker titlebars visibility',
        args={}
    )
    def toggle_docker_titles(self):
        """Toggle docker titlebars on/off"""
        try:
            app = Krita.instance()
            action = app.action('view_toggledockertitlebars')
            if action:
                action.trigger()
                return {'success': True, 'message': 'Toggled docker titlebars visibility'}
            return {'success': False, 'message': 'Toggle docker titles action not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Open a new window for the current document',
        args={}
    )
    def new_window(self):
        """Open a new window for the current document"""
        try:
            app = Krita.instance()
            action = app.action('view_newwindow')
            if action:
                action.trigger()
                return {'success': True, 'message': 'Opened new window'}
            return {'success': False, 'message': 'New window action not found'}
        except Exception as e:
            return {'success': False, 'message': str(e)}
