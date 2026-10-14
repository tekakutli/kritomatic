"""Batch execution engine for running multiple commands"""

import json
import random
import string
from typing import List, Dict, Any, Optional, Callable
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from kritomatic.client import KritaClient
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
        """The scope id for commands this executor emits.

        Top-level commands get the bundle id. Commands from an include
        get `<bundle_id>_inc<depth>_<n>`, and nested includes extend
        the string. The daemon resolves names against the scope's
        created-nodes map, so two inclusions of the same macro can both
        create a layer called `selections` and each reference resolves
        to its own.
        """
        return '_'.join(self.prefix_stack)

    def _process_command(self, cmd: Dict[str, Any]) -> Dict[str, Any]:
        """Tag a command with the current scope.

        No names are rewritten. A command's `name`, `layer_name`, and
        friends carry exactly the string the bundle author wrote; the
        scope tag is what tells the daemon which namespace to register
        into and which to resolve against. This is the whole of the
        executor's interaction with the layer namespace.
        """
        processed = cmd.copy()
        processed['_scope'] = self._get_current_prefix()
        return processed

    def _flatten_commands(self, commands: List[Dict], visited: set = None, depth: int = 0) -> List[Dict]:
        """Flatten includes recursively, tagging each command with the
        scope it belongs to. Names are never mangled: an included
        command carries the same bare names its author wrote, plus a
        `_scope` tag identifying which inclusion it came from.

        Cycle protection via `visited` is unchanged from the previous
        implementation.
        """
        if visited is None:
            visited = set()

        result = []
        for cmd in commands:
            if cmd.get('type') == 'include':
                batch_name = cmd.get('batch')
                if not batch_name:
                    print(f"⚠️ Include missing batch name")
                    continue

                if batch_name in visited:
                    print(f"⚠️ Circular include detected: {batch_name}")
                    continue

                visited.add(batch_name)

                batch_data = self.library.load(batch_name)
                if batch_data:
                    nested_commands = batch_data.get('commands', [])

                    self._include_counter += 1
                    include_suffix = f"inc{depth}_{self._include_counter}"

                    nested_prefix_stack = self.prefix_stack + [include_suffix]

                    print(f"  🔄 Including batch '{batch_name}' (scope: "
                          f"{'_'.join(nested_prefix_stack)})")

                    nested_executor = BatchExecutor(
                        client=self.client,
                        batch_id=self.batch_id,
                        prefix_stack=nested_prefix_stack
                    )

                    for nested_cmd in nested_commands:
                        if nested_cmd.get('type') == 'include':
                            nested_result = nested_executor._flatten_commands(
                                [nested_cmd], visited, depth + 1
                            )
                            result.extend(nested_result)
                        else:
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

        self._include_counter = 0

        print(f"📦 Batch ID: {self.batch_id}")
        print(f"📍 Scope stack: {' → '.join(self.prefix_stack)}")
        commands = self._flatten_commands(raw_commands)

        if len(raw_commands) != len(commands):
            print(f"📋 Flattened {len(raw_commands)} commands into {len(commands)} (resolved includes)")

        total = len(commands)

        # Pre-process every command (tag with scope) and build the
        # single bundled payload. This is the only thing we send.
        bundled_commands: List[Dict[str, Any]] = []
        original_names: List[Optional[str]] = []
        for cmd in commands:
            bundled_commands.append(self._process_command(cmd))
            original_names.append(
                cmd.get('name') or cmd.get('layer_name') or cmd.get('mask_name')
            )

        payload = {
            'id': self.batch_id,
            'commands': bundled_commands,
        }

        results: List[Dict[str, Any]] = []

        def handle_progress(index: int, result_entry: Dict[str, Any]):
            processed_cmd = bundled_commands[index] if index < len(bundled_commands) else {}
            params = {k: v for k, v in processed_cmd.items() if k not in ('type', '_scope')}
            cmd_type = result_entry.get('command', processed_cmd.get('type'))
            is_success = result_entry.get('status') == 'success'

            result_data = {
                'index': index,
                'command': cmd_type,
                'original_name': original_names[index] if index < len(original_names) else None,
                'processed_name': params.get('name') or params.get('layer_name') or params.get('mask_name'),
                'scope': processed_cmd.get('_scope'),
                'status': 'success' if is_success else 'error',
                'message': result_entry.get('message', ''),
                'data': result_entry.get('data', None),
            }
            results.append(result_data)

            status_icon = "✓" if is_success else "✗"
            print(f"  {status_icon} {cmd_type}: {result_data['message']}")

            for callback in self.callbacks:
                callback(index + 1, total, cmd_type, result_data)

        response = self.client.send_batch(payload, on_progress=handle_progress)

        if response and not results:
            for r in response.get('results', []):
                handle_progress(r.get('index', 0), r)

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
