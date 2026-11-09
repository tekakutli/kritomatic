"""Connection lifecycle.

`comfy_connect` establishes the HTTP client, auto-starts the proxy if
needed, and queries /object_info.

The browser-workflow watcher and publisher pinning that used to live
here were removed in Stage 6 — the v2 pipeline is file-or-config
driven, not browser-published. If a future feature needs to react to
browser graph changes, it re-adds a watcher on top of the v2 proxy's
job/asset surface, not on top of ComfyUI's `etn_workflow_published`
bus.
"""

from __future__ import annotations

from ..decorators import command, AutoDispatchHandler
from ..utils.comfyui_client import ComfyError, get_client


class ConnectionCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Connect to a ComfyUI server. Ensures the "
                  "comfy-api-proxy is running and pointed at the same "
                  "ComfyUI (auto-starts it when not).",
        args={
            "--host": {"type": "str", "default": "127.0.0.1",
                       "help": "ComfyUI host"},
            "--port": {"type": "int", "default": 8188,
                       "help": "ComfyUI port"},
            "--check": {"type": "bool", "default": True,
                        "help": "Query /object_info to confirm the "
                                "connection."},
        },
    )
    def comfy_connect(self, params):
        parent = self.parent
        host = params.get("host", "127.0.0.1")
        port = int(params.get("port", 8188))
        check = bool(params.get("check", True))

        # ---- Proxy prelude ----
        # Every generation path goes through /api/v2/* via the proxy;
        # make sure it is running before we bother with ComfyUI.
        from ..proxy import manager as proxy_manager
        proxy_state = proxy_manager.status()
        proxy_data = proxy_state.get("data") or {}
        if not proxy_data.get("running"):
            proxy_start = proxy_manager.start(
                comfyui=f"http://{host}:{port}",
            )
            proxy_state = proxy_manager.status()
            proxy_data = proxy_state.get("data") or {}
            if not proxy_start.get("success"):
                proxy_data["start_error"] = proxy_start.get("message")

        parent._host = host
        parent._port = port
        parent._client = get_client(host, port)

        if not check:
            return {
                "success": True,
                "message": f"Connected to {host}:{port}",
                "data": {"host": host, "port": port,
                         "proxy": proxy_data},
            }

        try:
            info = parent._client.object_info()
        except ComfyError as e:
            parent._client = None
            return {"success": False,
                    "message": f"Could not reach ComfyUI at "
                               f"{host}:{port}: {e}",
                    "data": {"proxy": proxy_data}}

        has_tooling = "ETN_LoadImageCache" in info

        msg = (f"Connected to {host}:{port} "
               f"({len(info)} node classes registered)")
        if proxy_data.get("running") and proxy_data.get("healthy"):
            msg += ", proxy healthy"
        elif proxy_data.get("start_error"):
            msg += f", proxy unavailable ({proxy_data['start_error']})"

        return {
            "success": True,
            "message": msg,
            "data": {"host": host, "port": port,
                     "node_count": len(info),
                     "has_tooling_nodes": has_tooling,
                     "proxy": proxy_data},
        }