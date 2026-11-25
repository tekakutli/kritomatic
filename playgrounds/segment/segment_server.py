"""HTTP routes and ComfyUI bridge for the segment playground.

Routes:

  POST /comfy/status         ComfyUI + proxy preflight
  POST /comfy/active_state   proxy the daemon's `comfy_status`
  POST /comfy/segment        Segment All  → SAM3 detection, no mask
  POST /comfy/refine         Refine mask → SAM2 (true refine) or SAM3 (re-detect)
  POST /comfy/interrupt      forward POST /interrupt to ComfyUI

  POST /version              liveness probe for the daemon's sweep
  POST /playground_reregister single-shot re-registration with the daemon

  POST /krita/status         kritomatic daemon reachable?
  POST /krita/apply          send a mask to Krita
  POST /krita/import         fetch active document composite (used by resource_image source=active)
  POST /krita/import_masks   fetch selected mask or paint layer(s)
  POST /krita/resource_image resolve a `{source, layer_name}` spec to a PNG
  POST /rebind_document      clear pinned doc + re-capture from activeDocument()

EXECUTION MODEL (native v2)
===========================
Segment keeps its own execution path — it does NOT delegate to the
daemon's `comfy_generate`.  The pipeline is:

  browser → POST /comfy/segment (hash + points + params)
    → look up the cached PNG under that hash
    → upload it to the comfy-api-proxy via POST /api/v2/assets
    → substitute `@thesegment` and each `Primitive*.value` into the
      workflow with `substitute_into_graph`
    → POST /api/v2/jobs
    → poll GET /api/v2/jobs/{id} to terminal
    → download each output via GET /api/v2/assets/{id}/content
    → return base64 PNGs to the browser

The old path — `expand_custom` + `ETN_Parameter` + `ETN_KritaCanvas`
+ `ETN_KritaOutput` + `PUT /api/etn/image/{id}` + `ComfyClient.generate`
— is gone.  See `kritomatic_shared/comfyui_substitute.py` and
`pg_comfy/__init__.py` for the native-v2 primitives this module now
composes.

The workflows are in native ComfyUI v2 form (`LoadImage` with
`@`-prefixed `image`, `Primitive*` with `_meta.title`, `SaveImage`
at the terminal).  See `workflows/segment.json` and
`workflows/sam2_refine.json`.

LAZY `pg_comfy.image` IMPORT
============================
`pg_comfy.image` pulls PIL, ~30 MB of RSS and ~500 ms of import time.
This module does not touch image encoding at import time, so
`make_encoded_image` is imported lazily inside the handlers that use
it (`handle_krita_import`, `handle_resource_image`).  The segment
pipeline itself does not need PIL — the PNG bytes it downloads come
back from the proxy already in the format `download_outputs` expects.
"""

from __future__ import annotations

import base64
import json
import time
import traceback
from pathlib import Path
from threading import Lock
from typing import Optional
from urllib.parse import unquote

from pg_comfy import (
    ComfyClient, ComfyError, ComfyTimeout, ComfyCancelled,
    EncodedImage, get_client, reset_client, cached_object_info,
    ProxyV2, ProxyError, ProxyHTTPError, ProxyUnreachable,
    extract_all_slots,
    substitute_into_graph,
)

from pgruntime import daemon_client as krita_client
from pgruntime.daemon_registry import reregister_handler
from pgruntime.daemon_routes import daemon_route_handlers
from pgruntime.ports import port_for


COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
GENERATE_TIMEOUT = 120.0

# Identity — mirrors inpaint's LAYER_SLOTS contract.
PLAYGROUND_ID   = "segment"
PLAYGROUND_NAME = "Segment"
PLAYGROUND_PORT = port_for(PLAYGROUND_ID)

LAYER_SLOTS = [
    {"id": "@thesegment"},
]

WORKFLOW_SEGMENT_PATH = Path(__file__).parent / "workflows" / "segment.json"
WORKFLOW_REFINE_PATH  = Path(__file__).parent / "workflows" / "sam2_refine.json"

_IMAGE_CACHE_MAX = 8

_image_cache: dict = {}
_image_cache_lock = Lock()

_workflow_lock = Lock()
_workflow_segment: Optional[dict] = None
_workflow_refine:  Optional[dict] = None

_slot_tables_lock = Lock()
_slot_tables_cache: dict = {}  # str(path) -> (resource_slots, param_slots, output_nodes)

_target_document_name: str = ""
_target_lock = Lock()

_proxy_lock = Lock()
_proxy: Optional[ProxyV2] = None


