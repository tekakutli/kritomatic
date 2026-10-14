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
ETN_KritaOutput nodes.

Layer aliases
=============
A workflow that uses `ETN_KritaMaskLayer` / `ETN_KritaImageLayer` names
a layer in the document by a string. The handler keeps a per-run map
from the workflow's declared names to actual document names, set by
`comfy_bind_layer_alias`. The map is consulted when reading the layer
out of the document; the workflow graph is not touched.

Output nodes
============
A workflow may contain more than one ETN_KritaOutput node. Each fires
its own `executed` event; the client records which event produced each
image; this handler groups images by that event and pastes each group
as its own layer (or its own animation, or its own batch of layers).

Batch generation
================
`comfy_generate` accepts `--count N` and `--seed S`. When N > 1, the
handler runs two phases:

  Phase 1 (prepare). For each run, it reads the pixels the workflow
  needs, rewrites the graph with the run's seed, and uploads every
  image blob to ComfyUI. Nothing is submitted yet.

  Phase 2 (collect). The prepared graphs are submitted back to back on
  one WebSocket. As each prompt completes, the client invokes a
  callback that pastes its result into the document immediately.

Concurrent batches
==================
Multiple batches can run at once, each started by a different caller.
Their phases may interleave: phase 1 of batch B can run while phase 2
of batch A holds the client lock. The client's single-flight lock
guarantees only one phase 2 is on ComfyUI at a time.

Batch tracking
==============
Every `comfy_generate` call registers a `_BatchRecord` in
`self._batches`, keyed by the batch id the caller supplied. The record
is visible to `comfy_batches` (and by extension to
`kritomatic comfyui panel queue`) from the moment the batch starts
until it returns.

A `_BatchRecord` holds:

  - static: batch_id, workflow_name, requested_count, total_runs,
    base_seed, output_name, started_at, cancel_event
  - dynamic: current_phase, current_run_index, and run_records (a list
    the batch loop appends to as each run reaches a terminal state)

`comfy_cancel` looks up the record by batch id and sets its cancel
event. `comfy_batches` reads the records to answer "what is running
right now". The two share `self._batches_lock` for the dict itself;
individual dynamic fields are read without a lock, which is safe here
because every one of them is either a scalar (int, str) or a list the
reader only measures by `len()`.

Cancellation
============
Each batch registers a `threading.Event` inside its `_BatchRecord`.
`comfy_cancel --batch_id X` sets only X's event; `comfy_cancel` with
no id sets all of them. A panel's Ctrl+C handler sends its own batch
id, so cancelling one batch leaves any concurrently-running batch
untouched.

Both phases observe the batch's own event:

  Phase 1 checks it at the top of each iteration. Once set, the
  remaining runs are marked cancelled and phase 1 stops.

  Phase 2 passes the event's `is_set` to `client.generate_batch` as
  `should_stop`. The batch loop checks it at the top of every read
  iteration and between lock-acquisition attempts. Once set, every
  prompt that has not yet completed is marked cancelled, ComfyUI is
  asked to interrupt the current prompt and drop the queued ones, and
  the batch returns.

`comfy_cancel` never touches ComfyUI directly for a batch cancel: it
just sets the flag, and the batch loop calls `interrupt` /
`cancel_queued` when it notices. This is what keeps two batches from
interfering with each other's ComfyUI-side work.

Cancel is best-effort, not transactional: layers already pasted stay
in the document. Undo is the user's responsibility.

Document lifetime
=================
Krita recreates the Python `Document` wrapper for the same underlying
document across certain operations, and it also caches stale wrappers
for `Krita.instance().activeDocument()` after those recreations. A
`Document` reference obtained before such an event may point at a
deleted C++ object and raise "wrapped C/C++ object of type Document has
been deleted" on the next method call.

