"""
cable_server.py — threaded HTTP server for the cable playground.

Serves files from the current working directory and accepts POST /save with
a JSON body, which it writes to `state_file`.  Disables caching for any
*.json so a reload always sees the latest layout.

Also accepts:

    POST /diag              plain text from the browser's arrow-field
                            diagnostic — printed to the terminal between
                            separator rules, so the diagnostic lives in
                            the same scrollback as the rest of the Python
                            output instead of only in the browser's
                            devtools console.  Returns 200 immediately.

    POST /optimize-leaders  a batch of leader-geometry optimisation
                            requests.  Pipes them through the Rust
                            `leader_optimizer` binary on stdin and
                            streams its stdout straight back as the
                            response body.  This is the load-bearing
                            path for the printable cable-run exporter:
                            the JS-side leader optimiser
                            (pg_export_optimizer.py /
                            pg_export_pillpush.py) was the slowest part
                            of the export, so it has been ported to Rust
                            and now runs as a subroutine of this
                            server.  The binary is located via the
                            LEADER_OPTIMIZER_BIN environment variable,
                            or `which leader_optimizer`, or
                            ./leader_optimizer/target/release/leader_optimizer.
                            A 500 with a JSON error body is returned if
                            the binary is missing or fails.

The Rust optimiser is invoked one process per request (no daemon); a
batch of N cables is a single request and a single process spawn.  The
60 s timeout is generous slack for pathological layouts and would
signal a runaway rather than normal use.
"""

import json
import os
import shutil
import subprocess
import threading
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


# Where the compiled Rust leader-optimizer binary lives.  The env var
# wins so a deployment can point elsewhere; then `which`; then a
# convention path next to this file.  If none of those resolves, the
# /optimize-leaders handler returns 500 and the JS side falls back to
# its own (slower) in-page optimiser.
RUST_BIN = (
    os.environ.get("LEADER_OPTIMIZER_BIN")
    or shutil.which("leader_optimizer")
    or os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "leader_optimizer", "target", "release", "leader_optimizer",
    )
)

# Per-request wall-clock cap on the Rust subroutine.  Long enough that
# no realistic cable layout hits it; short enough that a hang does not
# wedge the server thread forever.
RUST_TIMEOUT_SEC = 60


def serve(port=8765,
          open_browser=True,
          html_file="cable_playground.html",
          state_file="room_layout.json"):

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=os.getcwd(), **kwargs)

        def do_POST(self):
            if self.path == "/save":
                self._handle_save()
            elif self.path == "/diag":
                self._handle_diag()
            elif self.path == "/optimize-leaders":
                self._handle_optimize_leaders()
            else:
                self.send_response(404)
                self.end_headers()

        # ---- POST /save ------------------------------------------------
        def _handle_save(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                data = json.loads(body)
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                n = (len(data.get("floorGrids", [])) +
                     len(data.get("wallGrids", [])) +
                     len(data.get("floorCables", [])) +
                     len(data.get("wallCables", [])))
                print(f"  saved {n} item(s) to {state_file}", flush=True)
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

        # ---- POST /diag ------------------------------------------------
        # Plain text from the browser's arrow diagnostic.  Printed
        # between two rules so a long dump is easy to spot in the
        # scrollback.  flush=True on every line so a redirected stdout
        # (tee, nohup, >) still shows output live.
        def _handle_diag(self):
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                text = body.decode("utf-8", errors="replace")
                rule = "=" * 72
                print("",      flush=True)
                print(rule,    flush=True)
                print(" ARROW DIAGNOSTIC", flush=True)
                print(rule,    flush=True)
                print(text,    flush=True)
                print(rule,    flush=True)
                print("",      flush=True)
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

        # ---- POST /optimize-leaders ------------------------------------
        # A batch of leader-optimisation requests.  Body is
        # {"cables": [OptimizeRequest, ...]}.  Piped through the Rust
        # binary verbatim; the binary's stdout is the response.  See
        # the module docstring for where the binary lives.
        #
        # The subprocess.run call is blocking on this handler thread,
        # which is fine: ThreadingHTTPServer gives each request its own
        # thread, and a batch is one spawn.  The 60 s timeout is the
        # only guard against a pathological input hanging the handler.
        def _handle_optimize_leaders(self):
            if not os.path.exists(RUST_BIN):
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(
                    json.dumps({
                        "error": "leader_optimizer binary not found",
                        "path": RUST_BIN,
                        "hint": "cargo build --release in leader_optimizer/, "
                                "or set LEADER_OPTIMIZER_BIN",
                    }).encode("utf-8"))
                return

            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)

                proc = subprocess.run(
                    [RUST_BIN],
                    input=body,
                    capture_output=True,
                    timeout=RUST_TIMEOUT_SEC,
                )

                if proc.returncode != 0:
                    stderr_text = proc.stderr.decode(
                        "utf-8", errors="replace")[:1000]
                    raise RuntimeError(
                        f"leader_optimizer exited {proc.returncode}: "
                        f"{stderr_text or '(no stderr)'}")

                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(proc.stdout)

            except subprocess.TimeoutExpired:
                self.send_response(504)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(
                    json.dumps({
                        "error": "leader optimizer timed out",
                        "timeoutSec": RUST_TIMEOUT_SEC,
                    }).encode("utf-8"))
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
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
    print(f"  (state is saved to ./{state_file})")
    if os.path.exists(RUST_BIN):
        print(f"  leader optimizer: {RUST_BIN}")
    else:
        print(f"  leader optimizer: NOT FOUND at {RUST_BIN}")
        print(f"                    export will fall back to the "
              f"in-page JS optimiser")
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
