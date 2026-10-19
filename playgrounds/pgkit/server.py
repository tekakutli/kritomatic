"""
pgkit.server — HTTP file server plus the WebSocket endpoint.

`serve(...)` serves the current working directory the way the
existing playground servers do, and additionally accepts WebSocket
upgrades on `/remote` when a `Hub` is provided.  Any HTTP `POST`
routes the project cares about — the existing `/generate-kra` is
the canonical example — are registered as a dict of
`path -> handler(payload) -> response_dict` and dispatched through
the same handler class.

RAW-BODY ROUTES
===============
The `raw_routes` dict handles POSTs that carry non-JSON bodies
(file uploads, binary payloads, `application/octet-stream`).  Each
entry is `path -> handler(headers: dict, body: bytes) ->
response_dict`.  The handler is responsible for its own parsing;
`serve()` reads the body off the socket and hands it over
unmodified.  The response dict is JSON-serialised exactly as for a
JSON route, so the caller sees the same shape either way.

Raw routes take precedence over JSON routes when both declare the
same path.  Registering a path twice is a project-side bug, not a
kit-side one — the last dict definition wins at Python level, and
raw-before-JSON ordering just makes the intent explicit.

Port selection is the same loop the pair and cone servers use: try
`port`, `port + 1`, …, `port + 19`, take the first free one.
Opening the browser is optional and off a short timer so the
socket is listening by the time the tab loads.
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
          routes=None, raw_routes=None, hub=None):
    """Start the HTTP + WebSocket server and block until Ctrl+C.

    `routes` is `path -> handler(payload: dict) -> response_dict` for
    JSON POSTs.  `raw_routes` is `path -> handler(headers: dict,
    body: bytes) -> response_dict` for binary POSTs.  Both are
    optional; a project supplies only what it needs.
    """
    routes     = routes or {}
    raw_routes = raw_routes or {}
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
            """Read the body off the socket as raw bytes; hand it to
            the caller with the request headers.  The body is not
            decoded, parsed, or validated; that is the handler's
            problem."""
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
