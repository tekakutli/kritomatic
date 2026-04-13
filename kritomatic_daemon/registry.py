"""Command registry for storing metadata about available commands"""

_command_registry = {}

def register_command(category, command_name, help_text, args):
    """Register a command with its metadata"""
    key = f"{category}_{command_name}"
    _command_registry[key] = {
        'category': category,
        'command': command_name,
        'help': help_text,
        'args': args
    }

def get_command_registry():
    """Return the entire command registry"""
    return _command_registry

def get_command(category, command_name):
    """Get metadata for a specific command"""
    key = f"{category}_{command_name}"
    return _command_registry.get(key)

def clear_registry():
    """Clear all registered commands (useful for testing)"""
    _command_registry.clear()
