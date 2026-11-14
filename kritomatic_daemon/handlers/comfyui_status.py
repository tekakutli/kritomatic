"""Read-only status queries.

Two commands that read the handler's state without mutating it.
Both are in `_READ_ONLY_COMMANDS` so the container's `execute`
does not publish a state change after them.

`_compute_status_data` is the shared shape used by both
`comfy_status` and `ComfyUIHandler._publish_state_change`.  It reads
handler state but neither sweeps expired intercepts nor publishes —
those are the caller's jobs.  Splitting the read from the sweep is
what prevents a recursion between `_sweep_expired_intercepts` →
`_publish_state_change` → `comfy_status` → sweep → publish.

PROXY STATUS CACHE
==================
`_compute_status_data` calls `proxy_manager.status()`, which is
expensive on POSIX: it spawns `ps -p <pid> -o args=` to verify the
proxy is ours (~5-10ms per call).  With a batch of concurrent runs,
`_publish_state_change` fires on each run start and each run finish,
so `_compute_status_data` can run 4+ times within milliseconds of a
concurrent burst — 4+ `ps` subprocesses for the same proxy state.

`_cached_proxy_status()` caches the `proxy_manager.status()` result
for 1 second.  This is fine because:

  · The proxy indicator in the docker / web panel is a display
    element that already tolerates ~1 s of staleness by design.
  · The generation path (`_comfy_generate_impl` etc.) does not go
    through `proxy_manager.status()` at all — it uses the cached
    `ProxyV2` client (`ComfyUIHandler.get_proxy_client()`) whose
    health state is queried lazily per operation.  So caching
    `status()` does not affect run correctness, only the display.

Invalidation sites (call `invalidate_proxy_status_cache()`):

  · `comfy_connect` after `proxy_manager.start()` when the proxy was
    not already running.  The state changed from "not running" to
    "running"; the UI should reflect this without waiting for the
    TTL.
  · `comfy_proxy_start` after `manager.start()` returns.
  · `comfy_proxy_stop` after `manager.stop()` returns.
  · `comfy_proxy_uninstall` after `manager.uninstall()` returns.

Without invalidation, the toggle/indicator would take up to 1 s to
reflect the just-issued state.  Self-heals via TTL even if a caller
forgets an invalidation.
"""

from __future__ import annotations

import threading
import time

from ..decorators import command, AutoDispatchHandler


# ---------------------------------------------------------------------------
#  Proxy status cache
# ---------------------------------------------------------------------------

_PROXY_STATUS_TTL = 1.0
_PROXY_STATUS_LOCK = threading.Lock()
_PROXY_STATUS_CACHE: dict = {"data": None, "at": 0.0}


def _cached_proxy_status() -> dict:
    """Return the cached proxy_manager.status() result, refreshing if stale.

    See the module docstring, PROXY STATUS CACHE.  Held under
    `_PROXY_STATUS_LOCK` for the full check-and-refresh, so a burst of
    concurrent `_compute_status_data` calls performs exactly one
    `status()` call.
    """
    from ..proxy import manager as proxy_manager

    with _PROXY_STATUS_LOCK:
        now = time.monotonic()
        cached = _PROXY_STATUS_CACHE["data"]
        if (cached is not None
                and (now - _PROXY_STATUS_CACHE["at"]) < _PROXY_STATUS_TTL):
            return cached
        fresh = proxy_manager.status()
        _PROXY_STATUS_CACHE["data"] = fresh
        _PROXY_STATUS_CACHE["at"] = time.monotonic()
        return fresh


def invalidate_proxy_status_cache() -> None:
    """Drop the cached proxy status.  Idempotent.

    Called from `comfy_connect` (after auto-starting the proxy),
    `comfy_proxy_start` / `comfy_proxy_stop` / `comfy_proxy_uninstall`
    so the next `_compute_status_data` reads fresh state.
    """
    with _PROXY_STATUS_LOCK:
        _PROXY_STATUS_CACHE["data"] = None
        _PROXY_STATUS_CACHE["at"] = 0.0


