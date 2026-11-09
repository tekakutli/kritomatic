"""Read-only status queries.

Three commands that read the handler's state without mutating it.
All three are in `_READ_ONLY_COMMANDS` so the container's `execute`
does not publish a state change after them.
"""

from __future__ import annotations

from ..decorators import command, AutoDispatchHandler
from .comfyui_inspect import _canvas_seed_is_wired


class StatusCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Return the handler's current workflow, parameters, "
                  "layer aliases, bindings, proxy state, and the active "
                  "workflow's signature.",
        args={},
    )
    def comfy_status(self, params):
        parent = self.parent
        with parent._batches_lock:
            active_batches = sorted(parent._batches.keys())

        from ..proxy import manager as proxy_manager
        proxy_state = proxy_manager.status()

        return {
            "success": True,
            "message": "ComfyUI handler state",
            "data": {
                "connected_to": (
                    f"{parent._host}:{parent._port}"
                    if parent._client is not None else None
                ),
                "workflow": parent._active_workflow_name,
                "workflow_source": parent._active_workflow_source,
                "workflow_signature": parent._active_workflow_signature,
                "workflow_parameters":
                    list(parent._active_workflow_parameters),
                "workflow_layer_requirements":
                    dict(parent._active_workflow_layers),
                "workflow_canvas_info":
                    dict(parent._active_workflow_canvas),
                "workflow_seed_wired": (
                    _canvas_seed_is_wired(
                        parent._active_workflow_graph or {}
                    )
                ),
                "workflow_exclusive": parent._file_exclusive,
                "active_batches": active_batches,
                "watching": (
                    parent._client.is_watching()
                    if parent._client is not None else False
                ),
                "params": dict(parent._params),
                "layer_aliases": dict(parent._layer_aliases),
                "scratch_blobs": sorted(parent._scratch_blobs.keys()),
                "canvas_source": parent._canvas_source,
                "canvas_layer": parent._canvas_layer,
                "selection_source": parent._selection_source,
                "selection_layer": parent._selection_layer,
                "selection_context": parent._selection_context,
                "selection_padding": parent._selection_padding,
                "pending": list(parent._pending.keys()),
                "pinned_publisher": parent._pinned_publisher_key,
                "active_publisher":
                    parent._active_workflow_publisher_key,
                "published_publishers":
                    sorted(parent._published_publishers.keys()),
                "proxy": proxy_state,
            },
        }

    @command(
        category="comfyui",
        help_text="List the currently-running batches.",
        args={},
    )
    def comfy_batches(self, params):
        parent = self.parent
        with parent._batches_lock:
            records = list(parent._batches.values())

        now = time.time() if False else __import__("time").time()
        items = []
        for r in records:
            items.append({
                "batch_id": r.batch_id,
                "workflow": r.workflow_name,
                "phase": r.current_phase,
                "requested_count": r.requested_count,
                "total_runs": r.total_runs,
                "finished_runs": len(r.run_records),
                "current_run_index": r.current_run_index,
                "base_seed": r.base_seed,
                "output_name": r.output_name,
                "started_at": r.started_at,
                "elapsed_s": round(now - r.started_at, 1),
            })
        items.sort(key=lambda x: x["started_at"])

        plural = "batch" if len(items) == 1 else "batches"
        return {
            "success": True,
            "message": f"{len(items)} active {plural}",
            "data": {"batches": items, "count": len(items)},
        }

    @command(
        category="comfyui",
        help_text="List the browser publishers the daemon has heard from "
                  "this session, newest publish first.",
        args={},
    )
    def comfy_list_publishers(self, params):
        parent = self.parent
        entries = list(parent._published_publishers.values())
        entries.sort(key=lambda e: e["published_at"], reverse=True)

        pinned = parent._pinned_publisher_key
        active = parent._active_workflow_publisher_key

        items = []
        for e in entries:
            items.append({
                "key": e["key"],
                "publisher_name": e["publisher_name"],
                "publisher_id": e["publisher_id"],
                "workflow_name": e["workflow_name"],
                "signature": e["signature"],
                "published_at": e["published_at"],
                "pinned": e["key"] == pinned,
                "active": e["key"] == active,
            })

        return {
            "success": True,
            "message": f"{len(items)} publisher(s)",
            "data": {
                "publishers": items,
                "pinned_key": pinned,
                "active_key": active,
            },
        }