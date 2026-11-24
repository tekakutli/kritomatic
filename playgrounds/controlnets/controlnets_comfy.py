"""ComfyUI preprocessing for the controlnets playground.

Reads `data/controlnets_workflow.json` (UI format), finds every
`ControlNetApply*` node, traces its `image` input back to the
preprocessor node feeding it (Zoe-DepthMapPreprocessor,
AnimeLineArtPreprocessor, OpenposePreprocessor, CannyEdgePreprocessor,
etc.), and for each **distinct** preprocessor runs a virtual
mini-workflow:

    LoadImage(@thecontrolnet)  →  Preprocessor  →  SaveImage

The label shown in the playground is the **preprocessor node's own
title** (or its `class_type` when the node was never renamed in
ComfyUI).  Because the label is a property of the preprocessor, not
the Apply node, two `ControlNetApply*` nodes that share the same
preprocessor collapse to a single entry — the image is computed once
and listed once.

The @thecontrolnet image is uploaded to ComfyUI's standard
`/upload/image` endpoint before any virtual workflow runs; the same
filename is reused across all preprocessors.  All images returned are
PNG bytes plus their pixel dims.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path

from PIL import Image

# Shared-module bootstrap — same pattern layout_comfy.py uses.
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfyui_proxy_client import (  # noqa: E402
    ProxyHTTPError,
    ProxyUnreachable,
    ProxyV2,
)

from pgruntime import daemon_client as krita_client


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_PATH = _REPO_ROOT / "data" / "controlnets" / "controlnets_workflow.json"

COMFY_HOST = "127.0.0.1"
COMFY_PORT = 8188
COMFY_URL = f"http://{COMFY_HOST}:{COMFY_PORT}"

POLL_INTERVAL_S = 0.5
POLL_TIMEOUT_S  = 180.0


def _log(msg: str) -> None:
    print(f"[controlnets:comfy] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  Workflow parsing — find Apply ControlNet nodes and their preprocessors
# ---------------------------------------------------------------------------

def _load_ui_workflow() -> dict:
    if not WORKFLOW_PATH.is_file():
        raise RuntimeError(
            f"Workflow file not found: {WORKFLOW_PATH}.  Drop a ComfyUI "
            f"workflow (UI format) there — it only needs to contain the "
            f"ControlNetApply nodes whose preprocessors you want to run "
            f"against @thecontrolnet."
        )
    try:
        raw = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"Workflow file invalid: {e}")
    if not isinstance(raw, dict) or "nodes" not in raw:
        raise RuntimeError(
            "Workflow must be UI format (has `nodes` array).  Re-export "
            "it from ComfyUI with File → Export (not 'Save API format')."
        )
    return raw


def _find_preprocessor_entries(ui_workflow: dict) -> list:
    """Return [{title, preprocessor_node}] in workflow order.

    One entry per **distinct** preprocessor node reached from a
    `ControlNetApply*` node's `image` input.  The display label is the
    preprocessor's own `title` field when set, otherwise its
    `class_type`.  Two `ControlNetApply*` nodes that feed off the same
    preprocessor therefore produce exactly one entry — the Zoe-
    depth workflow's `DEPTH` and `DEPTH FULL` collapse into a single
    `Zoe-DepthMapPreprocessor` row.

    `preprocessor_node` is the raw UI-format node dict so callers can
    pull its class_type and widgets.
    """
    nodes_by_id = {n["id"]: n for n in (ui_workflow.get("nodes") or [])}
    link_sources = {}
    for link in (ui_workflow.get("links") or []):
        # [link_id, src_node_id, src_slot, dst_node_id, dst_slot, type]
        link_sources[link[0]] = (link[1], link[2])

    seen_pp_ids = set()
    out = []
    for node in (ui_workflow.get("nodes") or []):
        class_type = node.get("type") or ""
        if not class_type.startswith("ControlNetApply"):
            continue

        image_link = None
        for inp in (node.get("inputs") or []):
            if inp.get("name") == "image":
                image_link = inp.get("link")
                break
        if image_link is None:
            continue
        src = link_sources.get(image_link)
        if not src:
            continue
        pp_node = nodes_by_id.get(src[0])
        if not pp_node:
            continue

        pp_id = pp_node.get("id")
        if pp_id in seen_pp_ids:
            continue
        seen_pp_ids.add(pp_id)

        title = (pp_node.get("title")
                 or pp_node.get("type")
                 or str(pp_id)).strip()
        out.append({
            "title": title,
            "preprocessor": pp_node,
        })
    return out


def _preprocessor_inputs(pp_node: dict) -> dict:
    """Extract the preprocessor's widget values as an API-format inputs
    dict.  Prefers `widgets_values_named` (present on workflows
    exported from modern ComfyUI).  Falls back to `widgets_values` with
    a warning if the node has widgets but no named form — such a node
    may end up running with defaults, which is usually fine.
    """
    named = pp_node.get("widgets_values_named")
    if isinstance(named, dict):
        return dict(named)
    positional = pp_node.get("widgets_values")
    if isinstance(positional, list) and positional:
        _log(
            f"warning: {pp_node.get('type')!r} has positional widget "
            f"values but no `widgets_values_named`; running with defaults"
        )
    return {}


def _build_virtual_workflow(pp_node: dict, uploaded_filename: str) -> dict:
    """Build an API-format mini-workflow: LoadImage → Preprocessor → SaveImage."""
    class_type = pp_node.get("type")
    if not class_type:
        raise RuntimeError("preprocessor node has no class_type/type")

    inputs = _preprocessor_inputs(pp_node)
    inputs["image"] = ["1", 0]

    return {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": uploaded_filename},
        },
        "2": {
            "class_type": class_type,
            "inputs": inputs,
        },
        "3": {
            "class_type": "SaveImage",
            "inputs": {
                "images": ["2", 0],
                "filename_prefix": "controlnets_preview",
            },
        },
    }


# ---------------------------------------------------------------------------
#  Image upload
# ---------------------------------------------------------------------------

def _upload_image_to_comfy(png_bytes: bytes, filename: str) -> str:
    """Upload a PNG to ComfyUI's input directory via /upload/image.

    Returns the resolved filename (with subfolder prefix if any)."""
    boundary = "----ControlNets" + os.urandom(8).hex()
    body = bytearray()
    body += f"--{boundary}\r\n".encode()
    body += (
        f'Content-Disposition: form-data; name="image"; '
        f'filename="{filename}"\r\n'
    ).encode()
    body += b"Content-Type: image/png\r\n\r\n"
    body += png_bytes
    body += b"\r\n"
    body += f"--{boundary}\r\n".encode()
    body += b'Content-Disposition: form-data; name="overwrite"\r\n\r\n'
    body += b"true"
    body += f"\r\n--{boundary}--\r\n".encode()

    req = urllib.request.Request(
        f"{COMFY_URL}/upload/image",
        data=bytes(body),
        method="POST",
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
    )
    with urllib.request.urlopen(req, timeout=30.0) as r:
        data = json.loads(r.read())
    name = data.get("name") or filename
    sub = data.get("subfolder") or ""
    return f"{sub}/{name}" if sub else name


# ---------------------------------------------------------------------------
#  Proxy: connect / submit / poll / fetch
# ---------------------------------------------------------------------------

def _ensure_proxy_running() -> None:
    resp = krita_client.send({
        "type":  "comfy_connect",
        "host":  COMFY_HOST,
        "port":  COMFY_PORT,
        "check": True,
    }, timeout=30.0)
    if not isinstance(resp, dict) or resp.get("status") != "success":
        msg = (resp.get("message") if isinstance(resp, dict)
               else "no response")
        raise RuntimeError(f"ComfyUI connect failed: {msg}")


def _get_proxy() -> ProxyV2:
    return ProxyV2.from_pid_file()


def _poll_job(proxy: ProxyV2, job_id: str) -> dict:
    start = time.monotonic()
    while time.monotonic() - start < POLL_TIMEOUT_S:
        try:
            job = proxy.get_job(job_id)
        except ProxyHTTPError as e:
            if e.status == 404:
                return {
                    "status": "expired",
                    "error": {"message": "job unknown to proxy"},
                }
            time.sleep(POLL_INTERVAL_S)
            continue
        except ProxyUnreachable:
            time.sleep(POLL_INTERVAL_S)
            continue
        if job.get("status") in (
            "succeeded", "failed", "canceled", "expired",
        ):
            return job
        time.sleep(POLL_INTERVAL_S)
    raise RuntimeError(f"job {job_id} did not finish within "
                       f"{POLL_TIMEOUT_S}s")


def _fetch_output_bytes(proxy: ProxyV2, job: dict) -> bytes:
    for out in (job.get("outputs") or []):
        if not isinstance(out, dict):
            continue
        if not (out.get("type") and out["type"] != "temp"
                and out.get("id")):
            continue
        try:
            return proxy.download_asset(out["id"])
        except (ProxyHTTPError, ProxyUnreachable):
            continue
    raise RuntimeError("job succeeded but returned no image outputs")


# ---------------------------------------------------------------------------
#  Public entry point
# ---------------------------------------------------------------------------

def preprocess_all(png_bytes: bytes) -> list:
    """Run every distinct preprocessor on `png_bytes`.

    Returns a list, in workflow order, of either:

        {"title": str, "png_bytes": bytes, "width": int, "height": int}
        {"title": str, "error": str}

    Exactly one entry per distinct preprocessor node.  The `title` is
    the preprocessor's own title (see `_find_preprocessor_entries`);
    two `ControlNetApply*` nodes feeding off the same preprocessor
    share one entry, not two.
    """
    ui_workflow = _load_ui_workflow()
    entries = _find_preprocessor_entries(ui_workflow)
    if not entries:
        return []

    _ensure_proxy_running()
    proxy = _get_proxy()

    uploaded_name = _upload_image_to_comfy(png_bytes, "thecontrolnet.png")
    _log(f"uploaded @thecontrolnet as {uploaded_name!r}")

    out = []
    for entry in entries:
        title = entry["title"]
        pp_node = entry["preprocessor"]

        try:
            workflow = _build_virtual_workflow(pp_node, uploaded_name)
            job = proxy.submit_job(workflow)
            job_id = job.get("id")
            if not job_id:
                raise RuntimeError("proxy returned no job id")

            terminal = _poll_job(proxy, str(job_id))
            status = terminal.get("status")
            if status != "succeeded":
                err = ((terminal.get("error") or {}).get("message")
                       or status or "unknown")
                raise RuntimeError(f"ComfyUI {status}: {err}")

            img_bytes = _fetch_output_bytes(proxy, terminal)
            pil = Image.open(BytesIO(img_bytes))
            w, h = pil.size
            out.append({
                "title":     title,
                "png_bytes": img_bytes,
                "width":     w,
                "height":    h,
            })
            _log(f"preprocessed {title!r} "
                 f"({pp_node.get('type')}) → {w}x{h}")
        except Exception as e:
            _log(f"preprocessor {title!r} failed: {e}")
            out.append({"title": title, "error": str(e)})

    return out
