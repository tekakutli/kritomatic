import json
import os
import time

from krita import Krita
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDockWidget,
    QScrollArea,
    QSizePolicy,
    QWidget,
)

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

    def _side_to_area(self, side):
        """Map a side string to a Qt dock area, or None."""
        if side == 'left':
            return Qt.DockWidgetArea.LeftDockWidgetArea
        if side == 'right':
            return Qt.DockWidgetArea.RightDockWidgetArea
        return None

    @staticmethod
    def _tabify_group(qwin, members, leader_visible):
        """Tabify a list of dockers into one group, respecting the
        desired final visibility.

        The order of operations here is not optional.  Qt's
        QMainWindow::tabifyDockWidget() is a silent no-op when either
        argument is hidden -- there is no visible tab to merge the
        widget into -- so every member must be shown before the
        tabification calls are issued.  The group's real visibility is
        applied afterwards.

        For a single-member group this reduces to setting visibility
        on the one docker.
        """
        if not members:
            return

        # 1. Move to a known starting state: every member visible.
        for d in members:
            try:
                d.setVisible(True)
            except Exception:
                pass

        # 2. Tabify.  leader is the first member.
        if len(members) >= 2:
            lead = members[0]
            for follower in members[1:]:
                try:
                    qwin.tabifyDockWidget(lead, follower)
                except Exception:
                    pass

        # 3. Apply the group's intended visibility.  Showing the
        #    leader plus raise_() brings the group to the front of its
        #    column; hiding the group means hiding every member,
        #    because hiding one tab in a stack does not collapse the
        #    stack.
        if leader_visible:
            try:
                members[0].setVisible(True)
                members[0].raise_()
            except Exception:
                pass
        else:
            for d in members:
                try:
                    d.setVisible(False)
                except Exception:
                    pass

    @staticmethod
    def _snapshot_column(qwin, area):
        """Snapshot one dock area's tab-group structure.

        Returns a list of dicts, each:

            {'members': [QDockWidget, ...],
             'leader_visible': bool}

        Members are in tab order, leader first.  A group of one is a
        docker that was not tabified with anything else.  The snapshot
        includes hidden dockers -- they occupy layout slots and their
        tab relationships matter to a rebuild even when they are not
        currently on screen.

        Used in three places:
          - export_column_layout serialises the return value.
          - import_column_layout takes one of the OTHER side before
            anything changes, so that side can be rebuilt from an
            explicit record rather than from whatever state Qt leaves
            it in mid-import.
          - Any future command that needs to preserve a column across
            an operation on the other side can call this for the same
            purpose.

        The deduplication set (`seen`) is keyed by widget identity, not
        by name.  Two dockers with the same objectName cannot happen
        in a single Krita session, but identity is what
        tabifiedDockWidgets returns and what the group members are
        compared against later, so identity is what we dedupe on.
        """
        dockers = [d for d in qwin.findChildren(QDockWidget)
                   if qwin.dockWidgetArea(d) == area]
        seen = set()
        snapshot = []
        for d in dockers:
            if d in seen:
                continue
            siblings = [s for s in qwin.tabifiedDockWidgets(d)
                        if s in dockers]
            members = [d] + siblings
            for m in members:
                seen.add(m)
            snapshot.append({
                'members': members,
                'leader_visible': bool(members[0].isVisible()),
            })
        return snapshot

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
        help_text='Toggle docker visibility, toolbars included.',
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
        help_text='Toggle docker visibility, leaving toolbars untouched.',
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
        help_text='Hide every docker in the left, right, or both '
                  'columns.',
        args={
            '--side': {
                'type': 'str', 'default': 'both',
                'choices': ['left', 'right', 'both'],
                'help': 'Which column(s) to hide.',
            },
        }
    )
    def delete_docker_columns(self, params):
        """Hide every docker in the target area(s), emptying the column.

        This is the destructive counterpart to `import_column_layout`:
        the import adds arrivals to a column, this removes what is
        there.  Nothing is moved to the other side; nothing is
        re-tabified; the dockers are simply hidden where they stand.
        Their position in the column and their tab-group structure
        survive untouched -- showing them again via `focus_docker`,
        `toggle_dockers_only`, or a subsequent `import_column_layout`
        brings them back in the shape they were in.

        Not to be confused with `toggle_dockers_only`, which hides
        *every* docker in the window and remembers the set so the next
        call can restore it.  This command targets a single column (or
        both), does not remember anything, and does not touch the
        other column.  Use it when the goal is "empty this column",
        not "hide the docker UI".

        `side` selects the target: "left", "right", or "both".  The
        default is "both", which is the equivalent of hiding every
        docker without any bookkeeping -- useful as a cleanup step
        before a fresh layout import.

        A docker that is already hidden is counted in `already_hidden`
        and not touched; the response distinguishes the two counts so
        the caller can tell how many were actually visible to begin
        with.  The per-area breakdown in `per_area` reports how many
        dockers were hidden on each side when `side=both`.

        Note on the name: "delete" is the user-facing verb for "make
        the column disappear", but the operation is a hide.  Qt
        dockers cannot be destroyed from Python without breaking
        Krita's internal bookkeeping, so the state after this command
        is "these dockers exist and are hidden", not "these dockers
        are gone".  Showing them again always works.
        """
        try:
            win = Krita.instance().activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()
            if qwin is None:
                return {'success': False,
                        'message': 'Active window has no qwindow'}

            side = str(params.get('side', 'both')).lower()
            area_by_side = {
                'left':  [Qt.DockWidgetArea.LeftDockWidgetArea],
                'right': [Qt.DockWidgetArea.RightDockWidgetArea],
                'both':  [Qt.DockWidgetArea.LeftDockWidgetArea,
                          Qt.DockWidgetArea.RightDockWidgetArea],
            }
            if side not in area_by_side:
                return {'success': False,
                        'message': f'side must be one of '
                                   f'{sorted(area_by_side)}, got '
                                   f'{side!r}'}
            areas = area_by_side[side]

            hidden = 0
            already_hidden = 0
            per_area = {a.name: 0 for a in areas}
            hidden_names = []

            for d in qwin.findChildren(QDockWidget):
                da = qwin.dockWidgetArea(d)
                if da not in areas:
                    continue
                try:
                    visible = d.isVisible()
                except Exception:
                    continue
                if not visible:
                    already_hidden += 1
                    continue
                try:
                    d.setVisible(False)
                    hidden += 1
                    per_area[da.name] += 1
                    hidden_names.append(d.objectName())
                except Exception:
                    pass

            qwin.updateGeometry()

            msg = (f'Hid {hidden} docker(s) in {side} column(s)')
            if already_hidden:
                msg += f' ({already_hidden} already hidden)'

            return {
                'success': True,
                'message': msg,
                'data': {
                    'side': side,
                    'areas': [a.name for a in areas],
                    'hidden': hidden,
                    'already_hidden': already_hidden,
                    'per_area': per_area,
                    'hidden_names': hidden_names,
                },
            }
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Bring a named docker to the front of its tab group.',
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

    @command(
        category='window',
        help_text='Resize the left, right, or both docker columns to a '
                  'specific width.',
        args={
            '--mode': {
                'type': 'str', 'default': 'left',
                'choices': ['left', 'right', 'both'],
                'help': 'Which column(s) to resize.',
            },
            '--target_width': {
                'type': 'int', 'default': 300,
                'help': 'Preferred column width in pixels.',
            },
            '--min_width': {
                'type': 'int', 'default': 180,
                'help': 'Minimum column width; how narrow the splitter '
                        'handle can drag the column.',
            },
            '--max_width': {
                'type': 'int', 'default': 800,
                'help': 'Maximum column width; also caps any pathological '
                        'widget size hint.',
            },
            '--hint_threshold': {
                'type': 'int', 'default': 2000,
                'help': 'A widget whose sizeHint or minimumSizeHint '
                        'exceeds this is treated as broken and clamped.',
            },
        }
    )
    def resize_docker_columns(self, params):
        """Force one or both docker columns to a target width.

        Three things happen per docker, in order:

          1. Every widget under the docker whose sizeHint or
             minimumSizeHint exceeds `hint_threshold` is clamped.  This
             is the load-bearing step.  The AI Diffusion docker's
             welcome page is a QLabel with an unbreakable line, and Qt
             faithfully reports it as needing ~87k pixels; without
             this clamp, every resizeDocks call gets silently reverted
             by Qt's own layout pass trying to satisfy that hint.

          2. The docker's top-level content widget is pinned to a range
             [min_width, max_width] with an Expanding horizontal size
             policy.  The range is what keeps the splitter handle
             visible (min == max would hide it); Expanding is what
             makes the content fill whatever width the column
             currently has.

          3. resizeDocks is called with every docker in the target
             area(s) and the target width.  Passing all of them at
             once -- not just one -- is what lets Qt redistribute the
             whole column.

        The sequence runs several times with short sleeps, because
        Krita's dock layout recomputes a beat after each change and
        the first attempt can be silently overridden.  QTimer is not
        an option here: the handler runs on the socket server's client
        thread, which has no Qt event loop, so a scheduled timer would
        never fire.  Blocking sleeps are impolite but brief, and the
        client thread is per-connection.

        Args are the params dict.  `mode` selects the areas:
        LeftDockWidgetArea, RightDockWidgetArea, or both.
        """
        try:
            win = Krita.instance().activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()
            if qwin is None:
                return {'success': False,
                        'message': 'Active window has no qwindow'}

            mode = str(params.get('mode', 'left')).lower()
            try:
                target_width = int(params.get('target_width', 300))
                min_width = int(params.get('min_width', 180))
                max_width = int(params.get('max_width', 800))
                hint_threshold = int(params.get('hint_threshold', 2000))
            except (TypeError, ValueError) as e:
                return {'success': False,
                        'message': f'Numeric arg could not be parsed: {e}'}

            if min_width < 0 or max_width <= 0 or min_width > max_width:
                return {'success': False,
                        'message': f'Invalid width range: '
                                   f'min={min_width}, max={max_width}'}
            if target_width < min_width or target_width > max_width:
                return {'success': False,
                        'message': f'target_width={target_width} outside '
                                   f'[min_width={min_width}, '
                                   f'max_width={max_width}]'}

            area_by_mode = {
                'left':  [Qt.DockWidgetArea.LeftDockWidgetArea],
                'right': [Qt.DockWidgetArea.RightDockWidgetArea],
                'both':  [Qt.DockWidgetArea.LeftDockWidgetArea,
                          Qt.DockWidgetArea.RightDockWidgetArea],
            }
            if mode not in area_by_mode:
                return {'success': False,
                        'message': f'mode must be one of '
                                   f'{sorted(area_by_mode)}, got {mode!r}'}
            areas = area_by_mode[mode]

            # -- offender discovery and clamping -----------------------

            def find_offenders(docker):
                content = docker.widget()
                if content is None:
                    return []
                offenders = []
                for w in [content] + content.findChildren(QWidget):
                    try:
                        hint = w.sizeHint().width()
                        minh = w.minimumSizeHint().width()
                    except Exception:
                        continue
                    if hint > hint_threshold or minh > hint_threshold:
                        offenders.append((w, hint, minh))
                return offenders

            def clamp_widget(w):
                w.setMinimumWidth(0)
                w.setMaximumWidth(max_width)
                w.updateGeometry()

            def tune_docker(d):
                content = d.widget()
                if content is None:
                    return []

                clamped = []
                for w, hint, minh in find_offenders(d):
                    clamp_widget(w)
                    try:
                        cname = w.metaObject().className()
                    except Exception:
                        cname = type(w).__name__
                    clamped.append(f'{cname}(hint={hint}, min={minh})')

                content.setMinimumWidth(min_width)
                content.setMaximumWidth(max_width)
                sp = content.sizePolicy()
                sp.setHorizontalPolicy(QSizePolicy.Policy.Expanding)
                content.setSizePolicy(sp)
                content.updateGeometry()

                for sa in d.findChildren(QScrollArea):
                    sa.setWidgetResizable(True)
                    sa.setMinimumWidth(min_width)
                    sa.setMaximumWidth(max_width)

                d.setMinimumWidth(0)
                d.setMaximumWidth(16777215)
                d.updateGeometry()
                return clamped

            # -- area collection ----------------------------------------

            def collect_dockers():
                return [d for d in qwin.findChildren(QDockWidget)
                        if qwin.dockWidgetArea(d) in areas]

            def apply_once():
                dockers = collect_dockers()
                if not dockers:
                    return []
                for d in dockers:
                    tune_docker(d)
                qwin.resizeDocks(dockers,
                                 [target_width] * len(dockers),
                                 Qt.Orientation.Horizontal)
                qwin.updateGeometry()
                return dockers

            # -- run ---------------------------------------------------

            dockers = apply_once()
            if not dockers:
                return {'success': False,
                        'message': f'No dockers found in area(s): '
                                   f'{[a.name for a in areas]}'}

            clamped_report = []
            for d in dockers:
                offenders = find_offenders(d)
                if offenders:
                    names = []
                    for w, hint, minh in offenders:
                        try:
                            cname = w.metaObject().className()
                        except Exception:
                            cname = type(w).__name__
                        names.append(f'{cname}(hint={hint}, min={minh})')
                    clamped_report.append({
                        'docker': d.objectName(),
                        'widgets': names,
                    })

            # Re-apply across the next few hundred ms.  Krita's own
            # layout pass fires shortly after any docker change and can
            # overwrite the first request.  No QTimer here: the handler
            # runs on a plain Python thread (see _HandlerBox in
            # kritomatic_daemon.py), which has no Qt event loop, so a
            # scheduled timer would never fire.
            for _ in range(3):
                time.sleep(0.05)
                apply_once()

            return {
                'success': True,
                'message': f'Resized {len(dockers)} docker(s) in '
                           f'{[a.name for a in areas]} to '
                           f'{target_width}px (mode={mode})',
                'data': {
                    'mode': mode,
                    'areas': [a.name for a in areas],
                    'docker_count': len(dockers),
                    'docker_names': [d.objectName() for d in dockers],
                    'target_width': target_width,
                    'min_width': min_width,
                    'max_width': max_width,
                    'hint_threshold': hint_threshold,
                    'clamped': clamped_report,
                },
            }
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Move an entire docker column to the opposite side '
                  'of the window.',
        args={
            '--from_side': {
                'type': 'str', 'required': True,
                'choices': ['left', 'right'],
                'help': 'The side the column is currently on.  The '
                        'opposite side becomes the destination.',
            },
        }
    )
    def translate_docker_column(self, params):
        """Move every docker in one dock area to the opposite area.

        Refused when the destination area already holds any *visible*
        docker.  Hidden dockers on the destination side do not count:
        they occupy slots in Qt's layout but do not produce a visible
        column, so merging into them is harmless -- the arriving
        dockers stack as the visible column and the hidden ones stay
        hidden behind whatever they were already behind.

        Tab groups are preserved across the move.  Qt's
        addDockWidget() does not carry the tabification across an
        area change -- the tab relationship is stored on the *area's*
        layout, not on the docker itself -- so the groups are
        snapshotted before the move and rebuilt after every docker
        has landed on the new side.  A group's visible member becomes
        the visible tab and the rest are tabified behind it, matching
        the original order.

        Move order matters.  Each group's visible member is moved
        before its hidden members, and all leaders are moved before
        all followers.  Without this, a hidden docker added first
        would occupy the topmost slot in the destination column, and
        the group's visible tab would end up behind it.

        Args are the params dict.  `from_side` selects the source
        area: "left" or "right".  The destination is its opposite.
        """
        try:
            win = Krita.instance().activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()
            if qwin is None:
                return {'success': False,
                        'message': 'Active window has no qwindow'}

            from_side = str(params.get('from_side', '')).lower()
            if from_side == 'left':
                src_area = Qt.DockWidgetArea.LeftDockWidgetArea
                dst_area = Qt.DockWidgetArea.RightDockWidgetArea
            elif from_side == 'right':
                src_area = Qt.DockWidgetArea.RightDockWidgetArea
                dst_area = Qt.DockWidgetArea.LeftDockWidgetArea
            else:
                return {'success': False,
                        'message': f'from_side must be "left" or '
                                   f'"right", got {from_side!r}'}

            src_dockers = [d for d in qwin.findChildren(QDockWidget)
                           if qwin.dockWidgetArea(d) == src_area]
            if not src_dockers:
                return {'success': False,
                        'message': f'Source side ({src_area.name}) is '
                                   f'empty; nothing to move.'}

            dst_visible = [d for d in qwin.findChildren(QDockWidget)
                           if qwin.dockWidgetArea(d) == dst_area
                           and d.isVisible()]
            if dst_visible:
                return {
                    'success': False,
                    'message': f'Destination side ({dst_area.name}) '
                               f'already holds {len(dst_visible)} '
                               f'visible docker(s).  Move or hide them '
                               f'first.',
                    'data': {'dst_visible':
                             [d.objectName() for d in dst_visible]},
                }

            # -- snapshot tab groups -----------------------------------

            seen = set()
            tab_groups = []
            for d in src_dockers:
                if d in seen:
                    continue
                siblings = [s for s in qwin.tabifiedDockWidgets(d)
                            if s in src_dockers]
                group = [d] + siblings
                for m in group:
                    seen.add(m)
                tab_groups.append(group)

            for i, g in enumerate(tab_groups):
                visible_members = [x for x in g if x.isVisible()]
                if visible_members and not g[0].isVisible():
                    leader = visible_members[0]
                    rest = [x for x in g if x is not leader]
                    tab_groups[i] = [leader] + rest

            # -- compute move order ------------------------------------

            move_order = []
            for g in tab_groups:
                move_order.append(g[0])
            for g in tab_groups:
                for m in g[1:]:
                    move_order.append(m)

            # -- move --------------------------------------------------

            for d in move_order:
                qwin.addDockWidget(dst_area, d)

            # -- rebuild tabification ----------------------------------

            leader_was_visible = {id(g[0]): bool(g[0].isVisible())
                                  for g in tab_groups}

            rebuilt = 0
            for g in tab_groups:
                self._tabify_group(qwin, g,
                                   leader_was_visible.get(id(g[0]), True))
                if len(g) >= 2:
                    rebuilt += 1

            qwin.updateGeometry()

            return {
                'success': True,
                'message': f'Moved {len(move_order)} docker(s) from '
                           f'{src_area.name} to {dst_area.name}'
                           + (f', rebuilt {rebuilt} tab group(s)'
                              if rebuilt else ''),
                'data': {
                    'from_side': from_side,
                    'src_area': src_area.name,
                    'dst_area': dst_area.name,
                    'docker_count': len(move_order),
                    'docker_names': [d.objectName() for d in move_order],
                    'tab_groups_rebuilt': rebuilt,
                    'group_leaders': [
                        {'leader': g[0].objectName(),
                         'members': [x.objectName() for x in g]}
                        for g in tab_groups if len(g) >= 2
                    ],
                },
            }
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Print or write the tab-group layout of one docker '
                  'column as JSON.  The output can be saved, edited, '
                  'and later fed to import_column_layout to rebuild '
                  'the column.',
        args={
            '--side': {
                'type': 'str', 'required': True,
                'choices': ['left', 'right'],
                'help': 'Which column to export.',
            },
            '--output': {
                'type': 'str', 'required': False,
                'help': 'Write the layout to this file.  When omitted, '
                        'the layout is returned on stdout as a JSON '
                        'string, so it can be redirected or piped.',
            },
        }
    )
    def export_column_layout(self, params):
        """Snapshot one column's tab-group structure as JSON.

        The output records each tab group in order, the group's
        visible member (its "leader"), and whether that leader was
        visible at export time.  Single-member groups -- dockers
        sitting alone in their row, not tabified with anything --
        are preserved as groups of one so their order in the column
        is retained.

        Output shape::

            {
              "side": "left",
              "groups": [
                {
                  "leader": "PaletteDocker",
                  "leader_visible": true,
                  "members": ["PaletteDocker", "KisLayerBox",
                              "ColorSelectorNg"]
                },
                {
                  "leader": "History",
                  "leader_visible": false,
                  "members": ["History"]
                }
              ]
            }

        Members are listed in tab order, leader first.  A group of one
        is a docker that was not tabified with anything else; whether
        it is visible is captured by its leader_visible flag.

        Export is read-only.  It does not touch visibility, tab
        memberships, or the layout; it only inspects them.

        The snapshot itself comes from _snapshot_column, which is the
        same helper import_column_layout uses to preserve the other
        column across an import.  Sharing it keeps export and import
        in agreement about what a "group" is.
        """
        try:
            win = Krita.instance().activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()
            if qwin is None:
                return {'success': False,
                        'message': 'Active window has no qwindow'}

            side = str(params.get('side', '')).lower()
            area = self._side_to_area(side)
            if area is None:
                return {'success': False,
                        'message': f'side must be "left" or "right", '
                                   f'got {side!r}'}

            snapshot = self._snapshot_column(qwin, area)
            groups = []
            for snap in snapshot:
                members = snap['members']
                groups.append({
                    'leader': members[0].objectName(),
                    'leader_visible': snap['leader_visible'],
                    'members': [m.objectName() for m in members],
                })

            layout = {
                'side': side,
                'groups': groups,
            }
            json_str = json.dumps(layout, indent=2)

            output_file = params.get('output', None)
            if output_file:
                try:
                    with open(output_file, 'w', encoding='utf-8') as f:
                        f.write(json_str)
                except OSError as e:
                    return {'success': False,
                            'message': f'Could not write '
                                       f'{output_file}: {e}'}
                total = sum(len(g['members']) for g in groups)
                return {
                    'success': True,
                    'message': f'Wrote {side} column layout to '
                               f'{output_file} ({total} docker(s) in '
                               f'{len(groups)} group(s))',
                    'data': {
                        'side': side,
                        'output': output_file,
                        'docker_count': total,
                        'group_count': len(groups),
                    },
                }

            total = sum(len(g['members']) for g in groups)
            return {
                'success': True,
                'message': f'Exported {side} column layout '
                           f'({total} docker(s) in {len(groups)} '
                           f'group(s))',
                'data': {'json': json_str},
            }
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False, 'message': str(e)}

    @command(
        category='window',
        help_text='Rebuild one docker column from a layout previously '
                  'produced by export_column_layout.  Additive by '
                  'default: dockers already visible on the target side '
                  'are left alone, and the other column is preserved.',
        args={
            '--side': {
                'type': 'str', 'required': True,
                'choices': ['left', 'right'],
                'help': 'Which column to rebuild.',
            },
            '--source': {
                'type': 'str', 'required': True,
                'help': 'A path to a layout JSON file, or the JSON '
                        'itself as a string.',
            },
            '--override': {
                'type': 'bool', 'default': False,
                'help': 'Prescriptive mode.  Hides every docker '
                        'currently visible on the target side before '
                        'applying the layout, and does not preserve '
                        'dockers that happen to already be visible '
                        'there.  Without this flag, the import is '
                        'additive: the layout adds arrivals around '
                        'the residents.',
            },
            '--preserve': {
                'type': 'str', 'required': False,
                'help': 'Comma-separated list of docker object names '
                        'that must not move, regardless of what the '
                        'layout claims.  Overrides the automatic '
                        'preservation rules.',
            },
        }
    )
    def import_column_layout(self, params):
        """Rebuild a column from a layout JSON.

        THE MODEL
        =========
        Two columns are involved: the *target*, named by `side`, and
        the *other* side.  The target is rebuilt from the layout.  The
        other is snapshotted before anything happens and rebuilt
        afterwards from its own members minus the ones the layout
        claims.

        The import is *additive by default*: dockers currently visible
        on the target side stay visible, and the layout's arrivals are
        stacked around them.  The `--override` flag switches to
        prescriptive mode: current visible dockers on the target side
        are hidden first, and the layout wins for everything.

        The other side is preserved in both modes.

        PRESERVATION RULES
        ==================
        Every docker named in the layout is potentially movable.  A
        name is removed from the moving set, and thus preserved, by
        any of these rules:

          Explicit `--preserve`
              Any name in the comma-separated list.  Highest priority;
              always wins.

          Rule A -- visible singleton on the other side
              A docker that is the only member of its group on the
              other side AND is currently visible.  This is the shape
              a manual addition takes: the user drags one docker out
              of a group, it stands alone, and it stays visible.  The
              layout's own singletons are usually hidden, so this
              rule does not touch them.

          Rule B -- visible layout-singleton on the other side
              A docker that the layout records as a singleton
              (`len(members) == 1`) AND that is currently visible on
              the other side.  Catches the case where the user has
              tabified a layout singleton into some other group since
              the export: Rule A no longer fires (the docker is not a
              singleton any more), but the layout still says it is
              standalone, and the user can see it, so it is a manual
              addition whose group membership has drifted.

          Rule C -- visible docker on the target side (additive mode)
              When `--override` is off, any docker currently visible
              on the target side is preserved.  The import adds
              arrivals around the residents; it does not disturb what
              is already on screen.  Suppressed by `--override`.

        All rules except the explicit list require the docker to be
        currently visible.  Hidden dockers are fair game: they are
        part of the layout's own bookkeeping, not user additions.

        LEFTOVER GROUPS
        ===============
        The other side's snapshot is filtered against the moving set.
        A snapshot group that loses some but not all of its members
        becomes a *leftover group*: the survivors are re-tabified
        together with the first survivor promoted to leader if the
        original leader was taken.  The group's visibility carries
        over unchanged, so a visible group stays visible with a new
        leader, and a hidden group stays hidden.

        The restore is driven by *name*, not by current area.  Once a
        column's visible content is removed, Qt may tear down its area
        widget, and its members stop reporting dockWidgetArea() ==
        other_area.  Testing by name (does the layout claim this?) is
        stable across that transition; testing by area is not.  The
        addDockWidget(other_area, ...) call that follows re-parents
        whatever Qt has orphaned and is a no-op otherwise.

        MISSING NAMES
        =============
        A name in the layout that does not correspond to any docker in
        the current session (plugin disabled, layout from another
        machine) is reported in `missing` and skipped.  The import
        continues with whatever resolved.

        ARGS
        ====
        `side` selects the target area.  `source` is a file path or a
        JSON string (a file path that exists is preferred; otherwise
        the string is parsed as JSON).  `override` is a bool; see
        above.  `preserve` is an optional comma-separated list.
        """
        try:
            win = Krita.instance().activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            qwin = win.qwindow()
            if qwin is None:
                return {'success': False,
                        'message': 'Active window has no qwindow'}

            # -- resolve target and other side -------------------------

            side = str(params.get('side', '')).lower()
            area = self._side_to_area(side)
            if area is None:
                return {'success': False,
                        'message': f'side must be "left" or "right", '
                                   f'got {side!r}'}
            if area == Qt.DockWidgetArea.LeftDockWidgetArea:
                other_area = Qt.DockWidgetArea.RightDockWidgetArea
            else:
                other_area = Qt.DockWidgetArea.LeftDockWidgetArea

            # -- parse override and preserve flags ---------------------

            override = bool(params.get('override', False))

            preserve_raw = params.get('preserve', '') or ''
            if isinstance(preserve_raw, str):
                explicit_preserve = {n.strip()
                                     for n in preserve_raw.split(',')
                                     if n.strip()}
            elif isinstance(preserve_raw, (list, tuple, set)):
                explicit_preserve = {str(n).strip()
                                     for n in preserve_raw
                                     if str(n).strip()}
            else:
                explicit_preserve = set()

            # -- load the layout ---------------------------------------
            #
            # A path that exists on disk wins over parsing the string
            # as JSON.  Same convention as diffusion import_params.

            source = params.get('source', '')
            if not source:
                return {'success': False,
                        'message': 'source is required (a file path or '
                                   'a JSON string)'}

            if os.path.exists(source):
                try:
                    with open(source, 'r', encoding='utf-8') as f:
                        layout = json.load(f)
                except (OSError, json.JSONDecodeError) as e:
                    return {'success': False,
                            'message': f'Could not read {source}: {e}'}
            else:
                try:
                    layout = json.loads(source)
                except json.JSONDecodeError as e:
                    return {'success': False,
                            'message': f'source is neither a file nor '
                                       f'valid JSON: {e}'}

            if not isinstance(layout, dict):
                return {'success': False,
                        'message': 'layout must be a JSON object'}
            groups = layout.get('groups')
            if not isinstance(groups, list) or not groups:
                return {'success': False,
                        'message': "layout has no non-empty 'groups' "
                                   "list"}

            # -- name -> docker, and the layout's claimed set ----------
            #
            # `claimed` is the set of names the layout mentions at all.
            # `layout_singletons` is the subset that the layout records
            # as a group of one.  The distinction matters for Rule B:
            # a name in layout_singletons is a docker the layout
            # considers standalone, so its current group membership in
            # the live session is a user alteration rather than a
            # layout fact.

            all_dockers = {}
            for d in qwin.findChildren(QDockWidget):
                name = d.objectName()
                if name:
                    all_dockers[name] = d

            claimed = set()
            layout_singletons = set()
            for g in groups:
                if not isinstance(g, dict):
                    continue
                member_names = g.get('members') or []
                if not isinstance(member_names, list):
                    continue
                for n in member_names:
                    if isinstance(n, str):
                        claimed.add(n)
                if (len(member_names) == 1
                        and isinstance(member_names[0], str)):
                    layout_singletons.add(member_names[0])

            # -- snapshot the other side BEFORE anything changes -------
            #
            # Taken before the override hiding and before any
            # addDockWidget.  This is the record the restore step
            # rebuilds from.

            other_snapshot = self._snapshot_column(qwin, other_area)

            # -- compute the preserve set ------------------------------
            #
            # See the docstring for the full rule set.  In summary:
            # A = visible singleton on the other side (manual addition
            # in its natural shape); B = visible layout-singleton on
            # the other side (manual addition whose group membership
            # has drifted since export); C = visible docker on the
            # target side, only when not override (additive mode).

            rule_a = set()
            rule_b = set()
            rule_c = set()

            # Rule A
            for snap in other_snapshot:
                if len(snap['members']) == 1 and snap['leader_visible']:
                    rule_a.add(snap['members'][0].objectName())

            # Rule B
            for d in qwin.findChildren(QDockWidget):
                if qwin.dockWidgetArea(d) != other_area:
                    continue
                name = d.objectName()
                if name and name in layout_singletons and d.isVisible():
                    rule_b.add(name)

            # Rule C
            if not override:
                for d in qwin.findChildren(QDockWidget):
                    if (qwin.dockWidgetArea(d) == area
                            and d.isVisible()):
                        rule_c.add(d.objectName())

            auto_preserve = rule_a | rule_b | rule_c
            preserve_set = explicit_preserve | auto_preserve
            moving_names = claimed - preserve_set

            # -- override: hide visible dockers on the target side -----
            #
            # In override mode, the target column is torn down to
            # whatever the layout puts back.  The hidden count is
            # reported so the caller can see how much was cleared.

            hidden_first = 0
            if override:
                for d in qwin.findChildren(QDockWidget):
                    if (qwin.dockWidgetArea(d) == area
                            and d.isVisible()):
                        try:
                            d.setVisible(False)
                            hidden_first += 1
                        except Exception:
                            pass

            # -- apply the layout --------------------------------------
            #
            # Every group is walked in the layout's order.  Preserved
            # members are filtered out; a group whose members are all
            # preserved collapses to an empty list and is skipped
            # entirely.  The leader is rotated to the front if it
            # survived; otherwise the first surviving member becomes
            # the effective leader for the tabification call.

            imported = 0
            groups_done = 0
            missing = []

            for group in groups:
                if not isinstance(group, dict):
                    continue

                member_names = group.get('members') or []
                if (not isinstance(member_names, list)
                        or not member_names):
                    continue

                leader_name = group.get('leader') or member_names[0]
                leader_visible = bool(group.get('leader_visible', True))

                members = []
                for name in member_names:
                    if name in preserve_set:
                        continue
                    d = all_dockers.get(name)
                    if d is None:
                        missing.append(name)
                        continue
                    members.append(d)

                if not members:
                    continue

                # Rotate the layout's leader to the front if it is
                # among the survivors.
                leader = all_dockers.get(leader_name)
                if leader is not None and leader in members:
                    members = [leader] + [m for m in members
                                          if m is not leader]

                for d in members:
                    qwin.addDockWidget(area, d)

                self._tabify_group(qwin, members, leader_visible)

                imported += len(members)
                groups_done += 1

            # -- compute leftover groups on the other side -------------
            #
            # Filter each snapshot group by the moving set.  A group
            # that keeps at least one member becomes a leftover; the
            # first survivor takes over as leader if the original
            # leader was among the moved.

            leftover_groups = []
            for snap in other_snapshot:
                survivors = [m for m in snap['members']
                             if m.objectName() not in moving_names]
                if not survivors:
                    continue
                original_leader = snap['members'][0].objectName()
                leader_was_taken = original_leader in moving_names
                leftover_groups.append({
                    'original_leader': original_leader,
                    'leader_was_taken': leader_was_taken,
                    'new_leader': survivors[0].objectName(),
                    'members': survivors,
                    'leader_visible': snap['leader_visible'],
                })

            # -- restore the other side --------------------------------
            #
            # For each leftover: re-add every member to the other area
            # (idempotent when already there; the operation that
            # re-parents an orphaned docker when Qt has torn the area
            # down), then re-tabify with the group's saved visibility.

            other_restored = 0
            for lg in leftover_groups:
                for m in lg['members']:
                    try:
                        qwin.addDockWidget(other_area, m)
                    except Exception:
                        pass
                self._tabify_group(qwin, lg['members'],
                                   lg['leader_visible'])
                other_restored += len(lg['members'])

            qwin.updateGeometry()

            # -- response ----------------------------------------------

            msg = (f'Imported {imported} docker(s) in {groups_done} '
                   f'group(s) to {side} column')
            if override:
                msg += (f' (override: {hidden_first} prior visible '
                        f'docker(s) hidden)')
            if other_restored:
                msg += (f'; preserved {other_restored} docker(s) on the '
                        f'other side')
            if missing:
                msg += (f'; {len(missing)} name(s) not found: '
                        f'{", ".join(missing)}')

            return {
                'success': True,
                'message': msg,
                'data': {
                    'side': side,
                    'docker_count': imported,
                    'group_count': groups_done,
                    'override': override,
                    'hidden_first': hidden_first,
                    'other_restored': other_restored,
                    'missing': missing,
                    'preserved': {
                        'explicit': sorted(explicit_preserve),
                        'rule_a_visible_singleton_other': sorted(rule_a),
                        'rule_b_visible_layout_singleton_other':
                            sorted(rule_b),
                        'rule_c_visible_on_target': sorted(rule_c),
                        'final': sorted(preserve_set),
                    },
                    'leftover_groups': [
                        {
                            'original_leader': lg['original_leader'],
                            'new_leader': lg['new_leader'],
                            'leader_was_taken': lg['leader_was_taken'],
                            'members': [m.objectName()
                                        for m in lg['members']],
                            'leader_visible': lg['leader_visible'],
                        }
                        for lg in leftover_groups
                    ],
                },
            }
        except Exception as e:
            import traceback as _tb
            _tb.print_exc()
            return {'success': False, 'message': str(e)}
