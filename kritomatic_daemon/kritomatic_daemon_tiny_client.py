#!/usr/bin/env python3
"""
Simple client to send JSON commands to Kritomatic Daemon (Krita Socket Server)
The daemon listens on port 12346 and accepts the same JSON format.
"""

import socket
import json
import sys

def send_json_to_kritomatic(json_data, host='localhost', port=12346):
    """Send JSON to Kritomatic daemon and return response"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((host, port))

        if isinstance(json_data, dict):
            json_data = json.dumps(json_data)

        sock.send(json_data.encode('utf-8'))

        response_data = sock.recv(65536).decode('utf-8')
        sock.close()

        return json.loads(response_data) if response_data else None

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
