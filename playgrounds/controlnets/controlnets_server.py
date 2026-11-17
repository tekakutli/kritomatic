"""HTTP routes for the controlnets playground.

The playground is a state host.  It exposes `{image_hash, image_dims,
controlnets, apply_enabled}` at `/state` for the `ControlNetsConditioning`
ComfyUI node to pull at generation time, plus Krita-proxy routes for
the browser.

Routes
======

    POST /state                  set (from browser) or get (from node
                                 AND browser; GET also returns
                                 `apply_nodes` merged with current
                                 enable flags)
    POST /version                version only (for IS_CHANGED sweeps)
    POST /krita_status           daemon reachable?
    POST /active_state           daemon comfy_status + playground registry
    POST /resource_image         resolve a {source, layer_name} spec to a PNG
    POST /playground_reregister  single-shot re-register with the daemon
    POST /preprocess             run every preprocessor wired into a
                                 ControlNetApply node in the workflow file;
                                 returns {title, hash, dataUrl, width, height}
                                 or {title, error}
    POST /controlnet_image       serve a preprocessed image by hash
                                 (consumed by the node)
    POST /krita/push_controlnets    create/replace a scratch Krita doc
                                    with one layer per controlnet
    POST /krita/refresh_controlnets read back the layers from that doc
                                    and update the server-side cache

There is no `/import_image` or `/upload_image` — the image can only
enter `/state` through `@thecontrolnet`.  `handle_import_image` still
exists as an internal helper for `handle_resource_image`.

APPLY NODES AND ENABLE FLAGS
============================
The workflow file at `<repo>/data/controlnets/controlnets_workflow.json`
contains the canonical list of `ControlNetApplyAdvanced` nodes.  This
module reads that file on every GET `/state` and returns:

    apply_nodes: [
        {title, preprocessor_title, controlnet_name, enabled}, ...
    ]

in `node.order` (i.e. the order the chain will be applied).  `enabled`
is merged from the state's `apply_enabled` dict, defaulting to `true`
for any title not explicitly set.

The `ControlNetsConditioning` node iterates its own chain (built from
the same file) and skips any entry whose `apply_title` maps to a
false value in `apply_enabled`.  Preprocessor entries in
`controlnets` are NEVER filtered — both DEPTH and DEPTH FULL need the
same Zoe-preprocessed image, and dropping the preprocessor because
one of its downstream applies is disabled would break the other.
"""

import base64
import hashlib
import json
import threading
import time
from io import BytesIO
from pathlib import Path

from PIL import Image

from . import krita_client, playground_registry, controlnets_comfy


PLAYGROUND_ID   = "controlnets"
PLAYGROUND_NAME = "Controlnets"
PLAYGROUND_PORT = 8779

CONTROLNETS_DOC_NAME = "kritomatic_controlnets"

LAYER_SLOTS = [
    {
        "id":                "@thecontrolnet",
        "auto_select_layer": "thecontrolnet",
    },
]


_state_lock = threading.RLock()
_state      = None
_version    = ""

# Preprocessed-image store.  Server-side so the node can fetch each
# hash via /controlnet_image without a live browser.  Capped so a
# long session that re-preprocesses against many different
# @thecontrolnet images does not grow without bound.
_preprocessed_lock  = threading.Lock()
_preprocessed       = {}
_PREPROCESSED_MAX   = 64


def _log(msg):
    print(f"[controlnets] {msg}", flush=True)


# ---------------------------------------------------------------------------
#  Workflow file discovery + apply-node extraction
# ---------------------------------------------------------------------------
#
# Symmetric with `kritomatic_nodes/controlnets/__init__.py`'s
# discovery — walks up from this file to find
# `<repo>/data/controlnets/controlnets_workflow.json`.  Server-side
# the file is only used to *list* the ControlNetApplyAdvanced nodes
# (so the panel can render their checkboxes and the node can consult
# `apply_enabled`).  The chain build itself happens in the ComfyUI
# node, which reads the same file via the symlink back to the repo.

def _find_workflow_path():
    p = Path(__file__).resolve().parent
    for _ in range(8):
        candidate = p / "data" / "controlnets" / "controlnets_workflow.json"
        if candidate.is_file():
            return str(candidate)
        if p == p.parent:
            break
        p = p.parent
    return str(p / "data" / "controlnets" / "controlnets_workflow.json")


