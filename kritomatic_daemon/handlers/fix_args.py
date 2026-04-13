#!/usr/bin/env python3
import re
import glob
import sys


#from positional to flags
def fix_args_in_commands(content):
    """Add -- to argument names only inside @command decorator args dicts"""

    lines = content.split('\n')
    in_command = False
    in_args = False
    result_lines = []

    for i, line in enumerate(lines):
        # Check if we're entering a @command decorator
        if '@command(' in line:
            in_command = True

        # Check if we're entering args={
        if in_command and 'args={' in line:
            in_args = True
            # Find the position of args={
            args_pos = line.find('args={')
            # Only modify after args={
            prefix = line[:args_pos]
            suffix = line[args_pos:]
            # Add -- to argument keys in this line
            suffix = re.sub(r"'([a-z_]+)':(\s*\{)", r"'--\1':\2", suffix)
            line = prefix + suffix

        # If we're inside args dict (multi-line), modify the line
        elif in_args:
            # Add -- to argument keys
            line = re.sub(r"^(\s*)'([a-z_]+)':(\s*\{)", r"\1'--\2':\3", line)

            # Check if this line closes the args dict
            if '}' in line and not '{' in line:
                in_args = False

        # Check if we're exiting the @command decorator
        if in_command and line.strip() == ')':
            in_command = False

        result_lines.append(line)

    return '\n'.join(result_lines)

def main():
    if len(sys.argv) > 1:
        files = sys.argv[1:]
    else:
        files = glob.glob("*.py")

    for filename in files:
        if filename in ['__init__.py', 'decorators.py', 'registry.py', 'base.py']:
            continue

        with open(filename, 'r') as f:
            content = f.read()

        new_content = fix_args_in_commands(content)

        if new_content != content:
            with open(filename, 'w') as f:
                f.write(new_content)
            print(f"✓ Updated: {filename}")
        else:
            print(f"  No changes: {filename}")

if __name__ == '__main__':
    main()
