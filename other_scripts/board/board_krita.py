"""
board_krita.py — Python-side bridge from this process to the running
Kritomatic daemon.

Three calls, each its own fresh socket round-trip:

    status()              is the daemon reachable?
    fetch_documents()     every open document, with a base64 data-URL
                          thumbnail and a metadata block
    activate_document()   bring one document to the front in Krita

The daemon's own CLI/plugin is what actually performs these; this
module just packages the args and unwraps the responses.

Usage of the CLI tool
=====================
The three commands this module issues — `get_all_document_thumbnails`,
`activate_document`, and (falling back) `get_all_documents` — are
standard `doc` category commands of the daemon.  The board project
does not add commands of its own; the daemon is used as-is.

If the daemon is unreachable, every call returns
{"success": False, "message": ...} rather than raising.  The server
turns that into a JSON response the browser can render.
"""

import json
import sys
from pathlib import Path


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


# The CLI is normally pipx-installed (-e .) so `kritomatic.client`
# imports directly.  When running from a bare checkout without the
# install, fall back to locating src/kritomatic via the parent walk.
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
        return None, "Kritomatic daemon not reachable"
    try:
        resp = client.execute(cmd_type, **kwargs)
    finally:
        client.close()
    if not resp:
        return None, "no response from daemon"
    if resp.get("status") != "success":
        return None, resp.get("message", "unknown daemon error")
    return resp.get("data", {}), None


def status():
    """Is the daemon reachable, and does it report any open documents?"""
    try:
        data, err = _call("get_all_documents")
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {
        "success": True,
        "documents_open": len(data.get("documents", [])),
    }


def fetch_documents(max_size=512):
    """Return every open document with a data-URL thumbnail."""
    try:
        data, err = _call("get_all_document_thumbnails", max_size=max_size)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
        # Old daemon without get_all_document_thumbnails.  Fall back
        # to the plain document list so the board still shows cards,
        # just without thumbnails.
        try:
            fallback, ferr = _call("get_all_documents")
        except Exception as e:
            return {"success": False, "message": str(e)}
        if fallback is None:
            return {"success": False, "message": err or ferr or "unknown"}
        docs = fallback.get("documents", [])
        return {
            "success": True,
            "documents": [
                {
                    "name": d.get("name", ""),
                    "width": d.get("width", 0),
                    "height": d.get("height", 0),
                    "modified": d.get("modified", False),
                    "active": False,
                    "data_url": None,
                }
                for d in docs
            ],
            "active": None,
            "partial": True,
            "message": (
                "Daemon does not expose get_all_document_thumbnails; "
                "showing document names only."
            ),
        }
    return {
        "success": True,
        "documents": data.get("documents", []),
        "active": data.get("active"),
    }


def activate_document(name):
    try:
        data, err = _call("activate_document", name=name)
    except Exception as e:
        return {"success": False, "message": str(e)}
    if data is None:
        return {"success": False, "message": err or "unknown"}
    return {"success": True, "message": f"Activated {name}"}
