"""Unified command registry with JSON cache and parser caching"""

import json
import argparse
import hashlib
from pathlib import Path
from typing import Dict, Any, Optional, List
from functools import lru_cache
from datetime import datetime


class CommandRegistry:
    """Manages the unified command registry with caching"""

    def __init__(self, cache_path: Optional[Path] = None):
        if cache_path is None:
            project_dir = Path(__file__).parent
            cache_path = project_dir / '.command_registry.json'
        self.cache_path = cache_path
        self._registry = None
        self._parser = None

    def _get_registry_paths(self) -> List[Path]:
        """Get all source files that affect the registry"""
        paths = []

        # Dynamic commands from YAMLs
        yaml_dir = Path(__file__).parent / 'commands'
        if yaml_dir.exists():
            paths.extend(yaml_dir.glob('*.yaml'))

        # Registry cache itself
        if self.cache_path.exists():
            paths.append(self.cache_path)

        return paths

    def _get_current_hash(self) -> str:
        """Compute hash of all source files"""
        paths = self._get_registry_paths()
        hasher = hashlib.md5()

        for path in sorted(paths):
            hasher.update(str(path).encode())
            if path.exists():
                hasher.update(str(path.stat().st_mtime).encode())

        return hasher.hexdigest()

    def _load_base_registry(self) -> Dict[str, Any]:
        """Load base registry with hardcoded commands"""
        return {
            "version": 1,
            "commands": {
                "compile": {
                    "type": "management",
                    "help": "Compile atomic YAMLs to Python",
                    "args": []
                },
                "import": {
                    "type": "management",
                    "help": "Import a command bundle",
                    "args": [
                        {"name": "bundle_file", "required": True, "help": "Path to the bundle file (.kritomatic.yaml)"}
                    ]
                },
                "export": {
                    "type": "management",
                    "help": "Export specific commands as a bundle",
                    "args": [
                        {"name": "-o", "dest": "output", "help": "Output file path"},
                        {"name": "--category", "dest": "category", "help": "Export only commands in this category"},
                        {"name": "--command", "dest": "command", "help": "Export only this command"}
                    ]
                },
                "export-all": {
                    "type": "management",
                    "help": "Export ALL commands as a bundle",
                    "args": [
                        {"name": "-o", "dest": "output", "required": True, "help": "Output file path"}
                    ]
                },
                "export-schema": {
                    "type": "management",
                    "help": "Export batch schema JSON",
                    "args": [
                        {"name": "-o", "dest": "output", "help": "Output file path"}
                    ]
                },
                "remove": {
                    "type": "management",
                    "help": "Remove a command",
                    "args": [
                        {"name": "category", "required": True, "help": "Command category"},
                        {"name": "command", "required": True, "help": "Command name"}
                    ]
                },
                "list": {
                    "type": "management",
                    "help": "List all available commands",
                    "args": [
                        {"name": "--verbose", "action": "store_true", "help": "Show detailed information"},
                        {"name": "-v", "action": "store_true", "help": "Alias for --verbose"},
                        {"name": "--tree", "action": "store_true", "help": "Show hierarchical view"},
                        {"name": "--category", "help": "Show only commands in this category"}
                    ]
                },
                "clear": {
                    "type": "management",
                    "help": "Delete ALL commands (confirmation required)",
                    "args": []
                },
                "clear-category": {
                    "type": "management",
                    "help": "Delete all commands in a category",
                    "args": [
                        {"name": "category", "required": True, "help": "Category to clear"}
                    ]
                },

                # Batch commands (as a category)
                "batch": {
                    "type": "category",
                    "help": "Batch operations",
                    "subcommands": {
                        "run": {
                            "help": "Run a batch from JSON string",
                            "args": [
                                {"name": "json_string", "required": True, "help": "JSON string with batch commands"}
                            ]
                        },
                        "file": {
                            "help": "Run a batch from JSON file",
                            "args": [
                                {"name": "file_path", "required": True, "help": "Path to JSON batch file"}
                            ]
                        },
                        "save": {
                            "help": "Save a batch to the library",
                            "args": [
                                {"name": "name", "required": True, "help": "Name for the saved batch"},
                                {"name": "json_string", "required": True, "help": "JSON string with batch commands"}
                            ]
                        },
                        "run-saved": {
                            "help": "Run a saved batch from library",
                            "args": [
                                {"name": "name", "required": True, "help": "Name of the saved batch"}
                            ]
                        },
                        "list-saved": {
                            "help": "List all saved batches in library",
                            "args": []
                        },
                        "info": {
                            "help": "Show information about a saved batch",
                            "args": [
                                {"name": "name", "required": True, "help": "Name of the saved batch"}
                            ]
                        },
                        "delete": {
                            "help": "Delete a saved batch from library",
                            "args": [
                                {"name": "name", "required": True, "help": "Name of the saved batch"}
                            ]
                        },
                        "translate": {
                            "help": "Convert bash script to JSON",
                            "args": [
                                {"name": "script_file", "required": True, "help": "Path to bash script file"},
                                {"name": "--save", "help": "Save to library with this name"}
                            ]
                        }
                    }
                }
            }
        }

    def _load_dynamic_commands(self) -> Dict[str, Any]:
        """Load dynamic commands from compiled YAMLs and attach functions"""
        commands = {}

        try:
            import commands_generated
            from decorators import get_registry

            registry = get_registry()
            if not registry:
                return commands

            # Group by category
            categories = {}
            for (category, cmd_name), cmd_info in registry.items():
                if category not in categories:
                    categories[category] = []
                categories[category].append((cmd_name, cmd_info))

            # Convert to registry format
            for category, cmds in categories.items():
                commands[category] = {
                    "type": "category",
                    "help": f"{category} operations",
                    "subcommands": {}
                }

                for cmd_name, cmd_info in cmds:
                    # Extract args from function
                    args = []
                    func = cmd_info['func']
                    if hasattr(func, '_args'):
                        for arg_name, arg_kwargs in func._args:
                            arg_def = {
                                "name": arg_name,
                                "help": arg_kwargs.get('help', '')
                            }
                            if 'required' in arg_kwargs:
                                arg_def["required"] = arg_kwargs["required"]
                            if 'default' in arg_kwargs:
                                arg_def["default"] = arg_kwargs["default"]
                            if 'choices' in arg_kwargs:
                                arg_def["choices"] = arg_kwargs["choices"]
                            if arg_name.startswith('--'):
                                arg_def["optional"] = True
                            args.append(arg_def)

                    commands[category]["subcommands"][cmd_name] = {
                        "help": cmd_info['help'],
                        "args": args,
                        "func": func
                    }

        except ImportError:
            pass

        return commands

    def build_registry(self, force: bool = False) -> Dict[str, Any]:
        """Build the complete registry from all sources"""
        current_hash = self._get_current_hash()

        # Check cache
        if not force and self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    cached = json.load(f)
                    if cached.get('hash') == current_hash:
                        self._registry = cached.get('registry')
                        return self._registry
            except:
                pass

        # Build fresh registry
        registry = self._load_base_registry()
        dynamic = self._load_dynamic_commands()

        # Merge dynamic commands
        for category, data in dynamic.items():
            if category not in registry['commands']:
                registry['commands'][category] = data
            else:
                if 'subcommands' in data:
                    if 'subcommands' not in registry['commands'][category]:
                        registry['commands'][category]['subcommands'] = {}
                    registry['commands'][category]['subcommands'].update(data['subcommands'])

        # Add hash and timestamp
        registry['hash'] = current_hash
        registry['last_updated'] = datetime.now().isoformat()

        # Save cache
        serializable_registry = self._make_serializable(registry)
        with open(self.cache_path, 'w') as f:
            json.dump({'hash': current_hash, 'registry': serializable_registry}, f, indent=2)

        self._registry = registry
        return registry

    def _make_serializable(self, registry):
        """Remove non-serializable items (like functions) for JSON cache"""
        import copy
        result = copy.deepcopy(registry)

        for cmd_name, cmd_info in result.get('commands', {}).items():
            if cmd_info.get('type') == 'category':
                for subcmd, subinfo in cmd_info.get('subcommands', {}).items():
                    if 'func' in subinfo:
                        del subinfo['func']

        return result

    def get_registry(self) -> Dict[str, Any]:
        """Get the registry, building if necessary"""
        if self._registry is None:
            self.build_registry()
        return self._registry

    @lru_cache(maxsize=1)
    def get_parser(self):
        """Get cached argument parser"""
        return self._build_parser()

    def _build_parser(self):
        """Build argparse parser from registry"""
        import argparse

        registry = self.get_registry()

        parser = argparse.ArgumentParser(
            prog='kritomatic',
            description='kritomatic - Control Krita from the command line'
        )

        subparsers = parser.add_subparsers(dest='command', help='Commands', required=True)

        for cmd_name, cmd_info in registry['commands'].items():
            if cmd_info['type'] == 'management':
                mgmt_parser = subparsers.add_parser(cmd_name, help=cmd_info['help'])
                for arg in cmd_info.get('args', []):
                    if arg.get('action') == 'store_true':
                        mgmt_parser.add_argument(arg['name'], action='store_true', help=arg.get('help', ''))
                    elif arg.get('dest'):
                        mgmt_parser.add_argument(arg['name'], dest=arg['dest'], help=arg.get('help', ''))
                    else:
                        mgmt_parser.add_argument(arg['name'], help=arg.get('help', ''))

            elif cmd_info['type'] == 'category':
                cat_parser = subparsers.add_parser(cmd_name, help=cmd_info['help'])
                cmd_subparsers = cat_parser.add_subparsers(dest='subcommand', help=f'{cmd_name} commands', required=True)

                for subcmd_name, subcmd_info in cmd_info.get('subcommands', {}).items():
                    subcmd_parser = cmd_subparsers.add_parser(subcmd_name, help=subcmd_info['help'])

                    if 'func' in subcmd_info:
                        subcmd_parser.set_defaults(func=subcmd_info['func'])

                    for arg in subcmd_info.get('args', []):
                        if arg.get('action') == 'store_true':
                            subcmd_parser.add_argument(arg['name'], action='store_true', help=arg.get('help', ''))
                        elif arg.get('optional') or arg['name'].startswith('--'):
                            subcmd_parser.add_argument(arg['name'], help=arg.get('help', ''))
                        else:
                            subcmd_parser.add_argument(arg['name'], help=arg.get('help', ''))

        return parser

    def invalidate_cache(self):
        """Invalidate the parser cache"""
        self.get_parser.cache_clear()
        self._registry = None

    def list_commands(self, verbose: bool = False, tree: bool = False, category: str = None):
        """List commands with various formatting options"""
        registry = self.get_registry()

        # Show only specific category
        if category:
            if category not in registry['commands']:
                print(f"❌ Category '{category}' not found")
                print(f"Available categories: {', '.join(registry['commands'].keys())}")
                return

            cmd_info = registry['commands'][category]

            if cmd_info['type'] == 'category':
                print(f"\n📁 {category} - {cmd_info['help']}")
                print("-" * 40)

                subcommands = cmd_info.get('subcommands', {})
                if not subcommands:
                    print("  No subcommands found")
                    return

                for subcmd_name, subcmd_info in sorted(subcommands.items()):
                    if verbose:
                        print(f"\n  {subcmd_name}")
                        print(f"    {subcmd_info['help']}")
                        if subcmd_info.get('args'):
                            print("    Arguments:")
                            for arg in subcmd_info['args']:
                                required = "required" if arg.get('required') else "optional"
                                default_info = f" (default: {arg['default']})" if arg.get('default') else ""
                                print(f"      {arg['name']} ({required}){default_info}")
                                print(f"        {arg.get('help', '')}")
                    elif tree:
                        print(f"  ├── {subcmd_name}")
                        print(f"  │   └── {subcmd_info['help']}")
                    else:
                        print(f"  {subcmd_name}")
            else:
                print(f"  {category} - {cmd_info['help']}")
            return

        # Tree view
        if tree:
            print("\n📁 Kritomatic Commands")
            print("=" * 40)
            for cmd_name, cmd_info in sorted(registry['commands'].items()):
                if cmd_info['type'] == 'management':
                    print(f"\n📄 {cmd_name}")
                    print(f"    └── {cmd_info['help']}")
                elif cmd_info['type'] == 'category':
                    print(f"\n📁 {cmd_name} - {cmd_info['help']}")
                    for subcmd, subinfo in sorted(cmd_info.get('subcommands', {}).items()):
                        print(f"    ├── {subcmd}")
                        print(f"    │   └── {subinfo['help']}")
            return

        # Verbose list
        if verbose:
            print("\n" + "=" * 60)
            print("KRITOMATIC - ALL COMMANDS")
            print("=" * 60)

            for cmd_name, cmd_info in sorted(registry['commands'].items()):
                if cmd_info['type'] == 'management':
                    print(f"\n{cmd_name.upper()}")
                    print(f"  {cmd_info['help']}")
                    if cmd_info.get('args'):
                        print("  Arguments:")
                        for arg in cmd_info['args']:
                            required = "required" if arg.get('required') else "optional"
                            print(f"    {arg['name']} ({required}) - {arg.get('help', '')}")

                elif cmd_info['type'] == 'category':
                    print(f"\n{cmd_name.upper()}")
                    print(f"  {cmd_info['help']}")
                    for subcmd, subinfo in sorted(cmd_info.get('subcommands', {}).items()):
                        print(f"\n  {subcmd}")
                        print(f"    {subinfo['help']}")
                        if subinfo.get('args'):
                            print("    Arguments:")
                            for arg in subinfo['args']:
                                required = "required" if arg.get('required') else "optional"
                                default_info = f" (default: {arg['default']})" if arg.get('default') else ""
                                print(f"      {arg['name']} ({required}){default_info}")
                                print(f"        {arg.get('help', '')}")
            return

        # Simple list (default)
        print("\nAvailable commands:")
        print("-" * 40)

        management = []
        categories = []

        for cmd_name, cmd_info in registry['commands'].items():
            if cmd_info['type'] == 'management':
                management.append(cmd_name)
            elif cmd_info['type'] == 'category':
                categories.append(cmd_name)

        if management:
            print("\nManagement commands:")
            for cmd in sorted(management):
                print(f"  {cmd}")

        if categories:
            print("\nCategories:")
            for cat in sorted(categories):
                print(f"  {cat}")

        print("\nUse 'kritomatic list --category <name>' for category details")
        print("Use 'kritomatic list --verbose' for detailed information")
        print("Use 'kritomatic list --tree' for hierarchical view")


# Global instance
_registry = None

def get_registry_manager():
    global _registry
    if _registry is None:
        _registry = CommandRegistry()
    return _registry
