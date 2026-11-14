"""
comfyui.py — the @command surface for driving ComfyUI from a bundle.

The container. Owns the shared state (connection, active workflow,
batch tracking, active panel config, intercept mode) and dispatches to
six sub-handlers, each in its own file:

    comfyui_connection   comfy_connect
    comfyui_workflow     comfy_load, comfy_list_workflows, recent aliases
    comfyui_config       comfy_apply_config, comfy_prepare_only
    comfyui_generation   comfy_generate, cancel, paste, history,
                         intercept mode (set/resume/discard/list)
    comfyui_status       comfy_status, comfy_batches
    comfyui_proxy        proxy install / start / stop / status / logs /
                         health / test / uninstall

Supporting modules, split by stability:

    Stable
    ======
    comfyui_types        dataclasses, path constants, alias persistence
    comfyui_document     document-lifetime helpers, color-mode check
    comfyui_inspect      native-v2 slot extraction

    Semi-stable
    ===========
    comfyui_connection   connection lifecycle
    comfyui_status       read-only state queries

    Dynamic
    =======
    comfyui_workflow     workflow loading
    comfyui_config       panel config consumption + prepare pass
    comfyui_generation   the generation pipeline
    comfyui_proxy        supervisor for the comfy-api-proxy child process

Every sub-handler holds `self.parent` back to this handler.

State publishing
================
`execute()` publishes a state change after every state-mutating
command (success or failure), so subscribers to the event bus always
see a current `active_batches` list. Sub-handlers do not publish;
the container does, once, after the sub-handler returns.

`_publish_state_change` reads `_compute_status_data` (a pure read of
handler state, no sweep, no publish) rather than calling
`comfy_status` itself.  That separation is what prevents a recursion
between `_sweep_expired_intercepts` (which publishes when it removes
something) and `_publish_state_change` (which used to call
`comfy_status`, which itself sweeps and publishes).

Concurrency
===========
The daemon's socket server spawns one handler thread per inbound
connection (see `server/socket_server.py`). That means two concurrent
`comfy_panel_run` bundles — whether from two docker clicks, the web
panel, or two CLI terminals — are dispatched on two threads that
share this handler's state.

Three locks protect that state:

  `_batches_lock`       guards `_batches`. Every mutation is a
                        dict insert/pop; the lock has held since before
                        concurrency was exposed at the client level.
  `_intercepted_lock`   guards `_intercepted_batches`; same story.
  `_paste_lock`         guards the document-mutating half of the
                        generation pipeline (`_paste_result` and the
                        `_last_result` / `_last_document` writes that
                        follow it). Two concurrent pastes on the same
                        Krita document corrupt its layer tree: the two
                        `rootNode().addChildNode` calls interleave, one
                        node is lost, and the `refreshProjection` sees
                        an inconsistent tree mid-rebuild. The lock
                        serializes pastes so each sees a consistent
                        document; prepares, network uploads, and the
                        proxy poll loops are still fully concurrent
                        because none of them touch the document.

A fourth lock guards the shared proxy client:

  `_proxy_client_lock`  guards the lazily-created `_proxy_client`.
                        The client is created once per proxy lifetime
                        and reused across every run; the lock
                        serializes the "check-then-create" so a burst
                        of concurrent runs performs exactly one
                        `ProxyV2.from_pid_file()` probe, not N.

Workflow activation
===================
`_set_active_workflow` is the only method that mutates the
active-workflow tuple. Sub-handlers invoke it through
`self.parent._xxx`; keeping it here keeps the `_active_workflow_*`
fields consistent no matter who triggers a change.

Native-v2 slot tables and `_active_config`
==========================================
`comfy_load` walks the loaded graph once and records three slot tables
on the handler:

    _active_workflow_resource_slots  — {"@thecanvas": {node_id, ...}, ...}
    _active_workflow_param_slots     — {"denoise": {node_id, kind}, ...}
    _active_workflow_output_nodes    — {"9": {class_type, filename_prefix}, ...}

`comfy_apply_config` validates a panel config against those tables and
stores the normalized result in `_active_config`. `comfy_generate`
consumes `_active_config` for the native v2 pipeline.

Both `_active_config` and the three slot tables are cleared on any
workflow change (via `_set_active_workflow`) so a stale config can
never accidentally apply to the wrong workflow.

`comfy_panel_run` bypasses `_active_config` entirely: it validates the
config inline (against the current slot tables) and passes it as a
kwarg to `comfy_generate`. This is what makes concurrent runs safe —
run-2's config never lands in `_active_config`, so it cannot overwrite
run-1's config mid-prepare. `_active_config` remains the fallback for
CLI callers that omit some sections; those callers are inherently
serial (one CLI invocation, one batch), so the fallback's shared-
mutable-state risk does not apply to them.

Shared ProxyV2 client
=====================
`_proxy_client` caches the proxy HTTP client across runs so
`ProxyV2.from_pid_file()` — which does a health probe on construction
— fires once per proxy lifetime, not once per run.  The client is
invalidated on `ProxyUnreachable` (proxy died or was stopped) and on
`comfy_proxy_start` / `comfy_proxy_stop` / `comfy_connect`-starts-proxy
(pid changed).  `get_proxy_client()` is what every generation path
calls; `invalidate_proxy_client()` is what failures and command
handlers call to reset it.

Slot extraction failures
========================
When `comfy_load` cannot extract slots (duplicate `@`-name or
`_meta.title`), the workflow still loads — the graph is valid for
ComfyUI — but the three slot tables stay empty and the native
pipeline cannot drive the graph.  `_active_workflow_slot_error`
carries the extractor's message so `comfy_status` (and, through it,
the panel description) can show *why* the panel is empty instead of
silently rendering a form with no fields.
"""