class StatusCommands(AutoDispatchHandler):

    def __init__(self, parent):
        self.parent = parent
        super().__init__()

    def _compute_status_data(self) -> dict:
        """Assemble the status snapshot the `state` event carries.

        Merges internal `_batches` ids with `_intercepted_batches` ids
        into `active_batches`, so a Cancel-All from the docker sees
        both kinds.  Also exposes the raw `intercepted_batches` list
        and an `intercept` block so the docker/panel can render the
        toggle and the pending count.

        No sweep, no publish.  Used by:
          - `comfy_status` (after its own sweep),
          - `ComfyUIHandler._publish_state_change` (no sweep: publishes
            whatever the current state is).

        `proxy_manager.status()` is replaced with the 1 s TTL cache
        `_cached_proxy_status()` — see the module docstring, PROXY
        STATUS CACHE, for the rationale and invalidation sites.
        """
        parent = self.parent

        with parent._batches_lock:
            internal_ids = sorted(parent._batches.keys())

        with parent._intercepted_lock:
            intercepted = [
                {
                    "batch_id":      r.batch_id,
                    "workflow_name": r.workflow_name,
                    "created_at":    r.created_at,
                    "expiry_at":     r.expiry_at,
                }
                for r in parent._intercepted_batches.values()
            ]
        intercepted_ids = sorted(e["batch_id"] for e in intercepted)
        active_batches = internal_ids + intercepted_ids

        proxy_state = _cached_proxy_status()

        return {
            "connected_to": (
                f"{parent._host}:{parent._port}"
                if parent._client is not None else None
            ),
            "workflow": parent._active_workflow_name,
            "workflow_source": parent._active_workflow_source,

            # Native-v2 slot tables. Empty when the workflow has no
            # native inputs (e.g. legacy ETN_* graphs) or when
            # extraction failed on duplicate names/titles.
            "workflow_resource_slots":
                dict(parent._active_workflow_resource_slots),
            "workflow_param_slots":
                dict(parent._active_workflow_param_slots),
            "workflow_output_nodes":
                dict(parent._active_workflow_output_nodes),
            # Set when comfy_load's slot extraction raised.  The
            # workflow is loaded and valid for ComfyUI, but the
            # native pipeline has no slots to drive.  The panel
            # renders this as a red "Slot error" line so the empty
            # form has an explanation.
            "workflow_slot_error":
                parent._active_workflow_slot_error,
            "config": (
                dict(parent._active_config)
                if parent._active_config is not None else None
            ),

            "active_batches": active_batches,
            "intercepted_batches": intercepted,
            "intercept": {
                "enabled": parent._intercept_enabled,
                "url": parent._intercept_url,
            },

            "proxy": proxy_state,
        }

    @command(
        category="comfyui",
        help_text="Return the handler's current workflow, native slot "
                  "tables, active panel config, and proxy state. "
                  "Sweeps expired intercepts before reading.",
        args={},
    )
    def comfy_status(self, params):
        self.parent._sweep_expired_intercepts()
        return {
            "success": True,
            "message": "ComfyUI handler state",
            "data": self._compute_status_data(),
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

        # Also include intercepted batches, distinguished by phase.
        with parent._intercepted_lock:
            intercepted_records = list(
                parent._intercepted_batches.values()
            )
        for r in intercepted_records:
            items.append({
                "batch_id": r.batch_id,
                "workflow": r.workflow_name,
                "phase": "intercepted",
                "requested_count": 1,
                "total_runs": 1,
                "finished_runs": 0,
                "current_run_index": 0,
                "base_seed": r.base_seed,
                "output_name": None,
                "started_at": r.created_at,
                "elapsed_s": round(now - r.created_at, 1),
                "intercepted": True,
            })

        plural = "batch" if len(items) == 1 else "batches"
        return {
            "success": True,
            "message": f"{len(items)} active {plural}",
            "data": {"batches": items, "count": len(items)},
        }