def _log(msg: str) -> None:
    print(f"[segment] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  Clients
# ---------------------------------------------------------------------------

_client: Optional[ComfyClient] = None


def _get_client() -> ComfyClient:
    global _client
    if _client is None:
        _client = get_client(COMFY_HOST, COMFY_PORT)
    return _client


def _reset_client() -> None:
    global _client
    reset_client()
    _client = None


def _get_proxy() -> ProxyV2:
    """Return the shared ProxyV2 client.

    Constructed lazily via `ProxyV2.from_pid_file()` — reads the pid
    file the daemon's `proxy/manager.py` writes on `comfy_proxy_start`
    and probes `/api/v2/health` before returning.  A missing pid file
    or a dead proxy raises `ProxyUnreachable`; the caller turns that
    into a "proxy unavailable" error response.
    """
    global _proxy
    with _proxy_lock:
        if _proxy is None:
            _proxy = ProxyV2.from_pid_file()
        return _proxy


def _invalidate_proxy() -> None:
    """Drop the cached proxy client.  Called after `ProxyUnreachable`
    so the next call rebuilds it from the (possibly new) pid file.
    A proxy restart between calls therefore recovers automatically
    on the next user action, without needing a manual refresh."""
    global _proxy
    with _proxy_lock:
        _proxy = None


# ---------------------------------------------------------------------------
#  Pinned-document helpers
# ---------------------------------------------------------------------------

def _capture_document_from_response(resp) -> None:
    global _target_document_name
    if not isinstance(resp, dict) or not resp.get("success"):
        return
    with _target_lock:
        if _target_document_name:
            return
        data = resp.get("data") or {}
        doc = data.get("document")
        if isinstance(doc, str) and doc:
            _target_document_name = doc
            _log(f"pinned target document: {doc!r}")


def _send_with_pin(command: dict, *,
                   doc_arg: str = "document",
                   timeout: float = 30.0) -> dict:
    global _target_document_name
    with _target_lock:
        pin = _target_document_name

    if pin:
        payload = {**command, doc_arg: pin}
        resp = krita_client.send(payload, timeout=timeout)
        msg = (resp.get("message") or "").lower()
        stale = (not resp.get("success")
                 and resp.get("status") != "success"
                 and "document" in msg)
        if not stale:
            _capture_document_from_response(resp)
            return resp
        with _target_lock:
            if _target_document_name == pin:
                _target_document_name = ""
                _log(f"pin {pin!r} stale; rebinding to active")

    resp = krita_client.send(command, timeout=timeout)
    _capture_document_from_response(resp)
    return resp


# ---------------------------------------------------------------------------
#  Image cache
# ---------------------------------------------------------------------------

def _cache_image(hash_hex: str, raw_bytes: bytes,
                 width: int, height: int, filename: str) -> None:
    with _image_cache_lock:
        if len(_image_cache) >= _IMAGE_CACHE_MAX:
            oldest = next(iter(_image_cache))
            _image_cache.pop(oldest, None)
        _image_cache[hash_hex] = {
            "raw": raw_bytes, "width": width, "height": height,
            "filename": filename,
        }


def _get_image(hash_hex: str) -> Optional[dict]:
    with _image_cache_lock:
        return _image_cache.get(hash_hex)


# ---------------------------------------------------------------------------
#  Workflow loading + slot tables
# ---------------------------------------------------------------------------

def _load_workflow(path: Path) -> Optional[dict]:
    if not path.is_file():
        return None
    try:
        wf = json.loads(path.read_text())
        _log(f"workflow loaded ({len(wf)} nodes): {path.name}")
        return wf
    except Exception as e:
        _log(f"failed to load {path}: {e}")
        return None


def _get_segment_workflow() -> Optional[dict]:
    global _workflow_segment
    with _workflow_lock:
        if _workflow_segment is None:
            _workflow_segment = _load_workflow(WORKFLOW_SEGMENT_PATH)
        return _workflow_segment


def _get_refine_workflow() -> Optional[dict]:
    global _workflow_refine
    with _workflow_lock:
        if _workflow_refine is None:
            _workflow_refine = _load_workflow(WORKFLOW_REFINE_PATH)
        return _workflow_refine


def _get_slot_tables(path: Path, workflow: dict):
    """Return `(resource_slots, param_slots, output_nodes)` for a
    workflow, cached by path.

    `extract_all_slots` is a pure function of the graph and cheap, but
    it walks every node on every call; caching means a hot `/comfy/segment`
    endpoint pays the cost once at first invocation, not per request.
    """
    key = str(path)
    with _slot_tables_lock:
        cached = _slot_tables_cache.get(key)
    if cached is not None:
        return cached
    result = extract_all_slots(workflow)
    with _slot_tables_lock:
        _slot_tables_cache[key] = result
    return result


_SAM3_NODE = "SAM3_Detect"
_SAM2_NODE = "Sam2Segmentation"


# ---------------------------------------------------------------------------
#  /comfy/status
# ---------------------------------------------------------------------------

def handle_status(_payload) -> dict:
    """Preflight: is ComfyUI reachable, are the SAM nodes installed,
    is the v2 proxy reachable.

    `has_sam3` / `has_sam2` drive the panel's status flash (see
    `pg_panel_bindings.js::_checkStatusAction`).  `proxy_reachable` is
    informational — the panel does not gate on it today, but
    surfacing the failure here means a user running the pipeline with
    a dead proxy sees "proxy unreachable" in the terminal log rather
    than only on the first segment/refine call.
    """
    client = _get_client()
    try:
        info = cached_object_info(client)
    except ComfyError as e:
        _log(f"status check: unreachable: {e}")
        _reset_client()
        return {
            "success": True,
            "reachable": False,
            "proxy_reachable": False,
            "message": f"ComfyUI unreachable at {COMFY_HOST}:{COMFY_PORT}: {e}",
        }

    seg_wf = _get_segment_workflow()
    ref_wf = _get_refine_workflow()

    proxy_reachable = True
    proxy_error = ""
    try:
        _get_proxy()
    except ProxyUnreachable as e:
        proxy_reachable = False
        proxy_error = str(e)

    return {
        "success": True,
        "reachable": True,
        "node_count": len(info),
        "has_sam3": _SAM3_NODE in info,
        "has_sam2": _SAM2_NODE in info,
        "proxy_reachable": proxy_reachable,
        "proxy_error": proxy_error,
        "segment_workflow_loaded": seg_wf is not None,
        "refine_workflow_loaded": ref_wf is not None,
        "segment_workflow_path": str(WORKFLOW_SEGMENT_PATH),
        "refine_workflow_path": str(WORKFLOW_REFINE_PATH),
    }


# ---------------------------------------------------------------------------
#  /version, /playground_reregister
# ---------------------------------------------------------------------------

def handle_version(_payload) -> dict:
    return {"success": True, "version": ""}


handle_playground_reregister = reregister_handler(
    PLAYGROUND_ID, PLAYGROUND_NAME, PLAYGROUND_PORT,
    layer_slots=LAYER_SLOTS,
)


# ---------------------------------------------------------------------------
#  v2 pipeline helpers
# ---------------------------------------------------------------------------

def _download_outputs(proxy: ProxyV2, job: dict) -> list:
    """Download every retrievable output of a terminal `Job` dict.

    Mirrors `kritomatic_daemon.handlers.comfyui_paste.download_outputs`,
    with the daemon-only imports dropped so the playground does not
    need `krita`.  Same filter (skip `type == "temp"`, require `id`),
    same tolerance (silently skip individual download failures — the
    caller decides whether zero results is a hard error).

    Returned entries are `{"png_bytes", "name", "node_id", "type"}`.
    """
    real_outputs = [
        o for o in (job.get("outputs") or [])
        if isinstance(o, dict) and o.get("type") and o["type"] != "temp"
        and o.get("id")
    ]
    downloaded = []
    for out in real_outputs:
        try:
            png = proxy.download_asset(out["id"])
        except ProxyHTTPError as e:
            if e.status == 404 and e.code == "output_unavailable":
                continue
            continue
        except ProxyUnreachable:
            continue
        downloaded.append({
            "png_bytes": png,
            "name":      out.get("name", ""),
            "node_id":   out.get("node_id", ""),
            "type":      out.get("type"),
        })
    return downloaded


_POLL_INTERVAL_S = 0.25
_MIN_EXEC_TIMEOUT_S = 300.0
_MAX_QUEUE_WAIT_S = 300.0
_MAX_TOTAL_POLL_S = 600.0


def _poll_until_done(proxy: ProxyV2, job_id: str, *,
                     timeout: float) -> Optional[dict]:
    """Poll one proxy job until it reaches a terminal status.

    Same budget structure as the daemon's `_poll_job`: a floor of
    `_MIN_EXEC_TIMEOUT_S` on the execution budget, a cap of
    `_MAX_QUEUE_WAIT_S` on the queue wait, a hard cap of
    `_MAX_TOTAL_POLL_S` on the total.  Returns the terminal Job dict,
    or `None` on any budget exhaustion (caller turns that into a
    "poll timed out" error message).
    """
    started = time.monotonic()
    exec_budget = max(float(timeout), _MIN_EXEC_TIMEOUT_S)
    queue_deadline = started + _MAX_QUEUE_WAIT_S
    run_deadline: Optional[float] = None
    last_snapshot: Optional[tuple] = None

    while True:
        now = time.monotonic()
        if now - started > _MAX_TOTAL_POLL_S:
            _log(f"poll {job_id[:8]}: hit _MAX_TOTAL_POLL_S")
            return None
        if run_deadline is not None:
            if now > run_deadline:
                _log(f"poll {job_id[:8]}: exec timeout")
                return None
        elif now > queue_deadline:
            _log(f"poll {job_id[:8]}: queue timeout")
            return None

        try:
            job = proxy.get_job(job_id)
        except ProxyUnreachable as e:
            _log(f"poll {job_id[:8]}: proxy unreachable: {e}")
            _invalidate_proxy()
            return None
        except ProxyHTTPError as e:
            if e.status == 404:
                return {
                    "id": job_id,
                    "status": "expired",
                    "outputs": [],
                    "error": {"code": "not_found",
                              "message": "job unknown to proxy"},
                }
            _log(f"poll {job_id[:8]}: HTTP {e.status}: {e.message}")
            time.sleep(_POLL_INTERVAL_S)
            continue

        status = job.get("status")
        if status in ("succeeded", "failed", "canceled", "expired"):
            return job

        prog = job.get("progress")
        if isinstance(prog, dict):
            snap = (status, tuple(sorted(prog.items())))
        else:
            snap = (status, prog, job.get("queue_position"))
        changed = (snap != last_snapshot)

        if status == "running":
            if run_deadline is None:
                run_deadline = now + exec_budget
            elif changed:
                run_deadline = now + exec_budget
        elif status == "queued" and changed:
            queue_deadline = now + _MAX_QUEUE_WAIT_S

        last_snapshot = snap
        time.sleep(_POLL_INTERVAL_S)


def _submit_and_collect(
    proxy: ProxyV2,
    prepared_graph: dict,
    *,
    label: str,
) -> dict:
    """Submit one prepared graph, poll to terminal, download outputs.

    Returns either a `{"success": True, "masks": [...], "timing": ...}`
    dict or a `{"success": False, "message": "..."}` dict.  The shape
    matches what `pg_panel_bindings.js`'s `runSegmentAllAction` /
    `runRefineAction` expect.
    """
    t0 = time.monotonic()
    try:
        job = proxy.submit_job(prepared_graph)
    except ProxyUnreachable as e:
        _invalidate_proxy()
        return {"success": False,
                "message": f"submit: proxy unreachable: {e}"}
    except ProxyHTTPError as e:
        return {"success": False,
                "message": f"submit: {e.status} {e.code or ''}: {e.message}"}

    job_id = job.get("id")
    if not job_id:
        return {"success": False,
                "message": "proxy did not return a job id"}

    terminal = _poll_until_done(proxy, job_id, timeout=GENERATE_TIMEOUT)
    if terminal is None:
        return {"success": False, "message": "poll timed out"}

    status = terminal.get("status")
    if status != "succeeded":
        err = terminal.get("error") or {}
        return {"success": False,
                "message": f"execution {status}: "
                           f"{err.get('message') or 'no detail'}"}

    outputs = _download_outputs(proxy, terminal)
    masks_b64 = [
        base64.b64encode(o["png_bytes"]).decode("ascii")
        for o in outputs if o.get("png_bytes")
    ]
    if not masks_b64:
        return {"success": False,
                "message": "job succeeded but produced no retrievable "
                           "outputs"}

    elapsed = time.monotonic() - t0
    _log(f"{label}: {len(masks_b64)} mask(s) in {elapsed:.2f}s")
    return {
        "success": True,
        "masks":   masks_b64,
        "timing":  round(elapsed, 3),
        "backend": label,
    }


def _upload_canvas(proxy: ProxyV2, image_entry: dict) -> dict:
    """Upload the cached canvas PNG as `@thesegment`.  Returns the
    proxy's `Asset` dict, ready for `asset_refs["@thesegment"]`."""
    return proxy.upload_asset(
        image_entry["raw"], "input/thesegment.png", "image/png",
    )


def _run_segment_pipeline(payload: dict, image_entry: dict,
                          *, backend_label: str) -> dict:
    """Shared pipeline for `handle_segment` and the SAM3 refine fallback.

    Both invoke the segment workflow (SAM3_Detect) with the canvas as
    the only resource.  The difference is only the parameters
    `_build_segment_params` produces (`individual_masks=True` for the
    refine fallback; the payload's value for Segment All).
    """
    workflow = _get_segment_workflow()
    if workflow is None:
        return {"success": False,
                "message": f"segment workflow not loaded from "
                           f"{WORKFLOW_SEGMENT_PATH}"}

    client = _get_client()
    try:
        info = cached_object_info(client)
    except ComfyError as e:
        _reset_client()
        return {"success": False,
                "message": f"ComfyUI unreachable: {e}"}

    if _SAM3_NODE not in info:
        return {"success": False,
                "message": f"ComfyUI has no {_SAM3_NODE} node."}

    resource_slots, param_slots, output_nodes = _get_slot_tables(
        WORKFLOW_SEGMENT_PATH, workflow,
    )
    if "@thesegment" not in resource_slots:
        return {"success": False,
                "message": "segment workflow has no '@thesegment' "
                           "LoadImage slot (edit workflows/segment.json)"}
    if not output_nodes:
        return {"success": False,
                "message": "segment workflow has no SaveImage output node"}

    try:
        proxy = _get_proxy()
    except ProxyUnreachable as e:
        return {"success": False,
                "message": f"proxy unavailable: {e}"}

    try:
        asset = _upload_canvas(proxy, image_entry)
    except ProxyUnreachable as e:
        _invalidate_proxy()
        return {"success": False,
                "message": f"asset upload: proxy unreachable: {e}"}
    except ProxyHTTPError as e:
        return {"success": False,
                "message": f"asset upload: {e.status}: {e.message}"}

    try:
        params = _build_segment_params(
            payload, image_entry["width"], image_entry["height"],
        )
    except Exception as e:
        return {"success": False,
                "message": f"could not build params: {e}"}

    try:
        prepared = substitute_into_graph(
            workflow, resource_slots, param_slots,
            {"@thesegment": asset}, params,
            seed=None,
        )
    except ValueError as e:
        return {"success": False,
                "message": f"substitute failed: {e}"}

    return _submit_and_collect(proxy, prepared, label=backend_label)


# ---------------------------------------------------------------------------
#  /comfy/segment
# ---------------------------------------------------------------------------

def _clamp_bbox(bbox, image_w: int, image_h: int) -> tuple:
    if not isinstance(bbox, dict):
        return 0, 0, image_w, image_h
    try:
        bx = int(bbox.get("x", 0))
        by = int(bbox.get("y", 0))
        bw = int(bbox.get("w", image_w))
        bh = int(bbox.get("h", image_h))
    except (TypeError, ValueError):
        return 0, 0, image_w, image_h
    bx = max(0, min(image_w - 1, bx))
    by = max(0, min(image_h - 1, by))
    bw = max(1, min(image_w - bx, bw))
    bh = max(1, min(image_h - by, bh))
    return bx, by, bw, bh


def _build_segment_params(payload: dict, image_w: int, image_h: int,
                          individual_masks_override: Optional[bool] = None
                          ) -> dict:
    def coords_json(points):
        return json.dumps([{"x": int(p["x"]), "y": int(p["y"])}
                           for p in points])

    bx, by, bw, bh = _clamp_bbox(payload.get("bbox"), image_w, image_h)
    individual = (individual_masks_override
                  if individual_masks_override is not None
                  else bool(payload.get("individual_masks", True)))
    return {
        "positive_coords":   coords_json(payload.get("positive") or []),
        "negative_coords":   coords_json(payload.get("negative") or []),
        "segment_text":      str(payload.get("text") or ""),
        "threshold":         float(payload.get("threshold", 0.5)),
        "refine_iterations": int(payload.get("refine_iterations", 2)),
        "individual_masks":  individual,
        "bbox_x":            bx,
        "bbox_y":            by,
        "bbox_w":            bw,
        "bbox_h":            bh,
    }


def handle_segment(payload: dict) -> dict:
    hash_hex = payload.get("hash")
    if not hash_hex:
        return {"success": False, "message": "no image hash"}

    image_entry = _get_image(hash_hex)
    if image_entry is None:
        return {"success": False,
                "message": "image not in cache \u2014 click Refresh "
                           "@thesegment in the panel"}

    return _run_segment_pipeline(
        payload, image_entry, backend_label="sam3",
    )


# ---------------------------------------------------------------------------
#  /comfy/refine
# ---------------------------------------------------------------------------

def _build_refine_sam2_params(payload: dict) -> dict:
    def coords_json(points):
        if not points:
            return '[{"x": 0, "y": 0}]'
        return json.dumps([{"x": int(p["x"]), "y": int(p["y"])}
                           for p in points])

    return {
        "positive_coords": coords_json(payload.get("positive") or []),
        "negative_coords": coords_json(payload.get("negative") or []),
    }


def _run_refine_sam2(payload: dict, image_entry: dict) -> dict:
    workflow = _get_refine_workflow()
    if workflow is None:
        return {"success": False,
                "message": f"refine workflow not loaded from "
                           f"{WORKFLOW_REFINE_PATH}"}

    mask_b64 = payload.get("mask_b64")
    if not mask_b64:
        return {"success": False,
                "message": "no mask_b64 for SAM2 refine"}

    try:
        mask_bytes = base64.b64decode(mask_b64)
    except Exception as e:
        return {"success": False, "message": f"bad mask_b64: {e}"}

    resource_slots, param_slots, output_nodes = _get_slot_tables(
        WORKFLOW_REFINE_PATH, workflow,
    )
    for needed in ("@thesegment", "@current_mask"):
        if needed not in resource_slots:
            return {"success": False,
                    "message": f"refine workflow has no '{needed}' "
                               f"LoadImage slot (edit "
                               f"workflows/sam2_refine.json)"}
    if not output_nodes:
        return {"success": False,
                "message": "refine workflow has no SaveImage output node"}

    try:
        proxy = _get_proxy()
    except ProxyUnreachable as e:
        return {"success": False,
                "message": f"proxy unavailable: {e}"}

    try:
        canvas_asset = _upload_canvas(proxy, image_entry)
        mask_asset = proxy.upload_asset(
            mask_bytes, "input/current_mask.png", "image/png",
        )
    except ProxyUnreachable as e:
        _invalidate_proxy()
        return {"success": False,
                "message": f"asset upload: proxy unreachable: {e}"}
    except ProxyHTTPError as e:
        return {"success": False,
                "message": f"asset upload: {e.status}: {e.message}"}

    asset_refs = {
        "@thesegment":  canvas_asset,
        "@current_mask": mask_asset,
    }
    params = _build_refine_sam2_params(payload)

    try:
        prepared = substitute_into_graph(
            workflow, resource_slots, param_slots,
            asset_refs, params,
            seed=None,
        )
    except ValueError as e:
        return {"success": False,
                "message": f"substitute failed: {e}"}

    return _submit_and_collect(proxy, prepared, label="sam2")


def _run_refine_sam3(payload: dict, image_entry: dict) -> dict:
    """SAM3 refine re-detects using the segment workflow.

    SAM3's detect node has no `mask` input, so this shares the segment
    pipeline with `individual_masks_override=True`.  Same semantic
    difference as before: each click produces a fresh detection rather
    than an incremental refinement of the previous mask.
    """
    return _run_segment_pipeline(
        payload, image_entry, backend_label="sam3",
    )


def handle_refine(payload: dict) -> dict:
    hash_hex = payload.get("hash")
    if not hash_hex:
        return {"success": False, "message": "no image hash"}

    image_entry = _get_image(hash_hex)
    if image_entry is None:
        return {"success": False,
                "message": "image not in cache \u2014 click Refresh "
                           "@thesegment in the panel"}

    model = payload.get("model", "sam2")
    if model not in ("sam2", "sam3"):
        return {"success": False,
                "message": f"unknown refine model: {model!r}"}

    if model == "sam3":
        return _run_refine_sam3(payload, image_entry)

    client = _get_client()
    try:
        info = cached_object_info(client)
    except ComfyError as e:
        _reset_client()
        return {"success": False,
                "message": f"ComfyUI unreachable: {e}"}

    refine_wf = _get_refine_workflow()
    if refine_wf is not None and _SAM2_NODE in info:
        try:
            result = _run_refine_sam2(payload, image_entry)
            if result.get("success"):
                return result
            _log(f"sam2 refine failed: {result.get('message')}")
        except Exception as e:
            _log(f"sam2 refine raised: {e}")

    _log("sam2 unavailable or failed; falling back to sam3 refine")
    return _run_refine_sam3(payload, image_entry)


def handle_interrupt(_payload) -> dict:
    """POST /interrupt to ComfyUI.

    Segment's per-run jobs are handled through the proxy, but the
    underlying ComfyUI instance still answers the stock `/interrupt`.
    A running SAM detection is interruptible exactly as before.
    """
    client = _get_client()
    try:
        client.interrupt()
        return {"success": True}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ---------------------------------------------------------------------------
#  Krita export / import
# ---------------------------------------------------------------------------


def _coerce_edge_args(payload: dict) -> dict:
    def _int(key):
        v = payload.get(key, 0)
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    return {
        "expand_pixels":     _int("expand_pixels"),
        "contract_pixels":   _int("contract_pixels"),
        "smooth_iterations": _int("smooth_iterations"),
        "feather_radius":    _int("feather_radius"),
        "invert":            bool(payload.get("invert", False)),
    }


def _normalize_response(response: dict) -> dict:
    if response.get("status") == "success":
        return {
            "success": True,
            "message": response.get("message") or "applied",
            "data": response.get("data") or {},
        }
    return {
        "success": False,
        "message": response.get("message") or "daemon rejected the command",
    }


def _extract_with_post_apply(payload: dict, edge: dict) -> dict:
    daemon_mode = ("new_layer" if payload.get("mode") == "extract_new_layer"
                   else "duplicate")

    extract_cmd = {
        "type": "extract_mask_to_layer",
        "mask_b64": payload.get("mask_b64"),
        "layer_name": payload.get("layer_name") or "",
        "new_layer_name": payload.get("new_layer_name") or "",
        "mode": daemon_mode,
        **edge,
    }
    extract_resp = _send_with_pin(extract_cmd, timeout=60.0)
    if extract_resp.get("status") != "success":
        return {
            "success": False,
            "message": extract_resp.get("message") or "extraction failed",
        }

    new_layer = ((extract_resp.get("data") or {}).get("new_layer") or "")
    if not new_layer:
        return {
            "success": False,
            "message": "extraction succeeded but daemon returned no layer name",
        }

    post_warnings: list = []

    blend_mode = payload.get("blend_mode") or ""
    if blend_mode:
        r = _send_with_pin({
            "type": "set_layer_blend_mode",
            "layer_name": new_layer,
            "blend_mode": blend_mode,
        }, timeout=30.0)
        if r.get("status") != "success":
            post_warnings.append(
                f"blend mode: {r.get('message') or 'unknown error'}"
            )

    opacity = payload.get("opacity")
    if opacity is not None and opacity != "":
        try:
            op_int = int(opacity)
        except (TypeError, ValueError):
            post_warnings.append(f"opacity not an integer: {opacity!r}")
        else:
            if 0 <= op_int <= 255:
                r = _send_with_pin({
                    "type": "set_layer_opacity",
                    "layer_name": new_layer,
                    "opacity": op_int,
                }, timeout=30.0)
                if r.get("status") != "success":
                    post_warnings.append(
                        f"opacity: {r.get('message') or 'unknown error'}"
                    )
            else:
                post_warnings.append(
                    f"opacity {op_int} out of range (0-255); skipped"
                )

    group_name = payload.get("group_name") or ""
    if group_name:
        r = _send_with_pin({
            "type": "move_layer_to_group",
            "layer_name": new_layer,
            "group_name": group_name,
            "position": "inside",
        }, timeout=30.0)
        if r.get("status") != "success":
            post_warnings.append(
                f"group '{group_name}': "
                f"{r.get('message') or 'move failed'}"
            )

    result_data = dict(extract_resp.get("data") or {})
    if post_warnings:
        result_data["post_warnings"] = post_warnings

    verb = "Extracted" if daemon_mode == "new_layer" else "Duplicated"
    msg = f"{verb} to '{new_layer}'"
    if post_warnings:
        msg += " (" + "; ".join(post_warnings) + ")"

    return {"success": True, "message": msg, "data": result_data}


def handle_krita_apply(payload: dict) -> dict:
    mask_b64 = payload.get("mask_b64")
    if not mask_b64:
        return {"success": False, "message": "no mask_b64"}

    mode = payload.get("mode")
    if mode not in ("selection", "transparency",
                    "extract_new_layer", "extract_duplicate",
                    "export_layer"):
        return {"success": False, "message": f"unknown mode: {mode!r}"}

    edge = _coerce_edge_args(payload)

    if mode == "selection":
        sel_mode = payload.get("sel_mode") or "replace"
        if sel_mode not in ("replace", "add", "subtract", "intersect"):
            return {"success": False,
                    "message": f"unknown sel_mode: {sel_mode!r}"}
        command = {
            "type": "set_selection_from_mask_b64",
            "mask_b64": mask_b64,
            "mode": sel_mode,
            **edge,
        }
        return _normalize_response(_send_with_pin(command, timeout=60.0))

    if mode == "transparency":
        command = {
            "type": "apply_transparency_mask_b64",
            "mask_b64": mask_b64,
            "layer_name": payload.get("layer_name") or "",
            **edge,
        }
        return _normalize_response(_send_with_pin(command, timeout=60.0))

    if mode == "export_layer":
        command = {
            "type": "create_paint_layer_from_mask_b64",
            "mask_b64": mask_b64,
            "name": payload.get("name") or "",
            "above_layer": payload.get("above_layer") or "",
            **edge,
        }
        return _normalize_response(_send_with_pin(command, timeout=60.0))

    return _extract_with_post_apply(payload, edge)


def handle_rebind_document(_payload) -> dict:
    global _target_document_name
    with _target_lock:
        old = _target_document_name
        _target_document_name = ""

    try:
        resp = krita_client.send(
            {"type": "get_document_composite_b64"}, timeout=30.0,
        )
        _capture_document_from_response(resp)
    except Exception:
        pass

    with _target_lock:
        new = _target_document_name

    _log(f"rebind: was {old!r}, now {new!r}")
    return {
        "success": True,
        "message": f"Rebind: was {old!r}, now {new!r}",
        "data": {"previous": old, "current": new},
    }


# ---------------------------------------------------------------------------
#  Shared daemon routes — adapted to segment's pinning + caching contract
# ---------------------------------------------------------------------------
#
# The shared `daemon_route_handlers(send=...)` factory accepts a uniform
# `send(command, timeout=...)` callable.  Segment's `_send_with_pin`
# dispatches on command type:
#
#   · get_document_composite_b64  with an explicit `name`  → direct send,
#                                    then _capture_document_from_response
#   · get_document_composite_b64  without `name`          → _send_with_pin
#                                                              doc_arg="name"
#   · comfy_status                                        → direct send
#   · everything else                                     → _send_with_pin
#                                                              doc_arg="document"
#
# `_pinned_send` is that dispatch, wired to the shared handlers' uniform
# signature.  It mirrors the behavior segment has always had:
# `_fetch_active_state` never pinned `comfy_status`; `handle_krita_import`
# took the `name`-branch direct; everything else went through
# `_send_with_pin`.

def _pinned_send(command: dict, *, timeout: float = 60.0) -> dict:
    ctype = command.get("type")
    if ctype == "get_document_composite_b64":
        if command.get("name"):
            resp = krita_client.send(command, timeout=timeout)
            _capture_document_from_response(resp)
            return resp
        return _send_with_pin(command, doc_arg="name", timeout=timeout)
    if ctype == "comfy_status":
        return krita_client.send(command, timeout=timeout)
    return _send_with_pin(command, doc_arg="document", timeout=timeout)


_h = daemon_route_handlers(send=_pinned_send)


# Segment caches the raw PNG bytes by hash at import time, so
# `/comfy/segment` and `/comfy/refine` can look them up without an extra
# daemon round-trip.  The shared handlers don't have this side effect;
# wrap both `/krita/import` and `/krita/resource_image` (the latter is
# the actual autosync path — source="active" calls import_image
# internally, but the wrapping happens on the outer resource_image
# result).

def _cache_from_result(res: dict) -> None:
    if not isinstance(res, dict):
        return
    if not res.get("success"):
        return
    h = res.get("hash")
    data_url = res.get("data_url")
    if not h or not data_url:
        return
    try:
        png_bytes = base64.b64decode(data_url.split(",", 1)[1])
    except Exception:
        return
    _cache_image(
        h, png_bytes,
        res.get("width") or 0,
        res.get("height") or 0,
        res.get("filename") or "",
    )


_import_base = _h["import_image"]
def handle_krita_import(payload):
    res = _import_base(payload)
    _cache_from_result(res)
    return res


_resource_base = _h["resource_image"]
def handle_resource_image(payload):
    res = _resource_base(payload)
    _cache_from_result(res)
    return res


ROUTES = {
    "/version":               handle_version,
    "/playground_reregister": handle_playground_reregister,
    "/comfy/status":          handle_status,
    "/comfy/active_state":    _h["active_state"],
    "/comfy/segment":         handle_segment,
    "/comfy/refine":          handle_refine,
    "/comfy/interrupt":       handle_interrupt,
    "/krita/status":          _h["krita_status"],
    "/krita/apply":           handle_krita_apply,
    "/krita/import":          handle_krita_import,
    "/krita/import_masks":    _h["import_masks"],
    "/krita/resource_image":  handle_resource_image,
    "/rebind_document":       handle_rebind_document,
}

RAW_ROUTES = {}