def _load_workflow_apply_nodes():
    """Return ordered list of ControlNetApplyAdvanced nodes from the
    workflow file.  Each entry:

        {
            "title":              str,
            "preprocessor_title": str,
            "controlnet_name":    str,
        }

    Skip nodes with a missing image or control_net input link, or with
    an unrecognized control-net loader.  The order is `node.order`, so
    the panel renders them in chain-application order.
    """
    path = _find_workflow_path()
    try:
        wf = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"could not load workflow file {path!r}: {e}")
        return []

    nodes = {n["id"]: n for n in (wf.get("nodes") or [])}
    links = {}
    for link in (wf.get("links") or []):
        lid, oid, osl, tid, tsl, typ = link
        links[lid] = (oid, osl, tid, tsl, typ)

    apply_nodes = [
        n for n in (wf.get("nodes") or [])
        if n.get("type") == "ControlNetApplyAdvanced"
    ]
    apply_nodes.sort(key=lambda n: n.get("order", n.get("id", 0)))

    out = []
    for node in apply_nodes:
        image_link = None
        ctrl_link = None
        for inp in (node.get("inputs") or []):
            name = inp.get("name")
            if name == "image":
                image_link = inp.get("link")
            elif name == "control_net":
                ctrl_link = inp.get("link")
        if image_link is None or ctrl_link is None:
            continue

        pre_oid = links.get(image_link, (None,))[0]
        cn_oid = links.get(ctrl_link, (None,))[0]
        preproc = nodes.get(pre_oid)
        cn_loader = nodes.get(cn_oid)
        if preproc is None or cn_loader is None:
            continue

        pre_title = preproc.get("title") or preproc.get("type") or ""
        apply_title = (
            node.get("title")
            or f"Apply#{node.get('id', 0)}"
        )

        cn_name = None
        named = cn_loader.get("widgets_values_named") or {}
        if named:
            cn_name = named.get("control_net_name")
        if cn_name is None:
            wv = cn_loader.get("widgets_values") or []
            if wv:
                cn_name = wv[0]
        if not cn_name:
            continue

        out.append({
            "title":              apply_title,
            "preprocessor_title": pre_title,
            "controlnet_name":    cn_name,
        })
    return out


# ---------------------------------------------------------------------------
#  Preprocessed-image store
# ---------------------------------------------------------------------------

def _store_preprocessed(h: str, png_bytes: bytes,
                        width: int, height: int) -> None:
    with _preprocessed_lock:
        if len(_preprocessed) >= _PREPROCESSED_MAX:
            oldest = next(iter(_preprocessed))
            _preprocessed.pop(oldest, None)
        _preprocessed[h] = {
            "png_bytes": png_bytes,
            "width":     width,
            "height":    height,
        }


# ---------------------------------------------------------------------------
#  State normalisation
# ---------------------------------------------------------------------------

def _default_state():
    return {
        "image_hash":    "",
        "image_dims":    [0, 0],
        "controlnets":   [],
        "apply_enabled": {},
    }


def _read_state_snapshot():
    with _state_lock:
        if _state is None:
            return _default_state(), _version
        return dict(_state), _version


def _normalize_state(raw):
    hash_val = raw.get("image_hash") or ""
    if not isinstance(hash_val, str):
        hash_val = ""

    try:
        w, h = raw.get("image_dims") or [0, 0]
        dims = [max(0, int(w)), max(0, int(h))]
    except (TypeError, ValueError):
        dims = [0, 0]

    controlnets = []
    for cn in (raw.get("controlnets") or []):
        if isinstance(cn, dict):
            controlnets.append(cn)

    apply_enabled = {}
    raw_ae = raw.get("apply_enabled")
    if isinstance(raw_ae, dict):
        for k, v in raw_ae.items():
            if isinstance(k, str) and isinstance(v, bool):
                apply_enabled[k] = v

    return {
        "image_hash":    hash_val,
        "image_dims":    dims,
        "controlnets":   controlnets,
        "apply_enabled": apply_enabled,
    }


