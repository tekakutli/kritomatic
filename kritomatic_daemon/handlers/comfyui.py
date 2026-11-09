"""
comfyui.py — the @command surface for driving ComfyUI from a bundle.

The container. Owns the shared state (connection, active workflow,
params, layer aliases, scratch blobs, batch tracking, publisher map,
active panel config) and dispatches to eight sub-handlers, each in its
own file:

    comfyui_connection   comfy_connect, comfy_watch, publisher pinning
    comfyui_workflow     comfy_load, comfy_set_param, layer aliases
    comfyui_bindings     comfy_bind_canvas, comfy_bind_selection
    comfyui_scratch      comfy_set_layer_pixels, scratch blob mgmt
    comfyui_config       comfy_apply_config
    comfyui_generation   comfy_generate, cancel, paste, history
    comfyui_status       comfy_status, comfy_batches, list_publishers
    comfyui_proxy        comfy_proxy_install / start / stop / status /
                         logs / health / test / uninstall

Supporting modules, split by stability:

    Stable
    ======
    comfyui_types        dataclasses, path constants, alias persistence
    comfyui_document     document-lifetime helpers, color-mode check
    comfyui_inspect      workflow interface extraction (legacy + native)

    Semi-stable
    ===========
    comfyui_connection   subscription lifecycle, publisher tracking
    comfyui_bindings     canvas/selection source bindings
    comfyui_status       read-only state queries

    Dynamic
    =======
    comfyui_workflow     workflow loading, params, aliases
    comfyui_scratch      scratch-blob subsystem
    comfyui_config       panel config consumption
    comfyui_generation   the whole generation pipeline
    comfyui_proxy        supervisor for the comfy-api-proxy child process

Every sub-handler holds `self.parent` back to this handler. The state
lives here because all eight sub-handlers touch overlapping pieces of
it. Sub-handlers are stateless apart from that back-reference.

State publishing
================
`execute()` publishes a state change after every state-mutating
command (success or failure), so subscribers to the event bus always
see a current `active_batches` list. Sub-handlers do not publish;
the container does, once, after the sub-handler returns.

Workflow activation
===================
`_set_active_workflow` and `_release_to_browser` are the only methods
that mutate the active-workflow tuple. Both are invoked through
`self.parent._xxx` from sub-handlers; keeping them here keeps the
`_active_workflow_*` fields consistent no matter who triggers a change.

Native-v2 slot tables and `_active_config`
==========================================
`comfy_load` walks the loaded graph once and records three slot tables
on the handler (see comfyui_inspect._extract_*):

    _active_workflow_resource_slots  — {"@thecanvas": {node_id, ...}, ...}
    _active_workflow_param_slots     — {"denoise": {node_id, kind}, ...}
    _active_workflow_output_nodes    — {"9": {class_type, filename_prefix}, ...}

`comfy_apply_config` validates a panel config against those tables and
stores the normalized result in `_active_config`. `comfy_generate`
consumes `_active_config` for the native v2 pipeline (Stage 5).

Both `_active_config` and the three slot tables are cleared on any
workflow change (via `_set_active_workflow` and `comfy_load`) so a
stale config can never accidentally apply to the wrong workflow.
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
    _compute_signature,
    _extract_canvas_info,
    _extract_layer_requirements,
    _extract_output_nodes,
    _extract_param_slots,
    _extract_parameters,
    _extract_resource_slots,
)
from .comfyui_connection import ConnectionCommands
from .comfyui_workflow import WorkflowCommands
from .comfyui_bindings import BindingsCommands
from .comfyui_scratch import ScratchCommands
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
        self._object_info: Optional[dict] = None

        # ---- batch tracking ----
        self._batches: dict = {}
        self._batches_lock = threading.Lock()

        # ---- active workflow ----
        self._active_workflow_name: Optional[str] = None
        self._active_workflow_graph: Optional[dict] = None
        self._active_workflow_source: Optional[str] = None
        self._active_workflow_signature: Optional[str] = None
        self._active_workflow_parameters: list = []
        self._active_workflow_layers: dict = {
            "image_layers": [], "mask_layers": []
        }
        self._active_workflow_canvas: dict = {
            "has_canvas": False,
            "has_selection": False,
            "selection_context": "entire_image",
            "selection_padding": 0,
        }
        # Native-v2 slot tables. Populated by comfy_load, cleared on any
        # workflow change. Empty for legacy ETN_* graphs.
        self._active_workflow_resource_slots: dict = {}
        self._active_workflow_param_slots: dict = {}
        self._active_workflow_output_nodes: dict = {}
        # Normalized panel config. Populated by comfy_apply_config,
        # consumed by comfy_generate (Stage 5). None = not configured.
        self._active_config: Optional[dict] = None

        # ---- publisher tracking ----
        self._published_publishers: dict = {}
        self._pinned_publisher_key: Optional[str] = None
        self._active_workflow_publisher_key: Optional[str] = None
        self._file_exclusive: bool = False

        # ---- workflow parameters and aliases ----
        self._params: dict = {}
        self._layer_aliases: dict = {}

        # ---- scratch blobs ----
        self._scratch_blobs: dict = {}

        # ---- recent aliases (persisted) ----
        self._recent_aliases: list = _load_recent_aliases()
        self._recent_aliases_lock = threading.Lock()

        # ---- canvas / selection bindings ----
        self._canvas_source: str = "active"
        self._canvas_layer: Optional[str] = None
        self._selection_source: str = "global"
        self._selection_layer: Optional[str] = None
        self._selection_context: str = "entire_image"
        self._selection_padding: int = 0

        # ---- last paste / pending async ----
        self._last_result = None
        self._last_document = None
        self._pending: dict = {}

        # ---- sub-handlers ----
        self._status_cmds = StatusCommands(self)
        self._connection_cmds = ConnectionCommands(self)
        self._workflow_cmds = WorkflowCommands(self)
        self._bindings_cmds = BindingsCommands(self)
        self._scratch_cmds = ScratchCommands(self)
        self._config_cmds = ConfigCommands(self)
        self._generation_cmds = GenerationCommands(self)
        self._proxy_cmds = ProxyCommands(self)

        self._sub_handlers = [
            self._connection_cmds,
            self._workflow_cmds,
            self._bindings_cmds,
            self._scratch_cmds,
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

        Called by comfy_load after the graph is set. Best-effort: a
        duplicate @-name or _meta.title raises ValueError, which the
        caller (comfy_load) should surface as a load error — an
        ambiguous graph is not loadable via the native pipeline.
        """
        self._active_workflow_resource_slots = _extract_resource_slots(graph)
        self._active_workflow_param_slots = _extract_param_slots(graph)
        self._active_workflow_output_nodes = _extract_output_nodes(graph)

    def _clear_native_slots(self) -> None:
        self._active_workflow_resource_slots = {}
        self._active_workflow_param_slots = {}
        self._active_workflow_output_nodes = {}
        self._active_config = None

    def _set_active_workflow(self, name: str, graph: dict, source: str,
                             publisher_key: Optional[str] = None) -> None:
        new_signature = _compute_signature(graph)
        if (self._active_workflow_signature is not None
                and new_signature != self._active_workflow_signature):
            self._params = {}
            self._layer_aliases = {}

        self._active_workflow_name = name
        self._active_workflow_graph = graph
        self._active_workflow_source = source
        self._active_workflow_publisher_key = publisher_key
        self._active_workflow_signature = new_signature
        self._active_workflow_parameters = _extract_parameters(graph)
        self._active_workflow_layers = _extract_layer_requirements(graph)
        self._active_workflow_canvas = _extract_canvas_info(graph)

        # Native slots + config are per-workflow. A publish replaces the
        # whole graph, so a config that was applied to the previous graph
        # must not carry over. Best-effort extraction: a duplicate-title
        # graph still loads through the legacy path, but the native
        # slots stay empty and comfy_apply_config will reject everything.
        try:
            self._populate_native_slots(graph)
        except ValueError:
            self._clear_native_slots()
        self._active_config = None

    def _release_to_browser(self) -> None:
        self._file_exclusive = False
        if self._active_workflow_source != "file":
            return
        entries = list(self._published_publishers.values())
        if not entries:
            return
        entries.sort(key=lambda e: e["published_at"], reverse=True)
        entry = entries[0]
        if self._pinned_publisher_key is not None:
            pinned = self._published_publishers.get(
                self._pinned_publisher_key
            )
            if pinned is not None:
                entry = pinned
        self._set_active_workflow(
            entry["workflow_name"], entry["graph"], "browser",
            publisher_key=entry["key"],
        )
