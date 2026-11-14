"""Connection lifecycle.

`comfy_connect` establishes the HTTP client, auto-starts the proxy if
needed, and queries /object_info.

Short-circuit on repeat
=======================
When the handler is already connected to the same host:port, the
command returns success immediately without touching the proxy. This
matters when multiple runs are launched concurrently (multiple docker
Run clicks, CLI multi-terminal): each `comfy_panel_run` calls
`comfy_connect` first, and the repeat calls would otherwise re-run
the proxy check-then-spawn sequence on every launch. That sequence is
already locked, so it is correct without the short-circuit; the
short-circuit is a small latency win and makes the common "already
connected" case trivially cheap.

If the proxy died since the last successful connect, the short-circuit
does not notice. The next operation that actually needs the proxy
(`prepare_workflow` via `ProxyV2.from_pid_file`) fails cleanly with
`ProxyUnreachable`, which is the appropriate error. Re-probing on
every connect would cost a round trip that adds nothing.

Cache invalidation on auto-start
================================
When `comfy_connect` finds the proxy not running and starts it, both
of the handler's proxy-side caches must be dropped:

  · `parent.invalidate_proxy_client()` — the new proxy has a new pid,
    so any cached `ProxyV2` client bound to the old pid is stale.
    Without this, the first generation after the start would use the
    dead client (fails with ProxyUnreachable, then invalidates). With
    this, the next `get_proxy_client()` picks up the new pid
    immediately.
  · `invalidate_proxy_status_cache()` — the "is the proxy running"
    display cache from `comfyui_status.py` shows "not running" from
    the pre-start probe; without invalidation the UI would lag up to
    a second behind.

Both invalidations are cheap no-ops when the proxy was already
running (which is the common case; start only fires on first connect
or after a proxy death that `comfy_connect` observed directly).

The browser-workflow watcher and publisher pinning that used to live
here were removed in Stage 6 — the v2 pipeline is file-or-config
driven, not browser-published.
"""

from __future__ import annotations

from ..decorators import command, AutoDispatchHandler
from ..utils.comfyui_client import ComfyError, get_client
from .comfyui_status import invalidate_proxy_status_cache


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

        # ---- Short-circuit when already connected to this ComfyUI ----
        if (parent._client is not None
                and parent._host == host
                and parent._port == port):
            return {
                "success": True,
                "message": f"Already connected to {host}:{port}",
                "data": {
                    "host": host,
                    "port": port,
                    "short_circuit": True,
                },
            }

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
            # The proxy just came up (or failed to). Either way the
            # cached state is stale.  Drop both caches so the next
            # read / run picks up the actual post-start state.
            invalidate_proxy_status_cache()
            parent.invalidate_proxy_client()
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