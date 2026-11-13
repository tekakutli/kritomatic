"""HTTP routes and ComfyUI/daemon bridge for the inpaint playground.

ARCHITECTURE — DIRECT PROXY V2
==============================
The playground submits ComfyUI jobs via the daemon's proxy
(`ProxyV2`, comfy-api-proxy on 127.0.0.1:8189) *directly*, not via
the daemon's `comfy_generate` command.  The daemon is consulted for:

  · `comfy_status` — the active workflow name, its slot tables, and
    the currently-applied `_active_config` (params, crop, paste);
  · `comfy_connect` — auto-starting the proxy on the first request;
  · `comfy_register_external_job` / `comfy_unregister_external_job` —
    announcing the job so `comfy_batches` / `comfy_status` see it;
  · `comfy_paste_image_b64` — writing the result into the active
    Krita document as a new layer, with un-rotation when the payload
    was rotated.

The daemon is NOT asked to read pixels from Krita.  Playground pixels
live in the browser; the batch protocol's `comfy_set_layer_pixels`
would require the daemon to write them into Krita layers, which no
command implements.  Under this architecture, the playground uploads
its pixel blobs to the proxy (`ProxyV2.upload_asset`) and substitutes
`core/ASSET` references into the graph.  No Krita layer is created,
modified, or deleted by the *submit* path — only by the explicit
paste step at the end.

PASTE-AND-UNROTATE
==================
The client sends `outputRotation = -view.rotation` when the payload
was built under a non-zero view rotation.  The submitted canvas and
selection are the rotated view's crop.  ComfyUI's stitched output is
at those (cropW × cropH) dims, still in the rotated frame.

After download, the server calls `comfy_paste_image_b64` with
`rotation = output_rotation`.  The daemon's `paste_image` runs
`QTransform().rotate(output_rotation)` on the PNG — which un-rotates
it back to the original orientation — then center-crops to the
document's dimensions and writes the pixels into a fresh layer.
Result: Krita gains the inpainted region at the right position,
un-rotated.  The client then refreshes its own view from the Krita
composite via `/krita/import`, so both sides agree.

WORKFLOW SOURCE
===============
The playground needs the workflow graph in API format.  Two files
are involved:

  data/comfy_panel/workflows/<name>.json — the source file.  May be
    in ComfyUI's UI format (has top-level `nodes` / `links` keys) or
    API format (a dict of node-id → {class_type, inputs}).

  data/convert/<name>.json — the API conversion.  Written by the
    daemon's `comfy_load` auto-conversion (`convert_if_ui`,
    `annotate=False`) the first time a UI-format workflow is loaded.

`_load_workflow_graph` tries the source first; if UI, tries the
conversion.  A UI-format source with no conversion is an error, with
a message pointing to `comfy_load` (one load triggers the
conversion) or to the CLI convert command.

A workflow loaded from a *custom path* (`comfy_load --name /abs/foo.json`)
returns only its stem in `comfy_status`, so the playground cannot
find the file.  That's a known limitation of reading from disk;
workflows loaded by slug from `data/comfy_panel/workflows/` work.

WORKFLOW RESOLUTION
===================
The inpaint workflow is whatever the daemon currently has loaded
(`comfy_status["workflow"]`).  No hardcoded default.  If no workflow
is loaded, the request fails with a message pointing at the docker's
Workflow picker / `comfy_load` / an explicit `workflow` in the body.

PROMPT RESOLUTION
=================
Same participant model as before:
  · Non-empty playground prompt → `param_values["prompt"] = prompt`.
  · Empty playground prompt → `param_values["prompt"]` stays whatever
    `_active_config["params"]["prompt"]` says (the docker / web panel
    shared state), falling through to the workflow's `PrimitiveString`
    default if neither has set one.

The client hydrates its prompt field from `_active_config["params"]
["prompt"]` on boot (see `handle_current_prompt`).

SEED
====
Randomized per run, matching the daemon's `comfy_generate` behavior.
`_active_config["params"]["seed"]` is deliberately ignored.

CANCELLATION
============
The client POSTs `/comfy/interrupt`, which cancels the current
inpaint job via `ProxyV2.cancel_job`.  The poll loop sees the job
transition to `canceled` and returns.

@thecanvas auto-sync
====================
Unchanged.  The browser-side poller tracks the daemon's
`config.resources["@thecanvas"]` spec and fetches pixels via
`/krita/resource_image`.  Independent of the inpaint submit path.
"""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

