"""Decorators for registering commands with metadata, and the
introspection helpers that let handler classes discover their own
commands without a manual dispatch list.

The @command decorator does two things: it registers the command in
the global schema registry (unchanged from before), and it stamps a
marker attribute on the decorated function.  `build_dispatch_map()`
reads that marker back at handler-construction time, replacing the
if/elif chains that used to live in `base.py` and `layer.py`.  Adding
a command is now a same-file edit: write the decorated method, and
the dispatch map picks it up on the next reload.

Container handlers — those that route to sub-handlers rather than
executing commands themselves — declare an attribute
`_sub_handlers`, a list of the handler instances they own.  The
dispatch-map builder descends through that attribute, so a top-level
handler's map claims every command reachable through its subtree.
"""

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
        # Marker read back by build_dispatch_map().  Set on the
        # function object itself, so it survives across every
        # instance of the owning handler class.
        func._kritomatic_command_name = command_name
        return func
    return decorator


def collect_command_names(handler):
    """Return the directly-decorated @command method names on
    `handler`.

    Uses `dir()` + `getattr` rather than `inspect.getmembers` because
    the marker lives on the function object, and going through
    `__func__` explicitly removes any doubt about attribute lookup
    forwarding from a bound method to its underlying function.
    """
    found = set()
    for name in dir(handler):
        attr = getattr(handler, name, None)
        if not callable(attr):
            continue
        func = getattr(attr, '__func__', attr)
        if getattr(func, '_kritomatic_command_name', None):
            found.add(name)
    return found


def all_command_names(handler):
    """Every @command name reachable from `handler`, including those
    owned by sub-handlers declared through `_sub_handlers`."""
    names = set()
    sub = getattr(handler, '_sub_handlers', None)
    if sub:
        for s in sub:
            names |= all_command_names(s)
    names |= collect_command_names(handler)
    return names


def build_dispatch_map(handlers):
    """Build {command_name: handler_instance} from an iterable of
    handler instances.

    A handler with `_sub_handlers` claims every command reachable
    through its subtree.  This is how base.py's map routes a layer
    command to the single LayerHandler instance, which then routes
    the same command to the appropriate sub-handler via its own
    map — the two-level dispatch structure is preserved, but neither
    level needs a manual list of command names.

    Raises if the same command name is reachable from two handlers
    in the same call.  Without the check, a duplicated name would
    silently shadow one handler, with the winner depending on
    iteration order.
    """
    out = {}
    for handler in handlers:
        for cmd in all_command_names(handler):
            if cmd in out:
                raise RuntimeError(
                    f"Command {cmd!r} is registered by two handlers: "
                    f"{type(out[cmd]).__name__} and "
                    f"{type(handler).__name__}"
                )
            out[cmd] = handler
    return out
