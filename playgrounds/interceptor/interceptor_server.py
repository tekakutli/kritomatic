"""HTTP routes for the interceptor playground.

Routes:
  POST /intercept/intake         accept a new task from a source playground
  POST /intercept/list           small summary of all tasks (polled)
  POST /intercept/item           full task body incl. payload, on selection
  POST /intercept/run            run handle_inpaint, strip expansion, callback
  POST /intercept/discard        drop a queued task; callback gets {error}
  POST /intercept/cancel         alias for discard, used by source's cancel
  POST /intercept/krita_open     open the (expanded) canvas in Krita
  POST /intercept/krita_refresh  fetch active document composite as PNG

DORMANT STATUS
==============
The module imports cleanly today even though
`inpaint_server.handle_inpaint` does not exist (inpaint's playground
was refactored into a state-synchronizer — no generation — so that
symbol is gone).  `handle_run` calls `_resolve_handle_inpaint()`, which
returns the real function once inpaint's generation path is restored,
and a stub that produces a normal task-error until then.  This keeps
`run_all.py`, lint, and the dev tooling working while the interceptor
is parked; nothing on the interceptor side needs to change when the
generation path returns.

DOCUMENT NAME RESOLUTION
========================
The daemon's `open_document` response carries no `name` field.  The
different commands that operate on a document have *different*
matching rules:

  · `paste_document_transform_as_layer` (used by board_krita.py) accepts
    a file path — that's why board_krita.py's open_file returns the
    path as `name` on a missing `name` in the response.

  · `get_document_composite_b64` accepts an *internal* document
    identifier, not a path.  Passing the path yields "no matching
    document".

`_lookup_document_name` bridges the gap: after `open_document`, it
enumerates the daemon's open documents and matches by file path to
recover the internal name.  `handle_krita_refresh` additionally
falls back to the *active* document (our `add_view=True` open made
it active) if the name-lookup still fails, so a stale or
unresolvable name never leaves the user without a refresh path.
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
from io import BytesIO
from pathlib import Path

from PIL import Image

# `from inpaint.inpaint_server import handle_inpaint` used to live
# here.  It no longer resolves: inpaint's playground was refactored
# into a state-synchronizer (no generation), so its
# inpaint_server.py does not define `handle_inpaint` today.  The
# generation path is expected to return; until it does, the call
# site uses `_resolve_handle_inpaint()` below to fetch it lazily and
# fall back to a dormant stub.  See that function's docstring.
from inpaint import krita_client


_TASKS: dict = {}
_LOCK = threading.Lock()
_GC_MAX_AGE_SEC = 1800   # 30 min


def _log(msg):
    print(f"[interceptor] {msg}", flush=True)


def _post_json(url, body, timeout=10.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()


def _post_callback(url, body, task_id):
    body = dict(body)
    body["task_id"] = task_id
    for attempt in range(3):
        try:
            status, _ = _post_json(url, body, timeout=10.0)
            _log(f"callback {url} -> {status}")
            return
        except Exception as e:
            _log(f"callback {url} attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    _log(f"callback {url} abandoned after 3 attempts")


def _gc():
    now = time.time()
    with _LOCK:
        stale = [tid for tid, t in _TASKS.items()
                 if (now - t["received_at"]) > _GC_MAX_AGE_SEC
                 and t["status"] in ("done", "error", "cancelled")]
        for tid in stale:
            del _TASKS[tid]


def _norm_path(p):
    try:
        return os.path.realpath(os.path.expanduser(str(p)))
    except Exception:
        return str(p)


def _lookup_document_name(file_path, max_size=64):
    """Resolve a Krita document's internal name from its file path.

    `open_document` returns no `name` field in its response, but
    `get_document_composite_b64` needs the internal identifier, not
    the path.  Enumerate open documents and match by path.

    Tries `get_all_document_thumbnails` first (returns both `name` and
    `file_name`), then `get_all_documents` (whose `name` the daemon
    may set to the path).  Falls back to basename matching in case the
    daemon reports relative paths.

    Returns None if no match — caller falls back to using the raw
    path and lets refresh's active-document fallback resolve it.
    """
    target_norm = _norm_path(file_path)
    target_base = os.path.basename(str(file_path))

    def _match(field_value):
        if not field_value:
            return False
        if _norm_path(field_value) == target_norm:
            return True
        if target_base and os.path.basename(str(field_value)) == target_base:
            return True
        return False

    # Primary: thumbnails endpoint has both name and file_name.
    try:
        resp = krita_client.send(
            {"type": "get_all_document_thumbnails", "max_size": max_size},
            timeout=10.0,
        )
        if isinstance(resp, dict) and resp.get("status") == "success":
            docs = (resp.get("data") or {}).get("documents", []) or []
            for doc in docs:
                if _match(doc.get("file_name", "")):
                    name = doc.get("name") or ""
                    if name:
                        return name
    except Exception as e:
        _log(f"_lookup_document_name: thumbnails query failed: {e}")

    # Fallback: get_all_documents.
    try:
        resp = krita_client.send(
            {"type": "get_all_documents"}, timeout=10.0,
        )
        if isinstance(resp, dict) and resp.get("status") == "success":
            docs = (resp.get("data") or {}).get("documents", []) or []
            for doc in docs:
                if _match(doc.get("name", "")):
                    name = doc.get("name") or ""
                    if name:
                        return name
    except Exception as e:
        _log(f"_lookup_document_name: get_all_documents failed: {e}")

    return None


# ---------------------------------------------------------------------------
#  Task management — unchanged
# ---------------------------------------------------------------------------

def handle_intake(payload):
    task_id      = (payload or {}).get("task_id")
    callback_url = (payload or {}).get("callback_url")
    inner        = (payload or {}).get("payload")
    if not task_id or not callback_url or not isinstance(inner, dict):
        return {"success": False, "message": "bad intake shape"}

    with _LOCK:
        if task_id in _TASKS:
            return {"success": False, "message": "task_id already exists"}
        _TASKS[task_id] = {
            "task_id":       task_id,
            "callback_url":  callback_url,
            "source_label":  (payload or {}).get("source_label", ""),
            "received_at":   time.time(),
            "status":        "queued",
            "payload":       inner,
            "result":        None,
            "message":       "",
            "callback_sent": False,
        }
    _gc()
    _log(f"intake {task_id[:8]} from {payload.get('source_label')!r}")
    return {"success": True, "task_id": task_id}


def handle_list(_payload):
    with _LOCK:
        tasks = [
            {
                "task_id":      t["task_id"],
                "source_label": t["source_label"],
                "received_at":  t["received_at"],
                "status":       t["status"],
                "has_result":   t["result"] is not None,
            }
            for t in sorted(_TASKS.values(),
                            key=lambda x: -x["received_at"])
        ]
    return {"success": True, "tasks": tasks}


def handle_item(payload):
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        return {"success": True, "task": {
            "task_id":      t["task_id"],
            "source_label": t["source_label"],
            "received_at":  t["received_at"],
            "status":       t["status"],
            "payload":      t["payload"],
            "result":       t["result"],
            "message":      t["message"],
        }}


# ---------------------------------------------------------------------------
#  Run
# ---------------------------------------------------------------------------
#
# Inpaint's generation path (`inpaint_server.handle_inpaint`) is not
# present in the current tree; the playground no longer submits
# generation.  Interceptor is parked until that path returns, but the
# surrounding tooling (run_all.py, pgkit.lint, the dev servers) must
# still import this module cleanly.  `_resolve_handle_inpaint` gives
# us both: a clean import today, and a transparent swap to the real
# function the moment it exists again.

def _resolve_handle_inpaint():
    """Return `inpaint_server.handle_inpaint`, or a stub.

    On a tree where inpaint's generation path exists, this resolves
    the real function and returns it — no behavioural difference from
    the previous top-of-file import.

    On a tree where it does not (the current one), this returns
    `_unavailable_inpaint`, whose return value matches the shape the
    rest of `handle_run` already expects from a failed call:
    `{"success": False, "message": ...}`.  The existing failure path
    (task status → `"error"`, one callback with `{"error": msg}`)
    handles it without any branching.
    """
    try:
        from inpaint.inpaint_server import handle_inpaint
        return handle_inpaint
    except ImportError:
        return _unavailable_inpaint


def _unavailable_inpaint(_body):
    return {
        "success": False,
        "message": (
            "interceptor is dormant: inpaint_server.handle_inpaint "
            "is unavailable (inpaint playground does not currently "
            "generate). Interceptor resumes automatically once "
            "inpaint's generation path returns."
        ),
    }


def handle_run(payload):
    payload = payload or {}
    task_id = payload.get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] not in ("queued", "done", "error"):
            return {"success": False, "message": f"task is {t['status']}"}
        t["status"] = "running"
        entry = dict(t)

    expansion = payload.get("expansion") or {}
    try:
        left   = max(0, int(expansion.get("left",   0)))
        top    = max(0, int(expansion.get("top",    0)))
        right  = max(0, int(expansion.get("right",  0)))
        bottom = max(0, int(expansion.get("bottom", 0)))
    except (TypeError, ValueError):
        left = top = right = bottom = 0
    has_exp = (left + top + right + bottom) > 0

    aug_canvas = payload.get("augmented_canvas_b64")
    use_augmented = has_exp and bool(aug_canvas)

    if use_augmented and (entry["payload"].get("output_rotation") or 0) != 0:
        msg = "expansion not supported when source payload is rotated"
        with _LOCK:
            t = _TASKS.get(task_id)
            if t:
                t["status"]  = "error"
                t["message"] = msg
        return {"success": False, "message": msg}

    if use_augmented:
        body = {
            "canvas_b64":     aug_canvas,
            "selection_b64":  payload.get("augmented_selection_b64")
                              or entry["payload"].get("selection_b64"),
            "context_b64":    payload.get("augmented_context_b64")
                              or entry["payload"].get("context_b64"),
            "output_rotation": 0,
            "prompt":         payload.get("prompt")
                              if payload.get("prompt") is not None
                              else entry["payload"].get("prompt", ""),
        }
    else:
        body = dict(entry["payload"])
        if payload.get("prompt") is not None:
            body["prompt"] = payload["prompt"]

    _log(f"run {task_id[:8]} "
         f"(prompt={body.get('prompt', '')[:40]!r}"
         + (f" exp=L{left} T{top} R{right} B{bottom}" if use_augmented else "")
         + ")")

    try:
        result = _resolve_handle_inpaint()(body)
    except Exception as e:
        result = {"success": False,
                  "message": f"handle_inpaint threw: {e}"}

    if result.get("success"):
        full_b64 = result.get("image_b64", "")
        send_b64 = full_b64
        if use_augmented:
            try:
                img = Image.open(BytesIO(base64.b64decode(full_b64)))
                W_full, H_full = img.size
                W_orig = W_full - left - right
                H_orig = H_full - top  - bottom
                if W_orig > 0 and H_orig > 0:
                    cropped = img.crop((left, top,
                                        left + W_orig, top + H_orig))
                    buf = BytesIO()
                    cropped.save(buf, "PNG")
                    send_b64 = base64.b64encode(buf.getvalue()).decode("ascii")
            except Exception as e:
                _log(f"crop failed ({e}); sending full")

        with _LOCK:
            t = _TASKS.get(task_id)
            if t is not None:
                t["status"] = "done"
                t["result"] = {
                    "image_b64":         full_b64,
                    "cropped_image_b64": send_b64,
                    "timing":            result.get("timing"),
                }
                first_callback = not t["callback_sent"]
                if first_callback:
                    t["callback_sent"] = True

        if first_callback:
            _post_callback(entry["callback_url"],
                           {"image_b64": send_b64,
                            "timing":    result.get("timing")},
                           entry["task_id"])
        else:
            _log(f"re-run {task_id[:8]}: source callback suppressed")

        return {"success": True,
                "image_b64": full_b64,
                "timing":    result.get("timing")}

    msg = result.get("message", "unknown")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is not None:
            t["status"]  = "error"
            t["message"] = msg
            first_callback = not t["callback_sent"]
            if first_callback:
                t["callback_sent"] = True
    if first_callback:
        _post_callback(entry["callback_url"], {"error": msg},
                       entry["task_id"])
    return {"success": False, "message": msg}


def handle_discard(payload):
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] != "queued":
            return {"success": False, "message": f"task is {t['status']}"}
        t["status"]  = "cancelled"
        t["message"] = "discarded by interceptor"
        entry = dict(t)
        first_callback = not t["callback_sent"]
        if first_callback:
            t["callback_sent"] = True
    if first_callback:
        _post_callback(entry["callback_url"],
                       {"error": "discarded by interceptor"},
                       entry["task_id"])
    return {"success": True}


def handle_cancel(payload):
    task_id = (payload or {}).get("task_id")
    with _LOCK:
        t = _TASKS.get(task_id)
        if t is None:
            return {"success": False, "message": "unknown task"}
        if t["status"] != "queued":
            return {"success": True}
        t["status"]  = "cancelled"
        t["message"] = "cancelled by source"
        entry = dict(t)
        first_callback = not t["callback_sent"]
        if first_callback:
            t["callback_sent"] = True
    if first_callback:
        _post_callback(entry["callback_url"],
                       {"error": "cancelled by source"},
                       entry["task_id"])
    return {"success": True}


# ---------------------------------------------------------------------------
#  Krita round-trip
# ---------------------------------------------------------------------------

_KRITA_TMPDIR = None

def _krita_tmpdir():
    global _KRITA_TMPDIR
    if _KRITA_TMPDIR is None:
        _KRITA_TMPDIR = Path(tempfile.mkdtemp(prefix="interceptor_"))
    return _KRITA_TMPDIR


def handle_krita_open(payload):
    canvas_b64 = (payload or {}).get("canvas_b64")
    if not canvas_b64:
        return {"success": False, "message": "no canvas_b64"}

    try:
        png_bytes = base64.b64decode(canvas_b64)
    except Exception as e:
        return {"success": False, "message": f"bad b64: {e}"}

    filename = f"intercept_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}.png"
    target   = _krita_tmpdir() / filename
    try:
        target.write_bytes(png_bytes)
    except Exception as e:
        return {"success": False, "message": f"write failed: {e}"}

    resp = krita_client.send({
        "type":      "open_document",
        "file_path": str(target),
        "add_view":  True,
    }, timeout=30.0)
    if not isinstance(resp, dict) or resp.get("status") != "success":
        msg = (resp or {}).get("message", "open_document rejected")
        return {"success": False, "message": msg}

    data = resp.get("data") or {}
    doc_name = data.get("name")

    # `open_document` doesn't return a name in practice.  Look up the
    # internal identifier via document enumeration; fall back to the
    # path (which refresh's active-document fallback will resolve) if
    # the lookup fails.
    if not doc_name:
        doc_name = _lookup_document_name(target)

    looked_up = bool(doc_name) and doc_name != str(target)
    if not doc_name:
        doc_name = str(target)

    _log(f"krita_open: {filename} -> {doc_name!r} "
         f"({'resolved' if looked_up else 'path fallback'})")
    return {
        "success":       True,
        "document_name": doc_name,
        "file_hint":     filename,
        "file_path":     str(target),
    }


def handle_krita_refresh(payload):
    doc_name = (payload or {}).get("document_name")
    if not doc_name:
        return {"success": False, "message": "no document_name"}

    resp = krita_client.send({
        "type": "get_document_composite_b64",
        "name": doc_name,
    }, timeout=60.0)

    # If the daemon couldn't match the name, fall back to the active
    # document.  `open_document` with `add_view=True` made ours active,
    # so unless the user has switched away in Krita, this returns the
    # right composite.  This handles the case where the name we stored
    # was a path fallback (see `handle_krita_open`), and covers any
    # daemon whose get_document_composite_b64 identifier differs from
    # what we looked up.
    if not isinstance(resp, dict) or resp.get("status") != "success":
        _log(f"krita_refresh: name resolution failed for {doc_name!r}; "
             f"retrying as active document")
        resp = krita_client.send(
            {"type": "get_document_composite_b64"}, timeout=60.0,
        )

    if not isinstance(resp, dict) or resp.get("status") != "success":
        msg = (resp or {}).get("message", "composite rejected")
        return {"success": False, "message": msg}

    data = resp.get("data") or {}
    data_url = data.get("data_url")
    if not data_url or not data_url.startswith("data:image/png;base64,"):
        return {"success": False,
                "message": "no data_url in composite response"}

    b64 = data_url.split(",", 1)[1]
    try:
        png = base64.b64decode(b64)
        img = Image.open(BytesIO(png))
        w, h = img.size
    except Exception as e:
        return {"success": False, "message": f"decode failed: {e}"}

    _log(f"krita_refresh: {doc_name!r} -> {w}x{h}")
    return {
        "success":    True,
        "canvas_b64": b64,
        "width":      w,
        "height":     h,
    }


ROUTES = {
    "/intercept/intake":        handle_intake,
    "/intercept/list":          handle_list,
    "/intercept/item":          handle_item,
    "/intercept/run":           handle_run,
    "/intercept/discard":       handle_discard,
    "/intercept/cancel":        handle_cancel,
    "/intercept/krita_open":    handle_krita_open,
    "/intercept/krita_refresh": handle_krita_refresh,
}

RAW_ROUTES = {}
