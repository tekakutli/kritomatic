"""
comfyui.py — the @command surface for driving ComfyUI from a bundle.

This is the top-level handler for the `comfy_*` family. It ties together
three modules:

    comfyui_client.py    HTTP + WebSocket transport to ComfyUI
    comfyui_tooling.py   rewrite ETN_* nodes into real ComfyUI nodes
    comfyui_krita.py     read pixels from / write pixels into the document

Active workflow and its identity
================================
The handler holds one active workflow: a tuple of fields set together by
`_set_active_workflow` — name, graph, source, signature, plus derived
summaries (parameters, named-layer requirements, canvas/selection use).

`source` is "file" when loaded via `comfy_load`, "browser" when a graph
arrived over the publish bus. `signature` is a hash of the workflow's
interface — the sorted names of its ETN_Parameter nodes and its
ETN_KritaOutput nodes — and it is what a caller uses to recognize the
same workflow across a browser reload, since the publisher name in the
publish-bus message is a fixed string that does not carry the workflow's
filename.

Layer aliases
=============
A workflow that uses `ETN_KritaMaskLayer` / `ETN_KritaImageLayer` names
a layer in the document by a string. A document may not have a layer
with that exact name. Rather than require the user to rename their
document, the handler keeps a per-run map from the workflow's declared
names to actual document names, set by `comfy_bind_layer_alias`. The
map is consulted when reading the layer out of the document; the
workflow graph is not touched.

Aliases are cleared by `comfy_load`, matching how params are cleared.

Output nodes
============
A workflow may contain more than one ETN_KritaOutput node. Each fires
its own `executed` event; the client records which event produced each
image; this handler groups images by that event and pastes each group
as its own layer (or its own animation, or its own batch of layers).
A `--output_name` argument on `comfy_generate` and `comfy_wait`
restricts the paste to a single output node whose `name` widget
matches.

Paths
=====
The panel script and its data live under `<repo>/recipes/comfy_panel/`:

    <repo>/recipes/comfy_panel/workflows/*.json     workflow definitions
    <repo>/recipes/comfy_panel/panel_configs/*.toml panel configs
    <repo>/recipes/comfy_panel/history/*.json       generation records

Workflow definitions are only present when the user has chosen to
inject a graph from disk rather than follow the browser. The panel
configs and the history directory are always populated by the panel
and the daemon respectively.

Watcher
=======
The tooling-nodes extension broadcasts every browser graph edit over a
subscription bus. `comfy_connect` starts the watcher and waits for the
subscription to be live before returning. `comfy_watch --enable false`
stops it.

Command surface
===============
    comfy_connect          --host --port --check --watch
    comfy_watch            --enable
    comfy_status
    comfy_list_workflows
    comfy_load             --name <slug>
    comfy_set_param        --name <param> --value <value>
    comfy_bind_layer_alias --logical_name <n> --actual_name <n>
    comfy_bind_canvas      --source active|layer --layer_name?
    comfy_bind_selection   --source global|none|layer --layer_name?
                           --context entire_image|automatic|mask_bounds
                           --padding <int>
    comfy_generate         --timeout <s>? --output_name <n>?
    comfy_generate_async
    comfy_wait             --prompt_id <id> --timeout <s>?
                           --output_name <n>?
    comfy_cancel
    comfy_paste_last       --name? --blend_mode? --output_name?
    comfy_history          --limit <n>?

Node schema defaults
====================
ComfyUI's newer `comfy_api.latest.io` schema marks every input as
required, even those with a `default`. The handler caches the
`/object_info` response at connect time and passes it into
`expand_custom`, which fills missing inputs from their schema defaults.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from krita import Krita

from ..decorators import command, AutoDispatchHandler
from ..utils.comfyui_client import (
    ComfyCancelled,
    ComfyClient,
    ComfyError,
    ComfyTimeout,
    GenerationResult,
    JobInfo,
    get_client,
)
from ..utils.comfyui_krita import (
    build_krita_resources,
    paste_animation,
    paste_batch_as_layers,
    paste_image,
    resize_canvas,
)
from ..utils.comfyui_tooling import (
    ExpansionError,
    expand_custom,
    scan_requirements,
)


# ============================================================================
#  Paths
# ============================================================================
#
# The handler lives at <repo>/kritomatic_daemon/handlers/comfyui.py. The
# panel script and its data live under <repo>/recipes/comfy_panel/. The
# three directories under it are:
#
#     workflows/        JSON workflow definitions; only present when the
#                       user has chosen to inject a graph from disk
#     panel_configs/    the .toml files the panel writes and the user edits
#     history/          one JSON record per generation

_HANDLER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HANDLER_DIR.parent.parent
_PANEL_DIR = _REPO_ROOT / "recipes" / "comfy_panel"
_WORKFLOW_DIR = _PANEL_DIR / "workflows"
_HISTORY_DIR = _PANEL_DIR / "history"

_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
#  Workflow interface extraction
# ============================================================================

def _extract_parameters(graph: dict) -> list[dict]:
    """Return the ETN_Parameter nodes' declarations, sorted by name."""
    result: list[dict] = []
    for node in graph.values():
        if node.get("class_type") != "ETN_Parameter":
            continue
        ins = node.get("inputs", {}) or {}
        name = ins.get("name", "")
        if not name:
            continue
        result.append({
            "name": str(name),
            "type": str(ins.get("type", "auto")),
            "default": ins.get("default", ""),
        })
    result.sort(key=lambda p: p["name"])
    return result


