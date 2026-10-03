"""
boxes_server.py — threaded HTTP server for the boxes playground.

Same shape as cable_server.py in the cable project: serves the
current directory, accepts POST /save with a JSON body (writes to
boxes_file), and POST /print with a JSON body (formats a table to the
terminal).  Disables caching for any *.json so a reload always sees the
latest file.
"""

import json
import os
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


def _format_box_table(boxes):
    if not boxes:
        return "(no boxes placed)"
    name_w = max(4, max(len(str(b.get("name", ""))) for b in boxes))
    lines = []
    lines.append(f"  {'Name'.ljust(name_w)}  "
                 f"{'Width'.rjust(10)}  {'Height'.rjust(10)}")
    lines.append("  " + "-" * (name_w + 26))
    for b in boxes:
        name = str(b.get("name", ""))
        try:    w = float(b.get("w", 0))
        except (TypeError, ValueError): w = 0.0
        try:    h = float(b.get("h", 0))
        except (TypeError, ValueError): h = 0.0
        lines.append(f"  {name.ljust(name_w)}  {w:10.2f}  {h:10.2f}")
    return "\n".join(lines)


def serve(port=8766,
          open_browser=True,
          html_file="boxes_playground.html",
          boxes_file="room_boxes.json"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def do_POST(self):
            if self.path == "/save":
                self._handle_save()
            elif self.path == "/print":
                self._handle_print()
            else:
                self.send_response(404)
                self.end_headers()

        def _handle_save(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                data = json.loads(body)
                with open(boxes_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                n = (len(data.get("boxes", []))
                     if isinstance(data, dict) else len(data))
                print(f"  saved {n} box(es) to {boxes_file}", flush=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": str(e)}).encode("utf-8"))

        def _handle_print(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                data = json.loads(body)
                boxes = (data.get("boxes", [])
                         if isinstance(data, dict) else data)
                rule = "=" * 60
                print("", flush=True)
                print(rule, flush=True)
                print(" BOX LIST", flush=True)
                print(rule, flush=True)
                print(_format_box_table(boxes), flush=True)
                print(rule, flush=True)
                print("", flush=True)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"error": str(e)}).encode("utf-8"))

        def end_headers(self):
            if self.path.endswith(".json"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, *args):
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
        print(f"Could not find a free port in [{port}, {port+20}).")
        return

    url = f"http://127.0.0.1:{used_port}/{html_file}"
    print(f"Serving {os.getcwd()}")
    print(f"  → open  {url}")
    print(f"  (boxes are saved to ./{boxes_file})")
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
