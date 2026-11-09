"""HTTP routes and ComfyUI/daemon bridge for the inpaint playground.

Routes:

  POST /comfy/upload_image   raw body, X-Filename header → {hash, width, height}
  POST /comfy/inpaint        run NEWTekakutli45 with inline pixel blobs
  POST /comfy/interrupt      forward POST /interrupt to ComfyUI
  GET  /comfy/status         preflight (ComfyUI reachability)

  GET  /krita/status         kritomatic daemon reachable?
  POST /krita/apply          send a mask to Krita (unused by this playground)
  POST /krita/import         fetch active document composite from Krita
  POST /krita/import_masks   fetch selected mask or paint layer(s) as base64 masks

INPAINT TRANSPORT (since Stage 8)
=================================
`handle_inpaint` talks to comfy-api-proxy directly (comfy-api-proxy on
127.0.0.1:8189, the copy in pg_comfy/proxy_client.py), not through the
daemon's comfy_* command surface. The daemon still mediates Krita
document I/O (`/krita/*` routes), but generation happens entirely in
this process:

    1. Read the workflow JSON from `<repo>/data/comfy_panel/workflows/`.
    2. Extract `@`-resource slots (LoadImage/LoadImageMask image) and
       `Primitive*` param slots (_meta.title) from the graph.
    3. Upload canvas / selection / context as v2 assets via
       POST /api/v2/assets (blake3 dedup server-side).
    4. Fetch the daemon's active panel config so params set in the
       docker (denoise, Illustrous?, Hyper?, etc.) carry over to the
       inpaint. The `prompt` and `seed` are playground-owned and
       override whatever the daemon has.
    5. Substitute core/ASSET refs and the merged params into a
       two-level copy of the graph.
    6. Register the proxy job with the daemon
       (comfy_register_external_job) so comfy_status's active_batches
       and comfy_cancel can see it. Unregister in a finally block
       around the poll + download.
    7. Submit via POST /api/v2/jobs, poll GET /api/v2/jobs/{id}.
    8. Download each `type == "output"` entry via
       GET /api/v2/assets/{id}/content.
    9. Apply `-output_rotation` (the inverse of the client's display
       rotation) to the downloaded PNG.
   10. Return the rotated PNG as base64.

The rotation is applied server-side because the client sent the blobs
in the *rotated* frame (`_buildInpaintPayload` rotates the canvas and
mask by `view.rotation` when `inpaintEnabled` is true). Rotating the
result back to original orientation is what the client expects to
receive as the "new original" (`_setOriginalFromB64` on the JS side).
The client re-applies `view.rotation` for display, so the pasted
result appears in the correct location on screen.

The workflow's own InpaintCropImproved nodes handle cropping to the
selection bbox ∪ context mask. The server does NOT crop — it uploads
the full (rotated, W × H) canvas and lets the workflow crop internally.

Corner caveat: when `output_rotation != 0`, the (W × H) result is
rotated with `expand=False`, so the four corners of the rotated frame
are lost. Same limitation the earlier v1 flow had.

PARAM AND CANCEL SHARING WITH THE DOCKER
========================================
The playground drives generation itself, but it must not feel like a
separate universe from the docker. Two wiring details ensure that:

  · **Params.** Before substitution, `handle_inpaint` fetches
    `comfy_status` from the daemon and copies `config.params` into the
    playground's starting param map. `prompt` and `seed` are then
    overwritten by the playground's own values. The effect: any param
    the docker has applied (via `comfy_apply_config` from the Run
    button, or from the CLI's `panel run`) carries over to the next
    playground inpaint. Params the daemon has no opinion about fall
    through to the workflow's baked-in defaults, same as before.

  · **Cancel.** Every submitted proxy job is registered with the daemon
    via `comfy_register_external_job` immediately after `submit_job`.
    The daemon creates a `_BatchRecord` with the proxy job id under
    `external_job_ids`, so `comfy_status.active_batches` reflects the
    job and the docker's Cancel-All button enables. When the user
    clicks Cancel-All (or the CLI runs `panel cancel --all`), the
    daemon's `comfy_cancel` sees `external_job_ids` and routes to
    `ProxyV2.cancel_job` on each id. The playground's own poll loop
    then sees the proxy transition the job to `canceled` — no
    cooperation needed from the daemon beyond the forward. A
    `comfy_unregister_external_job` in a finally block cleans up on
    every exit path (success, poll failure, download failure,
    exception).
"""

from __future__ import annotations

import base64
import json
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

from pg_comfy import (
    ComfyClient, ComfyError,
    get_client, reset_client, cached_object_info,
    ProxyV2, ProxyHTTPError, ProxyUnreachable,
)
from pg_comfy.image import make_encoded_image