def _extract_layer_requirements(graph: dict) -> dict:
    """Return the named layers the workflow expects in the document."""
    image_layers: set[str] = set()
    mask_layers: set[str] = set()
    for node in graph.values():
        cls = node.get("class_type", "")
        if cls not in ("ETN_KritaImageLayer", "ETN_KritaMaskLayer"):
            continue
        ins = node.get("inputs", {}) or {}
        name = ins.get("name", "")
        if not name:
            continue
        if cls == "ETN_KritaImageLayer":
            image_layers.add(str(name))
        else:
            mask_layers.add(str(name))
    return {
        "image_layers": sorted(image_layers),
        "mask_layers": sorted(mask_layers),
    }


def _extract_canvas_info(graph: dict) -> dict:
    """Return whether and how the workflow uses the canvas and selection."""
    has_canvas = False
    selection_context: Optional[str] = None
    selection_padding: Optional[int] = None
    for node in graph.values():
        cls = node.get("class_type", "")
        if cls == "ETN_KritaCanvas":
            has_canvas = True
        elif cls == "ETN_KritaSelection" and selection_context is None:
            ins = node.get("inputs", {}) or {}
            selection_context = str(ins.get("context", "entire_image"))
            selection_padding = int(ins.get("padding", 0) or 0)
    return {
        "has_canvas": has_canvas,
        "has_selection": selection_context is not None,
        "selection_context": selection_context or "entire_image",
        "selection_padding": selection_padding or 0,
    }


def _compute_signature(graph: dict) -> str:
    """Stable hash of a workflow's interface."""
    params = sorted(
        str((node.get("inputs") or {}).get("name", ""))
        for node in graph.values()
        if node.get("class_type") == "ETN_Parameter"
    )
    outputs = sorted(
        str((node.get("inputs") or {}).get("name", ""))
        for node in graph.values()
        if node.get("class_type") == "ETN_KritaOutput"
    )

    if not params and not outputs:
        identity = "classes:" + "|".join(
            sorted(str(n.get("class_type", "")) for n in graph.values())
        )
    else:
        identity = (
            "params:" + "|".join(params)
            + ";outputs:" + "|".join(outputs)
        )

    digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()
    return digest[:12]


# ============================================================================
#  Pending async generation bookkeeping
# ============================================================================

class _PendingJob:
    def __init__(self, prompt_id: str, workflow_name: str,
                 params: dict, seed: int):
        self.prompt_id = prompt_id
        self.workflow_name = workflow_name
        self.params = params
        self.seed = seed
        self.event = threading.Event()
        self.result: Optional[GenerationResult] = None
        self.error: Optional[Exception] = None
        self.document = None
        self.started_at = time.time()
        self.warnings: list[str] = []


# ============================================================================
#  Handler
# ============================================================================

