"""Convert bash scripts to batch JSON format using structural parsing"""

import re
import json
from typing import Dict, Any, List, Optional
from pathlib import Path


class BashConverter:
    def __init__(self):
        pass

    def convert(self, script_content: str, batch_id: Optional[str] = None) -> Dict[str, Any]:
        """Convert bash script content to batch JSON using structural parsing"""
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

            # Command name is the second part (e.g., "create_layer")
            command_name = parts[1]

            # Special handling for batch run-saved (include)
            if parts[0] == 'batch' and command_name == 'run-saved':
                batch_name = None
                for i in range(2, len(parts)):
                    arg = parts[i]
                    if not arg.startswith('--'):
                        batch_name = arg.strip("'\"")
                        break
                if batch_name:
                    commands.append({'type': 'include', 'batch': batch_name})
                continue

            json_cmd = {'type': command_name}

            # Parse arguments
            i = 2
            while i < len(parts):
                arg = parts[i]

                if arg.startswith('--'):
                    # This is a flag
                    arg_name = arg[2:].replace('-', '_')
                    if i + 1 < len(parts) and not parts[i+1].startswith('--'):
                        # Flag with value
                        value = parts[i + 1].strip("'\"")
                        value = self._convert_value(value)
                        json_cmd[arg_name] = value
                        i += 2
                    else:
                        # Flag without value (boolean)
                        json_cmd[arg_name] = True
                        i += 1
                else:
                    # This should NOT happen in properly formatted commands
                    # All arguments should be flags with -- prefix
                    # If we get here, it's a bare positional argument
                    print(f"⚠️ Warning: Bare positional argument '{arg}' found in command '{command_name}'. Use --flag value instead.")
                    i += 1

            commands.append(json_cmd)

        return {
            'id': batch_id or 'converted_batch',
            'commands': commands
        }

    def convert_file(self, file_path: str, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Convert a bash script file to batch JSON"""
        with open(file_path, 'r') as f:
            content = f.read()

        batch_id = Path(file_path).stem
        batch = self.convert(content, batch_id)

        if output_path:
            with open(output_path, 'w') as f:
                json.dump(batch, f, indent=2)

        return batch

    def _convert_value(self, value: str) -> Any:
        """Convert string value to appropriate type"""
        if value.lower() == 'true':
            return True
        if value.lower() == 'false':
            return False
        if value.isdigit():
            return int(value)
        if value.replace('.', '', 1).isdigit():
            return float(value)
        return value.strip("'\"")
