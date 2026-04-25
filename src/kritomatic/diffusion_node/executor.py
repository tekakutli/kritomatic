"""Executor for diffusion node operations (ComfyUI mute-bypass extension)"""

import json
import urllib.request
import urllib.error
from typing import Dict, Any, Optional


class DiffusionNodeExecutor:
    """Execute diffusion node operations by sending HTTP requests to ComfyUI"""

    def __init__(self, comfyui_host='127.0.0.1', comfyui_port=8188):
        self.base_url = f"http://{comfyui_host}:{comfyui_port}"

    def set_node_mode(self, node_id: int, mode: str) -> Dict[str, Any]:
        """
        Set a node's mode using the comfyui-mute-bypass-by-ID extension

        Args:
            node_id: The node ID number
            mode: One of 'mute', 'bypass', or 'user'
        """
        try:
            url = f"{self.base_url}/stacker/set_mode"
            data = json.dumps({"node_id": node_id, "mode": mode}).encode('utf-8')

            req = urllib.request.Request(
                url,
                data=data,
                headers={'Content-Type': 'application/json'},
                method='POST'
            )

            with urllib.request.urlopen(req, timeout=10) as response:
                response_data = json.loads(response.read().decode('utf-8'))
                return {
                    'success': True,
                    'status': response_data.get('status', 'unknown'),
                    'mode': response_data.get('mode', mode),
                    'node_id': response_data.get('node_id', node_id),
                    'message': f"Node {node_id} set to {mode}"
                }

        except urllib.error.URLError as e:
            return {
                'success': False,
                'message': f"Failed to connect to ComfyUI at {self.base_url}. Make sure ComfyUI is running.",
                'error': str(e)
            }
        except json.JSONDecodeError as e:
            return {
                'success': False,
                'message': f"Invalid response from ComfyUI: {e}",
                'error': str(e)
            }
        except Exception as e:
            return {
                'success': False,
                'message': f"Error setting node mode: {e}",
                'error': str(e)
            }
