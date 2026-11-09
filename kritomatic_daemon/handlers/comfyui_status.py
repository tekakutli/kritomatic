"""Read-only status queries.

Two commands that read the handler's state without mutating it.
Both are in `_READ_ONLY_COMMANDS` so the container's `execute`
does not publish a state change after them.
"""

from __future__ import annotations

from ..decorators import command, AutoDispatchHandler


class StatusCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    @command(
        category="comfyui",
        help_text="Return the handler's current workflow, native slot "
                  "tables, active panel config, and proxy state.",
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

                # Native-v2 slot tables. Empty when the workflow has no
                # native inputs (e.g. legacy ETN_* graphs).
                "workflow_resource_slots":
                    dict(parent._active_workflow_resource_slots),
                "workflow_param_slots":
                    dict(parent._active_workflow_param_slots),
                "workflow_output_nodes":
                    dict(parent._active_workflow_output_nodes),
                "config": (
                    dict(parent._active_config)
                    if parent._active_config is not None else None
                ),

                "active_batches": active_batches,
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

        import time as _time
        now = _time.time()
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