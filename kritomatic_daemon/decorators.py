"""Decorators for registering commands with metadata"""

from .registry import register_command

def command(category, help_text, args):
    """
    Decorator to register a command with its metadata

    Usage:
        @command(
            category='layer',
            help_text='Create a new layer',
            args={
                'name': {'type': 'str', 'required': True, 'help': 'Layer name'},
                'layer_type': {'type': 'str', 'default': 'paintlayer', 'help': 'Layer type'}
            }
        )
        def create_layer(self, name, layer_type='paintlayer'):
            ...
    """
    def decorator(func):
        command_name = func.__name__
        register_command(category, command_name, help_text, args)
        return func
    return decorator
