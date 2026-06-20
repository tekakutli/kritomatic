#!/usr/bin/env python3
"""
Call a bash function from my configuration
"""

import subprocess
import sys
import os

# Configure which bash function to call
BASH_FUNCTION = "swap_image_app"

# Path to your config file (change if needed)
CONFIG_FILE = os.path.expanduser("~/.zshenv")

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

def call_bash_function():
    """Call the configured bash function"""
    try:
        # Source the config file (as bash, since you said it's bash syntax despite .zshenv)
        bash_command = f"""
            if [ -f {CONFIG_FILE} ]; then
                source {CONFIG_FILE}
                {BASH_FUNCTION}
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
    if call_bash_function():
        show_notification(f"Called {BASH_FUNCTION} BASH CONFIG FUNCTION successfully", is_error=False)
        sys.exit(0)
    else:
        sys.exit(1)

if __name__ == "__main__":
    main()
