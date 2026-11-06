"""
board_krita.py — Python-side bridge from this process to the running
Kritomatic daemon.

Every call and every result is logged to the terminal.  A failure on
either side — a refused connection, a per-command error inside a
batch — is printed with the daemon's own message intact, so the
reason is visible where the board was launched and not only in the
browser.

CROP FALLBACK
=============
The daemon does not expose a `create_document` command.  `crop_to_file`
therefore writes a transparent PNG of the target size to
`board_drops/`, opens it via `open_document`, and pastes the
transformed source on top of it as a new layer.  The blank pixels are
fully covered by the pasted content, so the result is equivalent to
what a native create-document command would produce.

The daemon's `open_document` response carries no `name` field (this
is the same reason `open_file`'s return has `"name": data.get("name", "")`
with a `""` default).  The identifier `paste_document_transform_as_layer`
actually wants — proven by the existing "project overlaps" feature —
is a *file path*, so when `name` is missing we fall back to the exact
string we passed to `open_document`.

SHADOWING NOTE
==============
`crop_to_file` takes affine elements `a, b, c, d, e, f` as
parameters.  The drop directory used to be stored in a local named
`d`, which silently shadowed the `d` parameter; the paste step then
did `float(d)` on a `PosixPath`.  The local is now `drop_dir`.  Any
future local added to that function must not collide with the six
parameter names.

GRID CELLS
==========
`crop_to_files` is the grid tool's batch entry point.  It calls
`crop_to_file` once per cell with the same source path and a different
target dimension + affine, collecting a per-cell result.  It is
strictly a loop over the existing single-cell function — the daemon
sees N separate `create_document` (or fallback) + paste sequences,
exactly as if the user had clicked "Create crop file" once per cell.

CALLER-SIDE RESPONSE SHAPE
==========================
Several daemon commands (notably `activate_document`) return
`{status: "success", message: ...}` with **no `data` key**.  The
daemon's command dispatcher explicitly writes `data: None` in that
case, and Python's `dict.get("data", {})` returns `None`, not `{}`,
when the key is present with value `None`.

Therefore every `_call(...)` caller that inspects `data` must treat
`err` as the success indicator, not `data`.  `activate_document` was
the last holdout — it checked `data is None`, which fired on the
no-data success path and produced `"activate FAILED: unknown"` in
the logs while Krita actually activated the document.  It now checks
`err is not None` instead, matching the shape `_call` documents.
"""

import json
import os
import sys
import threading
from pathlib import Path


def _log(msg):
    print(f"[board_krita] {msg}", flush=True)


def _find_repo_src() -> Path:
    p = Path(__file__).resolve().parent
    for _ in range(10):
        cand = p / "src"
        if (cand / "kritomatic").is_dir():
            return cand
        if p == p.parent:
            break
        p = p.parent
    raise RuntimeError(
        "Could not locate src/kritomatic from " + str(Path(__file__).resolve())
    )


try:
    from kritomatic.client import KritaClient
except ImportError:
    _SRC = _find_repo_src()
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))
    from kritomatic.client import KritaClient


def _norm(p):
    try:
        return os.path.realpath(os.path.expanduser(str(p)))
    except Exception:
        return str(p)


def _call(cmd_type, **kwargs):
    """Return (data, None) on success and (None, message) on failure.

    On success `data` may be `{}` *or* `None` — the daemon writes
    `data: None` for commands that do not return a payload.  Callers
    must test `err is not None` for failure, not `data is None`.
    """
    client = KritaClient()
    if not client.connect():
        _log(f"{cmd_type}: daemon not reachable")
        return None, "Kritomatic daemon not reachable"
    try:
        resp = client.execute(cmd_type, **kwargs)
    finally:
        client.close()
    if not resp:
        _log(f"{cmd_type}: no response")
        return None, "no response from daemon"
    if resp.get("status") != "success":
        msg = resp.get("message", "unknown daemon error")
        _log(f"{cmd_type}: FAILED: {msg}")
        return None, msg
    return resp.get("data", {}), None


def status():
    try:
        data, err = _call("get_all_documents")
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True,
            "documents_open": len((data or {}).get("documents", []))}