def _compute_version(state):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
#  /state, /version, /playground_reregister
# ---------------------------------------------------------------------------

def handle_state(payload):
    global _state, _version
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    if payload.get("op") == "set":
        raw = payload.get("state") or {}
        normalized = _normalize_state(raw)

        # If the caller omitted `apply_enabled` (older clients, or the
        # panel's first sync before it has fetched the apply-node
        # list), preserve whatever the server already had.  A
        # non-empty dict in the payload always replaces — including
        # one where every value is `false` (that is a valid "user
        # disabled everything" state, not "clear the field").
        if not normalized["apply_enabled"]:
            with _state_lock:
                if _state is not None:
                    normalized["apply_enabled"] = dict(
                        _state.get("apply_enabled") or {}
                    )

        new_version = _compute_version(normalized)
        with _state_lock:
            _state = normalized
            _version = new_version
        _log(
            f"state set: hash={normalized['image_hash'][:8]} "
            f"dims={normalized['image_dims']} "
            f"controlnets={len(normalized['controlnets'])} "
            f"apply_enabled={len(normalized['apply_enabled'])} "
            f"v{new_version[:8]}"
        )
        return {"success": True, "version": new_version}

    state_snapshot, version_snapshot = _read_state_snapshot()

    # Merge in workflow-file apply nodes with current enabled flags.
    # Re-reads the workflow file every GET so that adding a node to
    # the file is picked up by the panel on the next page refresh.
    apply_nodes = _load_workflow_apply_nodes()
    enabled_map = state_snapshot.get("apply_enabled") or {}
    apply_nodes_with_state = [
        {**n, "enabled": enabled_map.get(n["title"], True)}
        for n in apply_nodes
    ]

    return {
        "success":     True,
        "version":     version_snapshot,
        "apply_nodes": apply_nodes_with_state,
        **state_snapshot,
    }


def handle_version(_payload):
    with _state_lock:
        return {"success": True, "version": _version}


def handle_playground_reregister(_payload):
    url = f"http://127.0.0.1:{PLAYGROUND_PORT}"
    ok = playground_registry.reregister_once(
        PLAYGROUND_ID, PLAYGROUND_NAME, url, PLAYGROUND_PORT,
        layer_slots=LAYER_SLOTS,
    )
    return {"success": bool(ok)}


# ---------------------------------------------------------------------------
#  /preprocess, /controlnet_image
# ---------------------------------------------------------------------------

