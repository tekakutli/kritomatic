"""Unified command registry with JSON cache and parser caching"""

import json
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
            cache_path = project_dir / '.schema_cache.json'
        self.cache_path = cache_path
        self._registry = None
        self._cached_version = None

    def _get_cached_version(self) -> Optional[str]:
        """Get version from cached schema"""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    cache = json.load(f)
                    return cache.get('_version')
            except:
                pass
        return None

    def _save_cache(self, data: Dict, version: str):
        """Save schema to cache with version"""
        cache_data = {
            '_version': version,
            'commands': data
        }
        with open(self.cache_path, 'w') as f:
            json.dump(cache_data, f, indent=2)
        self._cached_version = version

    def _load_cache(self) -> Optional[Dict]:
        """Load schema from cache"""
        if self.cache_path.exists():
            try:
                with open(self.cache_path, 'r') as f:
                    cache = json.load(f)
                    self._cached_version = cache.get('_version')
                    return cache.get('commands', {})
            except:
                pass
        return None

    def get_daemon_version(self, client) -> Optional[str]:
        """Get version from daemon without full schema"""
        try:
            response = client.execute('get_schema')
            if response and response.get('status') == 'success':
                return response.get('version')
            return None
        except Exception:
            return None

    def refresh_from_daemon(self, client, force=False):
        """Query daemon for current schema and save to cache"""
        try:
            response = client.execute('get_schema')

            if response and response.get('status') == 'success':
                version = response.get('version', 'unknown')
                data = response.get('data', {})

                if data:
                    self._save_cache(data, version)
                    self._registry = None
                    self.get_parser.cache_clear()
                    print(f"✓ Saved schema with {len(data)} commands (version: {version})")
                    return True
                else:
                    print("❌ Schema data is empty")
                    return False
            else:
                print(f"❌ Response status not success: {response.get('status') if response else 'None'}")
                return False
        except Exception as e:
            print(f"❌ Failed to refresh schema: {e}")
            return False

    def ensure_fresh(self, client, force=False):
        """Check if schema is fresh, refresh if needed"""
        cached_version = self._get_cached_version()
        daemon_version = self.get_daemon_version(client)

        if force or cached_version != daemon_version:
            if cached_version is None and daemon_version is None:
                # Both missing, still need to refresh to get data
                print("No schema cached, fetching from daemon...")
                return self.refresh_from_daemon(client)
            elif cached_version != daemon_version:
                print(f"Schema version mismatch (cached: {cached_version}, daemon: {daemon_version}), refreshing...")
                return self.refresh_from_daemon(client)

        # Check if cache actually has data
        if self._load_cache() is None:
            print("Cache exists but no data, refreshing...")
            return self.refresh_from_daemon(client)

        return True

    def get_registry(self, client=None, auto_refresh=True) -> Dict[str, Any]:
        """Get the registry from cache, optionally refreshing if stale"""
        # Try to load from cache first
        cached = self._load_cache()
        if cached:
            self._registry = cached
            return self._registry

        # No cache, need to fetch
        if client and auto_refresh:
            if self.refresh_from_daemon(client):
                cached = self._load_cache()
                if cached:
                    self._registry = cached
                    return self._registry

        self._registry = {}
        return self._registry

    @lru_cache(maxsize=1)
    def get_parser(self):
        """Get cached argument parser"""
        return self._build_parser()

    def _build_parser(self):
        """Build argparse parser from registry schema"""
        import argparse

        registry = self.get_registry()

        parser = argparse.ArgumentParser(
            prog='kritomatic',
            description='Kritomatic - Control Krita from the command line',
            epilog='Examples:\n'
                   '  kritomatic brush size 75\n'
                   '  kritomatic layer create "My Layer"\n'
                   '  kritomatic batch run \'{"commands": [...]}\'\n'
                   '  kritomatic --refresh'
        )

        subparsers = parser.add_subparsers(dest='command', help='Commands', required=True)

        # Group commands by category
        categories = {}
        for cmd_key, cmd_info in registry.items():
            category = cmd_info.get('category', 'unknown')
            if category not in categories:
                categories[category] = []
            categories[category].append(cmd_info)

        # Add management commands
        subparsers.add_parser('compile', help='Compile atomic YAMLs to Python')

        import_parser = subparsers.add_parser('import', help='Import a command bundle')
        import_parser.add_argument('bundle_file', help='Path to the bundle file (.krita.yaml)')

        export_parser = subparsers.add_parser('export', help='Export commands as a bundle')
        export_parser.add_argument('-o', '--output', help='Output file path')
        export_parser.add_argument('--category', help='Export only commands in this category')
        export_parser.add_argument('--command', help='Export only this command')

        export_all_parser = subparsers.add_parser('export-all', help='Export ALL commands as a bundle')
        export_all_parser.add_argument('-o', '--output', required=True, help='Output file path')

        export_schema_parser = subparsers.add_parser('export-schema', help='Export batch schema JSON')
        export_schema_parser.add_argument('-o', '--output', help='Output file path')

        remove_parser = subparsers.add_parser('remove', help='Remove a command')
        remove_parser.add_argument('category', help='Command category')
        remove_parser.add_argument('command', help='Command name')

        list_parser = subparsers.add_parser('list', help='List all available commands')
        list_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed information')
        list_parser.add_argument('--tree', action='store_true', help='Show hierarchical view')
        list_parser.add_argument('--category', help='Show only commands in this category')

        subparsers.add_parser('clear', help='Delete ALL commands (confirmation required)')

        clear_cat_parser = subparsers.add_parser('clear-category', help='Delete all commands in a category')
        clear_cat_parser.add_argument('category', help='Category to clear')

        # Batch commands
        batch_parser = subparsers.add_parser('batch', help='Batch operations')
        batch_subparsers = batch_parser.add_subparsers(dest='batch_command', help='Batch subcommands', required=True)

        batch_subparsers.add_parser('run', help='Run a batch from JSON string').add_argument('json_string', help='JSON string with batch commands')
        batch_subparsers.add_parser('file', help='Run a batch from JSON file').add_argument('file_path', help='Path to JSON batch file')

        save_parser = batch_subparsers.add_parser('save', help='Save a batch to the library')
        save_parser.add_argument('name', help='Name for the saved batch')
        save_parser.add_argument('json_string', help='JSON string with batch commands')

        batch_subparsers.add_parser('run-saved', help='Run a saved batch from library').add_argument('name', help='Name of the saved batch')
        batch_subparsers.add_parser('list-saved', help='List all saved batches in library')
        batch_subparsers.add_parser('info', help='Show information about a saved batch').add_argument('name', help='Name of the saved batch')
        batch_subparsers.add_parser('delete', help='Delete a saved batch from library').add_argument('name', help='Name of the saved batch')

        translate_parser = batch_subparsers.add_parser('translate', help='Convert bash script to JSON')
        translate_parser.add_argument('script_file', help='Path to bash script file')
        translate_parser.add_argument('--save', help='Save to library with this name')

        # Dynamic categories from schema
        for category, commands in categories.items():
            cat_parser = subparsers.add_parser(category, help=f'{category} operations')
            cmd_subparsers = cat_parser.add_subparsers(dest='subcommand', help=f'{category} commands', required=True)

            for cmd_info in commands:
                cmd_name = cmd_info.get('command', 'unknown')
                cmd_help = cmd_info.get('help', '')
                cmd_parser = cmd_subparsers.add_parser(cmd_name, help=cmd_help)

                for arg_name, arg_info in cmd_info.get('args', {}).items():
                    arg_type = arg_info.get('type', 'str')
                    required = arg_info.get('required', False)
                    default = arg_info.get('default')
                    help_text = arg_info.get('help', '')

                    if arg_type == 'int':
                        arg_type = int
                    elif arg_type == 'float':
                        arg_type = float
                    elif arg_type == 'bool':
                        arg_type = bool
                    else:
                        arg_type = str

                    if arg_name.startswith('--'):
                        if required:
                            cmd_parser.add_argument(arg_name, type=arg_type, required=True, help=help_text)
                        elif default is not None:
                            cmd_parser.add_argument(arg_name, type=arg_type, default=default, help=help_text)
                        else:
                            cmd_parser.add_argument(arg_name, type=arg_type, help=help_text)
                    else:
                        if required:
                            cmd_parser.add_argument(arg_name, type=arg_type, help=help_text)
                        elif default is not None:
                            cmd_parser.add_argument(arg_name, type=arg_type, default=default, help=help_text)
                        else:
                            cmd_parser.add_argument(arg_name, type=arg_type, help=help_text)

                # Function will be added later when handlers are decorated
                cmd_parser.set_defaults(func=None)

        return parser

    def invalidate_cache(self):
        """Invalidate the parser cache and schema cache"""
        self.get_parser.cache_clear()
        self._registry = None
        self._cached_version = None
        if self.cache_path.exists():
            self.cache_path.unlink()

    def list_commands(self, verbose: bool = False, tree: bool = False, category: str = None):
        """List commands from the registry"""
        registry = self.get_registry()

        # Group by category
        categories = {}
        for cmd_key, cmd_info in registry.items():
            cat = cmd_info.get('category', 'unknown')
            if cat not in categories:
                categories[cat] = []
            categories[cat].append(cmd_info)

        if category:
            if category not in categories:
                print(f"❌ Category '{category}' not found")
                return
            print(f"\n{category}:")
            for cmd in categories[category]:
                print(f"  {cmd.get('command')}")
            return

        if tree:
            for cat, cmds in categories.items():
                print(f"\n📁 {cat}")
                for cmd in cmds:
                    print(f"    ├── {cmd.get('command')}")
                    print(f"    │   └── {cmd.get('help', '')}")
        elif verbose:
            for cat, cmds in categories.items():
                print(f"\n{cat.upper()}")
                for cmd in cmds:
                    print(f"\n  {cmd.get('command')}")
                    print(f"    {cmd.get('help', '')}")
                    for arg_name, arg_info in cmd.get('args', {}).items():
                        required = "required" if arg_info.get('required') else "optional"
                        print(f"      {arg_name} ({required}) - {arg_info.get('help', '')}")
        else:
            for cat, cmds in categories.items():
                print(f"\n{cat}:")
                for cmd in cmds:
                    print(f"  {cmd.get('command')}")


# Global instance
_registry = None

def get_registry_manager():
    global _registry
    if _registry is None:
        _registry = CommandRegistry()
    return _registry
