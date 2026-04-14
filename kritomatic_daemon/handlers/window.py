from krita import Krita
from ..decorators import command

class WindowHandler:
    def execute(self, cmd_type, params):
        """Execute window-related commands"""
        if cmd_type == 'toggle_detached':
            return self.toggle_detached()
        elif cmd_type == 'toggle_fullscreen':
            return self.toggle_fullscreen()
        elif cmd_type == 'toggle_dockers':
            return self.toggle_dockers()
        elif cmd_type == 'toggle_docker_titles':
            return self.toggle_docker_titles()
        elif cmd_type == 'new_window':
            return self.new_window()
        return {'success': False, 'message': f'Unknown window command: {cmd_type}'}

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
        help_text='Toggle dockers visibility',
        args={}
    )
    def toggle_dockers(self):
        """Toggle all dockers on/off"""
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
