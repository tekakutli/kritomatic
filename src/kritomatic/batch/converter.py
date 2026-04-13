"""Convert bash scripts to batch JSON format"""

import re
import json
from typing import Dict, Any, List, Optional
from pathlib import Path


class BashConverter:
    def __init__(self):
        self._command_map = None

    def _load_command_map(self):
        if self._command_map is not None:
            return

        self._command_map = {}
        commands_dir = Path(__file__).parent.parent / 'commands'

        for yaml_file in commands_dir.glob('*.yaml'):
            try:
                import yaml
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    cmd = yaml.safe_load(f)

                category = cmd.get('category')
                command = cmd.get('command')
                action = cmd.get('action')

                if category and command and action:
                    key = f"{category} {command}"
                    self._command_map[key] = {
                        'type': action,
                        'positional': self._extract_positional_args(cmd.get('args', [])),
                        'optional': self._extract_optional_args(cmd.get('args', []))
                    }
            except Exception as e:
                print(f"Warning: Could not load {yaml_file}: {e}")

    def _extract_positional_args(self, args):
        positional = []
        for arg in args:
            name = arg.get('name')
            if not name.startswith('--'):
                positional.append(name)
        return positional

    def _extract_optional_args(self, args):
        optional = []
        for arg in args:
            name = arg.get('name')
            if name.startswith('--'):
                optional.append(name[2:].replace('-', '_'))
        return optional

    def convert(self, script_content: str, batch_id: Optional[str] = None) -> Dict[str, Any]:
        self._load_command_map()
        commands = []

        for line in script_content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#') or line.startswith('echo'):
                continue

            # Match kritomatic commands
            match = re.search(r'kritomatic\s+(.+)', line)
            if not match:
                continue

            cmd_line = match.group(1)
            parts = cmd_line.split()

            if len(parts) < 2:
                continue

            category = parts[0]
            command = parts[1]
            key = f"{category} {command}"

            # Handle batch run-saved as include
            if category == 'batch' and command == 'run-saved':
                # Format: kritomatic batch run-saved "batch_name"
                batch_name = None
                i = 2
                while i < len(parts):
                    arg = parts[i]
                    if not arg.startswith('--'):
                        batch_name = arg.strip("'\"")
                        break
                    i += 1

                if batch_name:
                    json_cmd = {'type': 'include', 'batch': batch_name}
                    commands.append(json_cmd)
                    continue
                else:
                    print(f"⚠️ Missing batch name for batch run-saved")
                    continue

            mapping = self._command_map.get(key)
            if not mapping:
                print(f"⚠️ Unknown command: {key}")
                continue

            json_cmd = {'type': mapping['type']}
            positional_args = mapping['positional']
            optional_args = mapping['optional']

            i = 2
            positional_index = 0

            while i < len(parts):
                arg = parts[i]

                if arg.startswith('--'):
                    # Optional argument
                    arg_name = arg[2:].replace('-', '_')
                    # Handle special case: --type -> layer_type
                    if arg_name == 'type' and category == 'layer' and command == 'create':
                        arg_name = 'layer_type'

                    if arg_name in optional_args or arg_name in ['layer_type']:
                        if i + 1 < len(parts) and not parts[i+1].startswith('--'):
                            value = parts[i + 1].strip("'\"")
                            value = self._convert_value(value)
                            json_cmd[arg_name] = value
                            i += 2
                        else:
                            # Flag argument (no value)
                            json_cmd[arg_name] = True
                            i += 1
                    else:
                        i += 1
                else:
                    # Positional argument
                    arg_value = arg.strip("'\"")
                    arg_value = self._convert_value(arg_value)

                    if positional_index < len(positional_args):
                        param_name = positional_args[positional_index]
                        json_cmd[param_name] = arg_value
                    else:
                        json_cmd.setdefault('extra_args', []).append(arg_value)

                    positional_index += 1
                    i += 1

            commands.append(json_cmd)

        return {
            'id': batch_id or 'converted_batch',
            'commands': commands
        }

    def convert_file(self, file_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        self._load_command_map()

        with open(file_path, 'r') as f:
            content = f.read()

        batch_id = Path(file_path).stem
        batch = self.convert(content, batch_id)

        if output_path:
            with open(output_path, 'w') as f:
                json.dump(batch, f, indent=2)

        return batch

    def _convert_value(self, value: str) -> Any:
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        if value.isdigit():
            return int(value)
        if value.replace('.', '', 1).isdigit():
            return float(value)
        return value.strip("'\"")
