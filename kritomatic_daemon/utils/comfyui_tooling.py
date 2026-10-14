"""
comfyui_tooling.py — rewrite a custom ComfyUI workflow, replacing the
                     comfyui-tooling-nodes' ETN_* input nodes with the
                     real ComfyUI nodes that will actually run.

The tooling-nodes package (github.com/Acly/comfyui-tooling-nodes) defines
a family of "ETN_*" node classes. Some of them are genuinely server-side
(they run inside ComfyUI: image outputs, text outputs, mask application,
tiling, region attention masking, translate, NSFW filter). Others are
*client-side placeholders* whose `execute()` method is a no-op returning
dummy values — or, in the case of ETN_KritaStyle, literally raises
`NotImplementedError("This workflow must be started from Krita!")`.

The second family is the interesting one:

    ETN_KritaCanvas       returns _placeholder_image(), 512, 512, 0, ...
    ETN_KritaSelection    returns ones mask, False, 0, 0
    ETN_KritaImageLayer   returns placeholder image + ones mask
    ETN_KritaMaskLayer    returns ones mask
    ETN_Parameter         returns its own `default` widget value
    ETN_KritaStyle        raises NotImplementedError
    ETN_KritaStyleAndPrompt  raises NotImplementedError

These nodes exist so that a workflow graph can be authored visually in
ComfyUI's browser UI (with `ETN_KritaCanvas` standing in for "the thing
being edited") and still be executed by a client that knows what to put
there. The client's job, at submission time, is to *rewrite* the graph:
replace every client-side ETN_* node with a real node (or literal value),
remap all downstream input references through the replacement, and submit
the resulting graph to POST /prompt. The server never sees the ETN_* node
in a live prompt.

This module is that rewrite, factored out from any I/O so it can be tested
in isolation. It mirrors the algorithm that krita-ai-diffusion implements
in backend/workflow.py::expand_custom, with two simplifications:

    * The workflow is expected in *prompt form* (a dict of
      {node_id: {class_type, inputs}}) — the format POST /prompt accepts.
      Converting Litegraph UI workflow files to prompt form is a
      separate concern and is not handled here.
    * ETN_KritaStyle and ETN_KritaStyleAndPrompt are rejected with a
      clear error. The user is expected to author their checkpoint,
      LoRA, VAE and sampler chain with raw ComfyUI nodes. Adding style
      expansion later is a self-contained change to `_expand_style`.

=============================================================================
THE REWRITE CONTRACT
=============================================================================

A custom workflow graph is a dict::

    {
        "1": {"class_type": "ETN_KritaCanvas", "inputs": {}},
        "2": {"class_type": "ETN_Parameter",
              "inputs": {"name": "strength", "type": "number",
                         "default": 0.8}},
        "3": {"class_type": "KSampler",
              "inputs": {"model": ["4", 0], "steps": 20,
                         "cfg": ["2", 0]}},        # edge into ETN_Parameter
        ...
    }

Node ids are strings; edges are 2-element lists ``[str_node_id, int_slot]``.

The rewrite walks nodes in topological order, and for each node:

    * If it is a client-side ETN_* input node, expand it per the table
      below. The expansion may emit real nodes into the output graph
      and/or record a literal value. Either way, every output slot of the
      original node gets an entry in the `outputs` map, keyed by
      ``(old_node_id, slot_index)``.
    * Otherwise, copy the node through: remap every edge in its inputs
      through the `outputs` map (if the target was expanded) or through
      the `node_map` (if the target was copied), and emit it into the
      output graph with a fresh node id.

Per-node expansion rules
------------------------

ETN_Parameter → literal
    Read `name` from inputs; look it up in the caller's `params` dict.
    Fall back to the node's `default` widget value if absent. Coerce to
    the type declared in the node's `type` widget ("number",
    "number (integer)", "toggle", "text", "prompt (positive)",
    "prompt (negative)", "choice", "auto"). Record the value as a
    literal in `outputs[(node_id, 0)]`. Downstream edges into this slot
    are replaced by the literal directly, not by a node reference.

ETN_KritaCanvas → ETN_LoadImageCache + literals
    5 outputs: image, width, height, seed, mask.
    The caller supplies `resources.canvas_image` (an encoded PNG of the
    current composite) and `resources.canvas_extent`. Emit one
    `ETN_LoadImageCache` node whose `id` is the content hash of the
    PNG bytes; the node's two outputs are (image, mask). Route outputs
    0 and 4 through those two slots; outputs 1, 2, 3 become integer
    literals (canvas width, canvas height, seed).

ETN_KritaSelection → ETN_LoadImageCache or SolidMask + literals
    4 outputs: mask, active, offset_x, offset_y.
    If `resources.selection_mask` is set, load it as a mask (via
    ETN_LoadImageCache slot 1) and take offset_x/offset_y from
    `resources.selection_bounds`. Otherwise emit a SolidMask of the
    canvas extent with value 1.0, active=False, offsets 0/0.
    This mirrors how the KAID client synthesizes "no selection means
    the whole canvas is selected" without requiring the workflow to
    branch on it.

ETN_KritaImageLayer → ETN_LoadImageCache
    2 outputs: image, mask. Reads `name` from inputs and looks up
    `resources.layer_images[name]`. Same ETN_LoadImageCache dance as
    ETN_KritaCanvas, but the two outputs are used as-is (image, mask).

ETN_KritaMaskLayer → ETN_LoadImageCache (slot 1)
    1 output: mask. Reads `name`, looks up `resources.layer_masks[name]`.

ETN_KritaStyle, ETN_KritaStyleAndPrompt
    Not supported. Raise ExpansionError. Author the model/prompt chain
    with raw ComfyUI nodes instead.

Everything else
    Copied through with remapped inputs. This includes ETN_KritaOutput
    and ETN_KritaSendText (server-side output nodes that ship results
    back over the WebSocket), and all the server-side ETN_* nodes
    (tiling, region masking, translate, NSFW filter, mask application).

Node pruning
------------
ComfyUI's UI→API conversion is not perfect. Two classes of problem
show up in practice, and both would otherwise cause `_remap_value` to
raise on a dangling edge:

  1. Muted and bypassed nodes are dropped from the API output, but the
     consumers that were wired to them keep a reference to their id —
     the browser-side "Save (API Format)" writes the *link* id in that
     slot; the server-side comfyui-workflow-to-api-converter-endpoint
     writes the dropped node's id. Either way the referenced key has
     no entry in the graph.

  2. Authoring-only nodes are not part of the pipeline by construction.
     Bookmark (rgthree), Note, and floating Reroutes have no forward
     path to any output; a PrimitiveInt hidden behind a mute switch has
     none either. These are not graph errors — they are just not part
     of a runnable workflow — but if they land in the API output with
     a dangling reference or as a spurious node, the rewrite chokes.

`_prune_graph` handles both before the walk begins, as a fixed-point
loop over two rules:

    Unreachable
        A node that cannot reach any output node through its consumers
        is dropped. This is the general form of "strip UI-only
        helpers": no class name is hardcoded, only the graph-theoretic
        property that a pipeline node can reach a sink. Output nodes
        themselves are identified via `/object_info`'s output flags,
        with a small fallback set for the case where node_defs is not
        available.

    Unrunnable
        A node whose required inputs cannot be satisfied — either the
        edge dangles after the Unreachable pass, or the input was never
        provided and the schema offers no default — is dropped. Its
        own consumers then lose their edge and become unrunnable in
        turn.

The rules iterate because each can expose the other: dropping an
unrunnable node can make its former consumers unreachable; dropping
an unreachable node removes a producer that another node depended on.

Schema defaults and choice validation
-------------------------------------
ComfyUI's newer `comfy_api.latest.io` schema (used by tooling-nodes)
marks every input as required, even those with a `default`. A workflow
author who only wires the interesting inputs — which is normal, and
which the ComfyUI browser UI permits — produces a graph that ComfyUI
rejects at /prompt time with "Required input is missing: ...". The
rewrite therefore consults the `/object_info` response (passed in as
`node_defs`) and fills in any missing required input from its schema
default before emitting the node. This mirrors what
krita-ai-diffusion does in `ComfyWorkflow.add_default_values`.

The same schema is also used to validate existing values against the
declared `choices`. A workflow authored against an older version of a
node may carry a value that the current schema no longer accepts — for
example, a hex color where the node now only takes the string "Alpha" or
"Color". The rewriter drops such a value and lets the schema default
fill in its place, and records a warning so the caller can tell the
user to fix the workflow in the browser. Without this, the workflow
would be rejected outright by ComfyUI's strict schema validation.

Content addressing
------------------
The caller supplies image bytes already encoded as PNG. Every distinct
blob is registered in `ExpansionResult.image_data` keyed by an 8-character
lowercase hex CRC32 of its bytes. The caller is expected to PUT each blob
to `/api/etn/image/{id}` on the ComfyUI server before submitting the
graph; the `ETN_LoadImageCache` node in the rewritten graph references
that id. Duplicate blobs across a session collapse to one upload because
the hash is content-derived.

KAID uses the same scheme (backend/comfy_workflow.py,
ComfyWorkflow._add_image_hashed): crc32, hex, padded to 8 chars.

Topological order and cycles
----------------------------
Nodes are processed in dependency order (Kahn's algorithm on the edge
graph). A cycle, or an edge that references a node id not present in
the workflow, raises ExpansionError before any node is emitted, so a
malformed workflow fails loudly rather than producing a partial graph.
(The pruning pass removes dangling edges before this point, so the
"edge references nonexistent node" case only surfaces if a caller
somehow bypasses pruning.)

=============================================================================
WHAT THIS MODULE DOES NOT DO
=============================================================================
    * Read or write Krita pixels. The handler fetches EncodedImage blobs
      and passes them in via KritaResources. See comfyui_krita.py.
    * Talk to ComfyUI. No HTTP, no WebSocket, no I/O of any kind. The
      handler uploads the returned image_data and submits the graph.
    * Cache anything between calls. Each expand_custom() is a pure
      function of its arguments (modulo the `node_defs` input, which is
      itself just data). The content-hash dedup happens within one call,
      not across calls; if the handler wants cross-call caching, it can
      check its own upload registry before re-uploading.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import Any, NamedTuple, Optional


# ============================================================================
#  Public types
# ============================================================================

@dataclass
class EncodedImage:
    """One PNG-encoded image plus its pixel extent.

    `data` is the raw PNG bytes. `is_mask` is a hint for logging and for
    choosing the load path; it does not affect the wire format, since
    ETN_LoadImageCache reads the same PNG either way and exposes both an
    RGB image output and a mask output (mask = red channel for
    grayscale, alpha for RGBA).
    """
    data: bytes
    width: int
    height: int
    is_mask: bool = False

    @property
    def content_hash(self) -> str:
        """8-character lowercase hex CRC32 of the PNG bytes.

        Used as the ETN_LoadImageCache id. Matches KAID's hashing
        (backend/comfy_workflow.py, _add_image_hashed). The CRC32 is
        masked to unsigned 32-bit before formatting because Python's
        zlib.crc32 returns a signed int on some builds.
        """
        return f"{zlib.crc32(self.data) & 0xFFFFFFFF:08x}"


@dataclass
class KritaResources:
    """Pixels and metadata the handler fetched from the Krita document.

    All EncodedImage blobs are PNG. The handler is expected to fetch
    exactly what `scan_requirements()` reported and no more; missing
    resources cause `expand_custom()` to raise with a clear message
    naming the node that needed them.
    """
    # Canvas: the composite of every visible layer, clipped to the region
    # of interest if the workflow's ETN_KritaCanvas declares one.
    canvas_image: Optional[EncodedImage] = None
    # Optional canvas mask. Only used by ETN_KritaSelection when it needs
    # a mask derived from the canvas (rare; the handler usually has a
    # selection_mask instead).
    canvas_mask: Optional[EncodedImage] = None
    # Canvas extent in pixels. Used for ETN_KritaCanvas width/height
    # outputs and for synthesizing a SolidMask when there is no selection.
    canvas_extent: Optional[tuple[int, int]] = None

    # Selection: Krita's active selection, cropped to the workflow's
    # context bounds.
    selection_mask: Optional[EncodedImage] = None
    # (x, y, width, height) of the selection crop relative to the canvas.
    # Emitted as ETN_KritaSelection offset_x/offset_y outputs; downstream
    # workflows use them to place results back on the canvas.
    selection_bounds: Optional[tuple[int, int, int, int]] = None

    # Named layer pixel data, keyed by layer name as it appears in the
    # ETN_KritaImageLayer / ETN_KritaMaskLayer node's `name` widget.
    # Masks are separate so the handler can read from a mask layer
    # differently from a paint layer.
    layer_images: dict[str, EncodedImage] = field(default_factory=dict)
    layer_masks: dict[str, EncodedImage] = field(default_factory=dict)


@dataclass
class WorkflowRequirements:
    """What a workflow expects from the Krita side.

    Produced by scan_requirements() before the handler fetches pixels.
    The handler reads exactly this set and no more, so that a workflow
    with no canvas node never pays for a full-composite read.
    """
    needs_canvas: bool = False
    needs_selection: bool = False
    layer_images: set[str] = field(default_factory=set)
    layer_masks: set[str] = field(default_factory=set)
    parameter_names: set[str] = field(default_factory=set)
    # How many ETN_KritaStyle / ETN_KritaStyleAndPrompt nodes are present.
    # Non-zero is currently an error; see expand_custom.
    style_node_count: int = 0


@dataclass
class ExpansionResult:
    """Output of expand_custom()."""
    # The rewritten graph, ready for POST /prompt. Node ids are fresh
    # strings starting from "1"; every ETN_* input node has been replaced.
    graph: dict
    # Content-hash-keyed PNG blobs that the caller must upload via
    # PUT /api/etn/image/{hash} before submitting the graph. Empty when
    # the workflow had no image inputs.
    image_data: dict[str, bytes]
    # The parameter values that were actually used, after type coercion
    # and default-fallback. Useful for the generation-history record.
    parameters: dict[str, Any]
    # Diagnostics produced during the rewrite. Two kinds:
    #   * Node prunes: unreachable nodes, unrunnable nodes, and edges
    #     dropped because they pointed at a muted or bypassed node.
    #   * Schema substitutions: a workflow value that was not in the
    #     node's declared choices, replaced by the schema default.
    # The handler surfaces these in its response message so the user
    # can tell what happened to a workflow that did not run as drawn.
    warnings: list[str] = field(default_factory=list)


class ExpansionError(Exception):
    """Raised when a workflow cannot be rewritten.

    Reasons: missing required resource (canvas, selection, named layer),
    cyclic graph, edge referencing a nonexistent node, duplicate
    ETN_Parameter name, use of an unsupported tooling node, or a
    top-level node value that is not an object. Every message names the
    node id that triggered it.
    """


# ============================================================================
#  Internal types
# ============================================================================

class _Output(NamedTuple):
    """Internal reference to one output slot of a node in the *new* graph.

    While walking the workflow we carry these around as the "real" value
    of a slot; at emit time they become ``[node_id, slot]`` edges in the
    ComfyUI prompt format. Literal values (ints, strings, bools) are
    carried alongside them and pass through emit untouched.
    """
    node_id: str
    slot: int


# ============================================================================
#  Scanning
# ============================================================================

def scan_requirements(workflow: dict) -> WorkflowRequirements:
    """Walk a workflow and record what the handler must fetch.

    Cheap and side-effect-free. Reads only widget values (name, type,
    default) and node class names. Does not touch pixels or the network.
    Does not validate; the actual validation happens inside
    expand_custom() where the resources are available.

    Called by the handler before fetching pixels, so that a workflow
    that only needs a named layer mask does not trigger a full canvas
    composite read.
    """
    reqs = WorkflowRequirements()
    for _nid, node in workflow.items():
        node_type = node.get("class_type", "")
        inputs = node.get("inputs", {}) or {}

        if node_type == "ETN_KritaCanvas":
            reqs.needs_canvas = True

        elif node_type == "ETN_KritaSelection":
            reqs.needs_selection = True

        elif node_type == "ETN_KritaImageLayer":
            name = inputs.get("name")
            if isinstance(name, str) and name:
                reqs.layer_images.add(name)

        elif node_type == "ETN_KritaMaskLayer":
            name = inputs.get("name")
            if isinstance(name, str) and name:
                reqs.layer_masks.add(name)

        elif node_type == "ETN_Parameter":
            name = inputs.get("name")
            if isinstance(name, str) and name:
                reqs.parameter_names.add(name)

        elif node_type in ("ETN_KritaStyle", "ETN_KritaStyleAndPrompt"):
            reqs.style_node_count += 1

        # Everything else is passed through, including server-side
        # tooling nodes (ETN_KritaOutput, ETN_KritaSendText, ETN_Translate,
        # ETN_NSFWFilter, ETN_ApplyMaskToImage, ETN_ReferenceImage,
        # ETN_ApplyReferenceImages, ETN_BackgroundRegion, ETN_DefineRegion,
        # ETN_ListRegionMasks, ETN_AttentionMask, ETN_TileLayout,
        # ETN_ExtractImageTile, ETN_ExtractMaskTile, ETN_GenerateTileMask,
        # ETN_MergeImageTile, ETN_LoadImageBase64, ETN_LoadMaskBase64,
        # ETN_LoadImageCache, ETN_SaveImageCache, ETN_SendImageWebSocket,
        # ETN_control_load, ETN_control_apply) and the user's own raw
        # ComfyUI nodes (KSampler, CheckpointLoaderSimple, etc.).

    return reqs


# ============================================================================
#  Expansion — top-level
# ============================================================================

def expand_custom(workflow: dict,
                  params: dict,
                  resources: KritaResources,
                  *,
                  seed: int = 0,
                  node_defs: Optional[dict] = None) -> ExpansionResult:
    """Rewrite a custom workflow graph, replacing ETN_* input nodes.

    Args:
        workflow:  the graph to rewrite, in prompt form. The caller's
                   dict is not mutated.
        params:    parameter values keyed by the ETN_Parameter `name`
                   widget. Missing entries fall back to the node's
                   `default`. Values are coerced per the node's `type`.
        resources: pixels and metadata fetched by the handler.
        seed:      the seed value emitted as the ETN_KritaCanvas seed
                   output. The caller decides this; typically the same
                   seed passed to KSampler in the same run.
        node_defs: the `/object_info` response from ComfyUI, if available.
                   When supplied, every copied-through node gets any
                   missing required inputs filled in from its schema
                   defaults, and any present value that is not in the
                   schema's declared choices is dropped in favor of the
                   schema default. Both cases are recorded in the
                   returned `warnings` list. Pass this whenever the
                   client is connected.

                   It is also used by the pruning pass, both to identify
                   output nodes (via `is_output_node`/`output_node`) and
                   to decide whether a missing required input can be
                   filled from a schema default (and so does not justify
                   dropping the node).

    Returns:
        ExpansionResult with the rewritten graph, image_data to upload,
        the parameters actually used (post-coercion), and any warnings.

    Raises:
        ExpansionError — workflow is malformed, uses an unsupported
        tooling node, or references a resource the caller did not fetch.
    """
    if not workflow:
        raise ExpansionError("workflow is empty")

    # Reject malformed node values before anything else touches them.
    # The pruning pass, the schema pass, and the copy helper all
    # assume every top-level value is an object; checking once here
    # turns a stray string or list into a clear ExpansionError instead
    # of an AttributeError from deep inside one of those passes.
    _check_node_values_are_objects(workflow)

    # Prune first, so downstream passes only ever see a graph whose
    # edges resolve and whose nodes can reach an output. The copy is
    # two levels deep — top-level dict plus a fresh `inputs` dict per
    # node — because those are exactly the two levels the pruning pass
    # mutates. A bare `dict(workflow)` is not enough: the nested node
    # dicts would still be shared, and `_prune_unrunnable_nodes` does
    # `del inputs[key]` on them, silently editing the caller's graph.
    # `copy.deepcopy` would also work but is needlessly expensive for
    # graphs whose nodes carry large non-`inputs` payloads.
    workflow = _copy_for_pruning(workflow)
    warnings: list[str] = []
    _prune_graph(workflow, node_defs, warnings)

    if not workflow:
        raise ExpansionError(
            "workflow has no runnable nodes after pruning; "
            + (warnings[0] if warnings else "no reason recorded")
        )

    # ---- Validate up front, before emitting anything ----
    #
    # Both checks are authoring errors that are much clearer before the
    # graph is half-built. A duplicate parameter name silently shadows
    # in the params dict, which is the worst kind of bug to debug from
    # a runtime error message; catching it here names both nodes.

    _check_duplicate_parameter_names(workflow)

    reqs = scan_requirements(workflow)
    if reqs.style_node_count > 0:
        raise ExpansionError(
            "ETN_KritaStyle and ETN_KritaStyleAndPrompt are not "
            "supported by Kritomatic. Author the model, LoRA, VAE and "
            "sampler chain with raw ComfyUI nodes (CheckpointLoaderSimple, "
            "LoraLoader, CLIPTextEncode, KSampler, ...) directly in the "
            "workflow JSON."
        )

    # ---- Topological order ----
    order = _topological_order(workflow)

    # ---- Walk and expand ----
    #
    # `outputs` keys are (old_node_id: str, slot: int) and values are
    # either an _Output (a real node in the new graph) or a literal
    # (int/float/bool/str, e.g. from ETN_Parameter).
    #
    # `node_map` keys are old_node_id: str and values are the new node id
    # that a copied node landed under. Used when a downstream input
    # references a slot that was NOT expanded (a normal node output).
    #
    # `warnings` was created above so the pruning pass could append to
    # it; the expansion walk below continues to add to the same list.
    builder = _GraphBuilder()
    outputs: dict[tuple[str, int], Any] = {}
    node_map: dict[str, str] = {}

    for nid in order:
        node = workflow[nid]
        node_type = node.get("class_type", "")

        if node_type == "ETN_Parameter":
            _expand_parameter(nid, node, params, outputs)

        elif node_type == "ETN_KritaCanvas":
            _expand_canvas(nid, node, resources, builder, outputs, seed)

        elif node_type == "ETN_KritaSelection":
            _expand_selection(nid, node, resources, builder, outputs)

        elif node_type == "ETN_KritaImageLayer":
            _expand_image_layer(nid, node, resources, builder, outputs)

        elif node_type == "ETN_KritaMaskLayer":
            _expand_mask_layer(nid, node, resources, builder, outputs)

        elif node_type in ("ETN_KritaStyle", "ETN_KritaStyleAndPrompt"):
            # Guarded above; kept here as a defensive assertion in case
            # scan_requirements ever drifts from the dispatch below.
            raise ExpansionError(
                f"{node_type} ({nid!r}) is not supported"
            )

        else:
            _copy_node(nid, node, builder, outputs, node_map,
                       node_defs, warnings)

    # ---- Assemble the parameters-used record ----
    #
    # Coerced values as they were emitted, not the caller's raw input.
    # This is what goes into the generation-history entry so a rerun
    # uses the same concrete values.
    used_params: dict[str, Any] = {}
    for (old_id, slot), value in outputs.items():
        if slot != 0:
            continue
        node = workflow.get(old_id, {})
        if node.get("class_type") == "ETN_Parameter":
            name = (node.get("inputs") or {}).get("name", "")
            if name:
                used_params[name] = value

    return ExpansionResult(
        graph=builder.nodes,
        image_data=builder.image_data,
        parameters=used_params,
        warnings=warnings,
    )


# ============================================================================
#  Node expanders
# ============================================================================

def _expand_parameter(node_id: str,
                      node: dict,
                      params: dict,
                      outputs: dict) -> None:
    """ETN_Parameter → a literal value at outputs[(node_id, 0)].

    The value is looked up in `params` by the node's `name` widget,
    falling back to the node's `default` widget. Coercion follows the
    node's `type` widget, which is the same set the tooling node
    advertises in its schema (comfyui-tooling-nodes/krita.py, Parameter):

        "auto"                 pass through unchanged
        "number"               float
        "number (integer)"     int
        "toggle"               bool
        "text"                 str
        "prompt (positive)"    str
        "prompt (negative)"    str
        "choice"               str (not validated against option list
                                    here; the connected node will
                                    validate or ComfyUI will reject it)
    """
    inputs = node.get("inputs", {}) or {}
    name = inputs.get("name", "")
    ptype = inputs.get("type", "auto")
    default = inputs.get("default")

    raw_value = params.get(name, default)

    # Coerce. Failures are authoring errors and worth naming explicitly,
    # because the wrong type would surface much later as a cryptic
    # ComfyUI validation message.
    try:
        if ptype == "number (integer)":
            value = int(raw_value)
        elif ptype == "number":
            value = float(raw_value)
        elif ptype == "toggle":
            value = bool(raw_value)
        elif ptype in ("text", "prompt (positive)",
                       "prompt (negative)", "choice"):
            value = "" if raw_value is None else str(raw_value)
        else:
            # "auto" or an unrecognized type: emit verbatim.
            value = raw_value
    except (TypeError, ValueError) as e:
        raise ExpansionError(
            f"ETN_Parameter {node_id!r} (name={name!r}, type={ptype!r}): "
            f"cannot coerce {raw_value!r} to {ptype}: {e}"
        ) from e

    outputs[(node_id, 0)] = value


def _expand_canvas(node_id: str,
                   node: dict,
                   resources: KritaResources,
                   builder: "_GraphBuilder",
                   outputs: dict,
                   seed: int) -> None:
    """ETN_KritaCanvas → ETN_LoadImageCache + literal width/height/seed.

    Five outputs (see comfyui-tooling-nodes/krita.py, KritaCanvas):
        0  image       IMAGE    the canvas composite
        1  width       INT      canvas width in pixels
        2  height      INT      canvas height in pixels
        3  seed        INT      the generation seed for this run
        4  mask        MASK     the canvas alpha, or an all-ones mask

    The image and mask come from a single ETN_LoadImageCache node,
    which is the transport the tooling-nodes package provides for
    in-memory images uploaded via PUT /api/etn/image/{id}.
    """
    if resources.canvas_image is None:
        raise ExpansionError(
            f"ETN_KritaCanvas ({node_id!r}): the handler did not provide "
            f"resources.canvas_image. Scan the workflow with "
            f"scan_requirements() and fetch exactly what it reports."
        )
    canvas = resources.canvas_image

    img_out, mask_out = builder.load_image_and_mask(canvas)
    outputs[(node_id, 0)] = img_out
    outputs[(node_id, 4)] = mask_out

    # Width and height: prefer the extent the handler reported, since a
    # workflow may operate on a cropped region whose PNG dimensions do
    # not match the canvas the user sees. Fall back to the PNG's own
    # dimensions when the handler did not supply an extent, which is
    # fine for workflows that do not crop.
    if resources.canvas_extent is not None:
        w, h = resources.canvas_extent
    else:
        w, h = canvas.width, canvas.height
    outputs[(node_id, 1)] = int(w)
    outputs[(node_id, 2)] = int(h)
    outputs[(node_id, 3)] = int(seed)


def _expand_selection(node_id: str,
                      node: dict,
                      resources: KritaResources,
                      builder: "_GraphBuilder",
                      outputs: dict) -> None:
    """ETN_KritaSelection → a mask node + literal offsets.

    Four outputs (see comfyui-tooling-nodes/krita.py, KritaSelection):
        0  mask       MASK    the active selection
        1  active     BOOL    True iff a selection exists
        2  x          INT     selection crop offset x, relative to canvas
        3  y          INT     selection crop offset y, relative to canvas

    When no selection exists, the workflow still expects a mask. The
    KAID client synthesizes a fully-opaque mask at the canvas extent,
    so downstream nodes see "everything is selected" without needing
    to branch. We do the same.

    The `context` and `padding` widgets on the node are honoured by the
    *handler* (it decides what region of the canvas to crop the
    selection to before encoding it); by the time expand_custom sees
    the blob, cropping has already happened and the offsets are
    relative to that crop.
    """
    mask = resources.selection_mask

    if mask is not None:
        outputs[(node_id, 0)] = builder.load_mask(mask)
        outputs[(node_id, 1)] = True
        if resources.selection_bounds is not None:
            x, y, _w, _h = resources.selection_bounds
        else:
            x, y = 0, 0
        outputs[(node_id, 2)] = int(x)
        outputs[(node_id, 3)] = int(y)
        return

    # No selection. Emit a SolidMask covering the canvas extent.
    if resources.canvas_extent is None:
        raise ExpansionError(
            f"ETN_KritaSelection ({node_id!r}): no selection was provided "
            f"and no canvas extent was provided either, so a default "
            f"all-selected mask cannot be synthesized. The handler must "
            f"supply at least one of the two."
        )
    w, h = resources.canvas_extent
    outputs[(node_id, 0)] = builder.solid_mask(w, h, value=1.0)
    outputs[(node_id, 1)] = False
    outputs[(node_id, 2)] = 0
    outputs[(node_id, 3)] = 0


def _expand_image_layer(node_id: str,
                        node: dict,
                        resources: KritaResources,
                        builder: "_GraphBuilder",
                        outputs: dict) -> None:
    """ETN_KritaImageLayer → ETN_LoadImageCache.

    Two outputs (see comfyui-tooling-nodes/krita.py, KritaImageLayer):
        0  image      IMAGE
        1  mask       MASK    the alpha channel

    Reads `name` from the node's inputs and looks up the corresponding
    blob in resources.layer_images. The handler is expected to have
    populated that dict for every name reported by scan_requirements.
    """
    name = (node.get("inputs") or {}).get("name", "")
    image = resources.layer_images.get(name)
    if image is None:
        available = sorted(resources.layer_images)
        raise ExpansionError(
            f"ETN_KritaImageLayer ({node_id!r}, name={name!r}): the "
            f"handler did not provide pixels for this layer. "
            f"Layers with pixels available: {available or '(none)'}."
        )
    img_out, mask_out = builder.load_image_and_mask(image)
    outputs[(node_id, 0)] = img_out
    outputs[(node_id, 1)] = mask_out


def _expand_mask_layer(node_id: str,
                       node: dict,
                       resources: KritaResources,
                       builder: "_GraphBuilder",
                       outputs: dict) -> None:
    """ETN_KritaMaskLayer → ETN_LoadImageCache, mask slot.

    One output (see comfyui-tooling-nodes/krita.py, KritaMaskLayer):
        0  mask       MASK

    Reads `name` from the node's inputs and looks up the corresponding
    blob in resources.layer_masks (kept separate from layer_images so
    the handler can read from a mask layer with different Krita calls
    than from a paint layer).
    """
    name = (node.get("inputs") or {}).get("name", "")
    mask = resources.layer_masks.get(name)
    if mask is None:
        available = sorted(resources.layer_masks)
        raise ExpansionError(
            f"ETN_KritaMaskLayer ({node_id!r}, name={name!r}): the "
            f"handler did not provide a mask. "
            f"Layers with masks available: {available or '(none)'}."
        )
    outputs[(node_id, 0)] = builder.load_mask(mask)


# ============================================================================
#  Copy-through path
# ============================================================================

def _copy_node(old_id: str,
               node: dict,
               builder: "_GraphBuilder",
               outputs: dict,
               node_map: dict,
               node_defs: Optional[dict] = None,
               warnings: Optional[list] = None) -> None:
    """Copy a non-expanded node into the new graph with remapped inputs.

    Every input value is run through _remap_value: edges that pointed at
    an expanded tooling node get replaced by the literal or _Output the
    expansion produced; edges that pointed at other copied nodes get
    their target id translated through `node_map`; literals pass through
    unchanged.

    When `node_defs` is supplied, missing required inputs are filled in
    from the node's schema, and any present value that is not in the
    schema's declared `choices` is dropped in favor of the schema
    default. Both cases append to `warnings` so the caller can surface
    them. See the module docstring, "Schema defaults and choice
    validation".

    The node lands in the new graph under a fresh id, and that id is
    recorded in `node_map[old_id]` so downstream copies can find it.
    """
    node_type = node.get("class_type")
    if not node_type:
        raise ExpansionError(
            f"node {old_id!r} is missing the 'class_type' key"
        )

    inputs_in = node.get("inputs", {}) or {}
    inputs_out = {k: _remap_value(v, outputs, node_map)
                  for k, v in inputs_in.items()}

    if node_defs is not None:
        inputs_out = _apply_schema(
            node_defs, node_type, inputs_out,
            warnings if warnings is not None else [],
        )

    # add() emits the inputs through _emit_value, which converts any
    # _Output in the values back to [node_id, slot] edges for the wire.
    new_output = builder.add(node_type, output_count=1, **inputs_out)
    node_map[old_id] = new_output.node_id


def _remap_value(v: Any,
                 outputs: dict,
                 node_map: dict) -> Any:
    """Translate one input value from the old graph's id space.

    Three cases:

      * An edge ``[str_old_id, int_slot]`` whose target was expanded:
        return the replacement value (an _Output from the new graph,
        or a literal like an int). Callers see this directly.

      * An edge whose target was copied: return a new _Output in the
        new graph at the same slot. The caller's emit step turns it
        into ``[new_id, slot]``.

      * Anything else (literal int/float/bool/str, or a literal list):
        return as-is, recursing into lists so a list of edges remaps
        correctly.

    Raises ExpansionError if an edge points at a node that is not in
    the workflow at all. In practice the pruning pass removes such
    edges before this runs, so the raise here is a defensive check
    against callers that skip pruning.

    The edge heuristic (two-element list where the first element is a
    string and the second is an int) is the same one ComfyUI uses in
    its prompt format. It can in principle misidentify a literal
    ``["hello", 0]`` as an edge; in practice no tooling node takes such
    a shape, and the alternative — requiring every caller to tag edges
    explicitly — would complicate the graph format for no gain.
    """
    if _is_edge(v):
        old_id, slot = v[0], v[1]
        key = (old_id, slot)
        if key in outputs:
            return outputs[key]
        if old_id in node_map:
            return _Output(node_map[old_id], slot)
        raise ExpansionError(
            f"input references node {old_id!r} slot {slot}, which is "
            f"not present in the workflow"
        )
    if isinstance(v, list):
        return [_remap_value(x, outputs, node_map) for x in v]
    return v


def _is_edge(v: Any) -> bool:
    """True iff `v` looks like a ComfyUI prompt-format edge.

    Shape: two-element list, first element is a string (node id),
    second is an int (output slot). Anything else — a single value, a
    longer list, a list of lists — is a literal and is handled by the
    caller as such.
    """
    return (
        isinstance(v, list)
        and len(v) == 2
        and isinstance(v[0], str)
        and isinstance(v[1], int)
        and not isinstance(v[1], bool)  # bool is a subclass of int in Python
    )


def _emit_value(v: Any) -> Any:
    """Convert an internal value to wire format.

    _Output → ``[node_id, slot]``. Literal → itself. Lists → recursively
    converted, so a list of edges is emitted as a list of ``[id, slot]``
    pairs.
    """
    if isinstance(v, _Output):
        return [v.node_id, v.slot]
    if isinstance(v, list):
        return [_emit_value(x) for x in v]
    return v


# ============================================================================
#  Schema defaults and choice validation
# ============================================================================

def _apply_schema(node_defs: dict,
                  class_type: str,
                  inputs: dict,
                  warnings: list) -> dict:
    """Fill missing defaults and drop out-of-choice values.

    Reads ComfyUI's `/object_info` format:

        {
          "RMBG": {
            "input": {
              "required": {
                "background": [["Alpha", "Color"], {"default": "Alpha"}],
                "background_color": ["STRING", {"default": "#222222"}],
                ...
              }
            }
          }
        }

    For each required input:

      * If missing and a default can be inferred, fill it in.
      * If present with a value that is not in the schema's declared
        choices, remove it so the default fills in. The mismatch is
        recorded in `warnings` with the node type, input name, the
        workflow's value, and the choices.

    A field with neither a default nor a choice list is left alone; the
    validation error that ComfyUI raises for it names the node and input
    more usefully than a guess.

    Inputs already present with a valid value are not overwritten, so a
    wired value (or a widget the author did set) always wins over the
    schema default.
    """
    spec = node_defs.get(class_type)
    if not isinstance(spec, dict):
        return inputs

    required = (spec.get("input") or {}).get("required") or {}
    result = dict(inputs)
    for name, field in required.items():
        if name not in result:
            default = _schema_default(field)
            if default is not None:
                result[name] = default
            continue

        choices = _schema_choices(field)
        if choices and result[name] not in choices:
            default = _schema_default(field)
            warnings.append(
                f"{class_type}.{name}: workflow value "
                f"{result[name]!r} is not one of {choices}; "
                + (f"using default {default!r}."
                   if default is not None
                   else "removing it, ComfyUI will report if required.")
            )
            if default is not None:
                result[name] = default
            else:
                del result[name]

    return result


def _schema_choices(field: Any) -> Optional[list]:
    """Extract the declared choices from one `/object_info` field entry.

    Returns None when the field has no choice list. Returns the list of
    allowed values when it has one, in either of the two forms ComfyUI
    uses:

        [["Alpha", "Color"], {"default": "Alpha"}]        # combo
        ["COMBO", {"options": ["Alpha", "Color"]}]        # newer form
    """
    if not isinstance(field, list) or not field:
        return None

    field_type = field[0]
    options = field[1] if len(field) > 1 and isinstance(field[1], dict) else {}

    if isinstance(field_type, list):
        return list(field_type)

    if field_type == "COMBO":
        choices = options.get("options") or options.get("choices")
        if isinstance(choices, list):
            return list(choices)

    return None


def _schema_default(field: Any) -> Any:
    """Extract a default value from one `/object_info` field entry.

    Returns `None` when no default can be inferred; the caller treats
    `None` as "leave missing". `None` is also a legitimate default in
    rare cases, but no stock ComfyUI node uses it, so the ambiguity is
    not worth resolving.

    Defaults are resolved in this order:
      1. `options["default"]` if present
      2. first entry of the type list, for legacy combo-style inputs
         (where the type slot is itself a list of allowed values)
      3. type-based fallback: 0 for INT, 0.0 for FLOAT, False for BOOL,
         "" for STRING, first allowed value for COMBO
      4. nothing — the input is left missing, and ComfyUI will report it
    """
    if not isinstance(field, list) or not field:
        return None

    field_type = field[0]
    options = field[1] if len(field) > 1 and isinstance(field[1], dict) else {}

    if "default" in options:
        return options["default"]

    # Legacy combo: the type slot is itself a list of allowed values.
    if isinstance(field_type, list) and field_type:
        return field_type[0]

    if field_type == "INT":
        return 0
    if field_type == "FLOAT":
        return 0.0
    if field_type == "BOOL":
        return False
    if field_type == "STRING":
        return ""
    if field_type == "COMBO":
        choices = options.get("options") or options.get("choices")
        if isinstance(choices, list) and choices:
            return choices[0]

    return None


def _has_schema_default(field: Any) -> bool:
    """True iff the schema field can supply a value on its own.

    Mirrors the fallback logic in `_schema_default`, but returns a
    bool so callers can ask "would this be filled?" without confusing
    a legitimate `None` default with "no default at all".
    """
    if not isinstance(field, list) or not field:
        return False
    field_type = field[0]
    options = (field[1] if len(field) > 1
               and isinstance(field[1], dict) else {})
    if "default" in options:
        return True
    if isinstance(field_type, list) and field_type:
        return True
    if field_type in ("INT", "FLOAT", "BOOL", "STRING"):
        return True
    if field_type == "COMBO":
        choices = options.get("options") or options.get("choices")
        if isinstance(choices, list) and choices:
            return True
    return False


# ============================================================================
#  Node pruning
# ============================================================================

# Fallback set for when node_defs is unavailable. Only the classes our
# own tooling-nodes package guarantees to register as outputs. This is
# not a "bookmark blacklist" — it is a defensive minimum for the case
# where the daemon is generating without a cached schema.
_KNOWN_OUTPUT_CLASSES = frozenset({
    "ETN_KritaOutput",
    "ETN_KritaSendText",
})


def _copy_for_pruning(workflow: dict) -> dict:
    """Return a copy of `workflow` that the pruning pass may mutate.

    The pruning pass does exactly two kinds of edit:

      * `del workflow[nid]` on the top-level dict, and
      * `del inputs[key]` on a node's `inputs` dict.

    It does not touch `class_type`, `_meta`, or any other field on a
    node. A two-level copy — top-level dict plus a fresh `inputs` dict
    per node — is therefore sufficient to leave the caller's graph
    intact, and cheaper than `copy.deepcopy` for nodes that carry
    large payloads outside `inputs` (e.g. `_meta.handles` records
    stamped by `kritomatic comfyui convert`).

    Nodes that are not dicts are passed through unchanged; the pruning
    pass skips them without mutating, and downstream passes treat the
    workflow as malformed and raise a clear error rather than trying
    to fix it here.
    """
    out: dict = {}
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            out[nid] = node
            continue
        node_copy = dict(node)
        inputs = node.get("inputs")
        if isinstance(inputs, dict):
            node_copy["inputs"] = dict(inputs)
        out[nid] = node_copy
    return out


def _is_output_class(class_type: str,
                     node_defs: Optional[dict]) -> bool:
    """True iff the class is a terminal output node.

    Prefers the schema: ComfyUI's `/object_info` marks every output
    node with `is_output_node` (newer schema) or `output_node` (older
    schema). Falls back to a small set of classes from the tooling-
    nodes package, which our daemon always uses as its terminal nodes
    and which a stale schema cache may not list.
    """
    if isinstance(node_defs, dict):
        spec = node_defs.get(class_type)
        if isinstance(spec, dict):
            if spec.get("is_output_node") or spec.get("output_node"):
                return True
    return class_type in _KNOWN_OUTPUT_CLASSES


def _prune_unreachable_nodes(workflow: dict,
                             node_defs: Optional[dict],
                             warnings: list) -> bool:
    """Drop every node that cannot reach an output node.

    A node is kept if it either *is* an output, or has a path forward
    through the graph to some output. This is the general form of
    "strip UI-only helpers": Bookmark (rgthree) nodes, Note nodes,
    disconnected Reroutes, orphaned Primitives, and any other node
    that does not contribute to a result all fail the reachability
    test and are dropped. No class name appears in the rule.

    Returns True iff anything was dropped, so callers can iterate
    this together with `_prune_unrunnable_nodes` to a fixed point.

    Mutates `workflow` in place. Callers that need the original
    intact should pass a two-level copy — see `_copy_for_pruning`.
    """
    outputs = {
        nid for nid, node in workflow.items()
        if _is_output_class(node.get("class_type", ""), node_defs)
    }
    if not outputs:
        # No output node in the graph — cannot decide what "contributes
        # to a result" means. Leave the graph alone; the schema-based
        # required-input check downstream may still prune it.
        return False

    # Reverse-edge map: for each node, the set of nodes whose outputs
    # feed its inputs.
    producers: dict[str, set[str]] = {}
    for nid, node in workflow.items():
        ps: set[str] = set()
        for v in (node.get("inputs") or {}).values():
            if _is_edge(v) and v[0] in workflow:
                ps.add(v[0])
        producers[nid] = ps

    keep = set(outputs)
    stack = list(outputs)
    while stack:
        nid = stack.pop()
        for producer in producers.get(nid, ()):
            if producer not in keep:
                keep.add(producer)
                stack.append(producer)

    dropped = [nid for nid in workflow if nid not in keep]
    for nid in dropped:
        cls = workflow[nid].get("class_type", "?")
        warnings.append(f"dropped unreachable node {nid} ({cls})")
        del workflow[nid]
    return bool(dropped)


def _prune_unrunnable_nodes(workflow: dict,
                            node_defs: Optional[dict],
                            warnings: list) -> bool:
    """Drop nodes that cannot run, and their downstream consumers.

    ComfyUI's UI→API conversion (both the browser-side "Save (API
    Format)" and the server-side comfyui-workflow-to-api-converter-
    endpoint) leaves dangling references when a muted or bypassed
    node is dropped from the output but its consumers still hold a
    pointer to it. In the browser exporter the reference is written
    as a link id; in the endpoint's output it is written as the
    muted node's id. Either way, the referenced key has no entry in
    the graph.

    Two conditions are handled:

      * A node whose input is a two-element edge ``[X, Y]`` where
        ``X`` is not a key in the graph loses that edge. If the input
        was required and has no schema default, the node itself is
        then dropped.

      * A node missing a required input that has no schema default is
        dropped.

    Dropping a node makes every edge to it dangling, which can mark
    further nodes as unrunnable. This function makes a single pass
    over the graph; `_prune_graph` iterates it together with the
    reachability rule so both conditions reach a fixed point.

    Returns True iff anything was dropped.

    Mutates `workflow` in place. Callers that need the original
    intact should pass a two-level copy — see `_copy_for_pruning`.
    """
    changed = False
    present = set(workflow.keys())
    for nid in list(workflow.keys()):
        if nid not in present:
            continue
        node = workflow[nid]
        cls = node.get("class_type", "")
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            continue

        required: dict = {}
        if isinstance(node_defs, dict):
            spec = node_defs.get(cls)
            if isinstance(spec, dict):
                required = (spec.get("input") or {}).get(
                    "required"
                ) or {}

        # Drop edges that point at nodes no longer in the graph.
        for key in list(inputs.keys()):
            v = inputs[key]
            if not _is_edge(v):
                continue
            if v[0] in present:
                continue
            warnings.append(
                f"dropped edge {nid}.{key} -> missing node "
                f"{v[0]!r} (from muted/bypassed source)"
            )
            del inputs[key]
            changed = True

        # If a required input is now missing and cannot be filled from
        # the schema, drop the node. Its consumers will lose their edge
        # on the next pass.
        for key, field in required.items():
            if key in inputs:
                continue
            if _has_schema_default(field):
                continue
            warnings.append(
                f"pruned node {nid} ({cls}): required input "
                f"{key!r} missing, no schema default"
            )
            del workflow[nid]
            present.discard(nid)
            changed = True
            break

    return changed


def _prune_graph(workflow: dict,
                 node_defs: Optional[dict],
                 warnings: list) -> None:
    """Apply every pruning rule to a fixed point.

    Two rules run in sequence, repeated until neither drops anything:

      1. Unreachable — no forward path to an output node.
      2. Unrunnable  — a required input that cannot be satisfied
         (dangling edge + no schema default), or an edge to a node a
         previous rule already removed.

    Rule 1 runs first so Rule 2 never sees a fresh dangling edge from
    the muted-or-bypassed class of failure the earlier fix addresses.
    Rule 2 can drop additional nodes, which can in turn make further
    nodes unreachable; the loop reruns Rule 1 to catch that.
    """
    while True:
        before = len(workflow)
        _prune_unreachable_nodes(workflow, node_defs, warnings)
        _prune_unrunnable_nodes(workflow, node_defs, warnings)
        if len(workflow) == before:
            break


# ============================================================================
#  Validation helpers
# ============================================================================

def _check_node_values_are_objects(workflow: dict) -> None:
    """Raise if any top-level node value is not a JSON object.

    The API-format contract is a dict keyed by node id, each value a
    dict carrying at least `class_type`. A stray string, list, or null
    where a node should be is a malformed file — most likely a
    hand-edited workflow, a partial export, or a file that went
    through a lossy transformation.

    Without this check, the failure surfaces deep inside the pruning
    pass as `AttributeError: 'str' object has no attribute 'get'`,
    which names neither the file nor the offending key. Catching it
    here turns the same failure into a clear ExpansionError.

    Raises ExpansionError naming the first offending key.
    """
    for nid, node in workflow.items():
        if isinstance(node, dict):
            continue
        raise ExpansionError(
            f"node {nid!r} is {type(node).__name__}, not an object; "
            f"the workflow is not in ComfyUI API format. Each top-level "
            f"key must map to an object with a 'class_type'."
        )


def _check_duplicate_parameter_names(workflow: dict) -> None:
    """Raise if two ETN_Parameter nodes share a `name` widget value.

    Duplicates are silent shadowing: the params dict is keyed by name,
    so the second write wins and the first node's value never reaches
    the graph. The failure surfaces much later as "my parameter had no
    effect", which is hard to trace back here. Checking up front is
    cheap and names both offenders.
    """
    seen: dict[str, list[str]] = {}
    for nid, node in workflow.items():
        if node.get("class_type") != "ETN_Parameter":
            continue
        name = (node.get("inputs") or {}).get("name", "")
        if not name:
            continue
        seen.setdefault(name, []).append(nid)

    duplicates = {name: ids for name, ids in seen.items() if len(ids) > 1}
    if duplicates:
        detail = "; ".join(
            f"{name!r} at nodes {ids}" for name, ids in duplicates.items()
        )
        raise ExpansionError(
            f"duplicate ETN_Parameter names (must be unique across the "
            f"workflow): {detail}"
        )


# ============================================================================
#  Topological sort
# ============================================================================

def _topological_order(workflow: dict) -> list[str]:
    """Return node ids in dependency order (Kahn's algorithm).

    Edges to nodes not present in `workflow` are ignored here; the
    pruning pass removes them beforehand, so this only surfaces if a
    caller bypasses pruning. `_remap_value` still raises on them if
    they reach the walk.

    A genuine cycle — every remaining node has unresolved dependencies —
    is reported with the full set of stuck node ids.
    """
    deps: dict[str, set[str]] = {nid: set() for nid in workflow}
    for nid, node in workflow.items():
        for v in (node.get("inputs") or {}).values():
            for edge_id in _iter_edges(v):
                if edge_id in workflow and edge_id != nid:
                    deps[nid].add(edge_id)

    in_degree = {nid: len(d) for nid, d in deps.items()}
    ready = [nid for nid, d in in_degree.items() if d == 0]
    order: list[str] = []

    while ready:
        nid = ready.pop(0)
        order.append(nid)
        # Recompute in-degrees for remaining nodes. O(n*m) but n is
        # small (workflows are tens of nodes) and the code stays clear.
        for other, other_deps in deps.items():
            if nid in other_deps:
                in_degree[other] -= 1
                if in_degree[other] == 0:
                    ready.append(other)

    if len(order) != len(workflow):
        stuck = [nid for nid in workflow if nid not in set(order)]
        raise ExpansionError(
            f"workflow contains a cycle or unresolvable dependency chain; "
            f"nodes not reachable in dependency order: {stuck}"
        )

    return order


def _iter_edges(value: Any) -> list[str]:
    """Yield every node id referenced by `value`, if any.

    `value` may be an edge (``[id, slot]``), a nested list of edges, or
    a plain literal. Only the ids of edges are returned; literals yield
    nothing.
    """
    result: list[str] = []
    if _is_edge(value):
        result.append(value[0])
    elif isinstance(value, list):
        for item in value:
            result.extend(_iter_edges(item))
    return result


# ============================================================================
#  Graph builder
# ============================================================================

class _GraphBuilder:
    """Emits nodes into a fresh ComfyUI prompt graph.

    Node ids are fresh strings starting from "1" and counting up. Every
    emitted node has the shape ``{"class_type": str, "inputs": dict}``
    and the input values are already in wire format (edges as
    ``[id, slot]`` lists, literals untouched).

    `image_data` accumulates the content-hash → PNG-bytes map that the
    caller must PUT to /api/etn/image/{id} before submitting. A second
    load_image_and_mask for the same blob is deduplicated: the hash is
    the same, so the same ETN_LoadImageCache node is returned.
    """

    def __init__(self) -> None:
        self.nodes: dict[str, dict] = {}
        self.next_id: int = 1
        self.image_data: dict[str, bytes] = {}
        # hash -> (image_output, mask_output); used to dedupe repeated
        # references to the same blob within one expansion.
        self._loaded_images: dict[str, tuple[_Output, _Output]] = {}

    def add(self, class_type: str, output_count: int, **inputs: Any):
        """Emit one node. Returns an _Output for a single-output node,
        or a tuple of _Outputs for a multi-output node.
        """
        node_id = str(self.next_id)
        self.next_id += 1
        self.nodes[node_id] = {
            "class_type": class_type,
            "inputs": {k: _emit_value(v) for k, v in inputs.items()},
        }
        outputs = tuple(_Output(node_id, i) for i in range(output_count))
        return outputs[0] if output_count == 1 else outputs

    def load_image(self, image: EncodedImage) -> _Output:
        """Register `image`, emit an ETN_LoadImageCache, return its
        IMAGE output (slot 0)."""
        img_out, _ = self.load_image_and_mask(image)
        return img_out

    def load_mask(self, mask: EncodedImage) -> _Output:
        """Register `mask`, emit an ETN_LoadImageCache, return its
        MASK output (slot 1)."""
        _, mask_out = self.load_image_and_mask(mask)
        return mask_out

    def load_image_and_mask(self,
                            image: EncodedImage) -> tuple[_Output, _Output]:
        """Register `image` and return (image_output, mask_output).

        Deduplicated by content hash: a second call with the same bytes
        returns the same two _Outputs without emitting a duplicate node
        or registering the blob twice in image_data.
        """
        h = image.content_hash
        cached = self._loaded_images.get(h)
        if cached is not None:
            return cached
        self.image_data[h] = image.data
        img_out, mask_out = self.add("ETN_LoadImageCache", 2, id=h)
        self._loaded_images[h] = (img_out, mask_out)
        return img_out, mask_out

    def solid_mask(self, width: int, height: int,
                   value: float = 1.0) -> _Output:
        """Emit a SolidMask node and return its single output.

        Used to synthesize a fully-selected mask when the workflow
        declares an ETN_KritaSelection but the document has no
        selection. `value` is in [0, 1]; 1.0 means "fully opaque".
        """
        return self.add("SolidMask", 1,
                        width=int(width),
                        height=int(height),
                        value=float(value))