from pg_comfy import (
    ComfyError, get_client, reset_client,
    ProxyV2, ProxyError, ProxyHTTPError, ProxyUnreachable,
    substitute_into_graph,
)
from pg_comfy.image import make_encoded_image

from . import krita_client


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

INPAINT_TIMEOUT = 180.0
POLL_INTERVAL_S = 0.25

# <repo>/playgrounds/inpaint/inpaint_server.py
#   .parent           → .../playgrounds/inpaint
#   .parent.parent    → .../playgrounds
#   .parent.parent.parent → <repo>
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_WORKFLOW_DIR = _REPO_ROOT / "data" / "comfy_panel" / "workflows"
_CONVERT_DIR = _REPO_ROOT / "data" / "convert"

_inpaint_lock = threading.Lock()      # one inpaint at a time
_job_lock = threading.Lock()
_current_job_id: Optional[str] = None
_current_batch_id: Optional[str] = None

_IMAGE_CACHE_MAX = 8
_image_cache: dict = {}
_image_cache_lock = threading.Lock()


def _log(msg: str) -> None:
    print(f"[inpaint] {msg}", flush=True)


def _cache_image(hash_hex, raw, width, height, filename):
    with _image_cache_lock:
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            oldest = next(iter(_image_cache))
            _image_cache.pop(oldest, None)
        _image_cache[hash_hex] = {
            "raw": raw, "width": width, "height": height,
            "filename": filename,
        }


# ---------------------------------------------------------------------------
#  State reads
# ---------------------------------------------------------------------------

def _fetch_active_state() -> dict:
    """Return the daemon's `comfy_status` data payload, or {} on failure."""
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception:
        return {}
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return {}
    return resp.get("data") or {}


# ---------------------------------------------------------------------------
#  Workflow graph loading
# ---------------------------------------------------------------------------

def _load_workflow_graph(workflow_name: str) -> dict:
    """Return the API-format graph for `workflow_name`.

    Search order:

      1. `data/comfy_panel/workflows/<name>.json` — the source file.
      2. If that file is in ComfyUI UI format (has `nodes` or `links`
         keys), fall back to `data/convert/<name>.json`, which the
         daemon's `comfy_load` writes on first load.
      3. If the source is already API format, use it directly.

    Raises FileNotFoundError with an actionable message.
    """
    ui_path = _WORKFLOW_DIR / f"{workflow_name}.json"
    if not ui_path.is_file():
        # Defensive: maybe the name is already a path?
        candidate = Path(workflow_name).expanduser()
        if candidate.is_file():
            ui_path = candidate
        else:
            raise FileNotFoundError(
                f"workflow {workflow_name!r} not found in "
                f"{_WORKFLOW_DIR}.  If it was loaded from a custom "
                f"path via `comfy_load --name /abs/foo.json`, the "
                f"inpaint playground cannot locate it — reload it as "
                f"a slug from the workflows directory."
            )

    data = json.loads(ui_path.read_text(encoding="utf-8"))
    if "nodes" in data or "links" in data:
        # UI format — need the API conversion.
        api_path = _CONVERT_DIR / ui_path.name
        if not api_path.is_file():
            raise FileNotFoundError(
                f"workflow {workflow_name!r} is in ComfyUI UI format, "
                f"but no API conversion exists at {api_path}.  Load "
                f"the workflow in Krita once (which triggers "
                f"auto-conversion via comfy_load), or run "
                f"`kritomatic comfyui convert {ui_path}`."
            )
        data = json.loads(api_path.read_text(encoding="utf-8"))
    return data


