"""CLI handling for diffusion preset subcommands"""

import argparse
import json
import sys
from datetime import datetime

from kritomatic.decorators import get_client
from .executor import DiffusionPresetExecutor


def format_preset_list(presets_data, verbose=False):
    """Format preset list for human-readable output"""
    if not presets_data:
        print("No presets found")
        return

    # Sort by name
    presets = sorted(presets_data, key=lambda x: x['name'])

    if verbose:
        # Verbose table format
        print(f"\n{'─' * 80}")
        print(f"{'Name':<20} {'Workflow':<40} {'Params':<8} {'Modified':<20}")
        print(f"{'─' * 20} {'─' * 40} {'─' * 8} {'─' * 20}")
        for p in presets:
            name = p['name'][:18] if len(p['name']) > 18 else p['name']
            workflow = p['workflow'][:38] + '...' if len(p['workflow']) > 40 else p['workflow']
            params = str(p['param_count'])
            if p['modified'] != 'unknown':
                try:
                    dt = datetime.fromisoformat(p['modified'])
                    modified = dt.strftime('%Y-%m-%d %H:%M')
                except:
                    modified = p['modified'][:16]
            else:
                modified = 'unknown'
            print(f"{name:<20} {workflow:<40} {params:<8} {modified:<20}")
        print(f"{'─' * 80}\n")
        print("💡 Tip: Use '--json' for machine-readable output (piping/saving)")
    else:
        # Simple list format
        print(f"\n📦 Diffusion Presets ({len(presets)}):\n")
        for p in presets:
            print(f"  📄 {p['name']}")
            print(f"     Workflow: {p['workflow'][:60]}{'...' if len(p['workflow']) > 60 else ''}")
            print(f"     Parameters: {p['param_count']}")
            if p['modified'] != 'unknown':
                try:
                    dt = datetime.fromisoformat(p['modified'])
                    print(f"     Modified: {dt.strftime('%Y-%m-%d %H:%M')}")
                except:
                    print(f"     Modified: {p['modified']}")
            print()
        print("💡 Use '--verbose' for detailed table or '--json' for machine-readable output")


def run_preset_command():
    """Run the diffusion preset subcommand with proper argparse help"""

    # Create a parser for preset subcommands
    preset_parser = argparse.ArgumentParser(
        prog='kritomatic diffusion preset',
        description='Diffusion preset management - save/load diffusion settings'
    )
    preset_subparsers = preset_parser.add_subparsers(dest='preset_command', help='Preset subcommands', required=True)

    # save
    save_parser = preset_subparsers.add_parser('save', help='Save current settings as a preset')
    save_parser.add_argument('name', help='Preset name')
    save_parser.add_argument('--workflow', help='Optional workflow name (overrides current)')

    # load
    load_parser = preset_subparsers.add_parser('load', help='Load a preset and apply parameters')
    load_parser.add_argument('name', help='Preset name')

    # list
    list_parser = preset_subparsers.add_parser('list', help='List all saved presets')
    list_parser.add_argument('--verbose', '-v', action='store_true', help='Show detailed table format')
    list_parser.add_argument('--json', action='store_true', help='Output as JSON (for piping/saving)')

    # delete
    delete_parser = preset_subparsers.add_parser('delete', help='Delete a preset')
    delete_parser.add_argument('name', help='Preset name')

    # info
    info_parser = preset_subparsers.add_parser('info', help='Show information about a preset')
    info_parser.add_argument('name', help='Preset name')

    # export
    export_parser = preset_subparsers.add_parser('export', help='Export a preset to a file')
    export_parser.add_argument('name', help='Preset name')
    export_parser.add_argument('--output', help='Output file path (prints to stdout if not specified)')

    # import
    import_parser = preset_subparsers.add_parser('import', help='Import a preset from a file')
    import_parser.add_argument('--source', required=True, help='Source file path')
    import_parser.add_argument('--save', help='Save as preset name (optional)')

    # Parse arguments (skip the first three: kritomatic, diffusion, preset)
    args = preset_parser.parse_args(sys.argv[3:])

    client = get_client()
    if not client.connect():
        print("❌ Could not connect to daemon. Make sure Krita is running.")
        return 1

    executor = DiffusionPresetExecutor(client)

    if args.preset_command == 'save':
        result = executor.save(args.name, args.workflow)
        if result:
            print(json.dumps(result, indent=2))

    elif args.preset_command == 'load':
        result = executor.load(args.name)
        if result:
            print(json.dumps(result, indent=2))

    elif args.preset_command == 'list':
        result = executor.list_presets()
        if result and result.get('success'):
            if args.json:
                # JSON output for piping
                print(json.dumps(result, indent=2))
            else:
                # Human-readable output (default)
                format_preset_list(result.get('data', {}).get('presets', []), args.verbose)
        elif result:
            print(json.dumps(result, indent=2))

    elif args.preset_command == 'delete':
        result = executor.delete(args.name)
        if result:
            print(json.dumps(result, indent=2))

    elif args.preset_command == 'info':
        result = executor.info(args.name)
        if result:
            print(json.dumps(result, indent=2))

    elif args.preset_command == 'export':
        result = executor.export_preset(args.name, args.output)
        if result:
            if not args.output:
                # Already printed in executor
                pass
            else:
                print(json.dumps(result, indent=2))

    elif args.preset_command == 'import':
        result = executor.import_preset(args.source, args.save)
        if result:
            print(json.dumps(result, indent=2))

    else:
        result = {'success': False, 'message': 'Unknown preset command'}
        print(json.dumps(result, indent=2))

    client.close()
    return 0
