"""ComfyUI submission, polling, and image retrieval for the layout
playground's Generate button.

Workflow file
=============
`data/layout/drawatoon_workflow.json` — UI or API format.  On first
use, or whenever the file's mtime changes, the shared
`kritomatic_shared.comfy_convert.convert_if_ui` converts it to API
format and writes the result to `data/convert/<slug>.json`.  The
playground caches the converted graph by mtime so repeat clicks
don't re-convert.

Substitution — prompt, seed, bubble texts, latent size
======================================================
Four substitutions happen on the API graph before submit:

  · **seed** — every node's `inputs.seed` is replaced with the
    requested seed.
  · **prompt** — the positive-side `CLIPTextEncode` node(s), traced
    from every sampler's `positive` input, have their `text` field
    replaced with the combined global prompt + non-empty bubble
    texts.
  · **latent size** — every `EmptyLatentImage` node has its
    `inputs.width` and `inputs.height` replaced with the current
    viewer ratio's preset from `_LATENT_PRESET_512`.

Latent-size substitution (fix for pasted-image stretching)
----------------------------------------------------------
The current drawatoon workflow uses a custom widget-less
`PlaygroundResolutionSelect` node whose output is baked into the
graph (0.28 ratio / 256×928, hardcoded).  It has no widgets and no
inputs, so no substitution on the node itself is possible.

The robust fix is to override the *downstream* `EmptyLatentImage`
directly: replace `inputs.width` and `inputs.height` with literals
computed from the viewer's current ratio.  This bypasses the
resolution node entirely and is immune to what that node is named
or how it is configured.  `_substitute_latent_size` does this for
every `EmptyLatentImage` in the graph.

`_resize_png_bytes` still has an aspect-preserving centre-crop
fallback (with a warning) if a future workflow change ever leaves
the aspect uncorrected.

Submission flow
===============
  1. Load workflow (UI→API if needed, mtime-cached).
  2. Substitute seed, prompt, latent size.
  3. Ask the daemon to `comfy_connect`.
  4. `proxy.submit_job(workflow)` → job id.
  5. Poll `proxy.get_job(id)` until terminal, reporting progress.
  6. `proxy.download_asset(output_id)` → raw PNG bytes.
  7. Aspect-preserving PIL resize to the viewer's pixel dims.
  8. `comfy_paste_image_b64` to the daemon at the viewer's position.

Nothing is written to disk on the playground side.

Paste position
==============
Assumes `@thelayout` sits at (0, 0) in the Krita document.  If the
layer has a nonzero offset, the pasted image will be off by that
offset; that's a v2 fix.

Concurrent generations
======================
Each `start_generation` call spawns its own thread.  Multiple clicks
produce multiple in-flight jobs.  Each job's terminal status is kept
in `_generate_jobs` for `_JOB_RETENTION_S` (default 300s) so the
client can still poll the final state, then the entry is dropped.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

# ---------------------------------------------------------------------------
#  Shared-module bootstrap
# ---------------------------------------------------------------------------
_SRC = Path(__file__).resolve().parent.parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from kritomatic_shared.comfy_convert import (   # noqa: E402
    ConversionError,
    convert_if_ui,
)
from kritomatic_shared.comfyui_proxy_client import (  # noqa: E402
    ProxyHTTPError,
    ProxyUnreachable,
    ProxyV2,
)

from . import krita_client

# ---------------------------------------------------------------------------
#  Paths + timings
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
WORKFLOW_PATH = _REPO_ROOT / "data" / "layout" / "drawatoon_workflow.json"

POLL_INTERVAL_S  = 0.5
POLL_TIMEOUT_S   = 180.0
SUBMIT_TIMEOUT_S = 15.0
FETCH_TIMEOUT_S  = 60.0

_JOB_RETENTION_S = 300.0

_DEFAULT_PROMPT = "manga panel, black and white, screentone"
_DEFAULT_RATIO = "1.00"

_SAMPLER_CLASS_TYPES = frozenset({"KSampler", "KSamplerAdvanced"})

# Latent nodes whose `width` and `height` inputs we override.  Only
# `EmptyLatentImage` is used by the current workflow; other nodes
# would need to be added here if the workflow changes.
_LATENT_NODE_CLASSES = frozenset({"EmptyLatentImage"})

# Viewer ratio key → (width, height) at the 512-pixel PixArt base.
# Copied from `pixart_res["PixArtMS_Sigma_XL_2_512"]` in
# `kritomatic_nodes/PixArt/conf.py`.  The rounding is aesthetic
# (hand-curated upstream), so this table is duplicated rather than
# computed from a formula.
_LATENT_PRESET_512 = {
    "0.25": (256, 1024), "0.26": (256, 992),
    "0.27": (256, 960),  "0.28": (256, 928),
    "0.32": (288, 896),  "0.33": (288, 864),
    "0.35": (288, 832),  "0.40": (320, 800),
    "0.42": (320, 768),  "0.48": (352, 736),
    "0.50": (352, 704),  "0.52": (352, 672),
    "0.57": (384, 672),  "0.60": (384, 640),
    "0.68": (416, 608),  "0.72": (416, 576),
    "0.78": (448, 576),  "0.82": (448, 544),
    "0.88": (480, 544),  "0.94": (480, 512),
    "1.00": (512, 512),  "1.07": (512, 480),
    "1.13": (544, 480),  "1.21": (544, 448),
    "1.29": (576, 448),  "1.38": (576, 416),
    "1.46": (608, 416),  "1.67": (640, 384),
    "1.75": (672, 384),  "2.00": (704, 352),
    "2.09": (736, 352),  "2.40": (768, 320),
    "2.50": (800, 320),  "2.89": (832, 288),
    "3.00": (864, 288),  "3.11": (896, 288),
    "3.62": (928, 256),  "3.75": (960, 256),
    "3.88": (992, 256),  "4.00": (1024, 256),
}

# Aspect mismatch tolerance for the safety crop.  Below this we
# treat the aspects as equal and skip the crop.
_ASPECT_TOL = 0.01


def _log(msg: str) -> None:
    print(f"[layout:comfy] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  Workflow loading + conversion (mtime-cached)
# ---------------------------------------------------------------------------
_workflow_cache = {"mtime": None, "path": None, "data": None}
_workflow_lock  = threading.Lock()


def _load_api_workflow() -> dict:
    path = WORKFLOW_PATH
    if not path.is_file():
        raise RuntimeError(
            f"Workflow file not found at {path}. Drop your drawatoon "
            f"workflow there (UI or API format — UI is auto-converted "
            f"on first use)."
        )
    try:
        mtime = path.stat().st_mtime
    except OSError as e:
        raise RuntimeError(f"Workflow file unreadable: {e}")

    with _workflow_lock:
        if (_workflow_cache["mtime"] == mtime
                and _workflow_cache["data"] is not None):
            return _workflow_cache["data"]

        try:
            raw_text = path.read_text(encoding="utf-8")
            raw = json.loads(raw_text)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"Workflow file invalid: {e}")

        if not isinstance(raw, dict) or not raw:
            raise RuntimeError("Workflow file is empty or not an object.")

        if "nodes" in raw or "links" in raw:
            try:
                converted_path, _records, _was = convert_if_ui(
                    path, annotate=False,
                )
            except ConversionError as e:
                raise RuntimeError(
                    f"Workflow UI→API conversion failed: {e}. Open the "
                    f"workflow in ComfyUI and check for missing nodes."
                )
            try:
                data = json.loads(
                    converted_path.read_text(encoding="utf-8")
                )
            except (OSError, json.JSONDecodeError) as e:
                raise RuntimeError(
                    f"Converted workflow unreadable: {e}"
                )
            converted_note = " (auto-converted from UI)"
        else:
            data = raw
            converted_path = path
            converted_note = ""

        if not isinstance(data, dict):
            raise RuntimeError("Workflow must be a JSON object.")
        for k, v in data.items():
            if not isinstance(v, dict) or "class_type" not in v:
                raise RuntimeError(
                    f"Workflow node {k!r} missing 'class_type'. "
                    f"Is this actually API format?"
                )

        _workflow_cache["mtime"] = mtime
        _workflow_cache["path"]  = str(converted_path)
        _workflow_cache["data"]  = data
        _log(f"loaded {path.name}{converted_note} "
             f"→ {converted_path.name} ({len(data)} nodes)")
        return data


# ---------------------------------------------------------------------------
#  Substitution: seed / prompt / latent size
# ---------------------------------------------------------------------------

def _substitute_seed(workflow: dict, seed: int) -> dict:
    out = {}
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            out[node_id] = node
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and "seed" in inputs:
            node_copy = dict(node)
            node_copy["inputs"] = dict(inputs)
            node_copy["inputs"]["seed"] = int(seed) & 0xFFFFFFFF
            out[node_id] = node_copy
        else:
            out[node_id] = node
    return out


def _collect_positive_clip_ids(workflow: dict) -> list:
    result: list = []
    visited: set = set()

    def walk(node_id: str) -> None:
        if node_id in visited:
            return
        visited.add(node_id)
        node = workflow.get(node_id)
        if not isinstance(node, dict):
            return
        if node.get("class_type") == "CLIPTextEncode":
            if node_id not in result:
                result.append(node_id)
            return
        for v in (node.get("inputs") or {}).values():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], (str, int)):
                walk(str(v[0]))

    for nid, n in workflow.items():
        if not isinstance(n, dict):
            continue
        if n.get("class_type") not in _SAMPLER_CLASS_TYPES:
            continue
        pos = (n.get("inputs") or {}).get("positive")
        if isinstance(pos, list) and len(pos) == 2:
            walk(str(pos[0]))
    return result


def _combine_prompt(prompt: str, bubble_texts: list) -> str:
    parts = []
    p = (prompt or "").strip()
    if p:
        parts.append(p)
    for t in (bubble_texts or []):
        s = str(t or "").strip()
        if s:
            parts.append(s)
    if not parts:
        parts.append(_DEFAULT_PROMPT)
    return " ".join(parts)


def _substitute_prompt(workflow: dict, prompt_text: str) -> dict:
    clip_ids = _collect_positive_clip_ids(workflow)
    if not clip_ids:
        _log("no positive CLIPTextEncode found; prompt not substituted")
        return workflow
    if len(clip_ids) > 1:
        _log(
            f"warning: {len(clip_ids)} CLIPTextEncode nodes reached "
            f"from a positive input; all will receive the same text"
        )
    out = dict(workflow)
    for cid in clip_ids:
        node = dict(out[cid])
        inputs = dict(node.get("inputs") or {})
        inputs["text"] = prompt_text
        node["inputs"] = inputs
        out[cid] = node
    _log(f"substituted prompt into {len(clip_ids)} CLIP node(s): "
         f"{prompt_text[:60]!r}{'...' if len(prompt_text) > 60 else ''}")
    return out


def _substitute_latent_size(workflow: dict, ratio: str) -> dict:
    """Override `EmptyLatentImage.width` and `.height` from the viewer
    ratio.

    Bypasses whatever upstream node feeds those inputs.  In the
    current drawatoon workflow that upstream node is a widget-less
    `PlaygroundResolutionSelect` whose output is baked in as
    256×928 (ratio 0.28).  Because the node has no widgets and no
    inputs, no substitution on that node is possible — overriding
    the downstream latent inputs is the only fix.

    Works regardless of what the upstream node is called or how it
    is configured.
    """
    r = str(ratio or _DEFAULT_RATIO).strip()
    size = _LATENT_PRESET_512.get(r)
    if size is None:
        _log(f"warning: unknown ratio key {r!r}; "
             f"falling back to {_DEFAULT_RATIO}")
        size = _LATENT_PRESET_512[_DEFAULT_RATIO]
    w, h = int(size[0]), int(size[1])

    out = dict(workflow)
    count = 0
    for nid, node in workflow.items():
        if not isinstance(node, dict):
            continue
        if node.get("class_type") not in _LATENT_NODE_CLASSES:
            continue
        node_copy = dict(node)
        inputs = dict(node.get("inputs") or {})
        inputs["width"] = w
        inputs["height"] = h
        node_copy["inputs"] = inputs
        out[nid] = node_copy
        count += 1
    if count:
        _log(f"substituted latent size {w}x{h} (ratio={r}) into "
             f"{count} {sorted(_LATENT_NODE_CLASSES)} node(s)")
    else:
        _log(f"warning: no node of class {sorted(_LATENT_NODE_CLASSES)} "
             f"found in workflow; latent size not substituted, and the "
             f"generated image may not match the viewer aspect")
    return out


# ---------------------------------------------------------------------------
#  Proxy: ensure running + submit + poll + fetch
# ---------------------------------------------------------------------------

def _ensure_proxy_running() -> None:
    resp = krita_client.send({
        "type":  "comfy_connect",
        "host":  "127.0.0.1",
        "port":  8188,
        "check": True,
    }, timeout=30.0)
    if not isinstance(resp, dict) or resp.get("status") != "success":
        msg = (resp.get("message") if isinstance(resp, dict)
               else "no response")
        raise RuntimeError(
            f"Could not start/connect to ComfyUI proxy: {msg}"
        )


def _get_proxy() -> ProxyV2:
    return ProxyV2.from_pid_file()


def _submit_workflow(proxy: ProxyV2, workflow: dict) -> str:
    job = proxy.submit_job(workflow)
    job_id = job.get("id")
    if not job_id:
        raise RuntimeError(f"Proxy returned no job id: {job!r}")
    return str(job_id)


def _poll_job(proxy: ProxyV2, job_id: str,
              timeout: float = POLL_TIMEOUT_S,
              on_progress=None) -> dict:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            job = proxy.get_job(job_id)
        except ProxyUnreachable as e:
            _log(f"poll error (transient): {e}")
            time.sleep(POLL_INTERVAL_S)
            continue
        except ProxyHTTPError as e:
            if e.status == 404:
                return {
                    "id": job_id, "status": "expired", "outputs": [],
                    "error": {"code": "not_found",
                              "message": "job unknown to proxy"},
                }
            _log(f"poll error (transient): {e}")
            time.sleep(POLL_INTERVAL_S)
            continue
        if on_progress is not None:
            try:
                on_progress(job)
            except Exception:
                pass
        status = job.get("status")
        if status in ("succeeded", "failed", "canceled", "expired"):
            return job
        time.sleep(POLL_INTERVAL_S)
    raise RuntimeError(f"job {job_id} did not finish within {timeout}s")


def _fetch_output_bytes(proxy: ProxyV2, job: dict) -> bytes:
    for out in (job.get("outputs") or []):
        if not isinstance(out, dict):
            continue
        if not (out.get("type") and out["type"] != "temp"
                and out.get("id")):
            continue
        try:
            return proxy.download_asset(out["id"])
        except ProxyHTTPError as e:
            if e.status == 404 and e.code == "output_unavailable":
                continue
            continue
        except ProxyUnreachable:
            continue
    raise RuntimeError("job succeeded but returned no image outputs")


# ---------------------------------------------------------------------------
#  Aspect-preserving resize + paste
# ---------------------------------------------------------------------------

def _resize_png_bytes(png_bytes: bytes,
                      target_w: int, target_h: int) -> bytes:
    """Resize to (target_w, target_h).  Centre-crops first only if the
    source aspect does not match the target aspect within
    `_ASPECT_TOL` — a safety net that should never fire after
    `_substitute_latent_size`, but catches the case where a user's
    workflow lacks an `EmptyLatentImage`.
    """
    img = Image.open(BytesIO(png_bytes))
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA")

    if not img.height or not target_h:
        raise RuntimeError("resize: zero dimension")

    src_ratio = img.width / img.height
    dst_ratio = target_w / target_h

    if abs(src_ratio - dst_ratio) > _ASPECT_TOL:
        _log(
            f"warning: aspect mismatch: source {img.width}x{img.height} "
            f"(ratio {src_ratio:.3f}) vs target {target_w}x{target_h} "
            f"(ratio {dst_ratio:.3f}); centre-cropping source"
        )
        if src_ratio > dst_ratio:
            new_w = max(1, int(round(img.height * dst_ratio)))
            left = (img.width - new_w) // 2
            img = img.crop((left, 0, left + new_w, img.height))
        else:
            new_h = max(1, int(round(img.width / dst_ratio)))
            top = (img.height - new_h) // 2
            img = img.crop((0, top, img.width, top + new_h))

    resized = img.resize((int(target_w), int(target_h)), Image.LANCZOS)
    buf = BytesIO()
    resized.save(buf, "PNG")
    return buf.getvalue()


def _paste_into_krita(png_bytes: bytes, x: int, y: int,
                      name: str) -> dict:
    b64 = base64.b64encode(png_bytes).decode("ascii")
    return krita_client.send({
        "type":       "comfy_paste_image_b64",
        "image_b64":  b64,
        "x":          int(x),
        "y":          int(y),
        "rotation":   0.0,
        "blend_mode": "normal",
        "name":       str(name),
    }, timeout=30.0)


# ---------------------------------------------------------------------------
#  Generate job orchestration
# ---------------------------------------------------------------------------

_generate_jobs: dict = {}
_generate_jobs_lock = threading.Lock()


def _new_job_id() -> str:
    return f"gen-{int(time.time() * 1000)}-{os.urandom(4).hex()}"


def _remove_job(job_id: str) -> None:
    with _generate_jobs_lock:
        _generate_jobs.pop(job_id, None)


def start_generation(payload: dict) -> str:
    """Validate the payload, spawn a background thread, return job id.

    Payload keys:
        viewer_x_norm, viewer_y_norm, viewer_w_norm, viewer_h_norm
        image_width, image_height
        ratio        (optional, str)   — viewer's ratio key
        prompt       (optional, str)
        seed         (optional, int | str)
        bubble_texts (optional, list[str])
    """
    for key in ("viewer_x_norm", "viewer_y_norm",
                "viewer_w_norm", "viewer_h_norm",
                "image_width", "image_height"):
        if key not in payload:
            raise ValueError(f"missing field: {key}")

    img_w = int(payload["image_width"])
    img_h = int(payload["image_height"])
    if img_w <= 0 or img_h <= 0:
        raise ValueError(
            "image dims must be positive; load an image first"
        )

    px_x = int(round(payload["viewer_x_norm"] * img_w))
    px_y = int(round(payload["viewer_y_norm"] * img_h))
    px_w = max(1, int(round(payload["viewer_w_norm"] * img_w)))
    px_h = max(1, int(round(payload["viewer_h_norm"] * img_h)))

    seed = payload.get("seed")
    if seed in (None, ""):
        seed = int.from_bytes(os.urandom(4), "big")
    else:
        try:
            seed = int(seed) & 0xFFFFFFFF
        except (TypeError, ValueError):
            raise ValueError(f"seed must be an integer, got {seed!r}")

    prompt = str(payload.get("prompt") or "")
    bubble_texts = payload.get("bubble_texts") or []
    if not isinstance(bubble_texts, list):
        bubble_texts = []
    combined_prompt = _combine_prompt(prompt, bubble_texts)

    ratio = str(payload.get("ratio") or _DEFAULT_RATIO).strip()
    if not ratio:
        ratio = _DEFAULT_RATIO

    job_id = _new_job_id()
    with _generate_jobs_lock:
        _generate_jobs[job_id] = {
            "status":     "pending",
            "message":    "queued",
            "started":    time.time(),
            "seed":       seed,
            "ratio":      ratio,
            "paste_rect": [px_x, px_y, px_w, px_h],
        }

    threading.Thread(
        target=_run_generation,
        args=(job_id, seed, px_x, px_y, px_w, px_h,
              combined_prompt, ratio),
        name=f"layout-gen-{job_id[-8:]}",
        daemon=True,
    ).start()
    return job_id


def get_job_status(job_id: str) -> dict:
    with _generate_jobs_lock:
        entry = _generate_jobs.get(job_id)
        return dict(entry) if entry else {}


def _update_job(job_id: str, **fields) -> None:
    with _generate_jobs_lock:
        entry = _generate_jobs.get(job_id)
        if entry is None:
            return
        entry.update(fields)


def _run_generation(job_id: str, seed: int,
                    px_x: int, px_y: int,
                    px_w: int, px_h: int,
                    combined_prompt: str,
                    ratio: str) -> None:
    try:
        _update_job(job_id, status="loading-workflow",
                    message="loading workflow file")
        workflow = _load_api_workflow()
        workflow = _substitute_seed(workflow, seed)
        workflow = _substitute_prompt(workflow, combined_prompt)
        workflow = _substitute_latent_size(workflow, ratio)

        _update_job(job_id, status="connecting",
                    message="ensuring proxy is running")
        try:
            _ensure_proxy_running()
        except RuntimeError as e:
            _update_job(job_id, status="error", message=str(e))
            return

        try:
            proxy = _get_proxy()
        except (ProxyUnreachable, ImportError) as e:
            _update_job(job_id, status="error",
                        message=f"proxy unavailable: {e}")
            return

        _update_job(job_id, status="submitting",
                    message="submitting to ComfyUI")
        comfy_job_id = _submit_workflow(proxy, workflow)
        _update_job(job_id, status="running",
                    message=f"ComfyUI job {comfy_job_id[-8:]}",
                    comfy_job_id=comfy_job_id)

        def _on_progress(job):
            prog = job.get("progress")
            if isinstance(prog, dict):
                v = prog.get("value")
                m = prog.get("max")
                if v is not None and m:
                    _update_job(job_id,
                                message=f"running · {v}/{m}")

        terminal = _poll_job(proxy, comfy_job_id,
                             on_progress=_on_progress)
        status = terminal.get("status")
        if status != "succeeded":
            err = ((terminal.get("error") or {}).get("message")
                   or status)
            _update_job(job_id, status="error",
                        message=f"ComfyUI {status}: {err}")
            return

        _update_job(job_id, status="fetching",
                    message="fetching image")
        img_bytes = _fetch_output_bytes(proxy, terminal)

        _update_job(job_id, status="resizing",
                    message=f"resizing to {px_w}x{px_h}")
        resized = _resize_png_bytes(img_bytes, px_w, px_h)

        _update_job(job_id, status="pasting",
                    message=f"pasting at ({px_x}, {px_y})")
        paste_resp = _paste_into_krita(
            resized, px_x, px_y,
            name=f"drawatoon gen {seed}",
        )
        if paste_resp.get("status") != "success":
            _update_job(
                job_id, status="error",
                message=(f"Krita paste failed: "
                         f"{paste_resp.get('message')}"),
            )
            return

        layer = ((paste_resp.get("data") or {}).get("layer_name")
                 or "?")
        _update_job(
            job_id, status="done",
            message=(f"pasted layer {layer!r} "
                     f"at ({px_x}, {px_y})"),
            layer_name=layer,
        )
        _log(f"job {job_id[-8:]} done: layer {layer!r} "
             f"seed={seed} ratio={ratio} size={px_w}x{px_h}")

    except Exception as e:
        _log(f"job {job_id[-8:]} failed: "
             f"{type(e).__name__}: {e}")
        _update_job(job_id, status="error",
                    message=f"{type(e).__name__}: {e}")
    finally:
        threading.Timer(
            _JOB_RETENTION_S, _remove_job, args=(job_id,),
        ).start()