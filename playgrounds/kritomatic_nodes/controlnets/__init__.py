# kritomatic_nodes/controlnets/__init__.py
"""
ControlNetsConditioning — Playground-driven ControlNets node.

Drop-in replacement for the "ControlNets" subgraph in the user's
workflow.  Same inputs (`vae`, `positive`, `negative`, `image`) and
same outputs (`positive`, `negative`); the difference is what happens
inside.

WHAT THIS NODE DOES
===================
It fetches the preprocessed ControlNet images from the
`controlnets` playground's `/state` at generation time, reads the
serial chain configuration (which ControlNet, strength, start, end)
out of `data/controlnets/controlnets_workflow.json`, and rebuilds
the chain dynamically:

    LoadImage(preproc_1) ─┐
    ControlNetLoader(cn_1)─→ ControlNetApplyAdvanced ─┐
                                                        │
    LoadImage(preproc_2) ─┐                             │
    ControlNetLoader(cn_2)─→ ControlNetApplyAdvanced ←─┘
                                                        │
    ...                                                 → out

The order is the order the ControlNetApplyAdvanced nodes appear in
the workflow file (`node.order`).

WHY A SEPARATE WORKFLOW FILE
============================
The playground already runs the preprocessors once per `@thecontrolnet`
image and caches the resulting PNGs.  The workflow file supplies only
the *configuration* — which ControlNet model each preprocessing result
feeds, and with what strength/window.  Editing which ControlNets are
applied is a JSON edit; it never requires touching this node's
source.

MATCHING PLAYGROUND IMAGES TO WORKFLOW NODES
============================================
For each `ControlNetApplyAdvanced` node in the workflow file we need
the preprocessed image feeding it.  We look it up in the playground
state by trying two names, in order:

    1. the ControlNetApplyAdvanced's `title`     (e.g. "DEPTH")
    2. the upstream preprocessor's `type`/`title`
                                                 (e.g. "Zoe-DepthMapPreprocessor")

The first case covers a playground that returns one entry per
ControlNetApplyAdvanced; the second covers a playground that returns
one entry per unique preprocessor.  Either shape works.

PER-APPLY ENABLE FILTER
=======================
Before matching to a playground entry, the node consults
`state.apply_enabled` — a `{apply_title: bool}` dict set by the
playground's panel.  If an entry's `apply_title` maps to `False`,
the node skips that chain step with a log line and moves on:

    if apply_enabled.get(entry["apply_title"], True) is False:
        skipped.append(f"{title!r} (disabled in playground)")
        continue

This is how a user disables e.g. DEPTH FULL without affecting DEPTH:
both share the same Zoe preprocessed image, but their apply titles
are independently toggled.  The node still stages that shared image
if any enabled chain entry references it.

GRACEFUL DEGRADATION
====================
Matching `MultiAreaConditioning`'s philosophy:

  · Workflow file missing / unreadable → pass through unchanged.
  · Playground unreachable             → pass through unchanged.
  · Playground state has no entries    → pass through unchanged.
  · No chain entries could be applied  → pass through unchanged.

All four log a clear message to ComfyUI's stdout, so a silent failure
is impossible.  Generation continues either way — same behavior a
workflow with the original subgraph would have if its ControlNet
loaders / preprocessors were all disabled.

The `image` input is kept for parity with the subgraph this node
replaces but is currently unused when the playground is up — the
preprocessed images come from the playground, not from this node
running preprocessors itself.

EXPANSION
=========
The node returns its built subgraph via `io.NodeOutput(..., expand=…)`.
ComfyUI refuses to expand any node whose schema does not set
`enable_expand=True`.  This is why `define_schema` has that flag —
without it, `execute()` succeeds, builds the graph correctly, and
then the framework raises "Node … is not expandable, but expand
included in NodeOutput".
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from comfy_api.latest import ComfyExtension, io
import folder_paths
from comfy_execution.graph_utils import GraphBuilder


_DEFAULT_PLAYGROUND_URL = "http://127.0.0.1:8779"


# ---------------------------------------------------------------------------
#  Workflow file discovery
# ---------------------------------------------------------------------------
#
# `install.py` symlinks `<repo>/playgrounds/kritomatic_nodes` into
# `<comfyui>/custom_nodes/kritomatic_nodes`.  `Path(__file__).resolve()`
# follows that symlink back to `<repo>/playgrounds/kritomatic_nodes/…`,
# so walking up to find `data/controlnets/controlnets_workflow.json`
# lands on the repo root regardless of which side of the symlink Python
# imported us from.  A copy-install (no symlink) will not find the file
# — the user passes `workflow_path` explicitly in that case.
#
# Kept as a discovery loop rather than `.parents[3]` so this also works
# if the file is later moved, or the package is vendored into a
# different layout.

def _find_workflow_path():
    p = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = p / "data" / "controlnets" / "controlnets_workflow.json"
        if candidate.is_file():
            return str(candidate)
        if p == p.parent:
            break
        p = p.parent
    # Best-guess default — the input is overridable.
    return str(
        Path(__file__).resolve().parents[3]
        / "data" / "controlnets" / "controlnets_workflow.json"
    )


_DEFAULT_WORKFLOW_PATH = _find_workflow_path()


# ---------------------------------------------------------------------------
#  HTTP helpers
# ---------------------------------------------------------------------------

def _post_json(url, body, timeout=5.0):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def _fetch_state(playground_url, timeout=5.0):
    return _post_json(
        playground_url.rstrip("/") + "/state", {}, timeout,
    )


def _fetch_version(playground_url, timeout=2.0):
    return _post_json(
        playground_url.rstrip("/") + "/version", {}, timeout,
    )


def _fetch_controlnet_b64(playground_url, hash_hex, timeout=10.0):
    r = _post_json(
        playground_url.rstrip("/") + "/controlnet_image",
        {"hash": hash_hex},
        timeout,
    )
    if not r.get("success"):
        raise RuntimeError(r.get("message", "unknown"))
    return r.get("b64", "")


# ---------------------------------------------------------------------------
#  Coercion
# ---------------------------------------------------------------------------

def _float(v, default):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
#  Workflow-file parsing
# ---------------------------------------------------------------------------

def _load_workflow(path):
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"workflow file not found: {path}")
    return json.loads(p.read_text(encoding="utf-8"))


def _build_chain_config(workflow):
    """Extract the serial ControlNet chain from the workflow file.

    Returns a list of entries (one per ControlNetApplyAdvanced node),
    in the file's order.  Each entry:

        {
            "apply_title":        str,     # e.g. "DEPTH"
            "preprocessor_title": str,     # e.g. "Zoe-DepthMapPreprocessor"
            "control_net_name":   str,     # e.g. "SDXL/...safetensors"
            "strength":           float,
            "start":              float,
            "end":                float,
        }

    Nodes with a missing image or control_net input link are skipped —
    a broken node in the source file is not fatal to the rest of the
    chain.
    """
    nodes = {n["id"]: n for n in (workflow.get("nodes") or [])}

    links = {}
    for link in (workflow.get("links") or []):
        lid, oid, osl, tid, tsl, typ = link
        links[lid] = (oid, osl, tid, tsl, typ)

    apply_nodes = [
        n for n in (workflow.get("nodes") or [])
        if n.get("type") == "ControlNetApplyAdvanced"
    ]
    apply_nodes.sort(key=lambda n: n.get("order", n.get("id", 0)))

    chain = []
    for node in apply_nodes:
        inputs = node.get("inputs") or []
        image_link = None
        ctrl_link = None
        for inp in inputs:
            name = inp.get("name")
            if name == "image":
                image_link = inp.get("link")
            elif name == "control_net":
                ctrl_link = inp.get("link")

        if image_link is None or ctrl_link is None:
            continue

        pre_oid = links.get(image_link, (None,))[0]
        cn_oid = links.get(ctrl_link, (None,))[0]
        preprocessor = nodes.get(pre_oid)
        cn_loader = nodes.get(cn_oid)
        if preprocessor is None or cn_loader is None:
            continue

        pre_title = (
            preprocessor.get("title")
            or preprocessor.get("type")
            or ""
        )
        apply_title = (
            node.get("title")
            or f"Apply#{node.get('id', 0)}"
        )

        ctrl_name = None
        named = cn_loader.get("widgets_values_named") or {}
        if named:
            ctrl_name = named.get("control_net_name")
        if ctrl_name is None:
            wv = cn_loader.get("widgets_values") or []
            if wv:
                ctrl_name = wv[0]
        if not ctrl_name:
            continue

        strength = 1.0
        start = 0.0
        end = 1.0
        named = node.get("widgets_values_named") or {}
        if named:
            strength = _float(named.get("strength"), 1.0)
            start = _float(named.get("start_percent"), 0.0)
            end = _float(named.get("end_percent"), 1.0)
        else:
            wv = node.get("widgets_values") or []
            if len(wv) >= 3:
                strength = _float(wv[0], 1.0)
                start = _float(wv[1], 0.0)
                end = _float(wv[2], 1.0)

        chain.append({
            "apply_title":        apply_title,
            "preprocessor_title": pre_title,
            "control_net_name":   ctrl_name,
            "strength":           strength,
            "start":              start,
            "end":                end,
        })

    return chain


def _find_pg_entry(pg_by_name, entry):
    """Match a chain entry to a playground state entry.

    See the module docstring's "MATCHING PLAYGROUND IMAGES TO WORKFLOW
    NODES" for the two names tried.
    """
    return (
        pg_by_name.get(entry["apply_title"])
        or pg_by_name.get(entry["preprocessor_title"])
    )


# ---------------------------------------------------------------------------
#  Node
# ---------------------------------------------------------------------------

class ControlNetsConditioning(io.ComfyNode):

    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ControlNetsConditioning",
            display_name="ControlNets (Playground)",
            category="conditioning",
            description=(
                "Dynamically builds the ControlNet chain from the "
                "`controlnets` playground.  Reads the chain "
                "configuration (which ControlNet, strength, start, "
                "end) from `data/controlnets/controlnets_workflow.json` "
                "and pulls the preprocessed images from the playground "
                "at generation time.  Slots in place of the original "
                "ControlNets subgraph — same inputs, same outputs.  "
                "Per-apply enable flags are read from "
                "`/state.apply_enabled`; disabled entries are skipped."
            ),
            enable_expand=True,
            inputs=[
                io.Vae.Input(
                    "vae",
                    optional=True,
                    tooltip=(
                        "Optional VAE.  When wired, ControlNetApplyAdvanced "
                        "runs in latent space (same as the subgraph this "
                        "node replaces); when not wired, it runs in pixel "
                        "space."
                    ),
                ),
                io.Conditioning.Input("positive"),
                io.Conditioning.Input("negative"),
                io.Image.Input(
                    "image",
                    optional=True,
                    tooltip=(
                        "Source image.  Kept for parity with the "
                        "subgraph this node replaces; not currently "
                        "used when the playground is up (the preprocessed "
                        "images come from the playground)."
                    ),
                ),
                io.String.Input(
                    "playground_url",
                    default=_DEFAULT_PLAYGROUND_URL,
                    optional=True,
                    tooltip=(
                        "Base URL of the controlnets playground. "
                        "Standalone: http://127.0.0.1:8779 . "
                        "Under the pgkit hub: "
                        "http://127.0.0.1:8780/controlnets ."
                    ),
                ),
                io.String.Input(
                    "workflow_path",
                    default=_DEFAULT_WORKFLOW_PATH,
                    optional=True,
                    tooltip=(
                        "Path to the workflow file whose "
                        "ControlNetApplyAdvanced nodes define the chain.  "
                        "Leave blank to use the default "
                        "data/controlnets/controlnets_workflow.json."
                    ),
                ),
            ],
            outputs=[
                io.Conditioning.Output("positive"),
                io.Conditioning.Output("negative"),
            ],
        )

    @classmethod
    def IS_CHANGED(cls, playground_url=_DEFAULT_PLAYGROUND_URL,
                   workflow_path="", **kwargs):
        """Invalidate the cache when *either* the playground state or
        the workflow file changes.

        ComfyUI will not re-execute a node whose `IS_CHANGED` value
        has not moved since the last run.  The playground `/version`
        covers "user re-preprocessed the controlnet images" and "user
        toggled an Apply Node's checkbox"; the workflow file's content
        hash covers "user added / removed / edited a
        ControlNetApplyAdvanced node in
        `controlnets_workflow.json`".
        """
        playground_url = (playground_url or _DEFAULT_PLAYGROUND_URL).strip()
        wf_path = (workflow_path or "").strip() or _DEFAULT_WORKFLOW_PATH

        try:
            wf_hash = hashlib.sha256(
                Path(wf_path).read_bytes()
            ).hexdigest()[:16]
        except Exception:
            wf_hash = "no-wf"

        try:
            v = _fetch_version(playground_url).get("version") or "empty"
        except Exception:
            v = "unreachable"

        return f"{v}:{wf_hash}"

    @classmethod
    def execute(cls, vae, positive, negative, image=None,
                playground_url=_DEFAULT_PLAYGROUND_URL,
                workflow_path=""):
        playground_url = (playground_url or _DEFAULT_PLAYGROUND_URL).strip()
        wf_path = (workflow_path or "").strip() or _DEFAULT_WORKFLOW_PATH

        # --- 1. Load chain configuration ---
        try:
            workflow = _load_workflow(wf_path)
        except Exception as e:
            print(
                f"[ControlNetsConditioning] could not load workflow "
                f"file {wf_path!r}: {e}; passing through unchanged.",
                flush=True,
            )
            return io.NodeOutput(positive, negative)

        chain = _build_chain_config(workflow)
        if not chain:
            print(
                f"[ControlNetsConditioning] workflow {wf_path!r} contains "
                f"no ControlNetApplyAdvanced nodes; passing through "
                f"unchanged.",
                flush=True,
            )
            return io.NodeOutput(positive, negative)

        # --- 2. Fetch playground state ---
        try:
            state = _fetch_state(playground_url)
        except Exception as e:
            print(
                f"[ControlNetsConditioning] controlnets playground "
                f"unreachable at {playground_url!r}: {e}; passing "
                f"positive/negative through unchanged.",
                flush=True,
            )
            return io.NodeOutput(positive, negative)

        controlnets = state.get("controlnets") or []
        pg_by_name = {}
        for c in controlnets:
            if not isinstance(c, dict):
                continue
            for key in ("name", "id"):
                v = c.get(key)
                if isinstance(v, str) and v:
                    pg_by_name[v] = c

        if not pg_by_name:
            print(
                "[ControlNetsConditioning] playground state has no "
                "controlnets; passing through unchanged.",
                flush=True,
            )
            return io.NodeOutput(positive, negative)

        # Per-apply enable flags.  Missing entries default to enabled,
        # so a fresh playground with no toggles yet applies all
        # ControlNets.  See module docstring "PER-APPLY ENABLE FILTER".
        apply_enabled = state.get("apply_enabled") or {}
        if not isinstance(apply_enabled, dict):
            apply_enabled = {}

        # --- 3. Stage images and build the graph ---
        input_dir = folder_paths.get_input_directory()
        staged = {}          # hash -> staged filename
        graph = GraphBuilder()

        current_pos = positive
        current_neg = negative
        applied_count = 0
        skipped = []

        for entry in chain:
            if apply_enabled.get(entry["apply_title"], True) is False:
                skipped.append(
                    f"{entry['apply_title']!r} (disabled in playground)"
                )
                continue

            pg = _find_pg_entry(pg_by_name, entry)
            if pg is None:
                skipped.append(
                    f"{entry['apply_title']!r} (no playground entry; "
                    f"tried {entry['preprocessor_title']!r})"
                )
                continue

            img_meta = pg.get("image") or {}
            img_hash = img_meta.get("hash")
            if not img_hash:
                skipped.append(
                    f"{entry['apply_title']!r} (no image hash on "
                    f"playground entry)"
                )
                continue

            if img_hash not in staged:
                try:
                    b64 = _fetch_controlnet_b64(playground_url, img_hash)
                except Exception as e:
                    skipped.append(
                        f"{entry['apply_title']!r} (image fetch failed: {e})"
                    )
                    continue
                fn = f"_kritomatic_controlnet_{img_hash[:8]}.png"
                dst = os.path.join(input_dir, fn)
                try:
                    with open(dst, "wb") as f:
                        f.write(base64.b64decode(b64))
                except Exception as e:
                    skipped.append(
                        f"{entry['apply_title']!r} (stage failed: {e})"
                    )
                    continue
                staged[img_hash] = fn

            fn = staged[img_hash]

            load = graph.node("LoadImage", image=fn)
            cn_loader = graph.node(
                "ControlNetLoader",
                control_net_name=entry["control_net_name"],
            )

            apply_kwargs = {
                "positive":      current_pos,
                "negative":      current_neg,
                "control_net":   cn_loader.out(0),
                "image":         load.out(0),
                "strength":      entry["strength"],
                "start_percent": entry["start"],
                "end_percent":   entry["end"],
            }
            if vae is not None:
                apply_kwargs["vae"] = vae

            applied = graph.node("ControlNetApplyAdvanced", **apply_kwargs)
            current_pos = applied.out(0)
            current_neg = applied.out(1)
            applied_count += 1

        if skipped:
            print(
                "[ControlNetsConditioning] skipped chain entry(ies): "
                + "; ".join(skipped),
                flush=True,
            )

        if applied_count == 0:
            print(
                "[ControlNetsConditioning] no chain entries could be "
                "applied; passing through unchanged.",
                flush=True,
            )
            return io.NodeOutput(positive, negative)

        finalized = graph.finalize()
        print(
            f"[ControlNetsConditioning] built chain of {applied_count} "
            f"ControlNet(s) from {playground_url} "
            f"(v{(state.get('version') or '')[:8]}); "
            f"expanded to {len(finalized)} node(s).",
            flush=True,
        )
        return io.NodeOutput(current_pos, current_neg, expand=finalized)


# ---------------------------------------------------------------------------
#  Extension wrapper — imported by kritomatic_nodes/__init__.py
# ---------------------------------------------------------------------------

class KritomaticExtension(ComfyExtension):
    async def get_node_list(self):
        return [ControlNetsConditioning]