from __future__ import annotations

import threading
import time
from typing import Optional

from ..decorators import AutoDispatchHandler
from .comfyui_types import (
    _READ_ONLY_COMMANDS,
    _load_intercept_config,
    _load_recent_aliases,
)
from .comfyui_inspect import (
    extract_output_nodes,
    extract_param_slots,
    extract_resource_slots,
)
from .comfyui_connection import ConnectionCommands
from .comfyui_workflow import WorkflowCommands
from .comfyui_config import ConfigCommands
from .comfyui_generation import GenerationCommands
from .comfyui_status import StatusCommands
from .comfyui_proxy import ProxyCommands


class ComfyUIHandler(AutoDispatchHandler):
    """Stateful handler for the comfy_* command family."""

    def __init__(self):
        super().__init__()  # not the real dispatch build; see bottom

        # ---- connection ----
        self._host: str = "127.0.0.1"
        self._port: int = 8188
        self._client = None

        # ---- batch tracking ----
        self._batches: dict = {}
        self._batches_lock = threading.Lock()
        # Serializes the document-mutating half of generation
        # (`_paste_result` plus the `_last_result` / `_last_document`
        # writes it feeds). Prepare, uploads, and proxy polls stay
        # concurrent; only the paste-critical section is serialized.
        # See the module docstring, "Concurrency".
        self._paste_lock = threading.Lock()

        # ---- shared proxy client ----
        # Lazily created by `get_proxy_client()`; reused across every
        # run so the health probe in `ProxyV2.from_pid_file()` fires
        # once per proxy lifetime, not once per run.  The lock
        # serializes check-then-create so a burst of concurrent runs
        # performs exactly one probe.  See "Shared ProxyV2 client".
        self._proxy_client = None
        self._proxy_client_lock = threading.Lock()

        # ---- intercept mode ----
        # Config-loaded at construction; updated by comfy_set_intercept.
        # `_intercepted_batches` holds prepared-but-not-submitted
        # workflows awaiting user review in the interceptor playground.
        _cfg = _load_intercept_config()
        self._intercept_enabled: bool = _cfg["enabled"]
        self._intercept_url: str = _cfg["url"]
        self._intercepted_batches: dict = {}
        self._intercepted_lock = threading.Lock()

        # ---- active workflow ----
        self._active_workflow_name: Optional[str] = None
        self._active_workflow_graph: Optional[dict] = None
        self._active_workflow_source: Optional[str] = None

        # Native-v2 slot tables. Populated by comfy_load, cleared on any
        # workflow change. Empty for legacy ETN_* graphs.
        self._active_workflow_resource_slots: dict = {}
        self._active_workflow_param_slots: dict = {}
        self._active_workflow_output_nodes: dict = {}
        # Set when comfy_load's slot extraction raised (duplicate
        # @-name or _meta.title).  The workflow still loads — the panel
        # just has no configurable slots — but the message is surfaced
        # in comfy_status and in the panel description, so the user
        # sees *why* the panel is empty instead of a silently blank
        # form.
        self._active_workflow_slot_error: Optional[str] = None
        # Normalized panel config. Populated by comfy_apply_config,
        # consumed by comfy_generate. None = not configured.
        self._active_config: Optional[dict] = None

        # ---- recent aliases (persisted) ----
        self._recent_aliases: list = _load_recent_aliases()
        self._recent_aliases_lock = threading.Lock()

        # ---- last paste (used by comfy_paste_last) ----
        self._last_result = None
        self._last_document = None

        # ---- sub-handlers ----
        self._status_cmds = StatusCommands(self)
        self._connection_cmds = ConnectionCommands(self)
        self._workflow_cmds = WorkflowCommands(self)
        self._config_cmds = ConfigCommands(self)
        self._generation_cmds = GenerationCommands(self)
        self._proxy_cmds = ProxyCommands(self)

        self._sub_handlers = [
            self._connection_cmds,
            self._workflow_cmds,
            self._config_cmds,
            self._generation_cmds,
            self._status_cmds,
            self._proxy_cmds,
        ]

        # Rebuild the dispatch map now that _sub_handlers exists.
        self._cmd_map = __import__(
            "kritomatic_daemon.decorators", fromlist=["build_handler_map"]
        ).build_handler_map(self)

    def execute(self, cmd_type, params):
        result = super().execute(cmd_type, params)
        if (isinstance(result, dict)
                and cmd_type not in _READ_ONLY_COMMANDS):
            self._publish_state_change()
        return result

    def _publish_state_change(self):
        """Publish the current state as a `state` event.

        Uses `_compute_status_data` directly (not `comfy_status`) so
        this never recurses through `_sweep_expired_intercepts` →
        `_publish_state_change` → `comfy_status` → sweep → publish.
        The `state` event's `data` is exactly what `comfy_status`
        would return, minus the sweep that a fresh read implies.
        """
        try:
            from ..server.event_bus import get_event_bus
            status_data = self._status_cmds._compute_status_data()
            get_event_bus().publish("state", status_data)
        except Exception:
            pass

    def _sweep_expired_intercepts(self) -> None:
        """Remove expired InterceptedBatch records.

        Called from status queries and command entry points.  Publishes
        state only if something actually expired, so the common no-op
        case pays nothing.

        The publish uses `_publish_state_change` (which reads
        `_compute_status_data` directly), so there is no recursion.
        """
        now = time.time()
        expired_ids = []
        with self._intercepted_lock:
            for bid, rec in list(self._intercepted_batches.items()):
                if rec.expiry_at <= now:
                    expired_ids.append(bid)
                    del self._intercepted_batches[bid]
        if expired_ids:
            print(
                f"[comfyui] swept {len(expired_ids)} expired "
                f"intercept(s)",
                flush=True,
            )
            self._publish_state_change()

    def _populate_native_slots(self, graph: dict) -> None:
        """Extract and store the three native slot tables from `graph`.

        Called by comfy_load after the graph is set. A duplicate
        @-name or _meta.title raises ValueError, which the caller
        (comfy_load) surfaces as a load error — an ambiguous graph is
        not loadable via the native pipeline.
        """
        self._active_workflow_resource_slots = extract_resource_slots(graph)
        self._active_workflow_param_slots = extract_param_slots(graph)
        self._active_workflow_output_nodes = extract_output_nodes(graph)

    def _clear_native_slots(self) -> None:
        self._active_workflow_resource_slots = {}
        self._active_workflow_param_slots = {}
        self._active_workflow_output_nodes = {}
        self._active_config = None

    def _set_active_workflow(self, name: str, graph: dict, source: str) -> None:
        self._active_workflow_name = name
        self._active_workflow_graph = graph
        self._active_workflow_source = source

        # Native slots + config are per-workflow. Best-effort
        # extraction: a duplicate-title graph still loads, but the
        # native slots stay empty and comfy_apply_config will reject
        # everything.  The extractor's message is kept so comfy_status
        # and the panel description can surface it.
        self._active_workflow_slot_error = None
        try:
            self._populate_native_slots(graph)
        except ValueError as e:
            self._active_workflow_slot_error = str(e)
            self._clear_native_slots()
        self._active_config = None

    # ------------------------------------------------------------------
    #  Proxy client cache
    # ------------------------------------------------------------------

    def get_proxy_client(self):
        """Return the shared ProxyV2 client, creating it on first use.

        Raises `ProxyUnreachable` (from `ProxyV2.from_pid_file()`) if
        the proxy pid file is missing or its health probe fails.
        Cached clients are returned as-is: if the proxy died since the
        cache was populated, the caller's next operation raises
        `ProxyUnreachable`, and the caller is expected to call
        `invalidate_proxy_client()` before retrying (or letting the
        next run pick up a fresh client).  See the module docstring,
        "Shared ProxyV2 client".
        """
        from ..utils.comfyui_proxy_client import ProxyV2

        with self._proxy_client_lock:
            if self._proxy_client is not None:
                return self._proxy_client
            client = ProxyV2.from_pid_file()
            self._proxy_client = client
            return client

    def invalidate_proxy_client(self) -> None:
        """Drop the cached proxy client.  Idempotent.

        Called when a caller observes `ProxyUnreachable` (proxy died
        between runs, or was stopped), and by `comfy_proxy_start` /
        `comfy_proxy_stop` / `comfy_connect`-started-proxy so the next
        `get_proxy_client()` re-probes fresh state.
        """
        with self._proxy_client_lock:
            self._proxy_client = None
