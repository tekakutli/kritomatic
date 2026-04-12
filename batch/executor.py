"""Batch execution engine for running multiple commands"""

import json
import random
import string
from typing import List, Dict, Any, Optional, Callable
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from client import KritaClient
from .library import BatchLibrary


def generate_batch_id(length=4):
    """Generate a short random batch ID"""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


class BatchExecutor:
    def __init__(self, client=None, batch_id=None, prefix_stack=None):
        self.client = client or KritaClient()
        self.callbacks = []
        self.batch_id = batch_id or generate_batch_id()
        self.prefix_stack = prefix_stack or [self.batch_id]
        self.library = BatchLibrary()
        self._include_counter = 0

    def _get_current_prefix(self) -> str:
        """Get the current hierarchical prefix"""
        return '_'.join(self.prefix_stack)

    def _process_name(self, name: str) -> str:
        """Add hierarchical prefix to layer/resource names"""
        if not name:
            return name
        current_prefix = self._get_current_prefix()
        # Don't double-prefix if already prefixed
        if name.startswith(current_prefix + '_'):
            return name
        return f"{current_prefix}_{name}"

    def _process_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Process command to add hierarchical prefix to relevant fields"""
        cmd_type = cmd.get('type', '')
        processed = cmd.copy()

        # Skip processing for include commands (they get their own prefix stack)
        if cmd_type == 'include':
            return processed

        # Fields that contain names we want to prefix
        name_fields = ['name', 'layer_name', 'mask_name', 'group_name', 'reference']

        for field in name_fields:
            if field in processed and processed[field]:
                processed[field] = self._process_name(processed[field])

        return processed

    def _flatten_commands(self, commands: List[Dict], visited: set = None, depth: int = 0) -> List[Dict]:
        """Flatten includes recursively with hierarchical prefixing"""
        if visited is None:
            visited = set()

        result = []
        for cmd in commands:
            if cmd.get('type') == 'include':
                batch_name = cmd.get('batch')
                if not batch_name:
                    print(f"⚠️ Include missing batch name")
                    continue

                # Prevent circular includes
                if batch_name in visited:
                    print(f"⚠️ Circular include detected: {batch_name}")
                    continue

                visited.add(batch_name)

                # Load the batch
                batch_data = self.library.load(batch_name)
                if batch_data:
                    nested_commands = batch_data.get('commands', [])

                    # Create a new prefix for this inclusion
                    self._include_counter += 1
                    include_suffix = f"inc{depth}_{self._include_counter}"

                    # Create a new prefix stack for nested commands
                    nested_prefix_stack = self.prefix_stack + [include_suffix]

                    print(f"  🔄 Including batch '{batch_name}' (suffix: {include_suffix})")

                    # Create a new executor for the nested batch with its own prefix
                    nested_executor = BatchExecutor(
                        client=self.client,
                        batch_id=self.batch_id,
                        prefix_stack=nested_prefix_stack
                    )

                    # Process nested commands with the nested executor's prefix
                    for nested_cmd in nested_commands:
                        if nested_cmd.get('type') == 'include':
                            # Recursively flatten nested includes
                            nested_result = nested_executor._flatten_commands([nested_cmd], visited, depth + 1)
                            result.extend(nested_result)
                        else:
                            # Apply prefix to the command
                            processed_cmd = nested_executor._process_command(nested_cmd)
                            result.append(processed_cmd)
                else:
                    print(f"⚠️ Batch '{batch_name}' not found")

                visited.remove(batch_name)
            else:
                result.append(cmd)

        return result

    def _is_success(self, result: Dict) -> bool:
        """Check if a command result indicates success"""
        if not result:
            return False
        if result.get('success') is True:
            return True
        if result.get('status') == 'success':
            return True
        return False

    def execute(self, batch_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw_commands = batch_data.get('commands', [])

        # Reset include counter for this batch run
        self._include_counter = 0

        # Flatten includes before execution
        print(f"📦 Batch ID: {self.batch_id}")
        print(f"📍 Prefix stack: {' → '.join(self.prefix_stack)}")
        commands = self._flatten_commands(raw_commands)

        if len(raw_commands) != len(commands):
            print(f"📋 Flattened {len(raw_commands)} commands into {len(commands)} (resolved includes)")

        results = []
        total = len(commands)

        for i, cmd in enumerate(commands):
            cmd_type = cmd.get('type')
            processed_cmd = self._process_command(cmd)
            params = {k: v for k, v in processed_cmd.items() if k != 'type'}

            result = self.client.execute(cmd_type, **params)

            # Determine success properly
            is_success = self._is_success(result)

            result_data = {
                'index': i,
                'command': cmd_type,
                'original_name': cmd.get('name') or cmd.get('layer_name') or cmd.get('mask_name'),
                'processed_name': params.get('name') or params.get('layer_name') or params.get('mask_name'),
                'prefix': self._get_current_prefix(),
                'status': 'success' if is_success else 'error',
                'message': result.get('message', '') if result else 'No response',
                'data': result.get('data', None) if result else None
            }
            results.append(result_data)

            # Print progress for each command (optional)
            status_icon = "✓" if is_success else "✗"
            print(f"  {status_icon} {cmd_type}: {result_data['message']}")

            for callback in self.callbacks:
                callback(i + 1, total, cmd_type, result_data)

        return results

    def execute_from_file(self, file_path: str) -> List[Dict[str, Any]]:
        with open(file_path, 'r') as f:
            batch_data = json.load(f)
        return self.execute(batch_data)

    def execute_from_json(self, json_str: str) -> List[Dict[str, Any]]:
        batch_data = json.loads(json_str)
        return self.execute(batch_data)

    def get_batch_id(self) -> str:
        return self.batch_id

    def get_prefix(self) -> str:
        return self._get_current_prefix()

    def close(self):
        self.client.close()
