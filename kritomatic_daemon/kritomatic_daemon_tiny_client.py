#!/usr/bin/env python3
"""
Simple client to send JSON commands to Kritomatic Daemon (Krita Socket Server)
The daemon listens on port 12346 and accepts the same JSON format.

The daemon now speaks NDJSON: every response is one JSON object followed
by a newline. Batches stream one `progress` line per command, then a final
`batch_complete` summary line. This client reads them as they arrive.
"""

import socket
import json
import sys


def send_json_to_kritomatic(json_data, host='localhost', port=12346):
    """Send JSON to Kritomatic daemon and return the final response."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))

        if isinstance(json_data, dict):
            json_data = json.dumps(json_data)

        sock.send((json_data + '\n').encode('utf-8'))

        buffer = b''
        final = None
        while final is None:
            if b'\n' not in buffer:
                try:
                    chunk = sock.recv(65536)
                except Exception as e:
                    print(f"❌ Error: {e}")
                    break
                if not chunk:
                    break
                buffer += chunk
                continue

            line, buffer = buffer.split(b'\n', 1)
            if not line.strip():
                continue
            try:
                msg = json.loads(line.decode('utf-8'))
            except Exception:
                continue

            if msg.get('type') == 'progress':
                result = msg.get('result', {})
                icon = '✓' if result.get('status') == 'success' else '✗'
                idx = msg.get('index', 0)
                total = msg.get('total', '?')
                print(f"  {icon} [{idx + 1}/{total}] "
                      f"{result.get('command')}: {result.get('message', '')}")
            else:
                final = msg

        sock.close()
        return final

    except ConnectionRefusedError:
        print(f"❌ Cannot connect to Kritomatic daemon at {host}:{port}")
        print("   Make sure Krita is running and the Kritomatic Daemon plugin is enabled.")
        return None
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


if __name__ == "__main__":
    # Example usage
    if len(sys.argv) > 1:
        # Read from file
        with open(sys.argv[1], 'r') as f:
            json_data = json.load(f)
    else:
        # Default test commands (using new command names)
        json_data = {
            "id": 1,
            "commands": [
                {"type": "set_brush_opacity", "value": 37},
                {"type": "set_brush_size", "value": 33},
                {"type": "get_state"},
                {"type": "list_layers"}
            ]
        }

    response = send_json_to_kritomatic(json_data)
    if response:
        print(json.dumps(response, indent=2))
