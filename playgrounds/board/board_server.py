"""
board_server.py — route handlers for the board's HTTP endpoints.

`pgkit.server.serve()` owns the actual HTTP server; this module
supplies the routes dicts it dispatches through.  Two route tables:

    ROUTES         JSON POST endpoints.  Each handler takes a parsed
                   payload dict and returns a response dict.

    RAW_ROUTES     binary POST endpoints.  Each handler takes
                   (headers: dict, body: bytes) and returns a
                   response dict.

    /log                      { tag, message }              browser error log
    /krita/refresh            { paths, max_size }           sync state from Krita
    /krita/open               { path, add_view }            open a .kra in Krita
    /krita/activate           { name }                      bring a document forward
    /krita/pick_files         {}                            native file picker
    /krita/drop_file          raw body, filename in header  save a dropped file
    /krita/paste_transforms   { transforms: [...] }         transform-paste regions

The drop_file endpoint receives the file's raw bytes.  The browser
reads a dropped file via the File API (which hides the source path)
and streams it to the server; the server saves it under board_drops/
and returns the saved path.  The filename comes in the X-Filename
header, URL-encoded, because header values cannot contain arbitrary
bytes.
"""

import datetime
from urllib.parse import unquote

import board_krita


def _log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] board: {msg}", flush=True)


# ----------------------------------------------------------------------------
#  JSON handlers
# ----------------------------------------------------------------------------

def handle_log(payload):
    tag = payload.get("tag", "js")
    msg = payload.get("message", "")
    ts  = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] js/{tag}: {msg}", flush=True)
    return {"success": True}


def handle_refresh(payload):
    paths = payload.get("paths", [])
    max_size = int(payload.get("max_size", 512))
    _log(f"refresh: {len(paths)} path(s)")
    result = board_krita.refresh(paths, max_size)
    if not result.get("success"):
        _log(f"refresh FAILED: {result.get('message')}")
    else:
        opens = sum(1 for r in result.get("results", []) if r.get("open"))
        miss  = sum(1 for r in result.get("results", []) if r.get("missing"))
        _log(f"refresh ok: {opens} open, {miss} missing")
    return result


def handle_open(payload):
    path = payload.get("path", "")
    add_view = bool(payload.get("add_view", True))
    if not path:
        return {"success": False, "message": "no path"}
    _log(f"open: {path!r} add_view={add_view}")
    result = board_krita.open_file(path, add_view)
    if not result.get("success"):
        _log(f"open FAILED: {result.get('message')}")
    else:
        _log(f"open ok: name={result.get('name')!r}")
    return result


def handle_activate(payload):
    name = payload.get("name", "")
    _log(f"activate: {name!r}")
    result = board_krita.activate_document(name)
    if not result.get("success"):
        _log(f"activate FAILED: {result.get('message')}")
    else:
        _log(f"activate ok: {result.get('message')}")
    return result


def handle_pick_files(payload):
    _log("pick_files: opening native dialog")
    result = board_krita.pick_files()
    if not result.get("success"):
        _log(f"pick_files FAILED: {result.get('message')}")
    else:
        _log(f"pick_files ok: {len(result.get('paths', []))} path(s)")
    return result


def handle_paste_transforms(payload):
    transforms = payload.get("transforms", [])
    _log(f"paste_transforms: {len(transforms)} transform(s) requested")
    for t in transforms:
        _log(
            "  transform: "
            f"src={t.get('source_document')!r} "
            f"tgt={t.get('target_document')!r} "
            f"a={t.get('a',0):.6f} b={t.get('b',0):.6f} "
            f"c={t.get('c',0):.6f} d={t.get('d',0):.6f} "
            f"e={t.get('e',0):.1f} f={t.get('f',0):.1f}"
        )
    result = board_krita.paste_transforms(transforms)
    if not result.get("success"):
        _log(f"paste_transforms FAILED: {result.get('message')}")
    else:
        _log(f"paste_transforms done: "
             f"{result.get('successful',0)} ok, "
             f"{result.get('failed',0)} failed")
        for r in (result.get("results") or []):
            _log(f"  result[{r.get('index','?')}] "
                 f"{r.get('status','?')}: {r.get('message','')}")
    return result


# ----------------------------------------------------------------------------
#  Raw-body handlers
# ----------------------------------------------------------------------------

def handle_drop_file(headers, body):
    """Save a file the browser received from a drop event.

    The browser's File API hides the source path, so the only way to
    honour a filesystem drop in a served page is to receive the bytes
    and store them locally.  The file lands under board_drops/ next
    to the module; a numeric suffix is appended on collision.  The
    saved path is what goes on the board.

    The browser sets X-Filename to the URL-encoded basename.  Header
    values cannot contain arbitrary bytes, so the encode/decode
    round-trip is mandatory.
    """
    raw_name = headers.get("X-Filename", "")
    name = unquote(raw_name) if raw_name else "dropped_file"
    _log(f"drop_file: name={name!r} size={len(body)}")
    result = board_krita.save_dropped_file(name, body)
    if not result.get("success"):
        _log(f"drop_file FAILED: {result.get('message')}")
    else:
        _log(f"drop_file ok: {result.get('path')}")
    return result


# ----------------------------------------------------------------------------
#  Route tables
# ----------------------------------------------------------------------------

ROUTES = {
    "/log":                    handle_log,
    "/krita/refresh":          handle_refresh,
    "/krita/open":             handle_open,
    "/krita/activate":         handle_activate,
    "/krita/pick_files":       handle_pick_files,
    "/krita/paste_transforms": handle_paste_transforms,
}

RAW_ROUTES = {
    "/krita/drop_file":        handle_drop_file,
}
