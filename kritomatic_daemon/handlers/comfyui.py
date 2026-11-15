"""
comfyui.py — the @command surface for driving ComfyUI from a bundle.

The container. Owns the shared state (connection, active workflow,
batch tracking, active panel config, intercept mode) and dispatches to
seven sub-handlers, each in its own file:

    comfyui_connection   comfy_connect
    comfyui_workflow     comfy_load, comfy_list_workflows, recent aliases
    comfyui_config       comfy_apply_config, comfy_prepare_only
    comfyui_generation   comfy_generate, cancel, paste, history,
                         intercept mode (set/resume/discard/list)
    comfyui_status       comfy_status, comfy_batches
    comfyui_proxy        proxy install / start / stop / status / logs /
                         health / test / uninstall
    playgrounds          playground_register / unregister / set_layer

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
    playgrounds          live registry of running playgrounds

Every sub-handler holds `self.parent` back to this handler.

State publishing
================
`execute()` publishes a state change after every state-mutating
command (success or failure), so subscribers to the event bus always
see a current `active_batches` list and `playgrounds` list.  Sub-handlers
do not publish; the container does, once, after the sub-handler returns.

`_publish_state_change` reads `_compute_status_data` (a pure read of
handler state, no sweep, no publish) rather than calling `comfy_status`
itself.  That separation is what prevents a recursion between
`_sweep_expired_intercepts` (which publishes when it removes
something) and `_publish_state_change` (which used to call
`comfy_status`, which itself sweeps and publishes).

`comfy_status` returns are post-processed to inject `playgrounds` so
that clients querying the status dict directly see the live registry
without needing the event bus.  This is a container-level hook rather
than a `comfyui_status.py` change, keeping the status module a pure
read of ComfyUI state and isolating the playground subsystem's
contributions here.

Concurrency
===========
The daemon's socket server spawns one handler thread per inbound
connection.  Two concurrent `comfy_panel_run` bundles share this
handler's state.  Four locks protect it:

  `_batches_lock`       guards `_batches`.
  `_intercepted_lock`   guards `_intercepted_batches`.
  `_paste_lock`         guards the document-mutating half of the
                        generation pipeline.
  `_proxy_client_lock`  guards the lazily-created `_proxy_client`.

`PlaygroundCommands` owns its own `_lock` for the registry and a
background sweep thread; both live entirely inside that class and are
not shared with this container's state.

Workflow activation
===================
`_set_active_workflow` is the only method that mutates the
active-workflow tuple.  Sub-handlers invoke it through
`self.parent._xxx`; keeping it here keeps the `_active_workflow_*`
fields consistent no matter who triggers a change.

Native-v2 slot tables and `_active_config`
==========================================
`comfy_load` walks the loaded graph once and records three slot tables
on the handler; `comfy_apply_config` validates a panel config against
those tables and stores the normalized result in `_active_config`;
`comfy_generate` consumes `_active_config` for the native v2 pipeline.
All are cleared on any workflow change so a stale config can never
accidentally apply to the wrong workflow.

`comfy_panel_run` bypasses `_active_config` entirely: it validates the
config inline and passes it as a kwarg to `comfy_generate`.  This is
what makes concurrent runs safe.

Shared ProxyV2 client
=====================
`_proxy_client` caches the proxy HTTP client across runs so
`ProxyV2.from_pid_file()` fires once per proxy lifetime.  Invalidated
on `ProxyUnreachable` and on proxy start/stop/connect-started-proxy.

Slot extraction failures
========================
When `comfy_load` cannot extract slots, the workflow still loads, but
the three slot tables stay empty and the native pipeline cannot drive
the graph.  `_active_workflow_slot_error` carries the extractor's
message so `comfy_status` (and, through it, the panel description) can
show *why* the panel is empty.
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
from .playgrounds import PlaygroundCommands


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
        self._paste_lock = threading.Lock()

        # ---- shared proxy client ----
        self._proxy_client = None
        self._proxy_client_lock = threading.Lock()

        # ---- intercept mode ----
        _cfg = _load_intercept_config()
        self._intercept_enabled: bool = _cfg["enabled"]
        self._intercept_url: str = _cfg["url"]
        self._intercepted_batches: dict = {}
        self._intercepted_lock = threading.Lock()

        # ---- active workflow ----
        self._active_workflow_name: Optional[str] = None
        self._active_workflow_graph: Optional[dict] = None
        self._active_workflow_source: Optional[str] = None
        self._active_workflow_resource_slots: dict = {}
        self._active_workflow_param_slots: dict = {}
        self._active_workflow_output_nodes: dict = {}
        self._active_workflow_slot_error: Optional[str] = None
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
        self._playgrounds_cmds = PlaygroundCommands(self)

        self._sub_handlers = [
            self._connection_cmds,
            self._workflow_cmds,
            self._config_cmds,
            self._generation_cmds,
            self._status_cmds,
            self._proxy_cmds,
            self._playgrounds_cmds,
        ]

        # Rebuild the dispatch map now that _sub_handlers exists.
        self._cmd_map = __import__(
            "kritomatic_daemon.decorators", fromlist=["build_handler_map"]
        ).build_handler_map(self)

    def execute(self, cmd_type, params):
        result = super().execute(cmd_type, params)

        # Inject the live playground registry into comfy_status returns.
        # Doing it here (rather than in comfyui_status.py) keeps that
        # module a pure read of ComfyUI state and confines the
        # playground subsystem's contributions to this container.
        if isinstance(result, dict) and cmd_type == "comfy_status":
            playgrounds = self._playgrounds_cmds.list()
            data = result.get("data")
            if isinstance(data, dict):
                data["playgrounds"] = playgrounds
            else:
                result["data"] = {"playgrounds": playgrounds}

        if (isinstance(result, dict)
                and cmd_type not in _READ_ONLY_COMMANDS):
            self._publish_state_change()
        return result

    def _publish_state_change(self):
        """Publish the current state as a `state` event.

        Uses `_compute_status_data` directly (not `comfy_status`) so
        this never recurses.  Injects the playground registry into the
        published payload so event-bus subscribers see the same shape
        as clients polling `comfy_status`.
        """
        try:
            from ..server.event_bus import get_event_bus
            status_data = self._status_cmds._compute_status_data()
            status_data["playgrounds"] = self._playgrounds_cmds.list()
            get_event_bus().publish("state", status_data)
        except Exception:
            pass

    def _sweep_expired_intercepts(self) -> None:
        """Remove expired InterceptedBatch records."""
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
        from ..utils.comfyui_proxy_client import ProxyV2

        with self._proxy_client_lock:
            if self._proxy_client is not None:
                return self._proxy_client
            client = ProxyV2.from_pid_file()
            self._proxy_client = client
            return client

    def invalidate_proxy_client(self) -> None:
        with self._proxy_client_lock:
            self._proxy_client = None