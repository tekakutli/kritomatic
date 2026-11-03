"""Connection lifecycle, publisher tracking, and file-exclusive lock.

`ComfyUIHandler` keeps a per-session map of browser publishers and
pins the active workflow to one when asked. This module owns the
commands that drive that state.
"""

from __future__ import annotations

import time

from ..decorators import command, AutoDispatchHandler
from ..utils.comfyui_client import ComfyError, get_client
from .comfyui_inspect import _compute_signature


class ConnectionCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    # ------------------------------------------------------------------
    #  Connect / watch
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Connect to a ComfyUI server. Starts the workflow "
                  "watcher by default and blocks until the subscription "
                  "is live.",
        args={
            "--host": {"type": "str", "default": "127.0.0.1",
                       "help": "ComfyUI host"},
            "--port": {"type": "int", "default": 8188,
                       "help": "ComfyUI port"},
            "--check": {"type": "bool", "default": True,
                        "help": "Query /object_info to confirm the "
                                "connection and detect the tooling nodes"},
            "--watch": {"type": "bool", "default": True,
                        "help": "Subscribe to ComfyUI's workflow publish "
                                "bus so the daemon follows the browser's "
                                "current graph. Also clears any "
                                "file-exclusive lock."},
            "--subscribe_timeout": {"type": "float", "default": 5.0,
                                    "help": "Seconds to wait for the "
                                            "watcher subscription"},
        },
    )
    def comfy_connect(self, params):
        parent = self.parent
        host = params.get("host", "127.0.0.1")
        port = int(params.get("port", 8188))
        check = bool(params.get("check", True))
        watch = bool(params.get("watch", True))
        subscribe_timeout = float(params.get("subscribe_timeout", 5.0))

        parent._host = host
        parent._port = port
        parent._client = get_client(host, port)

        if not check:
            subscribed = False
            if watch:
                try:
                    parent._client.watch_workflows(
                        self._on_workflow_published, replace=True,
                    )
                    subscribed = parent._client.wait_subscribed(
                        timeout=subscribe_timeout,
                    )
                except ComfyError as e:
                    return {"success": False,
                            "message": f"Watcher unavailable: {e}"}
                parent._release_to_browser()
            return {
                "success": True,
                "message": f"Connected to {host}:{port}",
                "data": {"host": host, "port": port,
                         "watching": watch,
                         "subscribed": subscribed},
            }

        try:
            info = parent._client.object_info()
        except ComfyError as e:
            parent._object_info = None
            parent._client = None
            return {"success": False,
                    "message": f"Could not reach ComfyUI at "
                               f"{host}:{port}: {e}"}

        parent._object_info = info

        has_tooling = "ETN_LoadImageCache" in info
        if not has_tooling:
            return {
                "success": False,
                "message": (
                    f"Connected to {host}:{port}, but the "
                    f"comfyui-tooling-nodes package is not installed. "
                    f"Install it in ComfyUI/custom_nodes/ and restart "
                    f"ComfyUI."
                ),
                "data": {"host": host, "port": port,
                         "node_count": len(info)},
            }

        subscribed = False
        if watch:
            try:
                parent._client.watch_workflows(
                    self._on_workflow_published, replace=True,
                )
                subscribed = parent._client.wait_subscribed(
                    timeout=subscribe_timeout,
                )
            except ComfyError as e:
                return {
                    "success": False,
                    "message": f"Connected to {host}:{port}, but the "
                               f"workflow watcher could not start: {e}",
                    "data": {"host": host, "port": port,
                             "node_count": len(info),
                             "has_tooling_nodes": True,
                             "watching": False},
                }
            parent._release_to_browser()

        msg = f"Connected to {host}:{port} " \
              f"({len(info)} node classes registered)"
        if watch and subscribed:
            msg += ", watching browser workflows"
        elif watch:
            msg += ", watcher started but not yet subscribed"

        return {
            "success": True,
            "message": msg,
            "data": {"host": host, "port": port,
                     "node_count": len(info),
                     "has_tooling_nodes": True,
                     "watching": watch,
                     "subscribed": subscribed},
        }

    @command(
        category="comfyui",
        help_text="Start or stop the workflow watcher.",
        args={
            "--enable": {"type": "bool", "default": True,
                         "help": "True to start the watcher, False to "
                                 "stop it"},
            "--subscribe_timeout": {"type": "float", "default": 5.0,
                                    "help": "Seconds to wait for the "
                                            "subscription when enabling"},
        },
    )
    def comfy_watch(self, params):
        parent = self.parent
        enable = bool(params.get("enable", True))
        subscribe_timeout = float(params.get("subscribe_timeout", 5.0))
        if parent._client is None:
            return {"success": False,
                    "message": "Not connected; run comfy_connect first"}
        if enable:
            try:
                parent._client.watch_workflows(
                    self._on_workflow_published, replace=True,
                )
                subscribed = parent._client.wait_subscribed(
                    timeout=subscribe_timeout,
                )
            except ComfyError as e:
                return {"success": False,
                        "message": f"Watcher could not start: {e}"}
            parent._release_to_browser()
            return {"success": True,
                    "message": "Watching for browser workflow publishes"
                               if subscribed
                               else "Watcher started (subscription "
                                    "pending)",
                    "data": {"subscribed": subscribed}}
        else:
            parent._client.unwatch_workflows()
            return {"success": True, "message": "Stopped watching"}

    # ------------------------------------------------------------------
    #  Publisher callbacks
    # ------------------------------------------------------------------

    @staticmethod
    def _publisher_key(publisher: dict) -> str:
        pid = publisher.get("id")
        if pid:
            return str(pid)
        name = publisher.get("name") or "unknown"
        return f"name:{name}"

    def _on_workflow_published(self, publisher: dict, graph: dict):
        parent = self.parent
        key = self._publisher_key(publisher)
        signature = _compute_signature(graph)
        display_name = publisher.get("name") or "unknown"
        full_name = f"ComfyUI Web ({display_name}, sig={signature})"

        parent._published_publishers[key] = {
            "key": key,
            "publisher_name": display_name,
            "publisher_id": publisher.get("id"),
            "signature": signature,
            "workflow_name": full_name,
            "graph": graph,
            "published_at": time.time(),
        }

        if parent._file_exclusive:
            parent._publish_state_change()
            return

        if (parent._pinned_publisher_key is None
                or parent._pinned_publisher_key == key):
            parent._set_active_workflow(full_name, graph, "browser",
                                        publisher_key=key)

        parent._publish_state_change()

    # ------------------------------------------------------------------
    #  Pin / unlock
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Pin the active workflow to one browser publisher.",
        args={
            "--key": {"type": "str", "required": False,
                      "help": "Publisher key to pin. Omit to pin "
                              "whichever publisher is currently active."},
        },
    )
    def comfy_pin_publisher(self, params):
        parent = self.parent
        key = params.get("key") or parent._active_workflow_publisher_key
        if not key:
            return {
                "success": False,
                "message": "No key given and no browser publisher is "
                           "currently active. Run comfy_list_publishers "
                           "to see available keys, or pass --key.",
                "data": {
                    "available": sorted(parent._published_publishers.keys()),
                },
            }
        entry = parent._published_publishers.get(key)
        if entry is None:
            return {
                "success": False,
                "message": f"No publisher with key {key!r}.",
                "data": {
                    "available": sorted(parent._published_publishers.keys()),
                },
            }
        parent._pinned_publisher_key = key
        parent._set_active_workflow(
            entry["workflow_name"], entry["graph"], "browser",
            publisher_key=key,
        )
        return {
            "success": True,
            "message": f"Pinned to publisher {key!r}",
            "data": {
                "key": key,
                "publisher_name": entry["publisher_name"],
                "workflow": entry["workflow_name"],
            },
        }

    @command(
        category="comfyui",
        help_text="Clear the publisher pin.",
        args={},
    )
    def comfy_unpin_publisher(self, params):
        parent = self.parent
        was = parent._pinned_publisher_key
        parent._pinned_publisher_key = None
        if was:
            return {"success": True,
                    "message": f"Unpinned (was {was!r})",
                    "data": {"was": was}}
        return {"success": True,
                "message": "No publisher was pinned"}

    @command(
        category="comfyui",
        help_text="Clear the file-exclusive lock.",
        args={},
    )
    def comfy_unlock_file(self, params):
        parent = self.parent
        was = parent._file_exclusive
        parent._file_exclusive = False
        if was:
            return {
                "success": True,
                "message": "File lock cleared; browser publishes will "
                           "promote on next publish",
            }
        return {"success": True,
                "message": "No file lock was set"}