def refresh(paths, max_size=512):
    """Ask Krita which of `paths` are open, and which still exist on disk."""
    try:
        data, err = _call("get_all_document_thumbnails", max_size=max_size)
    except Exception as e:
        return {"success": False, "message": str(e)}

    if err is not None or data is None:
        try:
            fallback, ferr = _call("get_all_documents")
        except Exception as e:
            return {"success": False, "message": str(e)}
        if ferr is not None or fallback is None:
            return {"success": False, "message": err or ferr or "unknown"}

        incoming = []
        for d in fallback.get("documents", []):
            fn = d.get("name", "") or ""
            incoming.append({
                "name":      fn,
                "file_name": fn,
                "norm_path": _norm(fn) if fn else "",
                "width":     d.get("width", 0),
                "height":    d.get("height", 0),
                "resolution": d.get("resolution", 0),
                "modified":  d.get("modified", False),
                "data_url":  None,
                "active":    False,
            })
        partial = True
        partial_msg = ("Daemon does not expose get_all_document_thumbnails; "
                       "showing document names only.")
        active = None
    else:
        incoming = data.get("documents", []) or []
        for doc in incoming:
            fn = doc.get("file_name", "") or ""
            doc["norm_path"] = _norm(fn) if fn else ""
        active = data.get("active")
        partial = False
        partial_msg = ""

    norm_to_doc = {}
    for doc in incoming:
        n = doc.get("norm_path", "")
        if n:
            norm_to_doc[n] = doc

    results = []
    for p in paths or []:
        n = _norm(p)
        doc = norm_to_doc.get(n)
        if doc:
            results.append({
                "path":       p,
                "norm_path":  n,
                "open":       True,
                "missing":    False,
                "doc":        doc,
            })
        else:
            try:
                exists = os.path.isfile(os.path.expanduser(str(p)))
            except Exception:
                exists = False
            results.append({
                "path":       p,
                "norm_path":  n,
                "open":       False,
                "missing":    not exists,
                "doc":        None,
            })

    out = {
        "success":       True,
        "active":        active,
        "results":       results,
        "all_documents": incoming,
    }
    if partial:
        out["partial"] = True
        out["message"] = partial_msg
    return out


def open_file(path, add_view=True):
    try:
        data, err = _call("open_document",
                          file_path=path, add_view=add_view)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {
        "success": True,
        "message": f"opened {path}",
        "name":    (data or {}).get("name", ""),
    }


def activate_document(name):
    """Bring `name` (a document name or file path) to the front.

    The daemon command returns `{status: "success", message: ...}`
    with no `data` on success.  `_call` therefore yields
    `(None, None)` here, which is success — the trigger to test is
    `err is not None`, not `data is None`.  The previous
    `data is None` check fired on every successful call and produced
    a spurious `"activate FAILED: unknown"` log line while Krita
    actually activated the document.
    """
    try:
        data, err = _call("activate_document", name=name)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if err is not None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "message": f"Activated {name}"}


def paste_transforms(transforms):
    """Paste each transformed source into its target, in one bundled call.

    `transforms` is a list of dicts, each shaped:

        { name, source_document, target_document,
          a, b, c, d, e, f }

    where (a..f) is the 2x3 affine map from source-document pixels to
    target-document pixels, in column-vector form.

    Returns a summary the caller can surface: how many succeeded, how
    many failed, and the per-region messages.
    """
    if not transforms:
        _log("paste_transforms: nothing to send")
        return {"success": True, "successful": 0, "failed": 0, "results": []}

    client = KritaClient()
    if not client.connect():
        _log("paste_transforms: daemon not reachable")
        return {"success": False, "message": "Kritomatic daemon not reachable"}
    try:
        commands = [{"type": "paste_document_transform_as_layer", **t}
                    for t in transforms]
        payload = {"id": "board_project", "commands": commands}

        _log(f"paste_transforms: sending {len(commands)} command(s) in one batch")
        summary = client.send_batch(payload)
    finally:
        client.close()

    if not summary:
        _log("paste_transforms: no response from daemon")
        return {"success": False, "message": "no response from daemon"}

    results    = summary.get("results", []) or []
    successful = sum(1 for r in results if r.get("status") == "success")
    failed     = sum(1 for r in results if r.get("status") == "error")

    for r in results:
        idx = r.get("index", "?")
        st  = r.get("status", "?")
        msg = r.get("message", "")
        _log(f"  batch result[{idx}] {st}: {msg}")

    return {
        "success":    True,
        "successful": successful,
        "failed":     failed,
        "results":    results,
    }


