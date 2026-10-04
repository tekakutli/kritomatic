"""Unified command registry with JSON cache and parser caching"""

import json
from pathlib import Path
from typing import Dict, Any, Optional, List
from functools import lru_cache

from kritomatic.helpful_argparse import HelpfulArgumentParser


def _escape_help_for_argparse(text):
    """
    Escape % in help text so argparse's %-formatting pass renders a
    literal percent sign.

    argparse calls `help_string % params` on every `help=` value it
    receives. A bare `%` in the string is interpreted as the start of a
    format specifier and raises `ValueError: incomplete format`. Doubling
    to `%%` makes argparse emit a literal `%`.

    We do this here rather than asking every @command author to remember
    argparse's quirk. Decorators write natural text ("100%"), the parser
    builder escapes it on the way into argparse.
    """
    if not isinstance(text, str):
        return text
    return text.replace('%', '%%')


class CommandRegistry:
    """Manages the unified command registry with caching"""

    def __init__(self, cache_path: Optional[Path] = None):
        if cache_path is None:
            project_dir = Path(__file__).parent
            cache_path = project_dir / '.schema_cache.json'
        self.cache_path = cache_path
        self._registry = None
        self._cached_version = None

    @staticmethod
    def default_manual_path() -> Path:
        """
        Where `kritomatic export-manual` writes when no --output is
        given: `command_reference.md` at the repo root, next to
        BATCH_MANUAL.md.

        registry.py lives at <repo>/src/kritomatic/registry.py, so three
        parents up is the repo root. This is stable regardless of the
        caller's cwd, and stable across editable installs because
        pipx/pip -e resolves __file__ back to the checkout.
        """
        return Path(__file__).resolve().parent.parent.parent / 'command_reference.md'

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
                version = response.get('version')
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

        if cached_version is None:
            print("No schema cached, fetching from daemon...")
            return self.refresh_from_daemon(client)

        if cached_version == daemon_version:
            print(f"Schema is fresh (version: {cached_version})")
            return True

        print(f"Schema version mismatch (cached: {cached_version}, daemon: {daemon_version}), refreshing...")
        return self.refresh_from_daemon(client)

    def get_registry(self, client=None, auto_refresh=True) -> Dict[str, Any]:
        """Get the registry from cache, optionally refreshing if stale"""
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
        registry = self.get_registry()

        parser = HelpfulArgumentParser(
            prog='kritomatic',
            description='Kritomatic - Control Krita from the command line',
            epilog='Examples:\n'
                   '  kritomatic brush size 75\n'
                   '  kritomatic layer create "My Layer"\n'
                   '  kritomatic batch run \'{"commands": [...]}\'\n'
                   '  kritomatic batch validate \'{"commands": [...]}\'\n'
                   '  kritomatic --refresh\n'
                   '  kritomatic export-manual'
        )

        subparsers = parser.add_subparsers(
            dest='command', help='Commands', metavar='COMMAND', required=True
        )

        # Group commands by category
        categories = {}
        for cmd_key, cmd_info in registry.items():
            category = cmd_info.get('category', 'unknown')
            if category not in categories:
                categories[category] = []
            categories[category].append(cmd_info)

        # Add management commands
        export_manual_parser = subparsers.add_parser(
            'export-manual',
            help='Export auto-generated markdown command reference '
                 '(for BATCH_MANUAL.md and authoring AIs)'
        )
        export_manual_parser.add_argument(
            '-o', '--output',
            help='Output file path. Defaults to command_reference.md at '
                 'the repo root, next to BATCH_MANUAL.md.'
        )
        export_manual_parser.add_argument(
            '--stdout', action='store_true',
            help='Print to stdout instead of writing a file.'
        )
        export_manual_parser.add_argument(
            '--category',
            help='Restrict output to one category'
        )

        list_parser = subparsers.add_parser('list', help='List all available commands')
        list_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed information')
        list_parser.add_argument('--tree', action='store_true', help='Show hierarchical view')
        list_parser.add_argument('--category', help='Show only commands in this category')

        # Batch commands
        batch_parser = subparsers.add_parser('batch', help='Batch operations')
        batch_subparsers = batch_parser.add_subparsers(
            dest='batch_command', help='Batch subcommands', metavar='BATCH_COMMAND', required=True
        )

        run_parser = batch_subparsers.add_parser(
            'run',
            help='Run a batch from a JSON string, or enter interactive '
                 'mode if the JSON string is omitted'
        )
        run_parser.add_argument(
            'json_string', nargs='?',
            help='JSON string with batch commands (omit to enter interactive mode)'
        )

        batch_subparsers.add_parser('file', help='Run a batch from JSON file').add_argument('file_path', help='Path to JSON batch file')

        save_parser = batch_subparsers.add_parser('save', help='Save a batch to the library')
        save_parser.add_argument('name', help='Name for the saved batch')
        save_parser.add_argument('json_string', help='JSON string with batch commands')

        batch_subparsers.add_parser('run-saved', help='Run a saved batch from library').add_argument('name', help='Name of the saved batch')
        batch_subparsers.add_parser('list-saved', help='List all saved batches in library')
        batch_subparsers.add_parser('info', help='Show information about a saved batch').add_argument('name', help='Name of the saved batch')
        batch_subparsers.add_parser('delete', help='Delete a saved batch from library').add_argument('name', help='Name of the saved batch')

        validate_parser = batch_subparsers.add_parser(
            'validate',
            help='Check a bundle against the schema without running it. '
                 'Exit code 0 if valid, 1 if any errors.'
        )
        validate_parser.add_argument(
            'json_string', nargs='?',
            help='JSON string with batch commands (or use --file / --saved)'
        )
        validate_parser.add_argument(
            '--file',
            help='Path to a JSON batch file'
        )
        validate_parser.add_argument(
            '--saved',
            help='Name of a saved batch in the library'
        )
        validate_parser.add_argument(
            '--json', action='store_true',
            help='Output findings as machine-readable JSON'
        )

        translate_parser = batch_subparsers.add_parser('translate', help='Convert bash script to JSON')
        translate_parser.add_argument('script_file', help='Path to bash script file')
        translate_parser.add_argument('--save', help='Save to library with this name')

        # Dynamic categories from schema (including window, brush, layer, etc.)
        for category, commands in categories.items():
            cat_parser = subparsers.add_parser(
                category,
                help=_escape_help_for_argparse(f'{category} operations'),
            )
            cmd_subparsers = cat_parser.add_subparsers(
                dest='subcommand', help=f'{category} commands', metavar='COMMAND', required=True
            )

            for cmd_info in commands:
                cmd_name = cmd_info.get('command', 'unknown')
                cmd_help = cmd_info.get('help', '')
                cmd_parser = cmd_subparsers.add_parser(
                    cmd_name,
                    help=_escape_help_for_argparse(cmd_help),
                )

                for arg_name, arg_info in cmd_info.get('args', {}).items():
                    arg_type = arg_info.get('type', 'str')
                    required = arg_info.get('required', False)
                    default = arg_info.get('default')
                    help_text = _escape_help_for_argparse(arg_info.get('help', ''))

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

                cmd_parser.set_defaults(func=None)

        return parser

    # ------------------------------------------------------------------
    # Auto-generated command reference
    # ------------------------------------------------------------------

    def to_markdown(self, version: Optional[str] = None, category: Optional[str] = None) -> str:
        """
        Emit the full command registry as a markdown reference document.

        This is the enumerative layer that pairs with BATCH_MANUAL.md's
        interpretive layer. It is generated from the schema on demand and
        is never hand-edited. See BATCH_MANUAL.md for how the two fit
        together.

        Args:
            version:  schema version string to stamp into the header.
                      Falls back to the cached version if not given.
            category: if given, restrict output to a single category.
        """
        registry = self.get_registry()
        if version is None:
            version = self._cached_version or 'unknown'

        # Group by category, preserving no particular order yet.
        categories: Dict[str, List[Dict[str, Any]]] = {}
        for cmd_key, cmd_info in registry.items():
            cat = cmd_info.get('category', 'unknown')
            if category is not None and cat != category:
                continue
            categories.setdefault(cat, []).append(cmd_info)

        lines: List[str] = []

        # ---------- header ----------
        lines.append('# Kritomatic Command Reference (auto-generated)')
        lines.append('')
        lines.append(f'> Schema version: `{version}`')
        lines.append('>')
        lines.append('> This file is generated by `kritomatic export-manual`.')
        lines.append('> Do not hand-edit — regenerate with:')
        lines.append('>')
        lines.append('> ```')
        lines.append('> kritomatic export-manual')
        lines.append('> ```')
        lines.append('>')
        lines.append('> Interpretive notes — disambiguation between commands,')
        lines.append('> composition guidance, warnings about non-obvious arg')
        lines.append('> interactions — live in `BATCH_MANUAL.md`, not here.')
        lines.append('')
        lines.append('Arg names below are shown in **bundle form** (no leading')
        lines.append('dashes). The CLI\'s `--help` output uses `--name` spellings;')
        lines.append('a bundle uses the bare `name`.')
        lines.append('')

        # ---------- category index ----------
        lines.append('## Categories')
        lines.append('')
        for cat in sorted(categories.keys()):
            count = len(categories[cat])
            plural = 'command' if count == 1 else 'commands'
            lines.append(f'- **{cat}** — {count} {plural}')
        lines.append('')
        lines.append('---')
        lines.append('')

        # ---------- per-category sections ----------
        for cat in sorted(categories.keys()):
            lines.append(f'## {cat}')
            lines.append('')
            cmds = sorted(categories[cat], key=lambda c: c.get('command', ''))
            for cmd_info in cmds:
                cmd_name = cmd_info.get('command', 'unknown')
                cmd_help = cmd_info.get('help', '') or ''
                args = cmd_info.get('args', {}) or {}

                lines.append(f'### `{cmd_name}`')
                lines.append('')
                if cmd_help:
                    lines.append(cmd_help)
                    lines.append('')

                if not args:
                    lines.append('_No arguments._')
                    lines.append('')
                    continue

                lines.append('**Args:**')
                lines.append('')
                for arg_key, arg_info in args.items():
                    # Normalise the arg name to bundle form.
                    bundle_name = arg_key.lstrip('-')

                    arg_type = arg_info.get('type', 'str')
                    required = bool(arg_info.get('required', False))
                    default = arg_info.get('default')
                    choices = arg_info.get('choices')
                    arg_help = arg_info.get('help', '') or ''

                    # Build the parenthetical descriptor.
                    descr_parts = [arg_type]
                    descr_parts.append('required' if required else 'optional')
                    if default is not None:
                        descr_parts.append(f'default={default!r}')
                    if choices:
                        joined = '/'.join(str(c) for c in choices)
                        descr_parts.append(f'choices={joined}')
                    descr = ', '.join(descr_parts)

                    line = f'- `{bundle_name}` ({descr})'
                    if arg_help:
                        line += f' — {arg_help}'
                    lines.append(line)

                lines.append('')

            lines.append('---')
            lines.append('')

        # ---------- closing note ----------
        lines.append('_End of auto-generated reference._')
        lines.append('')

        return '\n'.join(lines)

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
