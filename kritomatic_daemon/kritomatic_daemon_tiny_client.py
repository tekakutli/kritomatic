#!/usr/bin/env python3
"""
Minimal CLI client for the Kritomatic daemon.

Sends a bundled payload (or a single command) to the daemon and prints
the streamed progress lines and the final summary as they arrive.

For the full CLI, use the `kritomatic` command instead. This script is
a direct socket-level escape hatch for cases where the schema layer is
in the way, or where you want to poke at the raw protocol without a
schema cache.

Usage:
    kritomatic_daemon_tiny_client.py <path-to-json>
    kritomatic_daemon_tiny_client.py          # runs a small default batch

The JSON file must be either:
  - a bundled payload: {"id": ..., "commands": [{"type": ...}, ...]}
  - a single command:  {"type": "<command_name>", ...args}
"""

import json
import sys
from pathlib import Path

# Make `kritomatic` importable when this script is run from a checkout.
# The repo layout is:
#     <repo>/kritomatic_daemon/kritomatic_daemon_tiny_client.py
#     <repo>/src/kritomatic/client.py
# so we climb two levels from this file and descend into src/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_SRC = _REPO_ROOT / 'src'
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic.client import KritaClient


DEFAULT_BATCH = {
    "id": "tiny_client_default",
    "commands": [
        {"type": "set_brush_opacity", "value": 37},
        {"type": "set_brush_size", "value": 33},
        {"type": "get_state"},
        {"type": "list_layers"},
    ],
}


def _print_progress(index, result):
    icon = '✓' if result.get('status') == 'success' else '✗'
    cmd = result.get('command', '?')
    msg = result.get('message', '')
    print(f"  {icon} [{index + 1}] {cmd}: {msg}")


def main():
    if len(sys.argv) > 1:
        with open(sys.argv[1], 'r') as f:
            payload = json.load(f)
    else:
        payload = DEFAULT_BATCH

    client = KritaClient()
    if not client.connect():
        print(f"❌ Cannot connect to the Kritomatic daemon at "
              f"{client.host}:{client.port}")
        print("   Make sure Krita is running and the plugin is enabled.")
        return 1

    try:
        if isinstance(payload, dict) and 'commands' in payload:
            summary = client.send_batch(payload, on_progress=_print_progress)
        else:
            # Single-command path. Strip 'type' before it goes into the
            # kwargs of client.execute, which re-adds it.
            cmd_type = payload.get('type')
            if not cmd_type:
                print("❌ Payload has no 'type' key and no 'commands' list.")
                return 2
            kwargs = {k: v for k, v in payload.items() if k != 'type'}
            summary = client.execute(cmd_type, **kwargs)

        if summary:
            print(json.dumps(summary, indent=2))
        return 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
