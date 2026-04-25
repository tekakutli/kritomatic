"""CLI handling for diffusion node subcommands"""

import argparse
import json
import sys

from .executor import DiffusionNodeExecutor


def run_node_command():
    """Run the diffusion node subcommand"""

    # Create a parser for node subcommands
    node_parser = argparse.ArgumentParser(
        prog='kritomatic diffusion node',
        description='Diffusion node operations - control ComfyUI nodes via mute-bypass extension'
    )
    node_subparsers = node_parser.add_subparsers(dest='node_command', help='Node subcommands', required=True)

    # set_mode
    set_mode_parser = node_subparsers.add_parser('set_mode', help='Set a node\'s mode (mute/bypass/user)')
    set_mode_parser.add_argument('--node-id', type=int, required=True, help='Node ID number')
    set_mode_parser.add_argument('--mode', choices=['mute', 'bypass', 'user'], required=True,
                                help='Mode to set: mute, bypass, or user')

    # Parse arguments (skip the first three: kritomatic, diffusion, node)
    args = node_parser.parse_args(sys.argv[3:])

    executor = DiffusionNodeExecutor()

    if args.node_command == 'set_mode':
        result = executor.set_node_mode(args.node_id, args.mode)
    else:
        result = {'success': False, 'message': f'Unknown node command: {args.node_command}'}

    print(json.dumps(result, indent=2))
    return 0 if result.get('success') else 1
