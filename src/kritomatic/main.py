#!/usr/bin/env python3
"""
Kritomatic - Command-line interface for Krita
"""

import sys
import argparse
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from decorators import get_client
from batch import BatchExecutor, BashConverter, BatchLibrary
from registry import get_registry_manager


def is_suppress_command():
    """Check if current command should suppress schema refresh messages"""
    if len(sys.argv) < 2:
        return False

    # batch translate
    if len(sys.argv) >= 3 and sys.argv[1] == 'batch' and sys.argv[2] == 'translate':
        return True

    # diffusion export_params (when piping JSON)
    if len(sys.argv) >= 3 and sys.argv[1] == 'diffusion' and sys.argv[2] == 'export_params':
        return True

    return False


def main():
    # ========== HANDLE --refresh FLAG ==========
    if '--refresh' in sys.argv:
        from kritomatic.client import KritaClient

        client = KritaClient()
        if client.connect():
            registry_mgr = get_registry_manager()
            if registry_mgr.refresh_from_daemon(client):
                print("✓ Schema refreshed from daemon")
            else:
                print("❌ Failed to refresh schema. Make sure Krita is running with the daemon enabled.")
            client.close()
        else:
            print("❌ Could not connect to daemon. Make sure Krita is running.")
        return

    # ========== HANDLE diffusion preset subcommands (client-side only) ==========
    if len(sys.argv) >= 3 and sys.argv[1] == 'diffusion' and sys.argv[2] == 'preset':
        from diffusion_preset import run_preset_command
        run_preset_command()
        return

    # ========== NORMAL COMMAND EXECUTION ==========
    registry_mgr = get_registry_manager()
    client = None
    suppress_output = is_suppress_command()

    # For suppress commands, redirect stdout/stderr to null during schema refresh
    if suppress_output:
        import io
        import contextlib
        null_output = io.StringIO()

        with contextlib.redirect_stdout(null_output), contextlib.redirect_stderr(null_output):
            try:
                client = get_client()
                if client.connect():
                    cached_version = registry_mgr._get_cached_version()
                    daemon_version = registry_mgr.get_daemon_version(client)

                    if cached_version is None or cached_version != daemon_version:
                        registry_mgr.refresh_from_daemon(client)
            except Exception:
                pass
            finally:
                if client:
                    client.close()
    else:
        # Normal output - show messages
        try:
            client = get_client()
            if client.connect():
                cached_version = registry_mgr._get_cached_version()
                daemon_version = registry_mgr.get_daemon_version(client)

                if cached_version is None:
                    print("No schema cached, fetching from daemon...")
                    registry_mgr.refresh_from_daemon(client)
                elif cached_version != daemon_version:
                    print(f"Schema version mismatch (cached: {cached_version}, daemon: {daemon_version}), refreshing...")
                    registry_mgr.refresh_from_daemon(client)
                else:
                    print(f"Schema is fresh (version: {cached_version})")
            else:
                print("⚠️ Could not connect to daemon, using cached schema if available")
        except Exception as e:
            print(f"⚠️ Error checking schema: {e}")
        finally:
            if client:
                client.close()

    # Build parser from schema cache
    parser = registry_mgr.get_parser()

    # Parse arguments
    if len(sys.argv) == 1:
        if not suppress_output:
            parser.print_help()
        return

    args = parser.parse_args()

    # Handle management commands
    if args.command == 'compile':
        if not suppress_output:
            print("⚠️ 'compile' command is deprecated. Use '--refresh' to update schema from daemon.")
            print("   The daemon is now the source of truth for commands.")
        return

    elif args.command == 'import':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.import_bundle(args.bundle_file)
        registry_mgr.invalidate_cache()
        if not suppress_output:
            print("⚠️ Imported bundle. Run 'kritomatic --refresh' to update schema from daemon.")
        return

    elif args.command == 'export':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.export_bundle(args.output, args.category, args.command)
        return

    elif args.command == 'export-all':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.export_bundle(args.output, None, None)
        return

    elif args.command == 'export-schema':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.export_batch_schema(args.output)
        return

    elif args.command == 'remove':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.remove_command(args.category, args.command)
        registry_mgr.invalidate_cache()
        if not suppress_output:
            print("⚠️ Command removed. Run 'kritomatic --refresh' to update schema from daemon.")
        return

    elif args.command == 'list':
        if not suppress_output:
            registry_mgr.list_commands(
                verbose=getattr(args, 'verbose', False),
                tree=getattr(args, 'tree', False),
                category=getattr(args, 'category', None)
            )
        return

    elif args.command == 'clear':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.clear_all_commands()
        registry_mgr.invalidate_cache()
        if not suppress_output:
            print("⚠️ All commands cleared. Run 'kritomatic --refresh' to update schema from daemon.")
        return

    elif args.command == 'clear-category':
        from compiler import CommandCompiler
        compiler = CommandCompiler()
        compiler.clear_category(args.category)
        registry_mgr.invalidate_cache()
        if not suppress_output:
            print("⚠️ Category cleared. Run 'kritomatic --refresh' to update schema from daemon.")
        return

    # Handle batch commands
    elif args.command == 'batch':
        if args.batch_command == 'run':
            executor = BatchExecutor()
            try:
                results = executor.execute_from_json(args.json_string)
                successful = sum(1 for r in results if r['status'] == 'success')
                failed = sum(1 for r in results if r['status'] == 'error')
                if not suppress_output:
                    print(f"\n✓ Batch complete: {successful} succeeded, {failed} failed")
                    if failed > 0:
                        print("\nFailed commands:")
                        for r in results:
                            if r['status'] == 'error':
                                print(f"  ✗ {r['command']}: {r['message']}")
            except json.JSONDecodeError as e:
                if not suppress_output:
                    print(f"❌ Invalid JSON: {e}")
            finally:
                executor.close()
            return

        elif args.batch_command == 'file':
            executor = BatchExecutor()
            try:
                results = executor.execute_from_file(args.file_path)
                successful = sum(1 for r in results if r['status'] == 'success')
                failed = sum(1 for r in results if r['status'] == 'error')
                if not suppress_output:
                    print(f"\n✓ Batch complete: {successful} succeeded, {failed} failed")
                    if failed > 0:
                        print("\nFailed commands:")
                        for r in results:
                            if r['status'] == 'error':
                                print(f"  ✗ {r['command']}: {r['message']}")
            except FileNotFoundError:
                if not suppress_output:
                    print(f"❌ File not found: {args.file_path}")
            except json.JSONDecodeError as e:
                if not suppress_output:
                    print(f"❌ Invalid JSON in file: {e}")
            finally:
                executor.close()
            return

        elif args.batch_command == 'save':
            library = BatchLibrary()
            try:
                batch_data = json.loads(args.json_string)
                if library.save(args.name, batch_data):
                    if not suppress_output:
                        print(f"✓ Batch '{args.name}' saved")
                        info = library.get_info(args.name)
                        print(f"  📁 {info['path']}")
                        print(f"  📊 {info['command_count']} commands")
                else:
                    if not suppress_output:
                        print(f"❌ Failed to save batch '{args.name}'")
            except json.JSONDecodeError as e:
                if not suppress_output:
                    print(f"❌ Invalid JSON: {e}")
            return

        elif args.batch_command == 'run-saved':
            library = BatchLibrary()
            batch_data = library.load(args.name)
            if batch_data:
                executor = BatchExecutor()
                results = executor.execute(batch_data)
                successful = sum(1 for r in results if r['status'] == 'success')
                failed = sum(1 for r in results if r['status'] == 'error')
                if not suppress_output:
                    print(f"\n✓ Batch '{args.name}' (ID: {executor.get_batch_id()}) complete: {successful} succeeded, {failed} failed")
                    if failed > 0:
                        print("\nFailed commands:")
                        for r in results:
                            if r['status'] == 'error':
                                print(f"  ✗ {r['command']}: {r['message']}")
                executor.close()
            else:
                if not suppress_output:
                    print(f"❌ Batch '{args.name}' not found")
            return

        elif args.batch_command == 'list-saved':
            if not suppress_output:
                library = BatchLibrary()
                batches = library.list_batches()
                if batches:
                    print(f"\n📦 Saved batches ({len(batches)}):")
                    for name in batches:
                        info = library.get_info(name)
                        print(f"  📄 {name} - {info['command_count']} commands")
                else:
                    print("No saved batches found")
            return

        elif args.batch_command == 'info':
            if not suppress_output:
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
            return

        elif args.batch_command == 'delete':
            library = BatchLibrary()
            if library.delete(args.name):
                if not suppress_output:
                    print(f"✓ Batch '{args.name}' deleted")
            else:
                if not suppress_output:
                    print(f"❌ Batch '{args.name}' not found")
            return

        elif args.batch_command == 'translate':
            converter = BashConverter()
            try:
                batch = converter.convert_file(args.script_file, None)
                if args.save:
                    library = BatchLibrary()
                    if library.save(args.save, batch):
                        if not suppress_output:
                            print(f"✓ Script converted and saved as batch '{args.save}'")
                            info = library.get_info(args.save)
                            print(f"  📁 {info['path']}")
                            print(f"  📊 {info['command_count']} commands")
                    else:
                        if not suppress_output:
                            print(f"❌ Failed to save batch '{args.save}'")
                else:
                    # Only print the JSON output, no extra messages
                    print(json.dumps(batch, indent=2))
            except FileNotFoundError:
                if not suppress_output:
                    print(f"❌ Script file not found: {args.script_file}")
            except Exception as e:
                if not suppress_output:
                    print(f"❌ Error: {e}")
            return

    # Handle dynamic commands (all categories from registry)
    elif hasattr(args, 'subcommand'):
        client = get_client()
        if not client.connect():
            if not suppress_output:
                print("❌ Could not connect to daemon. Make sure Krita is running.")
            return

        cmd_type = args.subcommand
        kwargs = {}
        for key, value in vars(args).items():
            if key not in ['command', 'subcommand', 'func'] and value is not None:
                kwargs[key] = value

        response = client.execute(cmd_type, **kwargs)

        if response:
            if response.get('status') == 'success' and 'json' in response.get('data', {}):
                print(response['data']['json'])
            else:
                print(json.dumps(response, indent=2))

        client.close()

    else:
        if not suppress_output:
            parser.print_help()


if __name__ == '__main__':
    main()
