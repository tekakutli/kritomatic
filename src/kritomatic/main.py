#!/usr/bin/env python3
"""
Krita Controller - Command-line interface for Krita
"""

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kritomatic.compiler import CommandCompiler
from kritomatic.decorators import get_client, get_registry
from kritomatic.batch import BatchExecutor, BashConverter, BatchLibrary
from kritomatic.registry import get_registry_manager


def main():
    # First, try to load dynamic commands
    try:
        import commands_generated
        from decorators import get_registry
        registry = get_registry()
        has_dynamic = bool(registry)
    except ImportError:
        registry = {}
        has_dynamic = False

    # Create parser with dynamic commands
    parser = argparse.ArgumentParser(
        prog='kritomatic',
        description='kritomatic - Control Krita from the command line',
        epilog='Examples:\n'
               '  kritomatic brush size 75\n'
               '  kritomatic layer create "My Layer"\n'
               '  kritomatic layer fill "My Layer" --color "#ff0000"\n'
               '  kritomatic layer add-text "Text Layer" --text "Hello" --x 400 --y 300\n'
               '  kritomatic doc new --width 1920 --height 1080\n'
               '  kritomatic batch run \'{"commands": [...]}\'\n'
               '  kritomatic compile'
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands', required=True)

    # ========== MANAGEMENT COMMANDS ==========
    subparsers.add_parser('compile', help='Compile atomic YAMLs to Python')

    import_parser = subparsers.add_parser('import', help='Import a command bundle (file path or pasted YAML)')
    import_parser.add_argument('bundle_source', help='Path to bundle file (.kritomatic.yaml) or pasted YAML string')

    export_parser = subparsers.add_parser('export', help='Export specific commands as a bundle')
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

    # ========== BATCH COMMANDS ==========
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

    # ========== DYNAMIC COMMANDS ==========
    if has_dynamic:
        # Group commands by category
        categories = {}
        for (category, cmd_name), cmd_info in registry.items():
            if category not in categories:
                categories[category] = {}
            categories[category][cmd_name] = cmd_info

        for category, cmds in categories.items():
            cat_parser = subparsers.add_parser(category, help=f'{category} operations')
            cmd_subparsers = cat_parser.add_subparsers(dest='subcommand', help=f'{category} commands', required=True)

            for cmd_name, cmd_info in cmds.items():
                cmd_parser = cmd_subparsers.add_parser(cmd_name, help=cmd_info['help'])
                func = cmd_info['func']
                if hasattr(func, '_args'):
                    for arg_name, arg_kwargs in func._args:
                        cmd_parser.add_argument(arg_name, **arg_kwargs)
                cmd_parser.set_defaults(func=func)

    # Parse arguments
    if len(sys.argv) == 1:
        parser.print_help()
        return

    args = parser.parse_args()

    # Handle management commands
    if args.command == 'compile':
        compiler = CommandCompiler()
        compiler.compile()
        get_registry_manager().invalidate_cache()

    elif args.command == 'import':
        compiler = CommandCompiler()
        compiler.import_bundle(args.bundle_source)
        get_registry_manager().invalidate_cache()

    elif args.command == 'export':
        compiler = CommandCompiler()
        compiler.export_bundle(args.output, args.category, args.command)

    elif args.command == 'export-all':
        compiler = CommandCompiler()
        compiler.export_bundle(args.output, None, None)

    elif args.command == 'export-schema':
        compiler = CommandCompiler()
        compiler.export_batch_schema(args.output)

    elif args.command == 'remove':
        compiler = CommandCompiler()
        compiler.remove_command(args.category, args.command)
        get_registry_manager().invalidate_cache()

    elif args.command == 'list':
        registry_mgr = get_registry_manager()
        registry_mgr.list_commands(
            verbose=getattr(args, 'verbose', False),
            tree=getattr(args, 'tree', False),
            category=getattr(args, 'category', None)
        )

    elif args.command == 'clear':
        compiler = CommandCompiler()
        compiler.clear_all_commands()
        get_registry_manager().invalidate_cache()

    elif args.command == 'clear-category':
        compiler = CommandCompiler()
        compiler.clear_category(args.category)
        get_registry_manager().invalidate_cache()

    # Handle batch commands
    elif args.command == 'batch':
        if args.batch_command == 'run':
            executor = BatchExecutor()
            try:
                results = executor.execute_from_json(args.json_string)
                successful = sum(1 for r in results if r['status'] == 'success')
                failed = sum(1 for r in results if r['status'] == 'error')
                print(f"\n✓ Batch complete: {successful} succeeded, {failed} failed")
                if failed > 0:
                    print("\nFailed commands:")
                    for r in results:
                        if r['status'] == 'error':
                            print(f"  ✗ {r['command']}: {r['message']}")
            except json.JSONDecodeError as e:
                print(f"❌ Invalid JSON: {e}")
            finally:
                executor.close()

        elif args.batch_command == 'file':
            executor = BatchExecutor()
            try:
                results = executor.execute_from_file(args.file_path)
                successful = sum(1 for r in results if r['status'] == 'success')
                failed = sum(1 for r in results if r['status'] == 'error')
                print(f"\n✓ Batch complete: {successful} succeeded, {failed} failed")
                if failed > 0:
                    print("\nFailed commands:")
                    for r in results:
                        if r['status'] == 'error':
                            print(f"  ✗ {r['command']}: {r['message']}")
            except FileNotFoundError:
                print(f"❌ File not found: {args.file_path}")
            except json.JSONDecodeError as e:
                print(f"❌ Invalid JSON in file: {e}")
            finally:
                executor.close()

        elif args.batch_command == 'save':
            library = BatchLibrary()
            try:
                batch_data = json.loads(args.json_string)
                if library.save(args.name, batch_data):
                    print(f"✓ Batch '{args.name}' saved")
                    info = library.get_info(args.name)
                    print(f"  📁 {info['path']}")
                    print(f"  📊 {info['command_count']} commands")
                else:
                    print(f"❌ Failed to save batch '{args.name}'")
            except json.JSONDecodeError as e:
                print(f"❌ Invalid JSON: {e}")

        elif args.batch_command == 'run-saved':
            library = BatchLibrary()
            batch_data = library.load(args.name)
            if batch_data:
                executor = BatchExecutor()
                results = executor.execute(batch_data)
                successful = sum(1 for r in results if r['status'] == 'success')
                failed = sum(1 for r in results if r['status'] == 'error')
                print(f"\n✓ Batch '{args.name}' (ID: {executor.get_batch_id()}) complete: {successful} succeeded, {failed} failed")
                if failed > 0:
                    print("\nFailed commands:")
                    for r in results:
                        if r['status'] == 'error':
                            print(f"  ✗ {r['command']}: {r['message']}")
                executor.close()
            else:
                print(f"❌ Batch '{args.name}' not found")

        elif args.batch_command == 'list-saved':
            library = BatchLibrary()
            batches = library.list_batches()
            if batches:
                print(f"\n📦 Saved batches ({len(batches)}):")
                for name in batches:
                    info = library.get_info(name)
                    print(f"  📄 {name} - {info['command_count']} commands")
            else:
                print("No saved batches found")

        elif args.batch_command == 'info':
            library = BatchLibrary()
            info = library.get_info(args.name)
            if info:
                print(f"\n📄 Batch: {info['name']}")
                print(f"   ID: {info['id']}")
                print(f"   Commands: {info['command_count']}")
                print(f"   Path: {info['path']}")
                batch = library.load(args.name)
                if batch:
                    print("\n   Commands:")
                    for i, cmd in enumerate(batch.get('commands', [])[:5]):
                        print(f"     {i+1}. {cmd.get('type')}")
                    if len(batch.get('commands', [])) > 5:
                        print(f"     ... and {len(batch['commands']) - 5} more")
            else:
                print(f"❌ Batch '{args.name}' not found")

        elif args.batch_command == 'delete':
            library = BatchLibrary()
            if library.delete(args.name):
                print(f"✓ Batch '{args.name}' deleted")
            else:
                print(f"❌ Batch '{args.name}' not found")

        elif args.batch_command == 'translate':
            converter = BashConverter()
            try:
                batch = converter.convert_file(args.script_file, None)
                if args.save:
                    library = BatchLibrary()
                    if library.save(args.save, batch):
                        print(f"✓ Script converted and saved as batch '{args.save}'")
                        info = library.get_info(args.save)
                        print(f"  📁 {info['path']}")
                        print(f"  📊 {info['command_count']} commands")
                    else:
                        print(f"❌ Failed to save batch '{args.save}'")
                else:
                    print(json.dumps(batch, indent=2))
            except FileNotFoundError:
                print(f"❌ Script file not found: {args.script_file}")
            except Exception as e:
                print(f"❌ Error: {e}")

    # Handle dynamic commands
    elif hasattr(args, 'func'):
        client = get_client()
        kwargs = {k: v for k, v in vars(args).items()
                 if k not in ['func', 'command', 'subcommand'] and v is not None}
        result = args.func(client, **kwargs)
        if result:
            print(json.dumps(result, indent=2))
        client.close()

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
