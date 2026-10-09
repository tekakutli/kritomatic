"""
board_server.py — threaded HTTP server.

    POST /krita/refresh       { paths, max_size }
    POST /krita/open          { path, add_view }
    POST /krita/activate      { name }
    POST /krita/pick_files    {}
    POST /krita/paste_regions { regions: [...] }
    POST /log                 { tag, message }

Every request and response is logged to the terminal, and the /log
endpoint lets the browser forward its own caught errors here so
there is one place to look.
"""

import datetime
import json
import os
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import board_krita


def _log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] board: {msg}", flush=True)


def serve(port=8770, open_browser=True, html_file="board_playground.html"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def log_message(self, *args):
            pass

        def _read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            body   = self.rfile.read(length).decode("utf-8") if length else ""
            return json.loads(body) if body else {}

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

                if self.path == "/krita/refresh":
                    payload = self._read_json()
                    paths = payload.get("paths", [])
                    max_size = int(payload.get("max_size", 512))
                    _log(f"refresh: {len(paths)} path(s)")
                    result = board_krita.refresh(paths, max_size)
                    if not result.get("success"):
                        _log(f"refresh FAILED: {result.get('message')}")
                    else:
                        opens = sum(1 for r in result.get("results", [])
                                    if r.get("open"))
                        miss  = sum(1 for r in result.get("results", [])
                                    if r.get("missing"))
                        _log(f"refresh ok: {opens} open, {miss} missing")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/open":
                    payload = self._read_json()
                    path = payload.get("path", "")
                    add_view = bool(payload.get("add_view", True))
                    if not path:
                        self._serve_json(400, {"success": False,
                                               "message": "no path"})
                        return
                    _log(f"open: {path!r} add_view={add_view}")
                    result = board_krita.open_file(path, add_view)
                    if not result.get("success"):
                        _log(f"open FAILED: {result.get('message')}")
                    else:
                        _log(f"open ok: name={result.get('name')!r}")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/activate":
                    payload = self._read_json()
                    name = payload.get("name", "")
                    _log(f"activate: {name!r}")
                    result = board_krita.activate_document(name)
                    if not result.get("success"):
                        _log(f"activate FAILED: {result.get('message')}")
                    else:
                        _log(f"activate ok: {result.get('message')}")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/pick_files":
                    _log("pick_files: opening native dialog")
                    result = board_krita.pick_files()
                    if not result.get("success"):
                        _log(f"pick_files FAILED: {result.get('message')}")
                    else:
                        _log(f"pick_files ok: {len(result.get('paths', []))} path(s)")
                    self._serve_json(200, result)
                    return

                if self.path == "/krita/paste_transforms":
                    payload = self._read_json()
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
                                 f"{r.get('status','?')}: "
                                 f"{r.get('message','')}")
                    self._serve_json(200, result)
                    return

                self.send_error(404)
            except Exception as e:
                traceback.print_exc()
                _log(f"unhandled error: {e}")
                self._serve_json(500, {"success": False, "message": str(e)})

        def _serve_json(self, code, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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
