"""
pgkit.server — HTTP file server, WebSocket endpoint, module serving.

`make_handler(routes, raw_routes, module_sources)` builds a fresh
`SimpleHTTPRequestHandler` subclass — one per playground, each with
its own `pgkit.remote_hub.Hub` instance.  `serve()` calls it and then
blocks; `serve_nonblocking()` calls it and returns the pieces so a
launcher can own threading and shutdown.

Module serving
==============
`GET /modules/<name>` looks up `module_sources[name]` and serves it
with `Cache-Control: no-store`.  A missing module is a 404 with a
short text body listing the present keys.

Built-in routes
===============
`POST /__log` is installed unconditionally by `make_handler` on every
playground, ahead of the per-playground `routes` lookup.  The browser
client (see `pgkit/panel_core.py::_postLog`) uses it to mirror panel
errors and uncaught JS exceptions to the playground server's stdout,
so a developer watching the terminal sees the same errors the browser
shows in the panel's status line.  Route name is `__log` to mark it
as an internal pgkit endpoint; the client must POST to `./__log`
(relative to the page), not `/__log`, so that under the pgkit hub the
request lands on `/<name>/__log` and the hub proxies it back to the
playground, rather than on the hub root where nothing handles it.

Hub registration
================
Both serve paths register with the pgkit hub if `hub_name` is given.
`serve_nonblocking` additionally accepts `group` (metadata; see
`pgkit/hub.py`'s GROUPS section) and `html_render` (a zero-argument
callable producing the HTML page, which `serve_nonblocking` writes
to disk before serving).  Standalone `serve()` keeps its old
behavior: it serves an HTML file the caller has already written.
"""

import json
import os
import sys
import threading
import traceback
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import ws as _ws
from .remote_hub import Client, Hub, handle_message


def make_handler(routes=None, raw_routes=None, module_sources=None, hub=None):
    """Create a fresh handler class for one playground.

    Each call produces a fresh closure and a fresh Hub (unless one is
    supplied) so two playgrounds in the same process never share
    routes, module tables, or WS peers.
    """
    routes = routes or {}
    raw_routes = raw_routes or {}
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
            # Built-in route, ahead of per-playground routes so that no
            # playground needs to know about it.
            if self.path == "/__log":
                self._handle_log()
                return
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
                result = {"success": False, "message": "Server error: %s" % e}
            self._send_json(200, result)

        def _handle_log(self):
            """Print a browser-side panel error to the server's stdout.

            Called by `pgkit/panel_core.py`'s `_postLog` for two kinds
            of message:

              * flashStatus(..., "bad") — panel errors that the user
                sees in the status line and that the server's terminal
                should show too.  `class` is "bad" and the message is
                exactly what the panel showed.

              * Uncaught JS exceptions and unhandled promise
                rejections — window.onerror and
                window.onunhandledrejection listeners in
                panel_core.py.  `class` is "js-error" or
                "js-rejection"; the message is err.stack when
                available.

            `prefix` is the playground's name derived from
            document.title (e.g. "inpaint", "multimask").  The output
            matches the `[inpaint] ...` / `[multimask] ...` prefix
            that each playground's own server-side `_log()` helpers
            use, so a terminal running several playgrounds under the
            hub stays readable.

            Multi-line messages (JS stack traces) are printed with the
            first line prefixed and subsequent lines indented by four
            spaces, matching the convention `traceback.print_exc`
            would use for its own frames.

            Response is a bare {"success": true} so the client's
            fire-and-forget fetch resolves without inspecting the
            body.
            """
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
                body = self.rfile.read(length) if length else b""
            except Exception as e:
                self._send_json(400, {"success": False,
                                      "message": str(e)})
                return
            try:
                payload = json.loads(body.decode("utf-8")) if body else {}
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                self._send_json(400, {"success": False,
                                      "message": str(e)})
                return
            if not isinstance(payload, dict):
                self._send_json(400, {"success": False,
                                      "message": "expected an object"})
                return
            msg    = str(payload.get("message") or "")
            cls    = str(payload.get("class") or "")
            prefix = str(payload.get("prefix") or "panel")
            if msg:
                lines = msg.splitlines() or [msg]
                tag = f"[{prefix}]"
                if cls:
                    tag += f" {cls}:"
                print(f"{tag} {lines[0]}", flush=True)
                for line in lines[1:]:
                    print(f"    {line}", flush=True)
            self._send_json(200, {"success": True})

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
                result = {"success": False, "message": "Server error: %s" % e}
            self._send_json(200, result)

        def _send_json(self, status, obj):
            body = json.dumps(obj).encode("utf-8")
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                # Force connection close on every JSON response. The
                # playground's SimpleHTTPRequestHandler-based server
                # does not implement HTTP/1.1 keep-alive correctly for
                # POST responses, and a keep-alive socket that has
                # just spent tens of seconds idle waiting for a
                # handler to finish is a common source of the browser
                # or the pgkit hub giving up on the connection.
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError) as e:
                # The client (browser, pgkit hub, curl) closed its
                # end before receiving this response. Not a server
                # bug: the pipeline ran, the response was serialised,
                # and the peer left. Log one line and unwind cleanly
                # rather than let socketserver dump a full traceback
                # into the playground's stderr.
                print(f"[server] client disconnected mid-response "
                      f"({len(body):,} bytes): {type(e).__name__}",
                      file=sys.stderr, flush=True)
                self.close_connection = True

        def _serve_module(self, name):
            source = module_sources.get(name)
            if source is None:
                available = ", ".join(sorted(module_sources.keys())) or "(none)"
                body = (f"Module not found: {name!r}\n"
                        f"Available modules: {available}\n").encode("utf-8")
                self.send_response(404)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            body = source.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type",
                             "application/javascript; charset=utf-8")
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

    return Handler