# ---------------------------------------------------------------------------
#  Proxy
# ---------------------------------------------------------------------------

def _get_proxy() -> ProxyV2:
    """Return a running ProxyV2, starting it via the daemon if needed."""
    try:
        return ProxyV2.from_pid_file()
    except ProxyUnreachable:
        pass

    # Ask the daemon to auto-start the proxy.  `comfy_connect` does
    # this as a prelude; `check: False` skips the /object_info call.
    try:
        resp = krita_client.send(
            {"type": "comfy_connect", "check": False}, timeout=20.0,
        )
    except Exception as e:
        raise RuntimeError(
            f"proxy not running and could not reach the daemon to "
            f"start it: {e}"
        ) from e
    if not isinstance(resp, dict) or resp.get("status") != "success":
        msg = (resp or {}).get("message", "daemon rejected comfy_connect")
        raise RuntimeError(
            f"proxy not running and daemon could not start it: {msg}"
        )

    try:
        return ProxyV2.from_pid_file()
    except ProxyUnreachable as e:
        raise RuntimeError(
            f"daemon reported success but proxy still unreachable: {e}"
        ) from e


# ---------------------------------------------------------------------------
#  /comfy/status, /comfy/active_state, /comfy/current_prompt
# ---------------------------------------------------------------------------

def handle_status(_payload) -> dict:
    try:
        client = get_client(COMFY_HOST, COMFY_PORT)
        info = client.object_info()
    except ComfyError as e:
        reset_client()
        return {
            "success": True,
            "reachable": False,
            "message": f"ComfyUI unreachable at {COMFY_HOST}:{COMFY_PORT}: {e}",
        }

    has_tooling = "ETN_LoadImageCache" in info
    return {
        "success": True,
        "reachable": True,
        "node_count": len(info),
        "has_tooling": has_tooling,
    }


def handle_active_state(_payload) -> dict:
    data = _fetch_active_state()
    return {"success": True, "data": data}


def handle_current_prompt(_payload) -> dict:
    """Return the prompt currently stored in the daemon's _active_config.

    Used by the playground on boot to hydrate its prompt field.  See
    the module docstring, PROMPT RESOLUTION.
    """
    data = _fetch_active_state()
    cfg = data.get("config")
    if not isinstance(cfg, dict):
        return {"success": True, "prompt": ""}
    params = cfg.get("params")
    if not isinstance(params, dict):
        return {"success": True, "prompt": ""}
    value = params.get("prompt")
    return {
        "success": True,
        "prompt": value if isinstance(value, str) else "",
    }


# ---------------------------------------------------------------------------
#  /comfy/upload_image (kept for parity; not used by the inpaint flow)
# ---------------------------------------------------------------------------

def handle_upload(headers: dict, body: bytes) -> dict:
    if not body:
        return {"success": False, "message": "empty upload"}

    raw_filename = headers.get("X-Filename", "") or headers.get("x-filename", "")
    filename = unquote(raw_filename) if raw_filename else "image"

    try:
        encoded = make_encoded_image(body)
    except Exception as e:
        return {"success": False, "message": f"could not decode image: {e}"}

    _cache_image(encoded.content_hash, body, encoded.width, encoded.height, filename)
    return {
        "success": True,
        "hash": encoded.content_hash,
        "width": encoded.width,
        "height": encoded.height,
        "filename": filename,
    }


# ---------------------------------------------------------------------------
#  /comfy/inpaint — the main route
# ---------------------------------------------------------------------------

