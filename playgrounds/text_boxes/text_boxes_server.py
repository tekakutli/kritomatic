"""
text_boxes_server.py — route handlers for text_boxes's HTTP endpoints.

`pgkit.server.serve()` owns the actual HTTP server; this module supplies
the routes dict it dispatches through.

    /log                 { tag, message }                browser error log
    /debug               {}                              diagnostic dump
    /krita/dump_text     {}                              pull every text shape
    /krita/patch_text    { document_path, layer_name, text_index, patch }
    /krita/activate      { name }
    /krita/pick_files    {}                              native file picker
    /krita/open          { path, add_view }              open a .kra in Krita

LOG FORMAT
==========
`_log(msg)` prints `[text_boxes] msg` with no timestamp.  Same shape
as `[inpaint] ...`, `[multimask] ...`, `[segment] ...`,
`[interceptor] ...`, `[board] ...`, and the
`pgkit/server.py::_handle_log` output for `/__log` messages — one
tagged-prefix convention across every playground, so a terminal
running several side by side stays readable without a per-playground
timestamp parser.  (`datetime` was previously used here for an
`[HH:MM:SS]` prefix; it is now gone.)

THE /log ROUTE
==============
Browser-side `reportError` in tbox_krita.py no longer posts here —
it calls `console.error` and lets pgkit/panel_core.js's override
route the message through `POST ./__log` (handled by
`pgkit/server.py::_handle_log`).  This route is kept as a
compatibility endpoint: any external caller that still POSTs `{tag,
message}` gets a print in the standard `[text_boxes] js/{tag}: ...`
format.  Nothing in the current tree reaches it.
"""

import os
import socket

from . import text_boxes_krita


def _log(msg):
    print(f"[text_boxes] {msg}", flush=True)


# ----------------------------------------------------------------------------
#  Handlers — each takes a parsed JSON payload dict and returns a
#  response dict.  `pgkit.server.serve` handles the wire.
# ----------------------------------------------------------------------------

def handle_log(payload):
    tag = payload.get("tag", "js")
    msg = payload.get("message", "")
    print(f"[text_boxes] js/{tag}: {msg}", flush=True)
    return {"success": True}


def handle_debug(payload):
    """Diagnostic: report daemon reachability and route table.

    Hit this from a browser:  fetch('/debug', {method:'POST', body:'{}'})
    or from curl:             curl -s -X POST http://127.0.0.1:8771/debug
    """
    info = {
        "success": True,
        "cwd":     os.getcwd(),
        "routes":  sorted(ROUTES.keys()),
    }
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", 12346))
        s.close()
        info["daemon_reachable"] = True
    except OSError as e:
        info["daemon_reachable"] = False
        info["daemon_error"]     = str(e)
    return info


def handle_dump_text(payload):
    _log("dump_text")
    result = text_boxes_krita.dump_text_shapes()
    if not result.get("success"):
        _log(f"dump_text FAILED: {result.get('message')}")
    else:
        _log(f"dump_text ok: {len(result.get('shapes', []))} shape(s)")
    return result


def handle_patch_text(payload):
    doc   = payload.get("document_path", "")
    layer = payload.get("layer_name", "")
    idx   = int(payload.get("text_index", 0))
    patch = payload.get("patch", {})
    _log(f"patch_text: doc={doc!r} layer={layer!r} "
         f"idx={idx} keys={sorted(patch.keys())}")
    result = text_boxes_krita.patch_text_shape(doc, layer, idx, patch)
    if not result.get("success"):
        _log(f"patch_text FAILED: {result.get('message')}")
    return result


def handle_activate(payload):
    name = payload.get("name", "")
    _log(f"activate: {name!r}")
    return text_boxes_krita.activate_document(name)


def handle_pick_files(payload):
    _log("pick_files: opening native dialog")
    result = text_boxes_krita.pick_files()
    if not result.get("success"):
        _log(f"pick_files FAILED: {result.get('message')}")
    else:
        _log(f"pick_files ok: {len(result.get('paths', []))} path(s)")
    return result


def handle_open(payload):
    path     = payload.get("path", "")
    add_view = bool(payload.get("add_view", True))
    if not path:
        return {"success": False, "message": "no path"}
    _log(f"open: {path!r} add_view={add_view}")
    result = text_boxes_krita.open_file(path, add_view)
    if not result.get("success"):
        _log(f"open FAILED: {result.get('message')}")
    else:
        _log(f"open ok: name={result.get('name')!r}")
    return result


ROUTES = {
    "/log":               handle_log,
    "/debug":             handle_debug,
    "/krita/dump_text":   handle_dump_text,
    "/krita/patch_text":  handle_patch_text,
    "/krita/activate":    handle_activate,
    "/krita/pick_files":  handle_pick_files,
    "/krita/open":        handle_open,
}