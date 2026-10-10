"""
text_server.py — threaded HTTP server for the text board.

    POST /krita/dump_text      { }                 pull every text shape
    POST /krita/patch_text     { document_path, layer_name, text_index, patch }
    POST /krita/activate       { name }
    POST /krita/pick_files     { }                 native file picker
    POST /krita/open           { path, add_view }  open a .kra in Krita
    POST /log                  { tag, message }

Every request and response is logged to the terminal; the /log
endpoint lets the browser forward its own caught errors here.
"""

import datetime
import json
import os
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import text_krita


def _log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] text: {msg}", flush=True)


def serve(port=8771, open_browser=True, html_file="text_board.html"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def log_message(self, *args):
            pass

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            body   = self.rfile.read(length).decode("utf-8") if length else ""
            return json.loads(body) if body else {}

        def _serve_json(self, code, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            try:
                if self.path == "/log":
                    payload = self._read_json()
                    tag = payload.get("tag", "js")
                    msg = payload.get("message", "")
                    ts  = datetime.datetime.now().strftime("%H:%M:%S")
                    print(f"[{ts}] js/{tag}: {msg}", flush=True)
                    self._serve_json(200, {"success": True})
                    return

                if self.path == "/krita/dump_text":
                    _log("dump_text")
                    result = text_krita.dump_text_shapes()
                    if not result.get("success"):
                        _log(f"dump_text FAILED: {result.get('message')}")
                    else:
                        _log(f"dump_text ok: "
                             f"{len(result.get('shapes', []))} shape(s)")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/patch_text":
                    payload = self._read_json()
                    doc   = payload.get("document_path", "")
                    layer = payload.get("layer_name", "")
                    idx   = int(payload.get("text_index", 0))
                    patch = payload.get("patch", {})
                    _log(f"patch_text: doc={doc!r} layer={layer!r} "
                         f"idx={idx} keys={sorted(patch.keys())}")
                    result = text_krita.patch_text_shape(doc, layer, idx, patch)
                    if not result.get("success"):
                        _log(f"patch_text FAILED: {result.get('message')}")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/activate":
                    payload = self._read_json()
                    name = payload.get("name", "")
                    _log(f"activate: {name!r}")
                    result = text_krita.activate_document(name)
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/pick_files":
                    _log("pick_files: opening native dialog")
                    result = text_krita.pick_files()
                    if not result.get("success"):
                        _log(f"pick_files FAILED: {result.get('message')}")
                    else:
                        _log(f"pick_files ok: "
                             f"{len(result.get('paths', []))} path(s)")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/open":
                    payload = self._read_json()
                    path = payload.get("path", "")
                    add_view = bool(payload.get("add_view", True))
                    if not path:
                        self._serve_json(400,
                                         {"success": False,
                                          "message": "no path"})
                        return
                    _log(f"open: {path!r} add_view={add_view}")
                    result = text_krita.open_file(path, add_view)
                    if not result.get("success"):
                        _log(f"open FAILED: {result.get('message')}")
                    else:
                        _log(f"open ok: name={result.get('name')!r}")
                    self._serve_json(200, result)
                    return

                self.send_error(404)
            except Exception as e:
                traceback.print_exc()
                _log(f"unhandled error: {e}")
                self._serve_json(500, {"success": False, "message": str(e)})

    httpd = None
    used_port = None
    for p in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", p), Handler)
            used_port = p
            break
        except OSError:
            continue
    if httpd is None:
        print(f"Could not find a free port in [{port}, {port + 20}).")
        return

    url = f"http://127.0.0.1:{used_port}/{html_file}"
    print(f"Serving {os.getcwd()}")
    print(f"  -> open  {url}")
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
