"""comfyui_substitute.py — apply resource and param substitutions to a
prompt graph.

`substitute_into_graph(graph, resource_slots, param_slots, asset_refs,
param_values, *, seed=None)` returns a node-level deep copy of the
graph with:

  * every `Primitive*` node whose `_meta.title` appears in
    `param_values` given that value at `inputs["value"]`;
  * every `LoadImage`/`LoadImageMask` node whose `@`-name appears in
    `asset_refs` given a `core/ASSET` reference at its `image` field;
  * if `seed` is not None and the graph has a param slot titled
    `"seed"`, that param is set to `seed` (overriding whatever
    `param_values["seed"]` said).

The input graph is never mutated — every node's `inputs` dict is
copied at the top of the function.

Missing-asset policy
====================
A resource slot with no matching entry in `asset_refs` is a hard
error: `ValueError` names the slot.  This catches a real class of bug
that a silent no-op would hide — the literal `"@name"` string would
otherwise reach ComfyUI, which treats it as a filename and fails with
an opaque "Invalid image file" message at prompt validation time,
long after the actual mistake (a workflow with an `@`-name the caller
does not upload).

Missing-param policy
====================
The reverse case is not an error.  `param_values` may contain titles
the workflow does not declare (e.g. a leftover `[params]` entry after
the workflow is edited), and `param_slots` may contain titles the
caller did not set (params the workflow has a default for).  Both
cases are silent no-ops — the only titles that reach the graph are
the intersection, resolved from `param_slots` side (each slot is
visited once, and only written if `param_values` has a value for it).

Callers
=======
  * `kritomatic_daemon/utils/comfy_apply.py::prepare_workflow` —
    passes `seed=` so a batch's per-run seed is substituted at
    preparation time.
  * `playgrounds/inpaint/inpaint_server.py::handle_inpaint` — passes
    `seed=` so each run gets a fresh seed.

Both import from `kritomatic_shared` — the daemon via the sys.path
bootstrap in `kritomatic_daemon/utils/comfyui_substitute.py`, the
playground via `pg_comfy`'s own bootstrap.
"""

from __future__ import annotations

from typing import Optional


def substitute_into_graph(
    graph: dict,
    resource_slots: dict,
    param_slots: dict,
    asset_refs: dict,
    param_values: dict,
    *,
    seed: Optional[int] = None,
) -> dict:
    """Return a node-level deep copy of `graph` with substitutions.

    `resource_slots` and `param_slots` are the tables produced by
    `kritomatic_shared.comfyui_inspect`'s extractors against the same
    `graph`. `asset_refs` maps each `@-name` to the proxy's asset dict
    (with at least `id`). `param_values` maps each Primitive title to
    the value to substitute. `seed` optionally overrides the `"seed"`
    param.

    The `graph` argument is not mutated. Every node's `inputs` dict is
    copied into a fresh outer dict.

    Raises `ValueError` if a resource slot has no matching entry in
    `asset_refs`.
    """
    prepared = {
        node_id: {
            **node,
            "inputs": dict(node.get("inputs") or {}),
        }
        for node_id, node in graph.items()
    }

    effective_params = dict(param_values)
    if seed is not None and "seed" in param_slots:
        effective_params["seed"] = seed

    for title, slot in param_slots.items():
        if title not in effective_params:
            continue
        node = prepared[slot["node_id"]]
        node["inputs"]["value"] = effective_params[title]

    for name, slot in resource_slots.items():
        asset = asset_refs.get(name)
        if asset is None:
            raise ValueError(
                f"resource {name!r} has a slot but no uploaded asset"
            )
        node = prepared[slot["node_id"]]
        field = slot["input_field"]
        node["inputs"][field] = {
            "__type": "core/ASSET",
            "info": {"id": asset["id"]},
        }

    return prepared
