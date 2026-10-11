"""
text_krita.py — Python-side bridge from this process to the running
Kritomatic daemon.

Every call and every result is logged.  `dump_text_shapes` pulls the
full text-shape dump; `patch_text_shape` routes a patch to the
correct document (activating it first, since patch_layer_text
operates on the active document); `pick_files` opens the OS-native
file dialog so the user can add .kra files to the board; `open_file`
opens a single .kra through the daemon.
"""

import json
import os
import sys
import threading
from pathlib import Path


def _log(msg):
    print(f"[text_krita] {msg}", flush=True)


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


def dump_text_shapes():
    """Pull every text shape across every open Krita document."""
    try:
        data, err = _call("dump_all_text_shapes")
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "shapes": data.get("shapes", [])}


def patch_text_shape(document_path, layer_name, text_index, patch):
    """Route a patch to one text shape via the daemon."""
    try:
        # patch_layer_text operates on the active document, so bring
        # the target document forward first.  A failure here is
        # non-fatal: if the wrong doc is already active, the patch
        # will fail its own layer lookup and say so.
        if document_path:
            try:
                _call("activate_document", name=document_path)
            except Exception:
                pass

        data, err = _call(
            "patch_layer_text",
            layer_name = layer_name,
            text_index = int(text_index),
            patch      = json.dumps(patch),
        )
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, **data}


def activate_document(name):
    try:
        data, err = _call("activate_document", name=name)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "message": f"Activated {name}"}


_picker_lock = threading.Lock()


def pick_files():
    """Open the OS-native file picker and return the chosen .kra paths."""
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
                title="Add Krita documents to the text board",
                filetypes=[
                    ("Krita documents", "*.kra *.krz"),
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


def open_file(path, add_view=True):
    """Open a .kra in Krita (used after the picker returns)."""
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