_DROP_DIR = None


def _drop_dir():
    """Return the directory that dropped files are saved into,
    creating it on first use.  It lives beside this module so it
    survives restarts and is easy to find from a file manager."""
    global _DROP_DIR
    if _DROP_DIR is None:
        here = Path(__file__).resolve().parent
        d = here / "board_drops"
        d.mkdir(parents=True, exist_ok=True)
        _DROP_DIR = d
    return _DROP_DIR


def save_dropped_file(name, data_bytes):
    """Persist a file the browser received from a drop event.

    The browser's File API deliberately hides the source path, so
    when the board is served by a browser the only way to honour a
    filesystem drop is to receive the bytes and store them locally.
    Files land in board_drops/ next to this module, under their
    original name; a numeric suffix is appended if a file of that
    name already exists.
    """
    try:
        safe = os.path.basename(str(name)) or "dropped_file"
    except Exception:
        safe = "dropped_file"
    safe = safe.strip() or "dropped_file"

    try:
        drop_dir = _drop_dir()
    except Exception as e:
        _log(f"save_dropped_file: could not create drop dir: {e}")
        return {"success": False, "message": str(e)}

    target = drop_dir / safe
    if target.exists():
        stem, ext = os.path.splitext(safe)
        i = 1
        while True:
            cand = drop_dir / f"{stem}_{i}{ext}"
            if not cand.exists():
                target = cand
                break
            i += 1

    try:
        with open(target, "wb") as fh:
            fh.write(data_bytes)
    except Exception as e:
        _log(f"save_dropped_file: write failed: {e}")
        return {"success": False, "message": str(e)}

    path = str(target.resolve())
    _log(f"save_dropped_file: wrote {path} ({len(data_bytes)} bytes)")
    return {"success": True, "path": path, "name": target.name}


def _make_blank_png(width, height, rgba=(0, 0, 0, 0)):
    """Encode a solid-color PNG.  Pure Python — no PIL needed.

    Used by ``crop_to_file``'s fallback path when the daemon does
    not expose a ``create_document`` command: write a transparent
    PNG of the target size, open it in Krita, and paste the
    transformed source on top of it.  The blank pixels are fully
    covered by the pasted content, so the two paths produce
    equivalent results.
    """
    import struct
    import zlib

    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xffffffff))

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # RGBA8
    row = b"\x00" + bytes(rgba) * width
    raw = row * height
    compressed = zlib.compress(raw, 9)
    return (signature
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", compressed)
            + chunk(b"IEND", b""))


def crop_to_file(source_path, width, height, a, b, c, d, e, f):
    """Create a new Krita document of size (width, height), and paste
    the transformed source into it.

    The affine (a..f) is the 2x3 matrix mapping source-document pixels
    to target-document pixels in the codebase's convention::

        tx = a*sx + c*sy + e
        ty = b*sx + d*sy + f

    The caller is expected to have computed it so the crop region in
    the source maps to the full target canvas (see ``pg_crops.py``'s
    AFFINE CONVENTION note).

    Returns the same shape as ``paste_transforms`` plus a
    ``target_document`` field naming the new document.

    The six affine elements are parameters.  Do NOT introduce a local
    named after any of them (a, b, c, d, e, f) — the drop directory
    local used to be called `d`, which silently shadowed the `d`
    parameter and produced a `float(PosixPath)` TypeError at the paste
    step.  It is now `drop_dir`.
    """
    import time as _time

    width  = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        return {"success": False, "message": "invalid dimensions"}

    # 1. Create (or open a blank PNG as) the target document.
    create_resp = None
    create_err  = None
    try:
        create_resp, create_err = _call(
            "create_document",
            width=width, height=height,
            name=f"crop_{int(_time.time() * 1000)}.kra",
            resolution=300.0,
        )
    except Exception as ex:
        create_err = str(ex)

    target_name = None
    if create_resp is not None:
        target_name = create_resp.get("name")

    if not target_name:
        _log(f"crop_to_file: create_document unavailable ({create_err}); "
             f"using blank PNG fallback")
        try:
            png_bytes = _make_blank_png(width, height)
            drop_dir = _drop_dir()
        except Exception as ex:
            return {"success": False, "message": f"prepare: {ex}"}
        target_path = drop_dir / f"crop_{int(_time.time() * 1000)}_{width}x{height}.png"
        try:
            with open(target_path, "wb") as fh:
                fh.write(png_bytes)
        except Exception as ex:
            return {"success": False, "message": f"write: {ex}"}
        open_resp, open_err = _call(
            "open_document",
            file_path=str(target_path), add_view=True,
        )
        if open_resp is None and open_err is not None:
            return {"success": False,
                    "message": f"could not open blank PNG: {open_err}"}
        target_name = (open_resp or {}).get("name") or str(target_path)
        _log(f"crop_to_file: opened blank PNG; target_document = "
             f"{target_name!r}")

    if not target_name:
        return {"success": False,
                "message": "could not determine target document name"}

    # 2. Paste source → target with the affine.
    transforms = [{
        "name":            f"crop_of_{os.path.basename(source_path)}",
        "source_document": source_path,
        "target_document": target_name,
        "a": float(a), "b": float(b),
        "c": float(c), "d": float(d),
        "e": float(e), "f": float(f),
    }]
    result = paste_transforms(transforms)
    result["target_document"] = target_name
    return result


