"""Decorators for registering commands, and the machinery that lets
each handler discover and invoke its own commands without a manual
dispatch list.

Three layers cooperate here:

  - @command stamps metadata on the decorated function: its name, its
    declared bundle args, an optional arg-rename map, and the schema
    defaults to apply when the caller omits an arg.

  - build_handler_map() reads those markers back at handler-construction
    time and returns {command_name: caller}, where a caller is a
    zero-arg-closure taking (cmd_type, params) and returning the result.

  - AutoDispatchHandler is the base class every handler inherits from.
    Its __init__ builds the map; its execute() consults it.  Subclasses
    that own sub-handlers declare them in `_sub_handlers`, and the map
    builder routes through them.

Two calling conventions are supported, inferred from the method's own
signature:

    def foo(self, params):  →  method(params)
        A single parameter named `params` means the method handles the
        whole bundle dict itself.

    def foo(self, value, other=0):  →  method(value=..., other=...)
        Any other signature is called with keyword arguments, built by
        forwarding each declared bundle arg (renamed via arg_map when
        the method's parameter is spelled differently) and applying the
        schema's default when the caller omitted the arg.

Adding a command is now a same-file, single-method edit: write the
decorated method, and dispatch picks it up.
"""

import inspect

from .registry import register_command


def command(category, help_text, args, arg_map=None):
    """
    Register a method as a dispatchable command.

    Args:
        category:  schema category, e.g. 'layer', 'brush'.
        help_text: one-line description shown in the generated manual.
        args:      schema arg spec, keyed by '--name', as before.
        arg_map:   optional {bundle_arg: method_param} rename map.
                   Bundle args not listed are forwarded under their own
                   name.  Ignored for methods that take `params`.
    """
    def decorator(func):
        command_name = func.__name__
        register_command(category, command_name, help_text, args)

        # Markers read back by build_handler_map().
        func._kritomatic_command_name = command_name
        func._kritomatic_arg_map = dict(arg_map) if arg_map else {}
        func._kritomatic_bundle_args = [
            a.lstrip('-') for a in args.keys()
        ]
        func._kritomatic_bundle_defaults = {
            a.lstrip('-'): spec['default']
            for a, spec in args.items()
            if 'default' in spec
        }
        return func
    return decorator


# ----------------------------------------------------------------------
#  Introspection
# ----------------------------------------------------------------------

def collect_command_names(handler):
    """Names of the @command-decorated methods defined on `handler`."""
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
    """Every command name reachable from `handler`, including aliases
    and, for container handlers, everything declared through
    `_sub_handlers`."""
    names = set()
    sub = getattr(handler, '_sub_handlers', None)
    if sub:
        for s in sub:
            names |= all_command_names(s)
    names |= collect_command_names(handler)
    names |= set(getattr(handler, '_aliases', {}).keys())
    return names


# ----------------------------------------------------------------------
#  Invocation
# ----------------------------------------------------------------------

def _make_leaf_caller(method):
    """Return a callable (cmd_type, params) -> result for a single
    @command-decorated bound method, applying the right convention."""
    func = method.__func__
    bundle_args = getattr(func, '_kritomatic_bundle_args', [])
    bundle_defaults = getattr(func, '_kritomatic_bundle_defaults', {})
    arg_map = getattr(func, '_kritomatic_arg_map', {})

    sig = inspect.signature(func)
    param_names = [p for p in sig.parameters if p != 'self']
    passes_params = (param_names == ['params'])

    if passes_params:
        def caller(cmd_type, params):
            return method(params)
        return caller

    def caller(cmd_type, params):
        kwargs = {}
        for bundle_arg in bundle_args:
            if bundle_arg in params:
                value = params[bundle_arg]
            elif bundle_arg in bundle_defaults:
                value = bundle_defaults[bundle_arg]
            else:
                # Required arg with no schema default and not
                # supplied by the caller.  Skip; the method's own
                # signature will raise a clear TypeError.
                continue
            method_param = arg_map.get(bundle_arg, bundle_arg)
            kwargs[method_param] = value
        return method(**kwargs)
    return caller


def build_handler_map(handler):
    """{command_name: callable(cmd_type, params) -> result}.

    For a container handler (one with `_sub_handlers`), routes each
    command to the owning sub-handler's execute.  For a leaf handler,
    routes each command to a caller that invokes the bound method
    with the correct convention.

    Raises if the same command name is claimed by two different
    sources — that would otherwise be a silent shadow at runtime.
    """
    sub = getattr(handler, '_sub_handlers', None)
    if sub:
        out = {}
        for s in sub:
            for name in all_command_names(s):
                if name in out:
                    raise RuntimeError(
                        f"Command {name!r} is registered by two "
                        f"sub-handlers: {type(out[name]).__name__} "
                        f"and {type(s).__name__}"
                    )
                out[name] = s.execute
        for alias, target in getattr(handler, '_aliases', {}).items():
            if target in out:
                out[alias] = out[target]
        return out

    out = {}
    for name in dir(handler):
        attr = getattr(handler, name, None)
        if not callable(attr):
            continue
        func = getattr(attr, '__func__', attr)
        cmd_name = getattr(func, '_kritomatic_command_name', None)
        if cmd_name:
            out[cmd_name] = _make_leaf_caller(attr)
    for alias, target in getattr(handler, '_aliases', {}).items():
        if target in out:
            out[alias] = out[target]
    return out


# ----------------------------------------------------------------------
#  Base class
# ----------------------------------------------------------------------

class AutoDispatchHandler:
    """Base for every handler.  Builds its own dispatch map at init and
    routes `execute(cmd_type, params)` through it.

    Subclasses that own state call `super().__init__()` after their own
    state is initialized.  Container subclasses set `_sub_handlers`
    before calling super.
    """
    def __init__(self):
        self._cmd_map = build_handler_map(self)

    def execute(self, cmd_type, params):
        caller = self._cmd_map.get(cmd_type)
        if caller is None:
            return {'success': False,
                    'message': f'Unknown {type(self).__name__} command: '
                               f'{cmd_type}'}
        return caller(cmd_type, params)
