#!/usr/bin/env python3
"""
util_call_bash_function.py

Source a shell config file and call a function defined in it.

The function to call comes from the BASH_FUNCTION variable, or from an
optional positional argument that overrides it. The config file to source
is set by CONFIG_FILE.

Usage:
    python util_call_bash_function.py [function_name]

Examples:
    # Use BASH_FUNCTION variable
    python util_call_bash_function.py

    # Override with a function name
    python util_call_bash_function.py viewer_toggle_default
"""

import subprocess
import sys
import os
import argparse

# ===== CONFIGURABLE SETTINGS =====
# Which bash function to call. Overridden by the positional argument.
BASH_FUNCTION = "viewer_toggle_default"

# Path to the config file to source
CONFIG_FILE = os.path.expanduser("~/.zshenv")
# =================================

def show_notification(message, is_error=True):
    """Show desktop notification"""
    try:
        icon = 'dialog-error' if is_error else 'dialog-information'
        subprocess.Popen([
            'notify-send',
            'Bash Function Call' if is_error else 'Success',
            message,
            '-i', icon,
            '-t', '3000'
        ])
    except FileNotFoundError:
        print(message)

def call_bash_function(bash_function):
    """Call the configured bash function"""
    try:
        # Source the config file (as bash, since you said it's bash syntax despite .zshenv)
        bash_command = f"""
            if [ -f {CONFIG_FILE} ]; then
                source {CONFIG_FILE}
                {bash_function}
            else
                echo "Config file not found: {CONFIG_FILE}"
                exit 1
            fi
        """
        result = subprocess.run(
            ['bash', '-c', bash_command],
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            error_msg = result.stderr.strip() or result.stdout.strip()
            show_notification(f"Failed: {error_msg or 'command not found'}")
            return False

        return True
    except Exception as e:
        show_notification(f"Error: {e}")
        return False

def main():
    parser = argparse.ArgumentParser(
        description='Call a bash function defined in a sourced config file. '
                    'Configure via variables in the script; the only positional '
                    'argument overrides BASH_FUNCTION.'
    )
    parser.add_argument('function_name', nargs='?', default=None,
                        help='Bash function to call (overrides BASH_FUNCTION variable)')
    args = parser.parse_args()

    # Resolve the function name: positional overrides variable
    bash_function = args.function_name or BASH_FUNCTION

    if call_bash_function(bash_function):
        show_notification(f"Called {bash_function} BASH CONFIG FUNCTION successfully", is_error=False)
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