def handle_preprocess(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    image_b64 = payload.get("image_b64")
    if not image_b64 or not isinstance(image_b64, str):
        return {"success": False, "message": "image_b64 required"}

    try:
        png_bytes = base64.b64decode(image_b64)
    except Exception as e:
        return {"success": False, "message": f"decode failed: {e}"}

    try:
        results = controlnets_comfy.preprocess_all(png_bytes)
    except Exception as e:
        _log(f"preprocess_all failed: {e}")
        return {"success": False, "message": str(e)}

    out = []
    for r in results:
        if "error" in r:
            out.append({"title": r["title"], "error": r["error"]})
            continue
        png = r["png_bytes"]
        h = hashlib.sha256(png).hexdigest()[:16]
        _store_preprocessed(h, png, r["width"], r["height"])
        data_url = ("data:image/png;base64,"
                    + base64.b64encode(png).decode("ascii"))
        out.append({
            "title":   r["title"],
            "hash":    h,
            "dataUrl": data_url,
            "width":   r["width"],
            "height":  r["height"],
        })

    return {"success": True, "results": out}


def handle_controlnet_image(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}
    h = payload.get("hash")
    if not h:
        return {"success": False, "message": "hash required"}
    with _preprocessed_lock:
        entry = _preprocessed.get(h)
    if not entry:
        return {"success": False, "message": f"unknown hash {h!r}"}
    b64 = base64.b64encode(entry["png_bytes"]).decode("ascii")
    return {
        "success": True,
        "hash":    h,
        "b64":     b64,
        "width":   entry["width"],
        "height":  entry["height"],
    }


# ---------------------------------------------------------------------------
#  /krita/push_controlnets, /krita/refresh_controlnets
# ---------------------------------------------------------------------------

def _all_documents():
    resp = krita_client.send({"type": "get_all_documents"}, timeout=15.0)
    if resp.get("status") != "success":
        return None, resp.get("message") or "daemon error"
    return ((resp.get("data") or {}).get("documents") or []), None


def handle_krita_push_controlnets(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    doc_name = str(payload.get("doc_name") or CONTROLNETS_DOC_NAME).strip()
    try:
        doc_w = int(payload.get("doc_width") or 0)
        doc_h = int(payload.get("doc_height") or 0)
    except (TypeError, ValueError):
        return {"success": False, "message": "bad doc dims"}
    controlnets = payload.get("controlnets") or []

    if doc_w <= 0 or doc_h <= 0:
        return {"success": False, "message": "doc dims must be positive"}
    if not controlnets:
        return {"success": False, "message": "no controlnets to push"}

    docs, err = _all_documents()
    if err:
        return {"success": False, "message": err}
    doc_exists = any(d.get("name") == doc_name for d in docs)

    if doc_exists:
        existing = next(d for d in docs if d.get("name") == doc_name)
        same_dims = (
            int(existing.get("width") or 0) == doc_w
            and int(existing.get("height") or 0) == doc_h
        )
        if not same_dims:
            krita_client.send(
                {"type": "close_document", "name": doc_name},
                timeout=15.0,
            )
            resp = krita_client.send({
                "type": "create_new_with_dimensions",
                "name": doc_name,
                "width": doc_w,
                "height": doc_h,
            }, timeout=60.0)
            if resp.get("status") != "success":
                return {"success": False,
                        "message": f"recreate doc failed: "
                                   f"{resp.get('message')}"}
    else:
        resp = krita_client.send({
            "type": "create_new_with_dimensions",
            "name": doc_name,
            "width": doc_w,
            "height": doc_h,
        }, timeout=60.0)
        if resp.get("status") != "success":
            return {"success": False,
                    "message": f"create doc failed: "
                               f"{resp.get('message')}"}

    commands = []
    titles = []
    for cn in controlnets:
        title = (cn.get("title") or "").strip()
        png_b64 = cn.get("png_b64") or ""
        if not title or not png_b64:
            continue
        try:
            raw = base64.b64decode(png_b64)
            pil = Image.open(BytesIO(raw))
            if pil.size != (doc_w, doc_h):
                pil = pil.convert("RGBA").resize(
                    (doc_w, doc_h), Image.LANCZOS,
                )
                buf = BytesIO()
                pil.save(buf, "PNG")
                scaled_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            else:
                scaled_b64 = png_b64
        except Exception as e:
            _log(f"scale failed for {title!r}: {e}")
            continue

        commands.append({
            "type": "create_paint_layer_from_image_b64",
            "image_b64": scaled_b64,
            "name": title,
            "replace": True,
            "document": doc_name,
        })
        titles.append(title)

    if not commands:
        return {"success": False, "message": "no valid controlnets"}

    batch_id = f"push_cn_{int(time.time() * 1000)}"
    summary = krita_client.send_batch(
        {"id": batch_id, "commands": commands},
        timeout=180.0,
    )

    if not summary or summary.get("status") not in ("batch_complete", "success"):
        msg = (summary.get("message") if isinstance(summary, dict) else None) \
              or "batch send failed"
        return {"success": False, "message": msg}

    failed = int(summary.get("failed", 0) or 0)
    pushed = []
    failed_titles = []
    for r in summary.get("results") or []:
        idx = r.get("index", -1)
        title = titles[idx] if 0 <= idx < len(titles) else "?"
        if r.get("status") == "success":
            pushed.append(title)
        else:
            failed_titles.append(f"{title}: {r.get('message', '?')}")

    _log(
        f"push to {doc_name!r}: {len(pushed)}/{len(titles)} layer(s)"
        + (f" ({failed} failed)" if failed else "")
    )
    return {
        "success": failed == 0,
        "message": f"pushed {len(pushed)}/{len(titles)} layer(s) to {doc_name}",
        "pushed":   pushed,
        "failed":   failed_titles,
        "doc_name": doc_name,
    }


def handle_krita_refresh_controlnets(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    doc_name = str(payload.get("doc_name") or CONTROLNETS_DOC_NAME).strip()
    titles = payload.get("titles") or []
    if not titles:
        return {"success": False, "message": "no titles to refresh"}

    docs, err = _all_documents()
    if err:
        return {"success": False, "message": err}
    if not any(d.get("name") == doc_name for d in docs):
        return {"success": False,
                "message": f"doc {doc_name!r} not found — push first"}

    results = []
    for title in titles:
        try:
            resp = krita_client.send({
                "type": "get_layer_pixels_b64",
                "name": title,
                "document": doc_name,
            }, timeout=30.0)
            if resp.get("status") != "success":
                results.append({
                    "title": title,
                    "error": resp.get("message") or "read failed",
                })
                continue
            data_url = (resp.get("data") or {}).get("data_url")
            if not data_url:
                results.append({"title": title, "error": "no data_url"})
                continue

            raw = base64.b64decode(data_url.split(",", 1)[1])
            pil = Image.open(BytesIO(raw))
            w, h = pil.size
            h_hex = hashlib.sha256(raw).hexdigest()[:16]
            _store_preprocessed(h_hex, raw, w, h)

            results.append({
                "title":   title,
                "hash":    h_hex,
                "dataUrl": data_url,
                "width":   w,
                "height":  h,
            })
        except Exception as e:
            _log(f"refresh failed for {title!r}: {e}")
            results.append({"title": title, "error": str(e)})

    n_ok = len([r for r in results if "error" not in r])
    _log(f"refresh from {doc_name!r}: {n_ok}/{len(titles)} layer(s)")
    return {
        "success": True,
        "message": f"refreshed {n_ok}/{len(titles)} layer(s) from {doc_name}",
        "results": results,
    }


# ---------------------------------------------------------------------------
#  Krita proxies (unchanged)
# ---------------------------------------------------------------------------

def _fetch_active_state():
    try:
        resp = krita_client.send({"type": "comfy_status"}, timeout=10.0)
    except Exception as e:
        return None, str(e)
    if not isinstance(resp, dict):
        return None, "bad daemon response"
    if resp.get("status") != "success":
        return None, resp.get("message") or "daemon rejected comfy_status"
    return resp.get("data") or {}, None


def handle_active_state(_payload):
    data, err = _fetch_active_state()
    if err is not None:
        return {
            "success": False,
            "daemon_reachable": False,
            "message": err,
        }
    return {
        "success": True,
        "daemon_reachable": True,
        "data": data,
    }


def handle_krita_status(_payload):
    return {"success": True, "reachable": krita_client.is_reachable()}


def handle_import_image(payload):
    name = (payload or {}).get("name") if isinstance(payload, dict) else None
    cmd = {"type": "get_document_composite_b64"}
    if name:
        cmd["name"] = name

    response = krita_client.send(cmd, timeout=60.0)
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
        img = Image.open(BytesIO(png_bytes))
        w, h = img.size
    except Exception as e:
        return {"success": False, "message": f"decode failed: {e}"}

    hash_hex = hashlib.sha256(png_bytes).hexdigest()[:16]
    return {
        "success":   True,
        "hash":      hash_hex,
        "width":     w,
        "height":    h,
        "data_url":  data_url,
        "filename":  data.get("name") or "krita_composite",
    }


def handle_resource_image(payload):
    if not isinstance(payload, dict):
        return {"success": False, "message": "expected a JSON object"}

    source = payload.get("source", "active")
    layer_name = payload.get("layer_name")

    if source == "active":
        return handle_import_image({})
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
        "message": f"source={source!r} cannot be displayed as an image.",
    }


ROUTES = {
    "/state":                       handle_state,
    "/version":                     handle_version,
    "/krita_status":                handle_krita_status,
    "/active_state":                handle_active_state,
    "/resource_image":              handle_resource_image,
    "/playground_reregister":       handle_playground_reregister,
    "/preprocess":                  handle_preprocess,
    "/controlnet_image":            handle_controlnet_image,
    "/krita/push_controlnets":      handle_krita_push_controlnets,
    "/krita/refresh_controlnets":   handle_krita_refresh_controlnets,
}

RAW_ROUTES = {}