The batch holds a document reference for the entire duration of phase
2. Because phase 2 includes pastes that call `refreshProjection`, the
reference can become stale between runs. The paste callback re-resolves
a live document from the (fileName, name) key captured at prepare
time, falling back to the captured reference only if the fresh lookup
fails.

`comfy_generate` itself is a thin wrapper around `_comfy_generate_impl`.
The wrapper catches any exception the implementation raises, prints its
full traceback to stdout, and returns an ordinary error response.

Paths
=====
The panel script and its data live under `<repo>/data/comfy_panel/`:

    <repo>/data/comfy_panel/workflows/*.json     workflow definitions
    <repo>/data/comfy_panel/panel_configs/*.toml panel configs
    <repo>/data/comfy_panel/history/*.json       generation records

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
    comfy_batches
    comfy_list_workflows
    comfy_load             --name <slug>
    comfy_set_param        --name <param> --value <value>
    comfy_bind_layer_alias --logical_name <n> --actual_name <n>
    comfy_bind_canvas      --source active|layer --layer_name?
    comfy_bind_selection   --source global|none|layer --layer_name?
                           --context entire_image|automatic|mask_bounds
                           --padding <int>
    comfy_generate         --timeout <s>? --output_name <n>?
                           --count <n>? --seed <s>?
    comfy_generate_async
    comfy_wait             --prompt_id <id> --timeout <s>?
                           --output_name <n>?
    comfy_cancel           --batch_id <id>?
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
    CANCELLED,
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

_HANDLER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _HANDLER_DIR.parent.parent
_PANEL_DIR = _REPO_ROOT / "data" / "comfy_panel"
_WORKFLOW_DIR = _PANEL_DIR / "workflows"
_HISTORY_DIR = _PANEL_DIR / "history"

_WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
_HISTORY_DIR.mkdir(parents=True, exist_ok=True)


_SEED_PARAMETER_NAME = "seed"
_CANVAS_SEED_SLOT = 3


# ============================================================================
#  Batch record
# ============================================================================

class _BatchRecord:
    """Live state of one `comfy_generate` call.

    Held in `ComfyUIHandler._batches`, keyed by batch id, from the
    moment the batch starts until the moment it returns. `comfy_batches`
    reads this to answer "what is currently running", and `comfy_cancel`
    looks up the cancel event by batch id.

    Static fields are set at construction and never change. Dynamic
    fields are updated by the batch loop as it moves through phases;
    they are read by `comfy_batches` from a different thread without a
    lock. That is safe here because every dynamic field is either a
    scalar (int, str) or a list the reader only measures by `len()`.
    Do not promote a dynamic field to a container that the reader
    would have to iterate or index into; that would need a lock.
    """

    def __init__(self, batch_id: str, workflow_name: str,
                 requested_count: int, total_runs: int,
                 base_seed: int, output_name: Optional[str]):
        # Static, set once.
        self.batch_id = batch_id
        self.workflow_name = workflow_name
        self.requested_count = requested_count
        self.total_runs = total_runs
        self.base_seed = base_seed
        self.output_name = output_name
        self.started_at = time.time()
        self.cancel_event = threading.Event()

        # Dynamic, updated by the batch loop.
        #
        # phase runs through:
        #   "preparing" — phase 1 in progress
        #   "running"   — phase 2 in progress
        #   "finalizing" — writing history, building the response
        #
        # current_run_index tracks the run being prepared during phase
        # 1. It is not meaningful in phase 2.
        #
        # run_records is the same list the batch loop appends to. The
        # reader measures it by `len()`; it never iterates it.
        self.current_phase = "preparing"
        self.current_run_index = 0
        self.run_records: list[dict] = []


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


def _canvas_seed_is_wired(graph: dict) -> bool:
    """True iff any node consumes the ETN_KritaCanvas seed output."""
    canvas_ids = {
        nid for nid, n in graph.items()
        if n.get("class_type") == "ETN_KritaCanvas"
    }
    if not canvas_ids:
        return False

    def scan(value: Any) -> bool:
        if (isinstance(value, list) and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
                and not isinstance(value[1], bool)
                and value[0] in canvas_ids
                and value[1] == _CANVAS_SEED_SLOT):
            return True
        if isinstance(value, list):
            return any(scan(x) for x in value)
        return False

    for node in graph.values():
        for v in (node.get("inputs") or {}).values():
            if scan(v):
                return True
    return False


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
        self.document_key: tuple[str, str] = ("", "")
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

        # Per-batch records, keyed by batch id. The lock guards the
        # dict itself; individual record fields are read without a
        # lock (see _BatchRecord's docstring for why that is safe).
        self._batches: dict[str, _BatchRecord] = {}
        self._batches_lock = threading.Lock()

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
            self._client = None
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
        with self._batches_lock:
            active_batches = sorted(self._batches.keys())
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
                "workflow_seed_wired": (
                    _canvas_seed_is_wired(self._active_workflow_graph or {})
                ),
                "active_batches": active_batches,
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

    @command(
        category="comfyui",
        help_text="List the currently-running batches. One batch per "
                  "comfy_generate call; includes the workflow, phase, "
                  "run count, base seed, and elapsed time.",
        args={},
    )
    def comfy_batches(self, params):
        """Return a snapshot of every active batch.

        Reads `self._batches` under the dict lock, then reads each
        record's fields without a lock. See `_BatchRecord` for why
        the field reads are safe.
        """
        with self._batches_lock:
            records = list(self._batches.values())

        now = time.time()
        items = []
        for r in records:
            finished = len(r.run_records)
            items.append({
                "batch_id": r.batch_id,
                "workflow": r.workflow_name,
                "phase": r.current_phase,
                "requested_count": r.requested_count,
                "total_runs": r.total_runs,
                "finished_runs": finished,
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
        help_text="Run the active workflow. With --count N, run it N "
                  "times with consecutive seeds. All runs are prepared "
                  "up front and submitted back to back; each paste "
                  "happens as soon as its image arrives. All runs share "
                  "one history entry.",
        args={
            "--timeout": {"type": "float", "default": 55.0,
                          "help": "Seconds to wait per run for ComfyUI."},
            "--output_name": {"type": "str", "required": False,
                              "help": "Paste only the ETN_KritaOutput "
                                      "node whose `name` widget matches "
                                      "this value."},
            "--count": {"type": "int", "default": 1,
                        "help": "Number of generations to run."},
            "--seed": {"type": "int", "required": False,
                       "help": "Base seed. Run i uses base+i. Omit for "
                               "a random base."},
        },
    )
    def comfy_generate(self, params):
        """Thin wrapper. Any exception the implementation raises is
        caught here and returned as a normal error response, with a
        full traceback printed to stdout so the source is visible in
        Krita's console.
        """
        try:
            return self._comfy_generate_impl(params)
        except Exception as e:
            import traceback
            print("[comfyui] comfy_generate raised:", flush=True)
            traceback.print_exc()
            return {
                "success": False,
                "message": f"comfy_generate raised: {e}",
                "data": {},
            }

    def _comfy_generate_impl(self, params):
        requested_count = max(1, int(params.get("count", 1)))
        count = requested_count
        timeout = float(params.get("timeout", 55.0))
        output_name = params.get("output_name") or None

        raw_seed = params.get("seed")
        if raw_seed in (None, ""):
            base_seed = int(time.time()) & 0xFFFFFFFF
        else:
            try:
                base_seed = int(raw_seed) & 0xFFFFFFFF
            except (TypeError, ValueError):
                base_seed = int(time.time()) & 0xFFFFFFFF

        batch_id = params.get("_batch_id") or str(uuid.uuid4())

        # Snapshot the workflow name before anything can change it.
        workflow_name = self._active_workflow_name or "(unnamed)"

        # Register the batch record before the try block so the batch
        # is visible to `comfy_batches` from this point on. If an
        # early return happens (no workflow, no client), the finally
        # block deregisters.
        record = _BatchRecord(
            batch_id=batch_id,
            workflow_name=workflow_name,
            requested_count=requested_count,
            total_runs=requested_count,   # may be clamped below
            base_seed=base_seed,
            output_name=output_name,
        )
        with self._batches_lock:
            self._batches[batch_id] = record

        try:
            warnings: list[str] = []

            graph = self._active_workflow_graph
            if graph is None:
                return {"success": False,
                        "message": "No workflow loaded. Run comfy_load or "
                                   "publish a graph from ComfyUI's browser.",
                        "data": {"batch_id": batch_id}}

            if self._client is None:
                return {"success": False,
                        "message": "Not connected; run comfy_connect first",
                        "data": {"batch_id": batch_id}}

            has_seed_param = any(
                p["name"] == _SEED_PARAMETER_NAME
                for p in self._active_workflow_parameters
            )
            has_seed_wire = _canvas_seed_is_wired(graph)
            can_batch = has_seed_param or has_seed_wire

            if count > 1 and not can_batch:
                warnings.append(
                    f"count={requested_count} clamped to 1: the active "
                    f"workflow does not vary its seed between runs. Wire "
                    f"either an ETN_Parameter named {_SEED_PARAMETER_NAME!r} "
                    f"or the ETN_KritaCanvas 'seed' output to the sampler's "
                    f"seed input to enable batch generation."
                )
                count = 1
                record.total_runs = 1

            batch_started = time.time()
            base_params = dict(self._params)
            original_seed = self._params.get(_SEED_PARAMETER_NAME)

            # ---- Phase 1: prepare every run. ----
            #
            # run_records is the same list the record exposes, so
            # appending to it here is enough for `comfy_batches` to
            # see the progress.
            prepared: list[dict] = []
            run_records = record.run_records
            record.current_phase = "preparing"

            for i in range(count):
                seed_i = (base_seed + i) & 0xFFFFFFFF
                record.current_run_index = i

                if record.cancel_event.is_set():
                    for j in range(i, count):
                        seed_j = (base_seed + j) & 0xFFFFFFFF
                        run_records.append({
                            "seed": seed_j, "prompt_id": None,
                            "status": "cancelled",
                            "error": "cancelled before submission",
                        })
                    warnings.append("batch cancelled during preparation")
                    break

                if has_seed_param:
                    self._params[_SEED_PARAMETER_NAME] = seed_i

                print(f"[comfyui] batch {i + 1}/{count}: preparing seed "
                      f"{seed_i}", flush=True)

                doc = _get_live_active_document()
                if doc is None:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": "no active document",
                    })
                    continue

                ok, msg = _check_color_mode(doc)
                if not ok:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error", "error": msg,
                    })
                    continue

                try:
                    reqs = scan_requirements(graph)
                    resources = build_krita_resources(
                        doc, reqs,
                        selection_context=self._selection_context,
                        selection_padding=self._selection_padding,
                        layer_aliases=dict(self._layer_aliases),
                    )
                except (ValueError, ExpansionError) as e:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error", "error": str(e),
                    })
                    continue

                try:
                    expansion = expand_custom(
                        graph, self._params, resources,
                        seed=seed_i,
                        node_defs=self._object_info,
                    )
                except ExpansionError as e:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error",
                        "error": f"Workflow rewrite failed: {e}",
                    })
                    continue

                upload_error = None
                for h, data in expansion.image_data.items():
                    try:
                        self._client.upload_image(h, data)
                    except ComfyError as e:
                        upload_error = f"image upload failed: {e}"
                        break
                if upload_error is not None:
                    run_records.append({
                        "seed": seed_i, "prompt_id": None,
                        "status": "error", "error": upload_error,
                    })
                    continue

                prepared.append({
                    "index": i,
                    "seed": seed_i,
                    "graph": expansion.graph,
                    "document": doc,
                    "document_key": _capture_document_key(doc),
                })

            if has_seed_param:
                if original_seed is None:
                    self._params.pop(_SEED_PARAMETER_NAME, None)
                else:
                    self._params[_SEED_PARAMETER_NAME] = original_seed

            if record.cancel_event.is_set() and prepared:
                for p in prepared:
                    run_records.append({
                        "seed": p["seed"], "prompt_id": None,
                        "status": "cancelled",
                        "error": "cancelled before submission",
                    })
                prepared = []
                if "batch cancelled during preparation" not in warnings:
                    warnings.append("batch cancelled during preparation")

            # ---- Phase 2: submit and collect. ----

            total_layers = 0
            record.current_phase = "running"

            if prepared:
                prepared_graphs = [p["graph"] for p in prepared]
                batch_timeout = timeout * count + 60.0

                def on_result(index: int,
                              result: GenerationResult) -> None:
                    p = prepared[index]
                    seed_i = p["seed"]

                    if result.error == CANCELLED:
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "cancelled",
                            "error": "cancelled mid-batch",
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"cancelled", flush=True)
                        return

                    if result.error:
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "error",
                            "error": result.error,
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"failed: {result.error}", flush=True)
                        return

                    doc = _resolve_live_document_for_prepared(p)
                    if doc is None:
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "skipped",
                            "error": "target document not found",
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"skipped (no target document)", flush=True)
                        return

                    try:
                        paste_result = self._paste_result(
                            doc, result,
                            blend_mode="normal",
                            output_name=output_name,
                            seed_suffix=seed_i if count > 1 else None,
                        )
                    except Exception as e:
                        import traceback
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"paste raised:", flush=True)
                        traceback.print_exc()
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "paste_failed",
                            "error": f"paste raised: {e}",
                        })
                        return

                    if not paste_result.get("success"):
                        run_records.append({
                            "seed": seed_i,
                            "prompt_id": result.prompt_id,
                            "status": "paste_failed",
                            "error": paste_result.get("message",
                                                      "paste failed"),
                        })
                        print(f"[comfyui] batch {index + 1}/{count}: "
                              f"paste failed: "
                              f"{paste_result.get('message')}", flush=True)
                        return

                    data = paste_result.get("data", {}) or {}
                    layer_names = list(data.get("layer_names", []))
                    run_records.append({
                        "seed": seed_i,
                        "prompt_id": result.prompt_id,
                        "status": "success",
                        "image_count": len(result.images),
                        "output_count": data.get("output_count", 0),
                        "layer_names": layer_names,
                    })
                    print(f"[comfyui] batch {index + 1}/{count}: pasted "
                          f"{len(layer_names)} layer(s) "
                          f"({', '.join(layer_names) or 'none'})",
                          flush=True)

                    self._last_result = result
                    self._last_document = doc

                try:
                    self._client.generate_batch(
                        prepared_graphs,
                        timeout=batch_timeout,
                        on_result=on_result,
                        should_stop=record.cancel_event.is_set,
                    )
                except ComfyError as e:
                    recorded_seeds = {r.get("seed") for r in run_records}
                    for p in prepared:
                        if p["seed"] not in recorded_seeds:
                            run_records.append({
                                "seed": p["seed"],
                                "prompt_id": None,
                                "status": "error",
                                "error": f"batch transport failed: {e}",
                            })

                total_layers = sum(
                    len(r.get("layer_names", [])) for r in run_records
                )

            record.current_phase = "finalizing"

            # ---- One history entry for the whole batch. ----

            self._write_history_record(
                batch_id=batch_id,
                base_seed=base_seed,
                base_params=base_params,
                run_records=run_records,
                warnings=warnings,
                duration_s=time.time() - batch_started,
            )

            # ---- Build the response. ----

            successful = sum(1 for r in run_records
                             if r["status"] == "success")
            cancelled = sum(1 for r in run_records
                            if r["status"] == "cancelled")
            failed = len(run_records) - successful - cancelled

            response_data = {
                "batch_id": batch_id,
                "workflow": workflow_name,
                "count": count,
                "requested_count": requested_count,
                "successful": successful,
                "failed": failed,
                "cancelled": cancelled,
                "base_seed": base_seed,
                "layer_count": total_layers,
                "layer_names": [
                    name for r in run_records
                    for name in r.get("layer_names", [])
                ],
                "runs": [_public_run_record(r) for r in run_records],
            }
            if warnings:
                response_data["warnings"] = warnings

            if successful == 0:
                if cancelled == len(run_records) and cancelled > 0:
                    msg = (f"Batch cancelled "
                           f"({cancelled} run(s) not completed)")
                else:
                    first_error = next(
                        (r.get("error") for r in run_records
                         if r.get("error")
                         and r["status"] != "cancelled"),
                        "no successful runs",
                    )
                    msg = f"Generation failed: {first_error}"
                if warnings:
                    msg += " — " + "; ".join(warnings)
                return {"success": False,
                        "message": msg,
                        "data": response_data}

            if requested_count == 1:
                msg = f"Pasted {total_layers} layer(s)"
            else:
                parts = [f"{successful} succeeded"]
                if failed:
                    parts.append(f"{failed} failed")
                if cancelled:
                    parts.append(f"{cancelled} cancelled")
                msg = (f"Batch of {requested_count}: " + ", ".join(parts)
                       + f"; pasted {total_layers} layer(s)")
            if warnings:
                msg += " — " + "; ".join(warnings)

            return {"success": True, "message": msg, "data": response_data}

        finally:
            with self._batches_lock:
                self._batches.pop(batch_id, None)

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

        if not pending.event.wait(timeout=timeout):
            return {"success": False,
                    "message": f"Generation {prompt_id} still running "
                               f"after {timeout}s"}

        return self._finish_pending(prompt_id, paste=True,
                                    output_name=output_name)

    @command(
        category="comfyui",
        help_text="Cancel one or more running batches. With --batch_id, "
                  "targets a single batch; without, cancels every active "
                  "batch and any async generates.",
        args={
            "--batch_id": {"type": "str", "required": False,
                           "help": "The batch id to cancel. Omit to "
                                   "cancel all active batches."},
        },
    )
    def comfy_cancel(self, params):
        batch_id = params.get("batch_id") or None

        with self._batches_lock:
            if batch_id:
                record = self._batches.get(batch_id)
                if record is None:
                    return {
                        "success": False,
                        "message": (f"No active batch with id "
                                    f"{batch_id!r}"),
                        "data": {
                            "active_batches": sorted(
                                self._batches.keys()
                            ),
                        },
                    }
                record.cancel_event.set()
                return {
                    "success": True,
                    "message": f"Cancel requested for batch {batch_id}",
                    "data": {"batch_id": batch_id},
                }

            records = list(self._batches.values())
            active = sorted(self._batches.keys())

        for r in records:
            r.cancel_event.set()

        async_interrupted = False
        if self._client is not None and self._pending:
            try:
                self._client.interrupt()
                self._client.cancel_queued(list(self._pending.keys()))
                async_interrupted = True
            except ComfyError:
                pass

        parts = []
        if records:
            parts.append(f"{len(records)} batch(es)")
        if async_interrupted:
            parts.append(f"{len(self._pending)} async generate(s)")
        if parts:
            msg = "Cancel requested for " + " and ".join(parts)
        else:
            msg = "Cancel requested (nothing was running)"

        return {
            "success": True,
            "message": msg,
            "data": {"batches": active,
                     "async": list(self._pending.keys())},
        }

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
    #  Internal: async generate pipeline (single prompt)
    # ------------------------------------------------------------------

    def _queue_generate(self,
                        seed: Optional[int] = None,
                        ) -> tuple[Optional[str], Optional[dict]]:
        """Validate state, read pixels, rewrite the graph, and start the
        worker thread. Returns (prompt_id, None) on success, or
        (None, error_dict) on failure.
        """
        try:
            workflow = self._active_workflow_graph
            if workflow is None:
                return None, {"success": False,
                              "message": "No workflow loaded. Run "
                                         "comfy_load or publish a graph "
                                         "from ComfyUI's browser."}
            workflow_name = self._active_workflow_name or "(unnamed)"

            if self._client is None:
                return None, {"success": False,
                              "message": "Not connected; run "
                                         "comfy_connect first"}

            doc = _get_live_active_document()
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

            if seed is None:
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
            pending.document_key = _capture_document_key(doc)
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
        except Exception as e:
            return None, {"success": False,
                          "message": f"queue setup failed: {e}"}

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
        """Collect a completed pending job, record history, paste.

        Used by the async path (`comfy_generate_async` + `comfy_wait`).
        """
        pending = self._pending.pop(prompt_id, None)
        if pending is None:
            return {"success": False,
                    "message": f"No pending job {prompt_id!r}"}

        run_record: dict = {
            "seed": pending.seed,
            "prompt_id": prompt_id,
            "status": "pending",
        }

        def finalize(status: str, error: Optional[str] = None,
                     layer_names: Optional[list] = None,
                     image_count: Optional[int] = None,
                     output_count: Optional[int] = None) -> None:
            run_record["status"] = status
            if error:
                run_record["error"] = error
            if layer_names is not None:
                run_record["layer_names"] = layer_names
            if image_count is not None:
                run_record["image_count"] = image_count
            if output_count is not None:
                run_record["output_count"] = output_count
            self._write_history_record(
                batch_id=prompt_id,
                base_seed=pending.seed,
                base_params={},
                run_records=[run_record],
                warnings=[],
                duration_s=time.time() - pending.started_at,
            )

        if pending.error is not None:
            finalize("error", error=str(pending.error))
            return {"success": False,
                    "message": f"Generation failed: {pending.error}",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        result = pending.result
        if result is None:
            finalize("error", error="no result")
            return {"success": False,
                    "message": "Generation completed without a result"}

        self._last_result = result
        self._last_document = pending.document

        if not paste:
            finalize("success", image_count=len(result.images))
            return {"success": True,
                    "message": f"Generation complete "
                               f"({len(result.images)} image(s))",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        doc = _resolve_live_document_for_pending(pending)
        if doc is None:
            finalize("skipped", error="target document not found")
            return {"success": True,
                    "message": "Generation complete but the target "
                               "document could not be found",
                    "data": {"prompt_id": prompt_id,
                             "workflow": pending.workflow_name}}

        paste_result = self._paste_result(
            doc, result,
            blend_mode="normal",
            output_name=output_name,
        )
        if not paste_result.get("success"):
            finalize("paste_failed",
                     error=paste_result.get("message", "paste failed"))
            return {"success": False,
                    "message": paste_result.get("message", "paste failed")}

        data = paste_result.get("data", {}) or {}
        finalize("success",
                 layer_names=list(data.get("layer_names", [])),
                 image_count=len(result.images),
                 output_count=data.get("output_count", 0))

        response_data = {
            "prompt_id": prompt_id,
            "workflow": pending.workflow_name,
            "image_count": len(result.images),
            "output_count": data.get("output_count", 0),
            "texts": [{"name": t.name, "text": t.text}
                      for t in result.texts],
            "layer_names": list(data.get("layer_names", [])),
        }
        warnings = list(pending.warnings)
        warnings.extend(result.warnings)
        if warnings:
            response_data["warnings"] = warnings

        return {
            "success": True,
            "message": paste_result.get("message",
                                        "Generation complete and pasted"),
            "data": response_data,
        }

    def _paste_result(self, doc, result: GenerationResult,
                      name_override: Optional[str] = None,
                      blend_mode: str = "normal",
                      output_name: Optional[str] = None,
                      seed_suffix: Optional[int] = None) -> dict:
        """Paste every output node's contribution into `doc`."""
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
            if seed_suffix is not None:
                group_name = f"{group_name} ({seed_suffix})"

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

    def _write_history_record(self,
                              batch_id: str,
                              base_seed: int,
                              base_params: dict,
                              run_records: list,
                              warnings: list,
                              duration_s: float) -> None:
        """Write one history record for a batch or a single run."""
        try:
            runs_summary = []
            for r in run_records:
                entry = {
                    "seed": r.get("seed"),
                    "prompt_id": r.get("prompt_id"),
                    "status": r.get("status"),
                }
                if r.get("error"):
                    entry["error"] = r["error"]
                if r.get("image_count") is not None:
                    entry["image_count"] = r["image_count"]
                if r.get("output_count") is not None:
                    entry["output_count"] = r["output_count"]
                if r.get("layer_names"):
                    entry["layer_names"] = list(r["layer_names"])
                runs_summary.append(entry)

            record = {
                "batch_id": batch_id,
                "workflow": self._active_workflow_name,
                "base_seed": base_seed,
                "count": len(run_records),
                "runs": runs_summary,
                "parameters": dict(base_params),
                "warnings": list(warnings),
                "finished": datetime.now(timezone.utc).isoformat(),
                "duration_s": round(duration_s, 3),
            }
            path = _HISTORY_DIR / f"{batch_id}.json"
            path.write_text(json.dumps(record, indent=2),
                            encoding="utf-8")
        except Exception:
            pass


# ============================================================================
#  Helpers
# ============================================================================

def _public_run_record(r: dict) -> dict:
    """Strip internal keys (leading underscore) from a run record."""
    return {k: v for k, v in r.items() if not k.startswith("_")}


def _get_live_active_document():
    """Return the active document as a usable reference, or None."""
    doc = Krita.instance().activeDocument()
    if doc is not None:
        try:
            _ = doc.width()
            return doc
        except Exception:
            pass
    try:
        docs = list(Krita.instance().documents())
    except Exception:
        return None
    for d in docs:
        try:
            _ = d.width()
            return d
        except Exception:
            continue
    return None


def _capture_document_key(doc) -> tuple[str, str]:
    """A stable identifier for a document across object recreation."""
    try:
        return (doc.fileName() or "", doc.name() or "")
    except Exception:
        return ("", "")


def _find_document_by_key(key: tuple[str, str]):
    """Locate the live Document matching a previously-captured key."""
    if not key or not any(key):
        return None
    try:
        docs = list(Krita.instance().documents())
    except Exception:
        return None
    fn, name = key

    candidates = []
    for d in docs:
        try:
            if fn and d.fileName() == fn:
                candidates.append(d)
        except Exception:
            continue
    if not candidates and not fn:
        for d in docs:
            try:
                if name and d.name() == name:
                    candidates.append(d)
            except Exception:
                continue

    for d in candidates:
        try:
            _ = d.width()
            return d
        except Exception:
            continue
    return None


def _resolve_live_document_for_prepared(prepared_entry: dict):
    """Return a usable Document for a phase-2 prepared entry."""
    key = prepared_entry.get("document_key") or ("", "")
    fresh = _find_document_by_key(key)
    if fresh is not None:
        return fresh
    doc = prepared_entry.get("document")
    if doc is not None:
        try:
            _ = doc.width()
            return doc
        except Exception:
            pass
    return None


def _resolve_live_document_for_pending(pending):
    """Return a usable Document for a pending job, or None."""
    key = getattr(pending, "document_key", ("", ""))
    fresh = _find_document_by_key(key)
    if fresh is not None:
        return fresh
    doc = getattr(pending, "document", None)
    if doc is not None:
        try:
            _ = doc.width()
            return doc
        except Exception:
            pass
    return None


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
