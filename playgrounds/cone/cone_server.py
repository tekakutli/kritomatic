"""
cone_server.py — the same tiny threaded server the cable playground
uses, minus the /save and /optimize-leaders endpoints this project
does not need.  Serves the current working directory.

On top of static file serving, it exposes one POST endpoint:

    POST /generate-kra
        Body:   the payload documented in cone_kra.generate()
        Return: {"success": bool, "message": str, ...}

which forwards to cone_kra.generate.  That module is the only place
this server touches the Kritomatic daemon.
"""

import json
import os
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def serve(port=8766, open_browser=True, html_file="cone_playground.html"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def log_message(self, *args):
            pass

        def do_POST(self):
            if self.path != "/generate-kra":
                self.send_error(404)
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                body   = self.rfile.read(length).decode("utf-8")
                payload = json.loads(body)
            except Exception as e:
                self._send_json(400, {
                    "success": False,
                    "message": f"Bad request: {e}",
                })
                return

            # Import lazily so a failure to find src/kritomatic surfaces
            # as a JSON response rather than a traceback the browser
            # cannot read.
            try:
                import cone_kra
                result = cone_kra.generate(payload)
            except Exception as e:
                traceback.print_exc()
                result = {
                    "success": False,
                    "message": f"Server error: {e}",
                }

            self._send_json(200, result)

        def _send_json(self, code, data):
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