from . import krita_client


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188

DEFAULT_WORKFLOW = "NEWTekakutli45"

INPAINT_TIMEOUT = 180.0

# Placeholder image bytes for `handle_upload`, unchanged.
_IMAGE_CACHE_MAX = 8
_image_cache: dict = {}
_image_cache_lock = __import__("threading").Lock()


def _log(msg: str) -> None:
    print(f"[inpaint] {msg}", flush=True)


_WORKFLOW_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "data" / "comfy_panel" / "workflows"
)


# ---------------------------------------------------------------------------
#  Base64 PNG helpers (unchanged from the v1 path)
# ---------------------------------------------------------------------------

def _decode_b64_png(data_url_or_b64: str) -> Image.Image:
    s = data_url_or_b64
    if "," in s:
        s = s.split(",", 1)[1]
    return Image.open(BytesIO(base64.b64decode(s)))


def _encode_b64_png(img: Image.Image) -> str:
    buf = BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


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
#  Graph slot extraction
# ---------------------------------------------------------------------------
#
# Mirrors the daemon's comfyui_inspect extractors. Kept as a local
# duplicate because the playground cannot import kritomatic_daemon
# (that package imports krita at import time).

_RESOURCE_NODE_FIELDS = {
    "LoadImage": "image",
    "LoadImageMask": "image",
}

_PARAM_NODE_CLASSES = {
    "PrimitiveFloat": "float",
    "PrimitiveInt": "int",
    "PrimitiveString": "string",
    "PrimitiveBoolean": "boolean",
}


def _extract_resource_slots(graph: dict) -> dict:
    out: dict = {}
    for node_id, node in graph.items():
        cls = node.get("class_type", "")
        field = _RESOURCE_NODE_FIELDS.get(cls)
        if field is None:
            continue
        inputs = node.get("inputs", {}) or {}
        value = inputs.get(field)
        if not isinstance(value, str) or not value.startswith("@"):
            continue
        out[value] = {
            "node_id": node_id,
            "input_field": field,
            "kind": "mask" if cls == "LoadImageMask" else "image",
        }
    return out


def _extract_param_slots(graph: dict) -> dict:
    out: dict = {}
    for node_id, node in graph.items():
        cls = node.get("class_type", "")
        kind = _PARAM_NODE_CLASSES.get(cls)
        if kind is None:
            continue
        title = (node.get("_meta") or {}).get("title", "")
        if not isinstance(title, str) or not title.strip():
            continue
        out[title] = {"node_id": node_id, "kind": kind}
    return out


def _substitute_into_graph(graph: dict,
                           resource_slots: dict,
                           param_slots: dict,
                           asset_refs: dict,
                           param_values: dict) -> dict:
    """Two-level copy + substitutions. Same rules as the daemon's
    comfy_apply._substitute_into_graph, minus seed handling (the
    playground wants per-run random seeds, which it passes in via
    `param_values["seed"]`)."""
    prepared = {
        node_id: {
            **node,
            "inputs": dict(node.get("inputs") or {}),
        }
        for node_id, node in graph.items()
    }

    for title, value in param_values.items():
        slot = param_slots.get(title)
        if slot is None:
            continue
        prepared[slot["node_id"]]["inputs"]["value"] = value

    for name, asset in asset_refs.items():
        slot = resource_slots.get(name)
        if slot is None:
            continue
        prepared[slot["node_id"]]["inputs"][slot["input_field"]] = {
            "__type": "core/ASSET",
            "info": {"id": asset["id"]},
        }

    return prepared


# ---------------------------------------------------------------------------
#  /comfy/status  (unchanged)
# ---------------------------------------------------------------------------

def handle_status(_payload) -> dict:
    try:
        client = get_client(COMFY_HOST, COMFY_PORT)
        info = cached_object_info(client)
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


# ---------------------------------------------------------------------------
#  /comfy/upload_image  (unchanged)
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
#  /comfy/inpaint — the rewritten route
# ---------------------------------------------------------------------------

# The playground always sends exactly these three payload keys. Each
# maps to the workflow's @-name that receives it. The client's
# `_buildInpaintPayload` is the sole producer; the two must stay in sync.
_RESOURCE_PAYLOAD_KEYS = (
    ("@thecanvas",    "canvas_b64"),
    ("@theselection", "selection_b64"),
    ("@thecontext",   "context_b64"),
)


