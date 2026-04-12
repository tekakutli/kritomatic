#!/usr/bin/env python3
import argparse
from client import KritaClient

_command_registry = {}
_client = None

def get_client():
    global _client
    if _client is None:
        _client = KritaClient()
    return _client

def command(category, name, help=None):
    def decorator(func):
        _command_registry[(category, name)] = {
            'func': func,
            'help': help or func.__doc__,
            'category': category,
            'name': name
        }
        return func
    return decorator

def arg(name, **kwargs):
    def decorator(func):
        if not hasattr(func, '_args'):
            func._args = []
        # Filter out kwargs that argparse doesn't accept
        valid_kwargs = {}
        is_optional = name.startswith('--')

        for key, value in kwargs.items():
            if key in ['type', 'default', 'choices', 'help', 'action']:
                valid_kwargs[key] = value
            elif key == 'required':
                if is_optional and value:
                    valid_kwargs[key] = value
            elif key == 'min' or key == 'max':
                continue
            else:
                valid_kwargs[key] = value

        func._args.append((name, valid_kwargs))
        return func
    return decorator

def get_registry():
    return _command_registry
