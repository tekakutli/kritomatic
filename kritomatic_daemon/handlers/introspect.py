"""
Introspection commands for the Kritomatic daemon.

These commands answer "what does Krita know about itself right now" —
which filters exist, which resources are installed, which dockers are
open, what state the active document is in. They are read-only: none of
them mutate the document, the session, or any resource.

Design rule for this module: fixed return shape, small enum of inputs.
Anything that would take a free-form string identifying what to look
at, or what to call, belongs in the Scripter, not here. The Scripter
is a Python eval surface; that is a security property we do not want
to reproduce over a socket.
"""

from krita import Krita
from ..decorators import command
from ..utils.krita_actions import enumerate_actions


class IntrospectHandler:
    def execute(self, cmd_type, params):
        if cmd_type == 'list_actions':
            return self.list_actions(params)
        elif cmd_type == 'list_filters':
            return self.list_filters()
        elif cmd_type == 'list_resources':
            return self.list_resources(params)
        elif cmd_type == 'list_dockers':
            return self.list_dockers()
        elif cmd_type == 'describe_active':
            return self.describe_active(params)
        elif cmd_type == 'list_extensions':
            return self.list_extensions()
        elif cmd_type == 'get_node_xml':
            return self.get_node_xml(params)
        return {'success': False, 'message': f'Unknown introspect command: {cmd_type}'}

    # ------------------------------------------------------------------
    #  Actions
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='List QActions Krita exposes, optionally filtered by substring',
        args={
            '--substring': {'type': 'str', 'required': False,
                            'help': 'Case-insensitive substring to filter by; '
                                    'matches object name or displayed text. '
                                    'If omitted, returns all actions.'}
        }
    )
    def list_actions(self, params):
        try:
            sub = (params.get('substring') or '').lower()
            actions = enumerate_actions()
            matches = []
            for a in actions:
                oname = a.objectName() or ''
                otext = a.text() or ''
                blob = (oname + ' ' + otext).lower()
                if sub and sub not in blob:
                    continue
                matches.append({'name': oname, 'text': otext})
            matches.sort(key=lambda m: m['name'])
            return {
                'success': True,
                'message': f'{len(matches)} action(s)'
                           + (f' matching {sub!r}' if sub else ''),
                'data': {'actions': matches, 'total_enumerated': len(actions)}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    #  Filters
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='List the names of every image filter Krita has registered',
        args={}
    )
    def list_filters(self):
        try:
            names = list(Krita.instance().filters())
            names.sort()
            return {
                'success': True,
                'message': f'{len(names)} filter(s)',
                'data': {'filters': names}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    #  Resources
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='List resources of a given type (preset, pattern, gradient, palette, ...)',
        args={
            '--type': {'type': 'str', 'required': True,
                       'help': 'Resource type, e.g. preset, pattern, gradient, '
                               'gamutmask, palette, brush, workspace, session, '
                               'symbol, paintoppreset'}
        }
    )
    def list_resources(self, params):
        try:
            rtype = params.get('type', '')
            if not rtype:
                return {'success': False, 'message': 'Resource type is required'}
            resources = Krita.instance().resources(rtype)
            names = sorted(resources.keys()) if resources else []
            return {
                'success': True,
                'message': f'{len(names)} resource(s) of type {rtype!r}',
                'data': {'type': rtype, 'names': names}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    #  Dockers
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='List dockers in the active window',
        args={}
    )
    def list_dockers(self):
        try:
            app = Krita.instance()
            win = app.activeWindow()
            if not win:
                return {'success': False, 'message': 'No active window'}
            dockers = []
            for d in win.dockers():
                try:
                    title = d.windowTitle()
                except Exception:
                    title = ''
                dockers.append({
                    'name': d.objectName() or '',
                    'title': title,
                })
            dockers.sort(key=lambda x: x['name'])
            return {
                'success': True,
                'message': f'{len(dockers)} docker(s)',
                'data': {'dockers': dockers}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    #  Describe active
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='Describe the active document / layer / view / window',
        args={
            '--target': {'type': 'str', 'required': True,
                         'choices': ['document', 'layer', 'view', 'window'],
                         'help': 'Which object to describe'},
            '--layer_name': {'type': 'str', 'required': False,
                             'help': 'Describe a specific layer (layer target only; '
                                     'defaults to the active layer)'}
        }
    )
    def describe_active(self, params):
        try:
            app = Krita.instance()
            target = params.get('target', '')
            if target == 'document':
                return self._describe_document(app)
            elif target == 'layer':
                return self._describe_layer(app, params.get('layer_name'))
            elif target == 'view':
                return self._describe_view(app)
            elif target == 'window':
                return self._describe_window(app)
            return {'success': False, 'message': f'Unknown target: {target}'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    def _describe_document(self, app):
        doc = app.activeDocument()
        if not doc:
            return {'success': False, 'message': 'No active document'}
        active = doc.activeNode()
        info = {
            'name': doc.name(),
            'file_name': doc.fileName(),
            'width': doc.width(),
            'height': doc.height(),
            'resolution': doc.resolution(),
            'color_model': doc.colorModel(),
            'color_depth': doc.colorDepth(),
            'color_profile': doc.colorProfile(),
            'modified': doc.modified(),
            'active_layer': active.name() if active else None,
        }
        return {'success': True, 'message': 'Document described', 'data': info}

    def _describe_layer(self, app, layer_name):
        doc = app.activeDocument()
        if not doc:
            return {'success': False, 'message': 'No active document'}
        if layer_name:
            layer = doc.nodeByName(layer_name)
            if not layer:
                return {'success': False, 'message': f'Layer "{layer_name}" not found'}
        else:
            layer = doc.activeNode()
            if not layer:
                return {'success': False, 'message': 'No active layer'}

        bounds = layer.bounds()
        info = {
            'name': layer.name(),
            'type': layer.type(),
            'visible': layer.visible(),
            'locked': layer.locked(),
            'opacity': layer.opacity(),
            'blending_mode': layer.blendingMode(),
            'bounds': {
                'x': bounds.x(),
                'y': bounds.y(),
                'width': bounds.width(),
                'height': bounds.height(),
            },
        }

        children = []
        try:
            for child in layer.childNodes():
                children.append({
                    'name': child.name(),
                    'type': child.type(),
                    'visible': child.visible(),
                })
        except Exception:
            pass
        info['children'] = children

        return {'success': True, 'message': 'Layer described', 'data': info}

    def _describe_view(self, app):
        win = app.activeWindow()
        if not win:
            return {'success': False, 'message': 'No active window'}
        view = win.activeView()
        if not view:
            return {'success': False, 'message': 'No active view'}
        info = {}
        try:
            canvas = view.canvas()
            center = canvas.preferredCenter()
            info = {
                'zoom_level': canvas.zoomLevel(),
                'preferred_center': {'x': center.x(), 'y': center.y()},
                'canvas_width': canvas.width(),
                'canvas_height': canvas.height(),
            }
        except Exception:
            pass
        return {'success': True, 'message': 'View described', 'data': info}

    def _describe_window(self, app):
        win = app.activeWindow()
        if not win:
            return {'success': False, 'message': 'No active window'}
        doc = win.activeDocument()
        qwin = win.qwindow()
        info = {
            'title': qwin.windowTitle() if qwin else '',
            'active_document': doc.name() if doc else None,
            'docker_count': len(win.dockers()),
        }
        return {'success': True, 'message': 'Window described', 'data': info}

    # ------------------------------------------------------------------
    #  Extensions
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='List Krita extensions currently registered',
        args={}
    )
    def list_extensions(self):
        try:
            app = Krita.instance()
            exts = app.extensions()
            result = []
            for e in exts:
                cls = type(e).__name__
                name = ''
                try:
                    if hasattr(e, 'objectName'):
                        name = e.objectName() or ''
                except Exception:
                    pass
                result.append({'class': cls, 'name': name})
            return {
                'success': True,
                'message': f'{len(result)} extension(s)',
                'data': {'extensions': result}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    # ------------------------------------------------------------------
    #  Node XML dump
    # ------------------------------------------------------------------

    @command(
        category='introspect',
        help_text='Get the raw XML of any node (layer or mask) by name',
        args={
            '--node_name': {'type': 'str', 'required': True,
                            'help': 'Name of the layer or mask to dump'},
        }
    )
    def get_node_xml(self, params):
        try:
            doc = Krita.instance().activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            node_name = params.get('node_name', '')
            node = doc.nodeByName(node_name)
            if not node:
                return {'success': False,
                        'message': f'Node "{node_name}" not found'}

            return {
                'success': True,
                'message': f'Got XML for "{node_name}"',
                'data': {
                    'name': node.name(),
                    'type': node.type(),
                    'xml': node.toXML(),
                },
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}