def _fetch_daemon_params() -> dict:
    """Return the daemon's current `_active_config["params"]` dict.

    Falls back to `{}` on any failure: daemon unreachable, no config
    applied, or the status response missing `config`. Non-fatal —
    the inpaint still works, it just doesn't inherit the docker's
    applied params. The caller overlays `prompt` and `seed` on top of
    whatever this returns, so an empty result is safe.
    """
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception:
        return {}
    if not isinstance(resp, dict) or resp.get("status") != "success":
        return {}
    cfg = (resp.get("data") or {}).get("config") or {}
    params = cfg.get("params") if isinstance(cfg, dict) else None
    return dict(params) if isinstance(params, dict) else {}


def _register_external_job(batch_id: str, job_id: str, workflow: str) -> None:
    """Tell the daemon to track this proxy job. Best-effort — a
    registration failure only means the docker's Cancel-All won't see
    this run; the inpaint itself is unaffected.
    """
    try:
        krita_client.send({
            "type": "comfy_register_external_job",
            "batch_id": batch_id,
            "job_id": job_id,
            "workflow": workflow,
        }, timeout=10.0)
    except Exception:
        pass


def _unregister_external_job(batch_id: str) -> None:
    try:
        krita_client.send({
            "type": "comfy_unregister_external_job",
            "batch_id": batch_id,
        }, timeout=5.0)
    except Exception:
        pass