def _bind_server(port, handler_cls):
    """Return (httpd, used_port) or (None, None)."""
    for p in range(port, port + 20):
        try:
            return ThreadingHTTPServer(("127.0.0.1", p), handler_cls), p
        except OSError:
            continue
    return None, None


def _register_hub(hub_name, hub_role, used_port, html_file, group=None):
    if not hub_name:
        return None, False
    try:
        from .hub_client import ensure_running
        conn = ensure_running(
            hub_name, used_port,
            role=hub_role,
            html_file=html_file,
            group=group,
        )
        return conn, conn.registered.is_set()
    except Exception as e:
        print(f"  hub registration failed: {e}", file=sys.stderr)
        return None, False


def _public_url(hub_conn, registered, used_port, html_file):
    if registered:
        return hub_conn.hub_url + html_file
    return "http://127.0.0.1:%d/%s" % (used_port, html_file)


def serve_nonblocking(*, port, html_file, html_render,
                      routes=None, raw_routes=None, module_sources=None,
                      hub_name=None, hub_role="playground", group=None,
                      open_browser=False):
    """Start a playground's HTTP+WS server without blocking.

    Returns `(httpd, hub_conn, url)`.  The caller owns
    `httpd.serve_forever()` (in a thread) and `httpd.shutdown()` on
    teardown.  `hub_conn.stop_now()` closes the hub WS cleanly.
    """
    html = html_render()
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)

    handler_cls = make_handler(routes, raw_routes, module_sources)
    httpd, used_port = _bind_server(port, handler_cls)
    if httpd is None:
        raise RuntimeError(
            "Could not find a free port in [%d, %d)." % (port, port + 20))

    hub_conn, registered = _register_hub(
        hub_name, hub_role, used_port, html_file, group=group)
    url = _public_url(hub_conn, registered, used_port, html_file)

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    return httpd, hub_conn, url


def serve(*, port=8766, open_browser=True,
          html_file="playground.html",
          routes=None, raw_routes=None,
          module_sources=None, hub=None,
          hub_name=None, hub_role="playground"):
    """Standalone entry point.  Signature and behavior unchanged."""
    handler_cls = make_handler(routes, raw_routes, module_sources, hub)
    httpd, used_port = _bind_server(port, handler_cls)
    if httpd is None:
        print("Could not find a free port in [%d, %d)." % (port, port + 20))
        return

    hub_conn, registered = _register_hub(
        hub_name, hub_role, used_port, html_file)
    url = _public_url(hub_conn, registered, used_port, html_file)

    print("Serving %s" % os.getcwd())
    print("  -> open  %s" % url)
    print("  -> modules at /modules/ (%d file(s))"
          % len(module_sources or {}))
    print("  -> remote ws at ws://127.0.0.1:%d/remote" % used_port)
    if registered:
        print("  -> registered as %r (role=%s) with pgkit hub (port %d)"
              % (hub_name, hub_role, used_port))
    elif hub_name:
        print("  -> hub registration FAILED; running standalone at "
              "http://127.0.0.1:%d/%s" % (used_port, html_file))
    print("  Press Ctrl+C to stop.")

    if open_browser:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()
        if hub_conn is not None:
            hub_conn.stop_now()
