"""
board_krita.py — Python-side bridge from this process to the running
Kritomatic daemon.

Every call and every result is logged to the terminal.  A failure on
either side — a refused connection, a per-command error inside a
batch — is printed with the daemon's own message intact, so the
reason is visible where the board was launched and not only in the
browser.
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
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "documents_open": len(data.get("documents", []))}


def refresh(paths, max_size=512):
    """Ask Krita which of `paths` are open, and which still exist on disk."""
    try:
        data, err = _call("get_all_document_thumbnails", max_size=max_size)
    except Exception as e:
        return {"success": False, "message": str(e)}

    if data is None:
        try:
            fallback, ferr = _call("get_all_documents")
        except Exception as e:
            return {"success": False, "message": str(e)}
        if fallback is None:
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
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {
        "success": True,
        "message": f"opened {path}",
        "name":    data.get("name", ""),
    }


def activate_document(name):
    try:
        data, err = _call("activate_document", name=name)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
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
