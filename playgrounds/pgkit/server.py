"""
pgkit.server — HTTP file server, WebSocket endpoint, module serving.

`serve(...)` serves the current working directory, accepts WebSocket
upgrades on `/remote`, dispatches JSON POST routes and raw-body POST
routes, and additionally serves `/modules/<name>` for every entry in
`module_sources` (the project's `_MODULE_SOURCES` dict).

Module serving
==============
`GET /modules/pgkit/remote_client.js` looks up
`module_sources["pgkit/remote_client.js"]` and serves the string with
`Content-Type: application/javascript` and `Cache-Control: no-store`.
The no-store matters during development: a stale cached module
combined with a fresh boot module produces load-order mismatches
that are hard to spot.

A missing module is a 404 with a short text body naming the missing
key and listing the keys that *are* present — the browser will show
it in the network tab and the console, so a typo in an `import`
path is visible without server access.

Port selection is the same loop the pair and cone servers use.
"""

import json
import os
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import ws as _ws
from .remote_hub import Client, Hub, handle_message


def serve(*, port=8766, open_browser=True,
          html_file="playground.html",
          routes=None, raw_routes=None,
          module_sources=None, hub=None):
    """Start the HTTP + WebSocket server and block until Ctrl+C."""
    routes         = routes or {}
    raw_routes     = raw_routes or {}
    module_sources = module_sources or {}
    if hub is None:
        hub = Hub()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/remote" and \
               self.headers.get("Upgrade", "").lower() == "websocket":
                self._serve_websocket()
                return
            if self.path.startswith("/modules/"):
                name = self.path[len("/modules/"):]
                self._serve_module(name)
                return
            super().do_GET()

        def do_POST(self):
            if self.path in raw_routes:
                self._handle_raw_post(raw_routes[self.path])
                return
            handler = routes.get(self.path)
            if handler is None:
                self.send_error(404)
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body   = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body) if body else {}
            except Exception as e:
                self._send_json(400, {
                    "success": False,
                    "message": "Bad request: %s" % e,
                })
                return
            try:
                result = handler(payload)
            except Exception as e:
                traceback.print_exc()
                result = {
                    "success": False,
                    "message": "Server error: %s" % e,
                }
            self._send_json(200, result)

        def _handle_raw_post(self, handler):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
            except Exception as e:
                self._send_json(400, {
                    "success": False,
                    "message": "Bad request: %s" % e,
                })
                return
            try:
                result = handler(dict(self.headers), body)
            except Exception as e:
                traceback.print_exc()
                result = {
                    "success": False,
                    "message": "Server error: %s" % e,
                }
            self._send_json(200, result)

        def _send_json(self, code, data):
            body = json.dumps(data).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _serve_module(self, name):
            source = module_sources.get(name)
            if source is None:
                available = ", ".join(sorted(module_sources.keys())) or "(none)"
                body = (
                    f"Module not found: {name!r}\n"
                    f"Available modules: {available}\n"
                ).encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            body = source.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/javascript; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _serve_websocket(self):
            key = self.headers.get("Sec-WebSocket-Key")
            if not key:
                self.send_error(400, "Missing Sec-WebSocket-Key")
                return

            self.wfile.write(_ws.handshake_response(key))
            self.wfile.flush()

            sock = self.connection
            client = Client(sock)
            hub.add(client)

            try:
                while True:
                    text = client.recv()
                    if text is None:
                        break
                    if not text:
                        continue
                    try:
                        msg = json.loads(text)
                    except Exception:
                        continue
                    handle_message(hub, client, msg)
            except Exception:
                pass
            finally:
                hub.remove(client)
                try:
                    sock.close()
                except Exception:
                    pass

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
        print("Could not find a free port in [%d, %d)." % (port, port + 20))
        return

    url = "http://127.0.0.1:%d/%s" % (used_port, html_file)
    print("Serving %s" % os.getcwd())
    print("  -> open  %s" % url)
    print("  -> modules at /modules/ (%d file(s))" % len(module_sources))
    print("  -> remote ws at ws://127.0.0.1:%d/remote" % used_port)
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