class ComfyUIHandler(AutoDispatchHandler):
    """Stateful handler for the comfy_* command family."""

    def __init__(self):
        super().__init__()

        self._host: str = "127.0.0.1"
        self._port: int = 8188
        self._client: Optional[ComfyClient] = None
        self._object_info: Optional[dict] = None

        self._active_workflow_name: Optional[str] = None
        self._active_workflow_graph: Optional[dict] = None
        self._active_workflow_source: Optional[str] = None
        self._active_workflow_signature: Optional[str] = None
        self._active_workflow_parameters: list[dict] = []
        self._active_workflow_layers: dict = {"image_layers": [],
                                              "mask_layers": []}
        self._active_workflow_canvas: dict = {
            "has_canvas": False,
            "has_selection": False,
            "selection_context": "entire_image",
            "selection_padding": 0,
        }

        self._params: dict = {}
        self._layer_aliases: dict[str, str] = {}

        self._canvas_source: str = "active"
        self._canvas_layer: Optional[str] = None
        self._selection_source: str = "global"
        self._selection_layer: Optional[str] = None
        self._selection_context: str = "entire_image"
        self._selection_padding: int = 0

        self._last_result: Optional[GenerationResult] = None
        self._last_document = None
        self._pending: dict[str, _PendingJob] = {}

    # ------------------------------------------------------------------
    #  Internal: active-workflow assignment
    # ------------------------------------------------------------------

    def _set_active_workflow(self, name: str, graph: dict,
                             source: str) -> None:
        """Assign the active workflow and every derived field together."""
        self._active_workflow_name = name
        self._active_workflow_graph = graph
        self._active_workflow_source = source
        self._active_workflow_signature = _compute_signature(graph)
        self._active_workflow_parameters = _extract_parameters(graph)
        self._active_workflow_layers = _extract_layer_requirements(graph)
        self._active_workflow_canvas = _extract_canvas_info(graph)

    # ------------------------------------------------------------------
    #  Connection
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
                                "current graph"},
            "--subscribe_timeout": {"type": "float", "default": 5.0,
                                    "help": "Seconds to wait for the "
                                            "watcher subscription to be "
                                            "established"},
        },
    )
    def comfy_connect(self, params):
        host = params.get("host", "127.0.0.1")
        port = int(params.get("port", 8188))
        check = bool(params.get("check", True))
        watch = bool(params.get("watch", True))
        subscribe_timeout = float(params.get("subscribe_timeout", 5.0))

        self._host = host
        self._port = port
        self._client = get_client(host, port)

        if not check:
            subscribed = False
            if watch:
                try:
                    self._client.watch_workflows(
                        self._on_workflow_published, replace=True,
                    )
                    subscribed = self._client.wait_subscribed(
                        timeout=subscribe_timeout,
                    )
                except ComfyError as e:
                    return {"success": False,
                            "message": f"Watcher unavailable: {e}"}
            return {
                "success": True,
                "message": f"Connected to {host}:{port}",
                "data": {"host": host, "port": port,
                         "watching": watch,
                         "subscribed": subscribed},
            }

        try:
            info = self._client.object_info()
        except ComfyError as e:
            self._object_info = None
            return {"success": False,
                    "message": f"Could not reach ComfyUI at "
                               f"{host}:{port}: {e}"}

        self._object_info = info

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
                self._client.watch_workflows(
                    self._on_workflow_published, replace=True,
                )
                subscribed = self._client.wait_subscribed(
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
        enable = bool(params.get("enable", True))
        subscribe_timeout = float(params.get("subscribe_timeout", 5.0))
        if self._client is None:
            return {"success": False,
                    "message": "Not connected; run comfy_connect first"}
        if enable:
            try:
                self._client.watch_workflows(
                    self._on_workflow_published, replace=True,
                )
                subscribed = self._client.wait_subscribed(
                    timeout=subscribe_timeout,
                )
            except ComfyError as e:
                return {"success": False,
                        "message": f"Watcher could not start: {e}"}
            return {"success": True,
                    "message": "Watching for browser workflow publishes"
                               if subscribed
                               else "Watcher started (subscription "
                                    "pending)",
                    "data": {"subscribed": subscribed}}
        else:
            self._client.unwatch_workflows()
            return {"success": True, "message": "Stopped watching"}

    def _on_workflow_published(self, publisher_name: str, graph: dict):
        """Watcher callback. Runs on the watcher thread."""
        signature = _compute_signature(graph)
        name = f"ComfyUI Web ({publisher_name}, sig={signature})"
        self._set_active_workflow(name, graph, "browser")

    @command(
        category="comfyui",
        help_text="Return the handler's current workflow, parameters, "
                  "layer aliases, bindings, and the active workflow's "
                  "signature.",
        args={},
    )
    def comfy_status(self, params):
        return {
            "success": True,
            "message": "ComfyUI handler state",
            "data": {
                "connected_to": (
                    f"{self._host}:{self._port}"
                    if self._client is not None else None
                ),
                "workflow": self._active_workflow_name,
                "workflow_source": self._active_workflow_source,
                "workflow_signature": self._active_workflow_signature,
                "workflow_parameters": list(self._active_workflow_parameters),
                "workflow_layer_requirements":
                    dict(self._active_workflow_layers),
                "workflow_canvas_info":
                    dict(self._active_workflow_canvas),
                "watching": (
                    self._client.is_watching()
                    if self._client is not None else False
                ),
                "params": dict(self._params),
                "layer_aliases": dict(self._layer_aliases),
                "canvas_source": self._canvas_source,
                "canvas_layer": self._canvas_layer,
                "selection_source": self._selection_source,
                "selection_layer": self._selection_layer,
                "selection_context": self._selection_context,
                "selection_padding": self._selection_padding,
                "pending": list(self._pending.keys()),
            },
        }

    # ------------------------------------------------------------------
    #  Workflow management
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="List workflow files available in the panel's "
                  "workflows directory.",
        args={},
    )
    def comfy_list_workflows(self, params):
        if not _WORKFLOW_DIR.is_dir():
            return {"success": True,
                    "message": "No workflows directory",
                    "data": {"workflows": []}}

        names = sorted(p.stem for p in _WORKFLOW_DIR.glob("*.json"))
        return {
            "success": True,
            "message": f"{len(names)} workflow(s)",
            "data": {"workflows": names},
        }

    @command(
        category="comfyui",
        help_text="Load a workflow by slug from the panel's workflows "
                  "directory. Clears any parameters and layer aliases "
                  "set by a previous workflow.",
        args={
            "--name": {"type": "str", "required": True,
                       "help": "Workflow slug"},
        },
    )
    def comfy_load(self, params):
        name = params.get("name", "")
        if not name:
            return {"success": False, "message": "No workflow name given"}

        path = _WORKFLOW_DIR / f"{name}.json"
        if not path.is_file():
            available = sorted(p.stem for p in _WORKFLOW_DIR.glob("*.json"))
            return {
                "success": False,
                "message": f"Workflow {name!r} not found in "
                           f"{_WORKFLOW_DIR}",
                "data": {"available": available},
            }

        try:
            workflow = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            return {"success": False,
                    "message": f"Workflow {name!r} is not valid JSON: {e}"}

        if not isinstance(workflow, dict) or not workflow:
            return {"success": False,
                    "message": f"Workflow {name!r} is empty or not an object"}

        if "nodes" in workflow or "links" in workflow:
            return {
                "success": False,
                "message": (
                    f"Workflow {name!r} appears to be in ComfyUI's UI "
                    f"format. Export it via 'Save (API Format)' in the "
                    f"ComfyUI menu, which produces the prompt format "
                    f"this handler requires."
                ),
            }

        self._set_active_workflow(name, workflow, "file")
        self._params = {}
        self._layer_aliases = {}

        return {
            "success": True,
            "message": f"Loaded workflow {name!r} "
                       f"({len(workflow)} nodes)",
            "data": {"name": name, "node_count": len(workflow),
                     "source": "file",
                     "signature": self._active_workflow_signature},
        }

    @command(
        category="comfyui",
        help_text="Set a value for an ETN_Parameter in the active "
                  "workflow. Values are coerced to the parameter's "
                  "declared type at generate time.",
        args={
            "--name": {"type": "str", "required": True,
                       "help": "Parameter name (the node's `name` widget)"},
            "--value": {"type": "str", "required": True,
                        "help": "Value, as a string. Coerced later."},
        },
    )
    def comfy_set_param(self, params):
        if self._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first, "
                               "or publish a graph from ComfyUI's browser"}

        name = params.get("name", "")
        raw = params.get("value", "")
        if not name:
            return {"success": False, "message": "No parameter name given"}

        if isinstance(raw, str):
            low = raw.lower()
            if low == "true":
                value: Any = True
            elif low == "false":
                value = False
            else:
                value = raw
        else:
            value = raw

        self._params[name] = value
        return {
            "success": True,
            "message": f"{name} = {value!r}",
            "data": {"name": name, "value": value,
                     "params": dict(self._params)},
        }

    @command(
        category="comfyui",
        help_text="Map a layer name the workflow declares to a different "
                  "layer in the document. The workflow graph is not "
                  "modified; the mapping applies when the layer is read.",
        args={
            "--logical_name": {"type": "str", "required": True,
                               "help": "The name the workflow declares "
                                       "(the node's `name` widget)"},
            "--actual_name": {"type": "str", "required": True,
                              "help": "The name to look up in the "
                                      "document instead"},
        },
    )
    def comfy_bind_layer_alias(self, params):
        if self._active_workflow_graph is None:
            return {"success": False,
                    "message": "No workflow loaded; run comfy_load first, "
                               "or publish a graph from ComfyUI's browser"}

        logical = params.get("logical_name", "")
        actual = params.get("actual_name", "")
        if not logical or not actual:
            return {"success": False,
                    "message": "Both --logical_name and --actual_name are "
                               "required"}

        self._layer_aliases[logical] = actual
        return {
            "success": True,
            "message": f"{logical} -> {actual}",
            "data": {"logical_name": logical, "actual_name": actual,
                     "layer_aliases": dict(self._layer_aliases)},
        }

    # ------------------------------------------------------------------
    #  Binding canvas / selection sources
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Choose what ETN_KritaCanvas reads from the document.",
        args={
            "--source": {"type": "str", "default": "active",
                         "choices": ["active", "layer"],
                         "help": "active = flattened composite of all "
                                 "visible layers; layer = one layer's "
                                 "projection"},
            "--layer_name": {"type": "str", "required": False,
                             "help": "Required when source=layer"},
        },
    )
    def comfy_bind_canvas(self, params):
        source = params.get("source", "active")
        layer_name = params.get("layer_name")
        if source == "layer" and not layer_name:
            return {"success": False,
                    "message": "source=layer requires --layer_name"}
        self._canvas_source = source
        self._canvas_layer = layer_name
        return {"success": True,
                "message": f"Canvas source: {source}"
                           + (f" ({layer_name})" if layer_name else "")}

    @command(
        category="comfyui",
        help_text="Choose what ETN_KritaSelection reads, and how the "
                  "selection region is cropped for the workflow.",
        args={
            "--source": {"type": "str", "default": "global",
                         "choices": ["global", "none", "layer"],
                         "help": "global = Krita's current selection; "
                                 "none = synthesize an all-selected mask; "
                                 "layer = a mask layer's alpha"},
            "--layer_name": {"type": "str", "required": False,
                             "help": "Required when source=layer"},
            "--context": {"type": "str", "default": "entire_image",
                          "choices": ["entire_image", "automatic",
                                      "mask_bounds"],
                          "help": "Crop region for the workflow's input"},
            "--padding": {"type": "int", "default": 0,
                          "help": "Extra pixels around the selection "
                                  "when context=automatic or mask_bounds"},
        },
    )
    def comfy_bind_selection(self, params):
        source = params.get("source", "global")
        layer_name = params.get("layer_name")
        if source == "layer" and not layer_name:
            return {"success": False,
                    "message": "source=layer requires --layer_name"}
        self._selection_source = source
        self._selection_layer = layer_name
        self._selection_context = params.get("context", "entire_image")
        self._selection_padding = int(params.get("padding", 0) or 0)
        return {
            "success": True,
            "message": f"Selection source: {source}"
                       + (f" ({layer_name})" if layer_name else "")
                       + f", context: {self._selection_context}",
        }

    # ------------------------------------------------------------------
    #  Generation
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Run the active workflow: read pixels, rewrite the "
                  "graph, submit to ComfyUI, wait for the result, paste "
                  "it back as new layer(s). When the workflow has "
                  "multiple ETN_KritaOutput nodes, every one is pasted; "
                  "use --output_name to restrict to a single one.",
        args={
            "--timeout": {"type": "float", "default": 55.0,
                          "help": "Seconds to wait for ComfyUI."},
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the ETN_KritaOutput "
                                      "node whose `name` widget matches "
                                      "this value. Omit to paste every "
                                      "output node's contribution."},
        },
    )
    def comfy_generate(self, params):
        timeout = float(params.get("timeout", 55.0))
        output_name = params.get("output_name") or None
        prompt_id, error = self._queue_generate()
        if error is not None:
            return error

        pending = self._pending[prompt_id]
        completed = pending.event.wait(timeout=timeout)
        if not completed:
            return {
                "success": False,
                "message": f"Generation {prompt_id} did not complete "
                           f"within {timeout}s. It may still finish on "
                           f"the ComfyUI side; use comfy_wait with the "
                           f"same prompt id to retrieve it later.",
                "data": {"prompt_id": prompt_id},
            }

        return self._finish_pending(prompt_id, paste=True,
                                    output_name=output_name)

    @command(
        category="comfyui",
        help_text="Queue the active workflow without waiting.",
        args={},
    )
    def comfy_generate_async(self, params):
        prompt_id, error = self._queue_generate()
        if error is not None:
            return error
        return {
            "success": True,
            "message": f"Queued generation {prompt_id}",
            "data": {"prompt_id": prompt_id,
                     "workflow": self._active_workflow_name},
        }

    @command(
        category="comfyui",
        help_text="Wait for a previously-queued generation and paste "
                  "its result.",
        args={
            "--prompt_id": {"type": "str", "required": True,
                            "help": "Prompt id returned by "
                                    "comfy_generate_async"},
            "--timeout": {"type": "float", "default": 55.0,
                          "help": "Seconds to wait"},
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the ETN_KritaOutput "
                                      "node whose `name` widget matches "
                                      "this value."},
        },
    )
    def comfy_wait(self, params):
        prompt_id = params.get("prompt_id", "")
        timeout = float(params.get("timeout", 55.0))
        output_name = params.get("output_name") or None

        pending = self._pending.get(prompt_id)
        if pending is None:
            return {"success": False,
                    "message": f"Unknown prompt id {prompt_id!r}. It "
                               f"may have been cleared by a daemon "
                               f"reload."}

        completed = pending.event.wait(timeout=timeout)
        if not completed:
            return {"success": False,
                    "message": f"Generation {prompt_id} still running "
                               f"after {timeout}s"}

        return self._finish_pending(prompt_id, paste=True,
                                    output_name=output_name)

    @command(
        category="comfyui",
        help_text="Interrupt the currently-executing prompt on ComfyUI "
                  "and clear any queued ones.",
        args={},
    )
    def comfy_cancel(self, params):
        if self._client is None:
            return {"success": False, "message": "Not connected"}
        self._client.interrupt()
        if self._pending:
            self._client.cancel_queued(list(self._pending.keys()))
        return {"success": True, "message": "Cancel requested"}

    # ------------------------------------------------------------------
    #  Result handling
    # ------------------------------------------------------------------

    @command(
        category="comfyui",
        help_text="Re-paste the most recent generation's result as new "
                  "layers.",
        args={
            "--name": {"type": "str", "required": False,
                       "help": "Override the layer name"},
            "--blend_mode": {"type": "str", "required": False,
                             "help": "Krita blend mode; default normal"},
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the output node whose "
                                      "name matches."},
        },
    )
    def comfy_paste_last(self, params):
        if self._last_result is None:
            return {"success": False,
                    "message": "No result to paste; run comfy_generate "
                               "first"}
        doc = self._last_document or Krita.instance().activeDocument()
        if doc is None:
            return {"success": False, "message": "No active document"}
        return self._paste_result(
            doc, self._last_result,
            name_override=params.get("name"),
            blend_mode=params.get("blend_mode", "normal"),
            output_name=params.get("output_name") or None,
        )

    @command(
        category="comfyui",
        help_text="Return the last N generation records from the panel's "
                  "history directory.",
        args={
            "--limit": {"type": "int", "default": 10,
                        "help": "How many entries to return"},
        },
    )
    def comfy_history(self, params):
        limit = int(params.get("limit", 10))
        files = sorted(
            _HISTORY_DIR.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )[:limit]
        entries = []
        for f in files:
            try:
                entries.append(json.loads(f.read_text(encoding="utf-8")))
            except Exception:
                continue
        return {
            "success": True,
            "message": f"{len(entries)} history entries",
            "data": {"entries": entries},
        }

    # ------------------------------------------------------------------
    #  Internal: generate pipeline
    # ------------------------------------------------------------------

    def _queue_generate(self) -> tuple[Optional[str], Optional[dict]]:
        """Validate state, read pixels, rewrite the graph, and start the
        worker thread. Returns (prompt_id, None) on success, or
        (None, error_dict) on failure.
        """
        workflow = self._active_workflow_graph
        if workflow is None:
            return None, {"success": False,
                          "message": "No workflow loaded. Run comfy_load "
                                     "or publish a graph from ComfyUI's "
                                     "browser."}
        workflow_name = self._active_workflow_name or "(unnamed)"

        if self._client is None:
            return None, {"success": False,
                          "message": "Not connected; run comfy_connect first"}

        doc = Krita.instance().activeDocument()
        if doc is None:
            return None, {"success": False,
                          "message": "No active document"}

        ok, msg = _check_color_mode(doc)
        if not ok:
            return None, {"success": False, "message": msg}

        try:
            reqs = scan_requirements(workflow)
            resources = build_krita_resources(
                doc, reqs,
                selection_context=self._selection_context,
                selection_padding=self._selection_padding,
                layer_aliases=dict(self._layer_aliases),
            )
        except (ValueError, ExpansionError) as e:
            return None, {"success": False, "message": str(e)}

        seed = int(time.time()) & 0xFFFFFFFF
        try:
            expansion = expand_custom(
                workflow, self._params, resources,
                seed=seed,
                node_defs=self._object_info,
            )
        except ExpansionError as e:
            return None, {"success": False,
                          "message": f"Workflow rewrite failed: {e}"}

        prompt_id = str(uuid.uuid4())
        pending = _PendingJob(
            prompt_id, workflow_name,
            dict(expansion.parameters), seed,
        )
        pending.document = doc
        pending.warnings = list(expansion.warnings)
        self._pending[prompt_id] = pending

        thread = threading.Thread(
            target=self._run_generation,
            args=(pending, expansion),
            name=f"comfy-gen-{prompt_id[:8]}",
            daemon=True,
        )
        thread.start()

        return prompt_id, None

    def _run_generation(self, pending: _PendingJob, expansion) -> None:
        """Worker thread body: upload images, submit, wait, store result."""
        try:
            for h, data in expansion.image_data.items():
                self._client.upload_image(h, data)

            result = self._client.generate(
                expansion.graph, timeout=600.0,
            )
            pending.result = result

        except (ComfyError, ComfyTimeout, ComfyCancelled,
                ExpansionError) as e:
            pending.error = e
        except Exception as e:
            pending.error = ComfyError(f"unexpected error: {e}")
        finally:
            pending.event.set()

    def _finish_pending(self, prompt_id: str, paste: bool,
                        output_name: Optional[str] = None) -> dict:
        """Collect a completed pending job, record history, paste."""
        pending = self._pending.pop(prompt_id, None)
        if pending is None:
            return {"success": False,
                    "message": f"No pending job {prompt_id!r}"}

        if pending.error is not None:
            return {"success": False,
                    "message": f"Generation failed: {pending.error}",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        result = pending.result
        if result is None:
            return {"success": False,
                    "message": "Generation completed without a result"}

        self._last_result = result
        self._last_document = pending.document

        self._write_history(pending, result)

        response_data = {
            "prompt_id": prompt_id,
            "workflow": pending.workflow_name,
            "image_count": len(result.images),
            "output_count": (
                len(set(result.event_index))
                if result.event_index else len(result.images)
            ),
            "texts": [{"name": t.name, "text": t.text}
                      for t in result.texts],
        }
        warnings = list(pending.warnings)
        warnings.extend(result.warnings)
        if warnings:
            response_data["warnings"] = warnings

        if not paste:
            return {"success": True,
                    "message": f"Generation complete "
                               f"({len(result.images)} image(s) in "
                               f"{response_data['output_count']} "
                               f"output(s))",
                    "data": response_data}

        doc = pending.document
        if doc is None or not _document_is_open(doc):
            return {"success": True,
                    "message": f"Generation complete "
                               f"({len(result.images)} image(s)), "
                               f"but the target document was closed",
                    "data": response_data}

        paste_result = self._paste_result(
            doc, result,
            blend_mode="normal",
            output_name=output_name,
        )
        response_data.update(paste_result.get("data", {}))
        if not paste_result.get("success"):
            return {"success": False,
                    "message": paste_result.get("message", "paste failed"),
                    "data": response_data}

        return {
            "success": True,
            "message": paste_result.get("message",
                                        "Generation complete and pasted"),
            "data": response_data,
        }

    def _paste_result(self, doc, result: GenerationResult,
                      name_override: Optional[str] = None,
                      blend_mode: str = "normal",
                      output_name: Optional[str] = None) -> dict:
        """Paste every output node's contribution into `doc`.

        Each `executed` event is one ETN_KritaOutput node's contribution:
        one info dict (its name, offset, batch mode) plus one or more
        images. The result carries an `event_index` per image so this
        method can reconstruct those groups.

        Groups are pasted in event order. Within a group:

          * batch_mode "animation" with >1 image: import as an animation.
          * batch_mode "layers" with >1 image: one layer per image,
            prefixed "[Layer i] ".
          * otherwise: one layer with the group's first image.

        `output_name`, when given, restricts the paste to groups whose
        ETN_KritaOutput node carries that name. The name is the node's
        own `name` widget, not the layer name the daemon generates.
        """
        if not result.images:
            return {"success": True, "message": "No images to paste"}

        groups: dict[int, list[tuple[bytes, JobInfo]]] = {}
        for i, (img, info) in enumerate(zip(result.images, result.infos)):
            ev = (result.event_index[i]
                  if i < len(result.event_index) else i)
            groups.setdefault(ev, []).append((img, info))

        if output_name:
            filtered = {
                ev: entries for ev, entries in groups.items()
                if entries and entries[0][1].name == output_name
            }
            if not filtered:
                available = sorted({
                    entries[0][1].name
                    for entries in groups.values()
                    if entries and entries[0][1].name
                })
                return {
                    "success": False,
                    "message": (
                        f"No output node named {output_name!r}. "
                        f"Available names: "
                        f"{available or '(none — outputs are unnamed)'}"
                    ),
                }
            groups = filtered

        pasted_layers: list[str] = []
        pasted_frames = 0
        failures: list[str] = []

        for ev in sorted(groups):
            entries = groups[ev]
            first_info = entries[0][1] if entries else JobInfo()

            group_name = name_override or first_info.name
            if not group_name:
                group_name = f"[Generated] {result.prompt_id[:8]}"
            elif not group_name.startswith("["):
                group_name = f"[Generated] {group_name}"

            if first_info.resize_canvas and entries:
                w, h = _png_dimensions(entries[0][0])
                if (int(doc.width()), int(doc.height())) != (w, h):
                    try:
                        resize_canvas(doc, w, h)
                    except Exception as e:
                        failures.append(
                            f"output {first_info.name or ev}: "
                            f"resize_canvas failed: {e}"
                        )
                        continue

            x = first_info.offset_x
            y = first_info.offset_y
            images = [img for img, _ in entries]

            try:
                if first_info.batch_mode == "animation" and len(images) > 1:
                    ok = paste_animation(doc, images)
                    if ok:
                        pasted_frames += len(images)
                        pasted_layers.append(f"[Animation] {group_name}")
                        continue
                    node = paste_image(doc, images[0], x, y, group_name,
                                       blend_mode=blend_mode)
                    pasted_layers.append(node.name())

                elif first_info.batch_mode == "layers" and len(images) > 1:
                    nodes = paste_batch_as_layers(
                        doc, images, x, y, group_name,
                        blend_mode=blend_mode,
                    )
                    pasted_layers.extend(n.name() for n in nodes)

                else:
                    node = paste_image(doc, images[0], x, y, group_name,
                                       blend_mode=blend_mode)
                    pasted_layers.append(node.name())

            except ValueError as e:
                failures.append(
                    f"output {first_info.name or ev}: {e}"
                )

        if failures and not pasted_layers:
            return {"success": False,
                    "message": "; ".join(failures)}

        summary = f"Pasted {len(pasted_layers)} layer(s)"
        if pasted_frames:
            summary += f", imported {pasted_frames} frame(s)"
        if failures:
            summary += f"; {len(failures)} failed: " + "; ".join(failures)

        return {
            "success": True,
            "message": summary,
            "data": {
                "layer_names": pasted_layers,
                "frame_count": pasted_frames,
                "output_count": len(groups),
            },
        }

    # ------------------------------------------------------------------
    #  History
    # ------------------------------------------------------------------

    def _write_history(self, pending: _PendingJob,
                       result: GenerationResult) -> None:
        """Write a history entry. Best-effort; failures are silent."""
        try:
            entry = {
                "prompt_id": pending.prompt_id,
                "workflow": pending.workflow_name,
                "parameters": pending.params,
                "seed": pending.seed,
                "image_count": len(result.images),
                "output_count": (
                    len(set(result.event_index))
                    if result.event_index else len(result.images)
                ),
                "texts": [{"name": t.name, "text": t.text}
                          for t in result.texts],
                "batch_modes": [i.batch_mode for i in result.infos],
                "finished": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(time.time() - pending.started_at, 3),
            }
            path = _HISTORY_DIR / f"{pending.prompt_id}.json"
            path.write_text(json.dumps(entry, indent=2),
                            encoding="utf-8")
        except Exception:
            pass


# ============================================================================
#  Helpers
# ============================================================================

def _check_color_mode(doc) -> tuple[bool, Optional[str]]:
    """Krita's setPixelData requires RGBA/U8."""
    model = doc.colorModel()
    depth = doc.colorDepth()
    if model != "RGBA":
        return False, (
            f"Document color model is {model!r}; only RGBA is supported. "
            f"Convert via Image > Convert Image Color Space."
        )
    if depth != "U8":
        return False, (
            f"Document color depth is {depth!r}; only 8-bit integer is "
            f"supported. Convert via Image > Convert Image Color Space."
        )
    return True, None


def _document_is_open(doc) -> bool:
    """True iff `doc` is still among the open documents."""
    try:
        for d in Krita.instance().documents():
            if d == doc:
                return True
    except Exception:
        return False
    return False


def _png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """Return (width, height) of a PNG without fully decoding it."""
    if len(png_bytes) < 24 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a valid PNG")
    import struct
    w, h = struct.unpack(">II", png_bytes[16:24])
    return int(w), int(h)