def handle_inpaint(payload: dict) -> dict:
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    canvas_b64    = payload.get("canvas_b64")
    selection_b64 = payload.get("selection_b64")
    context_b64   = payload.get("context_b64")
    prompt        = payload.get("prompt", "") or ""
    workflow      = payload.get("workflow") or DEFAULT_WORKFLOW

    try:
        timeout = float(payload.get("timeout", INPAINT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = INPAINT_TIMEOUT

    try:
        output_rotation = float(payload.get("output_rotation") or 0.0)
    except (TypeError, ValueError):
        output_rotation = 0.0

    if not canvas_b64 or not selection_b64 or not context_b64:
        return {"success": False,
                "message": "canvas_b64, selection_b64, context_b64 are required"}

    t0 = time.monotonic()

    # ---- 1. Decode the client PNGs ----
    try:
        images = {
            "@thecanvas":    _decode_b64_png(canvas_b64).convert("RGBA"),
            "@theselection": _decode_b64_png(selection_b64).convert("RGBA"),
            "@thecontext":   _decode_b64_png(context_b64).convert("RGBA"),
        }
    except Exception as e:
        return {"success": False, "message": f"could not decode inputs: {e}"}

    # ---- 2. Read the workflow JSON ----
    workflow_path = _WORKFLOW_DIR / f"{workflow}.json"
    if not workflow_path.is_file():
        return {"success": False,
                "message": f"workflow not found: {workflow_path}"}
    try:
        graph = json.loads(workflow_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {"success": False,
                "message": f"invalid workflow JSON at {workflow_path}: {e}"}

    # ---- 3. Extract slots ----
    try:
        resource_slots = _extract_resource_slots(graph)
        param_slots = _extract_param_slots(graph)
    except ValueError as e:
        return {"success": False,
                "message": f"workflow slot extraction failed: {e}"}

    missing = [name for name, _ in _RESOURCE_PAYLOAD_KEYS
               if name not in resource_slots]
    if missing:
        return {
            "success": False,
            "message": (
                f"workflow {workflow!r} is missing resource slots: "
                f"{missing}. This playground expects a native-v2 workflow "
                f"(LoadImage/LoadImageMask with @-prefixed image values). "
                f"Migrate the workflow with `kritomatic comfyui panel "
                f"print {workflow}` after Stage 7's JSON migration, or "
                f"check that {workflow_path} is the migrated version."
            ),
        }

    # ---- 4. Upload each blob as a v2 asset ----
    try:
        proxy = ProxyV2.from_pid_file()
    except ProxyUnreachable as e:
        return {"success": False,
                "message": f"proxy unreachable: {e}"}

    asset_refs: dict = {}
    for name, _payload_key in _RESOURCE_PAYLOAD_KEYS:
        img = images[name]
        buf = BytesIO()
        img.save(buf, "PNG")
        slug = name.lstrip("@") or "resource"
        try:
            asset = proxy.upload_asset(
                buf.getvalue(), f"input/{slug}.png", "image/png",
            )
        except (ProxyHTTPError, ProxyUnreachable) as e:
            return {"success": False,
                    "message": f"upload of {name!r} failed: {e}"}
        asset_refs[name] = asset

    # ---- 5. Build param map: docker's applied params, then override ----
    #
    # The daemon's `_active_config["params"]` is what the docker's Run
    # button (or the CLI's `panel run`) last applied. Reading it here
    # means any checkbox, slider, or numeric field the user set in the
    # docker carries over to the playground's inpaint — without the
    # playground having to know which params exist on which workflow.
    #
    # `prompt` and `seed` are playground-owned by design: the prompt is
    # what the user typed in the playground's own textarea, and the
    # seed must be fresh per run (the daemon's config never stores a
    # seed — that's a top-level field, not a `[params]` key — so
    # `daemon_params.get("seed")` is normally absent anyway; the
    # override is for the rare case where a workflow has a Primitive
    # titled "seed" whose default leaked into the config via a
    # hand-edited TOML).
    daemon_params = _fetch_daemon_params()
    seed = int(time.time() * 1000) & 0xFFFFFFFF
    param_values = dict(daemon_params)
    param_values["prompt"] = prompt
    param_values["seed"] = seed
    # Drop anything the loaded workflow doesn't actually expose as a
    # param slot. Avoids passing unknown titles to _substitute_into_graph
    # (which no-ops on them anyway, but the log line is clearer).
    param_values = {k: v for k, v in param_values.items() if k in param_slots}

    prepared = _substitute_into_graph(
        graph, resource_slots, param_slots, asset_refs, param_values,
    )

    _log(f"inpaint: workflow={workflow!r} prompt={prompt[:40]!r} "
         f"seed={seed} params={sorted(param_values)} "
         f"resources={list(asset_refs)}")

    # ---- 6. Submit ----
    try:
        job = proxy.submit_job(prepared)
    except ProxyHTTPError as e:
        return {"success": False,
                "message": f"submit failed: {e} (code={e.code})"}
    except ProxyUnreachable as e:
        return {"success": False, "message": f"submit failed: {e}"}

    job_id = job["id"]
    batch_id = f"inpaint_{job_id}"

    # ---- 7. Register with the daemon, poll, download, unregister ----
    #
    # Registration is what makes the docker's Cancel-All see this job.
    # Unregistration in the finally block guarantees the daemon's
    # `_batches` map is cleaned up on every exit path — success,
    # cancellation, download failure, or an unexpected exception. The
    # finally fires before the early returns propagate to the caller,
    # so the docker's state event with the updated active_batches will
    # already have been published by the time the user sees the
    # playground's result.
    _register_external_job(batch_id, job_id, workflow)

    try:
        deadline = time.monotonic() + timeout + 60.0
        final = job
        while time.monotonic() < deadline:
            try:
                final = proxy.get_job(job_id)
            except ProxyUnreachable as e:
                return {"success": False, "message": f"poll failed: {e}"}
            status = final.get("status")
            if status in ("succeeded", "failed", "canceled", "expired"):
                break
            time.sleep(0.25)

        if final.get("status") != "succeeded":
            err = (final.get("error") or {}).get("message") or final.get("status")
            return {"success": False,
                    "message": f"job {final.get('status')}: {err}"}

        outputs = [
            o for o in (final.get("outputs") or [])
            if o.get("type") and o["type"] != "temp" and o.get("id")
        ]
        if not outputs:
            return {"success": False,
                    "message": "job succeeded but produced no retrievable outputs"}

        try:
            png_bytes = proxy.download_asset(outputs[0]["id"])
        except (ProxyHTTPError, ProxyUnreachable) as e:
            return {"success": False, "message": f"download failed: {e}"}
    finally:
        _unregister_external_job(batch_id)

    # ---- 8. Un-rotate back to original orientation ----
    if output_rotation:
        try:
            img = Image.open(BytesIO(png_bytes))
            rotated = img.rotate(
                -output_rotation, expand=False,
                resample=Image.Resampling.BICUBIC,
            )
            buf = BytesIO()
            rotated.save(buf, "PNG")
            png_bytes = buf.getvalue()
        except Exception as e:
            return {"success": False,
                    "message": f"output rotation failed: {e}"}

    elapsed = time.monotonic() - t0
    _log(f"inpaint done in {elapsed:.2f}s (job {job_id[:8]})")

    return {
        "success": True,
        "image_b64": base64.b64encode(png_bytes).decode("ascii"),
        "timing": round(elapsed, 3),
    }


# ---------------------------------------------------------------------------
#  /comfy/interrupt  (unchanged)
# ---------------------------------------------------------------------------

def handle_interrupt(_payload) -> dict:
    try:
        client = get_client(COMFY_HOST, COMFY_PORT)
        client.interrupt()
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
#  /krita/*  (unchanged)
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


ROUTES = {
    "/comfy/status":        handle_status,
    "/comfy/inpaint":       handle_inpaint,
    "/comfy/interrupt":     handle_interrupt,
    "/krita/status":        handle_krita_status,
    "/krita/apply":         handle_krita_apply,
    "/krita/import":        handle_krita_import,
    "/krita/import_masks":  handle_krita_import_masks,
}

RAW_ROUTES = {
    "/comfy/upload_image": handle_upload,
}