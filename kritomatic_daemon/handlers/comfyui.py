"""
comfyui.py — the @command surface for driving ComfyUI from a bundle.

The container. Owns the shared state (connection, active workflow,
batch tracking, active panel config) and dispatches to six sub-handlers,
each in its own file:

    comfyui_connection   comfy_connect
    comfyui_workflow     comfy_load, comfy_list_workflows, recent aliases
    comfyui_config       comfy_apply_config, comfy_prepare_only
    comfyui_generation   comfy_generate, cancel, paste, history
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
from typing import Optional

from ..decorators import AutoDispatchHandler
from .comfyui_types import (
    _READ_ONLY_COMMANDS,
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
        try:
            from ..server.event_bus import get_event_bus
            status = self._status_cmds.comfy_status({})
            if isinstance(status, dict) and status.get("success"):
                get_event_bus().publish("state", status.get("data", {}))
        except Exception:
            pass

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
