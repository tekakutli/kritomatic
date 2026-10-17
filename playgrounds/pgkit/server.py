"""
pgkit.server — HTTP file server plus the WebSocket endpoint.

`serve(...)` serves the current working directory the way the
existing playground servers do, and additionally accepts WebSocket
upgrades on `/remote` when a `Hub` is provided.  Any HTTP `POST`
routes the project cares about — the existing `/generate-kra` is
the canonical example — are registered as a dict of
`path -> handler(payload) -> response_dict` and dispatched through
the same handler class.

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
          routes=None, hub=None):
    """Start the HTTP + WebSocket server and block until Ctrl+C."""
    routes = routes or {}
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
