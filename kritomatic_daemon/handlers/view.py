from krita import Krita
from PyQt5.QtCore import QPointF
from ..decorators import command

class ViewHandler:
    def __init__(self):
        # Stack for view states: each entry is (zoom_level, center_x, center_y)
        self._zoom_stack = []
        # Single saved state for simple toggle
        self._saved_state = None

    def _get_canvas(self):
        """Get the canvas object from the active view"""
        app = Krita.instance()
        view = app.activeWindow().activeView()
        return view.canvas()

    def _get_current_state(self):
        """Get current view state as (zoom, center_x, center_y)"""
        canvas = self._get_canvas()
        center = canvas.preferredCenter()
        return (canvas.zoomLevel(), center.x(), center.y())

    def _restore_state(self, state):
        """Restore view from a state tuple"""
        zoom, center_x, center_y = state
        canvas = self._get_canvas()
        canvas.setZoomLevel(zoom)
        canvas.setPreferredCenter(QPointF(center_x, center_y))

    def execute(self, cmd_type, params):
        """Execute view-related commands"""
        if cmd_type == 'push':
            return self.push()
        elif cmd_type == 'pop':
            return self.pop()
        elif cmd_type == 'toggle':
            return self.toggle()
        elif cmd_type == 'fit':
            return self.fit()
        elif cmd_type == 'fit_width':
            return self.fit_width()
        elif cmd_type == 'fit_height':
            return self.fit_height()
        elif cmd_type == 'zoom_to':
            percent = params.get('percent', 100)
            return self.zoom_to(percent)
        elif cmd_type == 'zoom_in':
            factor = params.get('factor', 1.2)
            return self.zoom_in(factor)
        elif cmd_type == 'zoom_out':
            factor = params.get('factor', 1.2)
            return self.zoom_out(factor)
        elif cmd_type == 'reset':
            return self.reset()
        elif cmd_type == 'get_state':
            return self.get_state()
        return {'success': False, 'message': f'Unknown view command: {cmd_type}'}

    @command(
        category='view',
        help_text='Push current view state onto stack',
        args={}
    )
    def push(self):
        """Save current view state to stack"""
        try:
            state = self._get_current_state()
            self._zoom_stack.append(state)
            zoom, cx, cy = state
            return {
                'success': True,
                'message': f'View state saved (stack size: {len(self._zoom_stack)})',
                'data': {'zoom': zoom, 'center_x': cx, 'center_y': cy, 'stack_size': len(self._zoom_stack)}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Pop and restore previous view state from stack',
        args={}
    )
    def pop(self):
        """Restore and remove the last saved state"""
        try:
            if not self._zoom_stack:
                return {'success': False, 'message': 'No saved view states to restore'}

            state = self._zoom_stack.pop()
            self._restore_state(state)
            zoom, cx, cy = state
            return {
                'success': True,
                'message': f'View state restored (stack size: {len(self._zoom_stack)})',
                'data': {'zoom': zoom, 'center_x': cx, 'center_y': cy, 'stack_size': len(self._zoom_stack)}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Toggle between current view and saved view (simple two-state toggle)',
        args={}
    )
    def toggle(self):
        """Simple two-state toggle: save current and fit, then restore"""
        try:
            if self._saved_state is None:
                # Save current state and zoom to fit
                self._saved_state = self._get_current_state()
                self.fit()
                zoom, cx, cy = self._saved_state
                return {
                    'success': True,
                    'message': 'Saved current view and zoomed to fit. Run again to restore.',
                    'data': {'saved_zoom': zoom, 'saved_center_x': cx, 'saved_center_y': cy}
                }
            else:
                # Restore saved state
                self._restore_state(self._saved_state)
                self._saved_state = None
                return {'success': True, 'message': 'Restored previous view state'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Zoom to fit entire document in view',
        args={}
    )
    def fit(self):
        """Zoom to fit the entire document"""
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}

            # Use Krita's built-in fit action
            action = app.action('zoom_to_fit')
            if action:
                action.trigger()
                return {'success': True, 'message': 'Zoomed to fit'}
            else:
                # Fallback: manual calculation
                canvas = self._get_canvas()
                view_width = canvas.width()
                view_height = canvas.height()
                doc_width = doc.width()
                doc_height = doc.height()
                zoom_x = view_width / doc_width
                zoom_y = view_height / doc_height
                zoom = min(zoom_x, zoom_y)
                canvas.setZoomLevel(zoom)
                canvas.setPreferredCenter(QPointF(doc_width / 2, doc_height / 2))
                return {'success': True, 'message': f'Zoomed to fit: {zoom:.2f}x'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Zoom to fit document width',
        args={}
    )
    def fit_width(self):
        """Zoom to fit document width to view width"""
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            canvas = self._get_canvas()
            view_width = canvas.width()
            doc_width = doc.width()
            zoom = view_width / doc_width
            canvas.setZoomLevel(zoom)
            canvas.setPreferredCenter(QPointF(doc_width / 2, canvas.preferredCenter().y()))
            return {'success': True, 'message': f'Zoomed to fit width: {zoom:.2f}x'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Zoom to fit document height',
        args={}
    )
    def fit_height(self):
        """Zoom to fit document height to view height"""
        try:
            app = Krita.instance()
            doc = app.activeDocument()
            if not doc:
                return {'success': False, 'message': 'No active document'}
            canvas = self._get_canvas()
            view_height = canvas.height()
            doc_height = doc.height()
            zoom = view_height / doc_height
            canvas.setZoomLevel(zoom)
            canvas.setPreferredCenter(QPointF(canvas.preferredCenter().x(), doc_height / 2))
            return {'success': True, 'message': f'Zoomed to fit height: {zoom:.2f}x'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Zoom to specific percentage',
        args={
            '--percent': {'type': 'int', 'required': True, 'help': 'Zoom percentage (e.g., 100, 200, 50)'}
        }
    )
    def zoom_to(self, percent):
        """Zoom to specific percentage"""
        try:
            canvas = self._get_canvas()
            zoom = percent / 100.0
            canvas.setZoomLevel(zoom)
            return {'success': True, 'message': f'Zoomed to {percent}%%', 'data': {'zoom': zoom}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Zoom in by factor',
        args={
            '--factor': {'type': 'float', 'default': 1.2, 'help': 'Zoom factor (default: 1.2)'}
        }
    )
    def zoom_in(self, factor=1.2):
        """Zoom in by factor"""
        try:
            canvas = self._get_canvas()
            current = canvas.zoomLevel()
            new_zoom = current * factor
            canvas.setZoomLevel(new_zoom)
            return {'success': True, 'message': f'Zoomed in to {new_zoom * 100:.1f}%%', 'data': {'zoom': new_zoom}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Zoom out by factor',
        args={
            '--factor': {'type': 'float', 'default': 1.2, 'help': 'Zoom factor (default: 1.2)'}
        }
    )
    def zoom_out(self, factor=1.2):
        """Zoom out by factor"""
        try:
            canvas = self._get_canvas()
            current = canvas.zoomLevel()
            new_zoom = current / factor
            canvas.setZoomLevel(new_zoom)
            return {'success': True, 'message': f'Zoomed out to {new_zoom * 100:.1f}%%', 'data': {'zoom': new_zoom}}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Reset zoom to 100%%',
        args={}
    )
    def reset(self):
        """Reset zoom to 100%"""
        try:
            canvas = self._get_canvas()
            canvas.resetZoom()
            return {'success': True, 'message': 'Zoom reset to 100%%'}
        except Exception as e:
            return {'success': False, 'message': str(e)}

    @command(
        category='view',
        help_text='Get current view state (zoom and center)',
        args={}
    )
    def get_state(self):
        """Get current view state for debugging"""
        try:
            zoom, cx, cy = self._get_current_state()
            return {
                'success': True,
                'message': f'Zoom: {zoom * 100:.1f}%%, Center: ({cx:.1f}, {cy:.1f})',
                'data': {'zoom': zoom, 'center_x': cx, 'center_y': cy}
            }
        except Exception as e:
            return {'success': False, 'message': str(e)}

