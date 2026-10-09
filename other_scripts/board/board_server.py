"""
board_server.py — the same tiny threaded server the cone playground
uses, plus two /krita endpoints that forward to the running Kritomatic
daemon.

    GET  /krita/status       → is the daemon reachable?
    GET  /krita/documents    → every open document with a base64
                               thumbnail and a small metadata block
    POST /krita/activate     → bring a document to the front in Krita

The Python side that talks to the daemon lives in board_krita.py.
This module only knows how to route HTTP to that bridge.
"""

import json
import os
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import board_krita


def serve(port=8770, open_browser=True, html_file="board_playground.html"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path.startswith("/krita/"):
                try:
                    if self.path == "/krita/status":
                        self._serve_json(200, board_krita.status())
                        return
                    if self.path.startswith("/krita/documents"):
                        max_size = 512
                        if "?" in self.path:
                            q = self.path.split("?", 1)[1]
                            for pair in q.split("&"):
                                if pair.startswith("max_size="):
                                    try:
                                        max_size = int(pair.split("=", 1)[1])
                                    except ValueError:
                                        pass
                        self._serve_json(200, board_krita.fetch_documents(max_size))
                        return
                except Exception as e:
                    traceback.print_exc()
                    self._serve_json(500, {"success": False, "message": str(e)})
                    return
            super().do_GET()

        def do_POST(self):
            if self.path != "/krita/activate":
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body   = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body) if body else {}
            except Exception as e:
                self._serve_json(400, {"success": False, "message": f"Bad request: {e}"})
                return
            try:
                name = payload.get("name", "")
                self._serve_json(200, board_krita.activate_document(name))
            except Exception as e:
                traceback.print_exc()
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
