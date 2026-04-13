#!/usr/bin/env python3
import yaml
import sys
import argparse
from pathlib import Path
import shutil
from datetime import datetime

class CommandCompiler:
    def __init__(self):
        # __file__ is src/kritomatic/compiler.py
        # src_dir is src/kritomatic/
        src_dir = Path(__file__).parent
        # project_root is kritomatic/ (parent of src/)
        self.project_root = src_dir.parent.parent

        # Commands are stored in src/kritomatic/commands/
        self.commands_dir = src_dir / 'commands'

        # Bundles are stored in data/bundles/
        self.bundles_dir = self.project_root / 'data' / 'bundles'
        self.imported_dir = self.bundles_dir / 'imported'

        # Generated files go in src/kritomatic/
        self.generated_file = src_dir / 'commands_generated.py'
        self.registry_cache = src_dir / '.command_registry.json'

        # Create directories if they don't exist
        self.commands_dir.mkdir(parents=True, exist_ok=True)
        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        self.imported_dir.mkdir(parents=True, exist_ok=True)

    def compile(self, force=False):
        all_commands = []
        seen_keys = set()

        for yaml_file in sorted(self.commands_dir.glob('*.yaml')):
            try:
                cmd = yaml.safe_load(open(yaml_file, 'r', encoding='utf-8'))
                if 'category' not in cmd or 'command' not in cmd:
                    print(f"⚠️ Skipping {yaml_file.name}: missing category or command")
                    continue

                key = (cmd['category'], cmd['command'])
                if key in seen_keys and not force:
                    print(f"⚠️ Duplicate command {cmd['category']}.{cmd['command']} in {yaml_file.name} - skipping (use --force to override)")
                    continue
                seen_keys.add(key)

                # Validate command
                errors = self._validate_command(cmd)
                if errors:
                    print(f"⚠️ Validation errors in {yaml_file.name}:")
                    for err in errors:
                        print(f"    - {err}")
                    continue

                all_commands.append(cmd)
                print(f"  ✓ Loaded {yaml_file.name}")
            except yaml.YAMLError as e:
                print(f"  ✗ YAML error in {yaml_file.name}: {e}")
            except Exception as e:
                print(f"  ✗ Error loading {yaml_file.name}: {e}")

        if not all_commands:
            print("⚠️ No command files found")
            return

        # Generate Python code
        with open(self.generated_file, 'w', encoding='utf-8') as f:
            f.write("# AUTO-GENERATED from commands/*.yaml - DO NOT EDIT\n")
            f.write("# Run 'kritomatic compile' to regenerate\n\n")
            f.write("from decorators import command, arg, get_client\n\n")

            for cmd in all_commands:
                self._generate_command(f, cmd)

            f.write("\n# Command dispatch table\n")
            f.write("COMMAND_REGISTRY = {\n")
            for cmd in all_commands:
                category = cmd['category']
                cmd_name = cmd['command']
                func_name = f"{category}_{cmd_name}".replace('-', '_')
                f.write(f"    ('{category}', '{cmd_name}'): {func_name},\n")
            f.write("}\n")

        # Clear registry cache
        if self.registry_cache.exists():
            self.registry_cache.unlink()
            print(f"  ✓ Cleared registry cache")

        print(f"\n✓ Generated {self.generated_file} with {len(all_commands)} commands")

    def _validate_command(self, cmd):
        """Validate command structure"""
        errors = []

        # Check required fields
        for field in ['category', 'command', 'action']:
            if field not in cmd:
                errors.append(f"Missing required field: {field}")

        # Check args structure
        for i, arg in enumerate(cmd.get('args', [])):
            if 'name' not in arg:
                errors.append(f"Argument {i} missing 'name' field")

        return errors

    def _generate_command(self, f, cmd):
        category = cmd['category']
        cmd_name = cmd['command']
        func_name = f"{category}_{cmd_name}".replace('-', '_')
        action = cmd['action']
        mapping = cmd.get('mapping')

        f.write(f"\n@command('{category}', '{cmd_name}', help=\"{cmd.get('help', '').replace('"', '\\"')}\")\n")

        # Generate arg decorators
        for arg_def in cmd.get('args', []):
            self._generate_arg_decorator(f, arg_def)

        # Generate function signature
        f.write(f"def {func_name}(")
        params = []
        for arg_def in cmd.get('args', []):
            arg_name = arg_def['name'].lstrip('-').replace('-', '_')
            if arg_def.get('required') and arg_def['name'].startswith('--'):
                params.append(f"{arg_name}=None")
            elif arg_def.get('required'):
                params.append(arg_name)
            elif 'default' in arg_def:
                default_repr = repr(arg_def['default'])
                params.append(f"{arg_name}={default_repr}")
            else:
                params.append(f"{arg_name}=None")

        f.write("client")
        if params:
            f.write(", " + ", ".join(params))
        f.write("):\n")

        # Generate function body with type conversion
        f.write("    # Convert numeric types\n")
        for arg_def in cmd.get('args', []):
            arg_name = arg_def['name'].lstrip('-').replace('-', '_')
            if arg_def.get('type') == 'int':
                f.write(f"    if {arg_name} is not None:\n")
                f.write(f"        {arg_name} = int({arg_name})\n")
            elif arg_def.get('type') == 'float':
                f.write(f"    if {arg_name} is not None:\n")
                f.write(f"        {arg_name} = float({arg_name})\n")

        f.write(f"    return client.execute('{action}'")

        for arg_def in cmd.get('args', []):
            arg_name = arg_def['name'].lstrip('-').replace('-', '_')

            # If this is the mapped argument, send as 'value'
            if mapping and arg_def['name'] == mapping:
                f.write(f", value={arg_name}")
            else:
                f.write(f", {arg_name}={arg_name}")

        f.write(")\n")

    def _generate_arg_decorator(self, f, arg_def):
        arg_name = arg_def['name']
        kwargs = []
        is_optional = arg_name.startswith('--')

        if 'type' in arg_def:
            type_map = {
                'int': 'int',
                'float': 'float',
                'str': 'str',
                'bool': 'bool',
                'choice': 'str',
                'hex_color': 'str',
                'file_path': 'str'
            }
            kwargs.append(f"type={type_map.get(arg_def['type'], 'str')}")

        if 'required' in arg_def and arg_def['required'] and is_optional:
            kwargs.append("required=True")

        if 'default' in arg_def:
            default_repr = repr(arg_def['default'])
            kwargs.append(f"default={default_repr}")

        if 'choices' in arg_def:
            kwargs.append(f"choices={arg_def['choices']}")

        if 'help' in arg_def:
            help_text = arg_def['help'].replace('"', '\\"')
            kwargs.append(f"help='{help_text}'")

        f.write(f"@arg('{arg_name}', {', '.join(kwargs)})\n")


    def import_bundle(self, bundle_source):
        """
        Import a bundle from file path or pasted YAML string

        Args:
            bundle_source: Either a file path or a YAML string
        """
        bundle_path = Path(bundle_source)
        bundle_data = None

        # Check if it's a file that exists
        if bundle_path.exists() and bundle_path.is_file():
            try:
                with open(bundle_path, 'r', encoding='utf-8') as f:
                    bundle_data = yaml.safe_load(f)
                print(f"📁 Importing from file: {bundle_path.name}")
            except Exception as e:
                print(f"❌ Error reading file: {e}")
                return
        else:
            # Try to parse as YAML string
            try:
                bundle_data = yaml.safe_load(bundle_source)
                print(f"📋 Importing from pasted YAML")
            except yaml.YAMLError as e:
                print(f"❌ Invalid YAML format: {e}")
                print(f"   Make sure you're passing either a valid file path or a YAML string")
                return

        if not bundle_data:
            print(f"❌ No data to import")
            return

        # Handle both single command and multi-command bundles
        if 'commands' in bundle_data:
            commands = bundle_data['commands']
        else:
            commands = [bundle_data]

        imported_count = 0
        for cmd in commands:
            if 'category' not in cmd or 'command' not in cmd:
                print(f"⚠️ Skipping invalid command in bundle")
                continue

            category = cmd['category']
            cmd_name = cmd['command']
            atomic_path = self.commands_dir / f"{category}_{cmd_name}.yaml"

            with open(atomic_path, 'w', encoding='utf-8') as f:
                yaml.dump(cmd, f, default_flow_style=False, allow_unicode=True, sort_keys=False)

            print(f"  ✓ Created {atomic_path.name}")
            imported_count += 1

        # Only archive if it came from a file
        if bundle_path.exists() and bundle_path.is_file():
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            archived_path = self.imported_dir / f"{bundle_path.stem}_{timestamp}{bundle_path.suffix}"
            shutil.copy(bundle_path, archived_path)
            print(f"  📦 Archived to {archived_path}")

        print(f"\n✓ Imported {imported_count} commands")
        self.compile(force=True)

    def export_bundle(self, output_file=None, category=None, command_name=None):
        bundle = {'version': 1, 'type': 'command_bundle', 'commands': []}

        if command_name:
            for yaml_file in self.commands_dir.glob('*.yaml'):
                cmd = yaml.safe_load(open(yaml_file, 'r', encoding='utf-8'))
                if cmd.get('command') == command_name:
                    bundle['commands'].append(cmd)
                    break
            if not bundle['commands']:
                print(f"❌ Command '{command_name}' not found")
                return

        elif category:
            for yaml_file in self.commands_dir.glob(f'{category}_*.yaml'):
                cmd = yaml.safe_load(open(yaml_file, 'r', encoding='utf-8'))
                bundle['commands'].append(cmd)
            if not bundle['commands']:
                print(f"❌ No commands found in category '{category}'")
                return

        else:
            for yaml_file in self.commands_dir.glob('*.yaml'):
                cmd = yaml.safe_load(open(yaml_file, 'r', encoding='utf-8'))
                bundle['commands'].append(cmd)

        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                yaml.dump(bundle, f, default_flow_style=False, allow_unicode=True)
            print(f"✓ Exported {len(bundle['commands'])} commands to {output_file}")
        else:
            print(yaml.dump(bundle, default_flow_style=False, allow_unicode=True))

    def export_batch_schema(self, output_file=None):
        """Export all commands as a batch schema JSON"""
        import json
        all_commands = []

        for yaml_file in self.commands_dir.glob('*.yaml'):
            try:
                cmd = yaml.safe_load(open(yaml_file, 'r', encoding='utf-8'))
                cmd_type = f"{cmd['category']}_{cmd['command']}".replace('-', '_')

                command_schema = {
                    'type': cmd_type,
                    'action': cmd['action'],
                    'help': cmd.get('help', ''),
                    'args': []
                }

                for arg in cmd.get('args', []):
                    command_schema['args'].append({
                        'name': arg['name'],
                        'type': arg.get('type', 'str'),
                        'required': arg.get('required', False),
                        'default': arg.get('default'),
                        'help': arg.get('help', '')
                    })

                all_commands.append(command_schema)
            except Exception as e:
                print(f"Error loading {yaml_file}: {e}")

        schema = {'version': 1, 'commands': all_commands}

        if output_file:
            with open(output_file, 'w') as f:
                json.dump(schema, f, indent=2)
            print(f"✓ Batch schema exported to {output_file}")
        else:
            print(json.dumps(schema, indent=2))

    def remove_command(self, category, command_name):
        atomic_path = self.commands_dir / f"{category}_{command_name}.yaml"
        if atomic_path.exists():
            atomic_path.unlink()
            print(f"✓ Removed {atomic_path.name}")
            self.compile(force=True)
        else:
            print(f"❌ Command '{category}.{command_name}' not found")

    def list_commands(self):
        commands = []
        for yaml_file in sorted(self.commands_dir.glob('*.yaml')):
            try:
                cmd = yaml.safe_load(open(yaml_file, 'r', encoding='utf-8'))
                commands.append((cmd.get('category', '?'), cmd.get('command', '?')))
            except:
                pass

        if not commands:
            print("No commands found. Run compile first.")
            return

        current_category = None
        for category, command in commands:
            if category != current_category:
                print(f"\n{category}:")
                current_category = category
            print(f"  {command}")

    def clear_all_commands(self):
        confirm = input("⚠️ This will delete ALL commands. Are you sure? (yes/no): ")
        if confirm.lower() == 'yes':
            for yaml_file in self.commands_dir.glob('*.yaml'):
                yaml_file.unlink()
                print(f"  ✗ Deleted {yaml_file.name}")
            self.compile(force=True)
            print("✓ All commands cleared")
        else:
            print("Cancelled")

    def clear_category(self, category):
        count = 0
        for yaml_file in self.commands_dir.glob(f'{category}_*.yaml'):
            yaml_file.unlink()
            print(f"  ✗ Deleted {yaml_file.name}")
            count += 1
        if count > 0:
            self.compile(force=True)
            print(f"✓ Deleted {count} commands from category '{category}'")
        else:
            print(f"No commands found in category '{category}'")