def handle_inpaint(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    if not _inpaint_lock.acquire(blocking=False):
        return {"success": False,
                "message": "an inpaint is already in progress"}

    try:
        return _handle_inpaint_locked(payload)
    finally:
        _inpaint_lock.release()


def _handle_inpaint_locked(payload: dict) -> dict:
    global _current_job_id, _current_batch_id

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")
    context_b64   = payload.get("context_b64")
    prompt        = payload.get("prompt", "") or ""
    explicit_wf   = payload.get("workflow")

    if not canvas_b64 or not selection_b64 or not context_b64:
        return {"success": False,
                "message": "canvas_b64, selection_b64, context_b64 are required"}

    try:
        timeout = float(payload.get("timeout", INPAINT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = INPAINT_TIMEOUT

    try:
        output_rotation = float(payload.get("output_rotation") or 0.0)
    except (TypeError, ValueError):
        output_rotation = 0.0

    # --- workflow + state resolution ---
    state = _fetch_active_state()
    current_wf = state.get("workflow")
    if explicit_wf:
        workflow = explicit_wf
    elif isinstance(current_wf, str) and current_wf:
        workflow = current_wf
    else:
        return {
            "success": False,
            "message": (
                "Cannot determine active workflow (no workflow "
                "loaded).  Load one first: in the docker's Workflow "
                "picker, or via `kritomatic comfyui comfy_load "
                "--name <name>`.  Or pass an explicit `workflow` in "
                "the request body."
            ),
        }

    resource_slots = state.get("workflow_resource_slots") or {}
    param_slots    = state.get("workflow_param_slots") or {}
    active_config  = state.get("config") or {}

    if not resource_slots:
        return {
            "success": False,
            "message": (
                f"workflow {workflow!r} has no native resource slots.  "
                f"Either it uses the old tooling-nodes `ETN_*` nodes "
                f"(not supported by the inpaint playground), or slot "
                f"extraction failed (check `comfy_status`'s "
                f"workflow_slot_error)."
            ),
        }

    # --- load workflow graph (UI → API as needed) ---
    try:
        graph = _load_workflow_graph(workflow)
    except FileNotFoundError as e:
        return {"success": False, "message": str(e)}
    except json.JSONDecodeError as e:
        return {"success": False,
                "message": f"workflow JSON malformed: {e}"}

    # --- ensure proxy, upload pixels ---
    try:
        proxy = _get_proxy()
    except RuntimeError as e:
        return {"success": False, "message": str(e)}

    asset_refs: dict = {}
    for name, b64 in (
        ("@thecanvas",    canvas_b64),
        ("@theselection", selection_b64),
        ("@thecontext",   context_b64),
    ):
        if name not in resource_slots:
            # The workflow doesn't reference this slot; skip upload.
            continue
        try:
            png_bytes = base64.b64decode(b64)
        except Exception as e:
            return {"success": False,
                    "message": f"{name}: bad base64: {e}"}
        slug = name.lstrip("@") or "resource"
        file_path = f"input/inpaint_{slug}.png"
        try:
            asset = proxy.upload_asset(png_bytes, file_path, "image/png")
        except ProxyError as e:
            return {"success": False,
                    "message": f"{name} upload failed: {e}"}
        asset_refs[name] = asset

    # --- param values ---
    #
    # Everything from the shared config flows through; a non-empty
    # playground prompt overwrites the shared `prompt`.  Seed is
    # randomized per run, matching the daemon's `comfy_generate`
    # behavior.  See the module docstring.
    param_values = dict(active_config.get("params") or {})
    if prompt and prompt.strip():
        param_values["prompt"] = prompt
    base_seed = int(time.time() * 1000) & 0xFFFFFFFF

    try:
        substituted = substitute_into_graph(
            graph, resource_slots, param_slots,
            asset_refs, param_values,
            seed=base_seed if "seed" in param_slots else None,
        )
    except ValueError as e:
        return {"success": False,
                "message": f"graph substitution failed: {e}"}

    # --- submit ---
    try:
        job = proxy.submit_job(substituted)
    except ProxyHTTPError as e:
        return {"success": False,
                "message": f"submit failed: {e.code or e.status}: {e.message}",
                "details": e.details}
    except ProxyUnreachable as e:
        return {"success": False,
                "message": f"proxy unreachable during submit: {e}"}

    job_id = job.get("id")
    if not job_id:
        return {"success": False,
                "message": f"submit returned no job id: {job!r}"}

    batch_id = f"inpaint_{int(time.time()*1000)}"
    with _job_lock:
        _current_job_id = job_id
        _current_batch_id = batch_id

    _log(f"inpaint batch {batch_id}: workflow={workflow} "
         f"job={job_id} assets={list(asset_refs)} "
         f"prompt={(param_values.get('prompt') or '')[:40]!r} "
         f"seed={base_seed} output_rotation={output_rotation} "
         f"timeout={timeout}s")

    # Register with the daemon so comfy_batches / comfy_status see it.
    try:
        krita_client.send({
            "type": "comfy_register_external_job",
            "batch_id": batch_id,
            "job_id": job_id,
            "workflow": workflow,
        }, timeout=5.0)
    except Exception as e:
        _log(f"comfy_register_external_job failed (non-fatal): {e}")

    t0 = time.monotonic()
    final = None
    deadline = t0 + timeout
    try:
        while time.monotonic() < deadline:
            try:
                final = proxy.get_job(job_id)
            except ProxyUnreachable as e:
                _log(f"poll: proxy unreachable (will retry): {e}")
                time.sleep(POLL_INTERVAL_S)
                continue
            except ProxyHTTPError as e:
                if e.status == 404:
                    return {"success": False,
                            "message": "job expired before completion"}
                _log(f"poll: HTTP {e.status} (will retry): {e.message}")
                time.sleep(POLL_INTERVAL_S)
                continue
            status = final.get("status")
            if status in ("succeeded", "failed", "canceled", "expired"):
                break
            time.sleep(POLL_INTERVAL_S)
        else:
            # Deadline hit
            try:
                proxy.cancel_job(job_id)
            except Exception:
                pass
            return {"success": False,
                    "message": f"inpaint timed out after {timeout:.0f}s"}
    finally:
        with _job_lock:
            _current_job_id = None
            _current_batch_id = None
        try:
            krita_client.send({
                "type": "comfy_unregister_external_job",
                "batch_id": batch_id,
            }, timeout=5.0)
        except Exception:
            pass

    elapsed = time.monotonic() - t0

    if final is None:
        return {"success": False,
                "message": "poll returned no job state",
                "timing": round(elapsed, 3)}

    status = final.get("status")
    if status == "canceled":
        return {"success": False,
                "message": "cancelled",
                "timing": round(elapsed, 3),
                "cancelled": True}
    if status == "expired":
        return {"success": False,
                "message": "job expired upstream",
                "timing": round(elapsed, 3)}
    if status == "failed":
        err = final.get("error") or {}
        return {"success": False,
                "message": err.get("message") or "execution failed",
                "timing": round(elapsed, 3),
                "details": err}
    if status != "succeeded":
        return {"success": False,
                "message": f"unexpected terminal status: {status}",
                "timing": round(elapsed, 3)}

    # --- download first usable output ---
    outputs = [
        o for o in (final.get("outputs") or [])
        if o.get("type") and o["type"] != "temp" and o.get("id")
    ]
    if not outputs:
        return {"success": False,
                "message": "job succeeded but produced no retrievable "
                           "outputs (workflow has no SaveImage with "
                           "type=output?)",
                "timing": round(elapsed, 3)}

    try:
        png = proxy.download_asset(outputs[0]["id"])
    except ProxyHTTPError as e:
        return {"success": False,
                "message": f"download failed: {e.code or e.status}: {e.message}",
                "timing": round(elapsed, 3)}
    except ProxyUnreachable as e:
        return {"success": False,
                "message": f"proxy unreachable during download: {e}",
                "timing": round(elapsed, 3)}

    # --- paste into Krita ---
    #
    # The proxy pipeline bypassed the daemon's comfy_generate, which
    # was the only code path that ever pasted into Krita.  Paste
    # explicitly here.  Krita's paste_image applies
    # QTransform().rotate(rotation) and center-crops to the document's
    # dims before writing, so passing output_rotation un-rotates the
    # rotated-frame result and lands it in the right place.
    paste_cfg = (active_config.get("paste") or {})
    try:
        paste_x = int(paste_cfg.get("x", 0) or 0)
        paste_y = int(paste_cfg.get("y", 0) or 0)
    except (TypeError, ValueError):
        paste_x, paste_y = 0, 0
    paste_name = paste_cfg.get("name") or ""

    paste_ok = False
    paste_msg = ""
    paste_layer_name = ""
    try:
        paste_resp = krita_client.send({
            "type": "comfy_paste_image_b64",
            "name": paste_name,
            "image_b64": base64.b64encode(png).decode("ascii"),
            "x": paste_x,
            "y": paste_y,
            "rotation": output_rotation,
            "blend_mode": "normal",
        }, timeout=60.0)
        if (isinstance(paste_resp, dict)
                and paste_resp.get("status") == "success"):
            paste_ok = True
            paste_layer_name = (
                (paste_resp.get("data") or {}).get("layer_name") or ""
            )
            paste_msg = paste_resp.get("message") or "pasted"
        else:
            paste_msg = (paste_resp or {}).get("message",
                                                "daemon rejected paste")
    except Exception as e:
        paste_msg = f"paste request failed: {e}"

    _log(f"inpaint batch {batch_id}: done in {elapsed:.2f}s "
         f"paste_ok={paste_ok} layer={paste_layer_name!r}")

    return {
        "success": True,
        "image_b64": base64.b64encode(png).decode("ascii"),
        "timing": round(elapsed, 3),
        "job_id": job_id,
        "paste_ok": paste_ok,
        "paste_layer_name": paste_layer_name,
        "paste_message": paste_msg,
    }


# ---------------------------------------------------------------------------
#  /comfy/interrupt — cancel the current inpaint job via ProxyV2
# ---------------------------------------------------------------------------

def handle_interrupt(_payload) -> dict:
    """Cancel the currently-running inpaint, if any.

    Under this architecture, the inpaint submit path uses `ProxyV2`
    directly, so cancellation goes straight to
    `ProxyV2.cancel_job`.  The client's poll loop (inside
    `_handle_inpaint_locked`) sees the job transition to `canceled`
    and returns; the client's Cancel button POSTs here.  No daemon
    round-trip needed.
    """
    with _job_lock:
        job_id = _current_job_id
    if not job_id:
        return {"success": True, "message": "no inpaint in progress"}
    try:
        proxy = ProxyV2.from_pid_file()
    except ProxyUnreachable as e:
        return {"success": False, "message": str(e)}
    try:
        proxy.cancel_job(job_id)
    except ProxyError as e:
        return {"success": False, "message": str(e)}
    return {"success": True, "job_id": job_id}


# ---------------------------------------------------------------------------
#  /krita/* — unchanged
# ---------------------------------------------------------------------------

def handle_krita_status(_payload) -> dict:
    reachable = krita_client.is_reachable()
    return {"success": True, "reachable": reachable}


def handle_krita_apply(payload: dict) -> dict:
    mask_b64 = payload.get("mask_b64")
    if not mask_b64:
        return {"success": False, "message": "no mask_b64"}
    return {"success": False,
            "message": "krita/apply not implemented in this playground"}


def handle_krita_import(payload: dict) -> dict:
    name = payload.get("name") if isinstance(payload, dict) else None
    command = {"type": "get_document_composite_b64"}
    if name:
        command["name"] = name

    response = krita_client.send(command, timeout=60.0)
    if response.get("status") != "success":
        return {"success": False,
                "message": response.get("message") or "daemon rejected"}

    data = response.get("data") or {}
    data_url = data.get("data_url")
    if not data_url:
        return {"success": False, "message": "daemon returned no data_url"}
    if not data_url.startswith("data:image/png;base64,"):
        return {"success": False,
                "message": f"unexpected data_url prefix: {data_url[:30]!r}"}

    try:
        png_bytes = base64.b64decode(data_url.split(",", 1)[1])
    except Exception as e:
        return {"success": False, "message": f"could not decode base64: {e}"}

    try:
        encoded = make_encoded_image(png_bytes)
    except Exception as e:
        return {"success": False, "message": f"could not decode image: {e}"}

    h = encoded.content_hash
    filename = data.get("name") or "krita_composite"
    _cache_image(h, png_bytes, encoded.width, encoded.height, filename)

    return {
        "success": True,
        "hash": h,
        "width": encoded.width,
        "height": encoded.height,
        "data_url": data_url,
        "filename": filename,
    }


def handle_krita_import_masks(payload: dict) -> dict:
    command = {"type": "import_krita_masks_b64"}
    if isinstance(payload, dict):
        if "layer_names" in payload and payload["layer_names"]:
            command["layer_names"] = str(payload["layer_names"])
        if "include_paint_alpha" in payload:
            command["include_paint_alpha"] = bool(payload["include_paint_alpha"])
        if "prefer_selection_mask_children" in payload:
            command["prefer_selection_mask_children"] = bool(
                payload["prefer_selection_mask_children"]
            )

    response = krita_client.send(command, timeout=60.0)
    if response.get("status") != "success":
        return {
            "success": False,
            "message": response.get("message") or "daemon rejected",
        }

    data = response.get("data") or {}
    masks = data.get("masks") or []
    skipped = data.get("skipped") or []
    selection_path = data.get("selection_path") or "unknown"

    return {
        "success": True,
        "masks": masks,
        "skipped": skipped,
        "selection_path": selection_path,
        "message": response.get("message") or f"{len(masks)} mask(s)",
    }


def handle_resource_image(payload: dict) -> dict:
    """Resolve a `{source, layer_name}` resource spec to a PNG data URL."""
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    source = payload.get("source", "active")
    layer_name = payload.get("layer_name")

    if source == "active":
        return handle_krita_import({})
    if source == "layer":
        if not layer_name:
            return {"success": False,
                    "message": "layer_name is required when source=layer"}
        response = krita_client.send(
            {"type": "get_layer_pixels_b64", "name": str(layer_name)},
            timeout=30.0,
        )
        if response.get("status") != "success":
            return {"success": False,
                    "message": response.get("message") or "daemon rejected"}
        data = response.get("data") or {}
        data_url = data.get("data_url")
        if not data_url:
            return {"success": False,
                    "message": "daemon returned no data_url for layer"}
        return {
            "success": True,
            "hash": "layer_" + str(layer_name),
            "width": data.get("width"),
            "height": data.get("height"),
            "data_url": data_url,
            "filename": str(layer_name),
        }
    return {
        "success": False,
        "message": (
            f"source={source!r} cannot be displayed as an image "
            f"(@thecanvas expects 'active' or 'layer')."
        ),
    }


ROUTES = {
    "/comfy/status":         handle_status,
    "/comfy/active_state":   handle_active_state,
    "/comfy/current_prompt": handle_current_prompt,
    "/comfy/inpaint":        handle_inpaint,
    "/comfy/interrupt":      handle_interrupt,
    "/krita/status":         handle_krita_status,
    "/krita/apply":          handle_krita_apply,
    "/krita/import":         handle_krita_import,
    "/krita/import_masks":   handle_krita_import_masks,
    "/krita/resource_image": handle_resource_image,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}