def crop_to_files(source_path, cells):
    """Create one new Krita document per grid cell.

    `cells` is a list of dicts, each shaped::

        { width, height, a, b, c, d, e, f }

    with the same affine convention as `crop_to_file`.  All cells share
    `source_path`; each becomes a separate target document.

    Returns::

        { success: True, successful: N, failed: M, results: [...] }

    where each result has ``index``, ``success`` (bool), ``message``,
    and ``target_document``.

    The loop is sequential and each iteration is a full
    create-target / paste cycle, so N cells cost N of the existing
    single-cell path.  That is intentional: it keeps the daemon
    interaction identical to N individual "Create crop file" clicks,
    which is what the current Krita daemon actually supports.
    """
    if not source_path:
        return {"success": False, "message": "no source_path"}
    if not isinstance(cells, list) or not cells:
        return {"success": False, "message": "no cells"}

    results = []
    for i, cell in enumerate(cells):
        try:
            width  = int(cell.get("width", 0))
            height = int(cell.get("height", 0))
            a = float(cell.get("a", 1.0))
            b = float(cell.get("b", 0.0))
            c = float(cell.get("c", 0.0))
            d = float(cell.get("d", 1.0))
            e = float(cell.get("e", 0.0))
            f = float(cell.get("f", 0.0))
        except (TypeError, ValueError) as ex:
            results.append({"index": i, "success": False,
                            "message": f"bad params: {ex}",
                            "target_document": ""})
            continue

        r = crop_to_file(source_path, width, height, a, b, c, d, e, f)
        ok = bool(r.get("success")) and (r.get("successful", 0) > 0)
        msg = ""
        if not ok:
            for rr in (r.get("results") or []):
                if rr.get("status") == "error":
                    msg = rr.get("message", "") or ""
                    break
            if not msg:
                msg = r.get("message", "unknown")
        results.append({
            "index": i,
            "success": ok,
            "message": msg,
            "target_document": r.get("target_document", ""),
        })
        _log(f"crop_to_files: cell[{i}] "
             f"{'ok' if ok else 'FAIL: ' + msg}")

    successful = sum(1 for r in results if r["success"])
    failed = len(results) - successful
    return {
        "success": True,
        "successful": successful,
        "failed": failed,
        "results": results,
    }


_picker_lock = threading.Lock()


def pick_files():
    with _picker_lock:
        try:
            import tkinter as tk
            from tkinter import filedialog
        except ImportError:
            _log("pick_files: tkinter not available")
            return {"success": False,
                    "message": "tkinter not installed; add paths via Load board"}

        try:
            root = tk.Tk()
            root.withdraw()
            try:
                root.attributes("-topmost", True)
            except Exception:
                pass
            paths = filedialog.askopenfilenames(
                title="Add files to the board",
                filetypes=[
                    ("Krita documents", "*.kra *.krz"),
                    ("Images",          "*.png *.jpg *.jpeg *.tif *.tiff *.webp *.psd"),
                    ("All files",       "*.*"),
                ],
            )
            root.destroy()
            return {"success": True, "paths": [str(p) for p in paths]}
        except Exception as e:
            try:
                root.destroy()
            except Exception:
                pass
            _log(f"pick_files: {e}")
            return {"success": False, "message": str(e)}